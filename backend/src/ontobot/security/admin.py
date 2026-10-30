"""관리 기능의 업무 규칙(TRD 14.1 관리 묶음) — 사용자·역할·데이터 그룹·감사 로그 조회.

라우터(``api/admin.py``)는 검증과 위임만 한다. 여기 규칙:

- 모든 변경은 같은 트랜잭션에 감사 기록을 남긴다(``audit.record``). 전후 값을 함께 남긴다.
- **자기 잠금 방지**: 관리자가 자기 자신을 정지하거나 자기 관리자 역할을 뺄 수 없다. 마지막 관리자가
  사라지면 관리 화면에 들어올 사람이 없다 — 그때는 CLI(``security.cli``)로만 되살릴 수 있다.
- 계정 정지·역할 변경은 그 사용자의 세션을 폐기한다. 역할은 요청마다 다시 읽지만, 정지된 계정이
  열어 둔 세션이 남아 있으면 안 된다.
- 감사 로그 조회도 감사 대상이다(TRD 9.3).
- 데이터 권한은 **그룹**이 준다(Phase 14). 그룹 권한·구성원을 바꿔도 세션은 그대로다 — 권한은
  요청마다 다시 읽는다. SSO 가 관리하는 소속(``external_id`` 가 있는 그룹)은 로그인 때 클레임으로
  다시 맞춘다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.security import (
    AppUser,
    AuditLog,
    DataGroup,
    GroupLinkPermission,
    GroupMember,
    GroupPropertyHide,
    GroupRowPolicy,
    GroupTypePermission,
    Role,
    UserRole,
)
from ontobot.ontology import registry
from ontobot.ontology.models import IDENT_MEMBER_RE, IDENT_TYPE_RE
from ontobot.query.validator import check_scopes
from ontobot.security import audit
from ontobot.security.auth import hash_password, revoke_user_sessions
from ontobot.security.pii import mask_contact
from ontobot.security.rbac import KNOWN_ROLES, ROLE_ADMIN


class AdminError(Exception):
    def __init__(self, message: str, *, code: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class UserView:
    id: int
    username: str | None
    display_name: str
    status: str
    auth: str  # local / oidc
    roles: list[str]
    created_at: datetime
    groups: list[str] = field(default_factory=list)
    # Action 알림 연락처 — 화면에는 마스킹해서만 낸다(PII).
    email: str | None = None
    phone: str | None = None


async def _roles_of(db: AsyncSession, user_id: int) -> list[str]:
    rows = await db.scalars(
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
        .order_by(Role.name)
    )
    return list(rows)


async def _groups_of(db: AsyncSession, user_id: int) -> list[str]:
    rows = await db.scalars(
        select(DataGroup.name)
        .join(GroupMember, GroupMember.group_id == DataGroup.id)
        .where(GroupMember.user_id == user_id)
        .order_by(DataGroup.name)
    )
    return list(rows)


async def _view(db: AsyncSession, u: AppUser) -> UserView:
    return UserView(
        id=u.id,
        username=u.username,
        display_name=u.display_name,
        status=u.status,
        auth="oidc" if u.oidc_sub else "local",
        roles=await _roles_of(db, u.id),
        created_at=u.created_at,
        groups=await _groups_of(db, u.id),
        email=mask_contact(u.email) if u.email else None,
        phone=mask_contact(u.phone) if u.phone else None,
    )


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9-]{6,19}$")


async def set_contacts(
    db: AsyncSession, *, actor_id: int, user_id: int, email: str | None, phone: str | None
) -> UserView:
    """Action 알림(메일·SMS)을 받을 연락처. 비우면 지운다. 감사에는 마스킹한 값만 남는다."""
    user = await db.get(AppUser, user_id)
    if user is None:
        raise AdminError("사용자를 찾을 수 없습니다", code="user_not_found", status=404)
    email = (email or "").strip() or None
    phone = (phone or "").strip() or None
    if email is not None and not EMAIL_RE.match(email):
        raise AdminError("메일 주소 형식이 아닙니다", code="invalid_contact", status=422)
    if phone is not None and not PHONE_RE.match(phone):
        raise AdminError("전화번호 형식이 아닙니다(숫자와 -)", code="invalid_contact", status=422)
    user.email, user.phone = email, phone
    audit.record(
        db,
        audit.USER_CONTACTS_CHANGED,
        actor_id=actor_id,
        target=f"app_user:{user_id}",
        payload={
            "email": mask_contact(email) if email else None,
            "phone": mask_contact(phone) if phone else None,
        },
    )
    await db.flush()
    return await _view(db, user)


async def list_users(db: AsyncSession) -> list[UserView]:
    users = await db.scalars(select(AppUser).order_by(AppUser.id))
    return [await _view(db, u) for u in users]


async def _role_ids(db: AsyncSession, names: Sequence[str]) -> dict[str, int]:
    unknown = sorted(set(names) - KNOWN_ROLES)
    if unknown:
        raise AdminError(f"알 수 없는 역할: {', '.join(unknown)}", code="unknown_role")
    rows = await db.execute(select(Role.name, Role.id).where(Role.name.in_(list(names))))
    return {r.name: r.id for r in rows}


async def create_local_user(
    db: AsyncSession,
    *,
    actor_id: int,
    username: str,
    display_name: str,
    password: str,
    roles: Sequence[str],
    groups: Sequence[str] = (),
) -> UserView:
    if await db.scalar(select(AppUser.id).where(AppUser.username == username)):
        raise AdminError("이미 있는 사용자 이름입니다", code="username_taken", status=409)
    try:
        password_hash = hash_password(password)
    except ValueError as e:
        raise AdminError(str(e), code="weak_password") from e
    ids = await _role_ids(db, roles)
    group_ids = await _group_ids(db, groups)
    user = AppUser(username=username, display_name=display_name, password_hash=password_hash)
    db.add(user)
    await db.flush()
    for rid in ids.values():
        db.add(UserRole(user_id=user.id, role_id=rid))
    for gid in group_ids.values():
        db.add(GroupMember(group_id=gid, user_id=user.id))
    audit.record(
        db,
        audit.USER_CREATED,
        actor_id=actor_id,
        target=f"app_user:{user.id}",
        payload={
            "username": username,
            "roles": sorted(roles),
            "groups": sorted(groups),
            "via": "admin",
        },
    )
    await db.flush()
    return await _view(db, user)


async def update_user(
    db: AsyncSession,
    *,
    actor_id: int,
    user_id: int,
    status: str | None = None,
    roles: Sequence[str] | None = None,
) -> UserView:
    user = await db.get(AppUser, user_id)
    if user is None:
        raise AdminError("사용자를 찾을 수 없습니다", code="user_not_found", status=404)
    before_roles = await _roles_of(db, user_id)
    if user_id == actor_id:
        if status == "disabled":
            raise AdminError("자기 계정은 정지할 수 없습니다", code="self_lockout", status=409)
        if roles is not None and ROLE_ADMIN in before_roles and ROLE_ADMIN not in roles:
            raise AdminError("자기 관리자 역할은 뺄 수 없습니다", code="self_lockout", status=409)
    revoke = False
    if status is not None and status != user.status:
        audit.record(
            db,
            audit.USER_STATUS_CHANGED,
            actor_id=actor_id,
            target=f"app_user:{user_id}",
            payload={"before": user.status, "after": status},
        )
        user.status = status
        revoke = status == "disabled"
    if roles is not None and sorted(set(roles)) != before_roles:
        ids = await _role_ids(db, roles)
        await db.execute(delete(UserRole).where(UserRole.user_id == user_id))
        for rid in ids.values():
            db.add(UserRole(user_id=user_id, role_id=rid))
        audit.record(
            db,
            audit.ROLE_CHANGED,
            actor_id=actor_id,
            target=f"app_user:{user_id}",
            payload={"before": before_roles, "after": sorted(set(roles))},
        )
        revoke = True
    if revoke and user_id != actor_id:
        await revoke_user_sessions(db, user_id)
    await db.flush()
    return await _view(db, user)


@dataclass(frozen=True)
class RoleView:
    """역할 — 화면·기능 권한. 데이터 권한은 그룹이 준다(``GroupView``)."""

    id: int
    name: str
    description: str | None


async def list_roles(db: AsyncSession) -> list[RoleView]:
    roles = await db.scalars(select(Role).order_by(Role.id))
    return [RoleView(r.id, r.name, r.description) for r in roles]


# ---------------------------------------------------------------- 데이터 그룹(Phase 14)

TYPE_FLAGS = ("can_read", "can_create", "can_edit", "can_delete")


@dataclass(frozen=True)
class TypeGrant:
    object_type: str
    can_read: bool = False
    can_create: bool = False
    can_edit: bool = False
    can_delete: bool = False
    # Phase 14b — 숨길 속성, 행 정책(QueryPlan Filter 문법의 조건 AND, None = 제한 없음)
    hidden: tuple[str, ...] = ()
    read_filters: tuple[dict[str, Any], ...] | None = None
    write_filters: tuple[dict[str, Any], ...] | None = None

    def flags(self) -> list[str]:
        return [f for f in TYPE_FLAGS if getattr(self, f)]

    def summary(self) -> dict[str, Any]:
        """감사 기록용 — 플래그·숨김·정책 유무(정책 값은 조건이라 그대로 남긴다)."""
        out: dict[str, Any] = {"flags": self.flags()}
        if self.hidden:
            out["hidden"] = list(self.hidden)
        if self.read_filters is not None:
            out["read_filters"] = list(self.read_filters)
        if self.write_filters is not None:
            out["write_filters"] = list(self.write_filters)
        return out


@dataclass(frozen=True)
class GroupView:
    id: int
    name: str
    display_name: str
    description: str | None
    external_id: str | None
    member_ids: list[int]
    types: list[TypeGrant]
    links: list[str]  # 연결할 수 있는 링크 타입


async def _group_ids(db: AsyncSession, names: Sequence[str]) -> dict[str, int]:
    rows = await db.execute(
        select(DataGroup.name, DataGroup.id).where(DataGroup.name.in_(list(names)))
    )
    found = {r.name: r.id for r in rows}
    unknown = sorted(set(names) - found.keys())
    if unknown:
        raise AdminError(f"알 수 없는 그룹: {', '.join(unknown)}", code="unknown_group")
    return found


async def _group(db: AsyncSession, group_id: int) -> DataGroup:
    g = await db.get(DataGroup, group_id)
    if g is None:
        raise AdminError("그룹을 찾을 수 없습니다", code="group_not_found", status=404)
    return g


async def _group_view(db: AsyncSession, g: DataGroup) -> GroupView:
    members = await db.scalars(
        select(GroupMember.user_id)
        .where(GroupMember.group_id == g.id)
        .order_by(GroupMember.user_id)
    )
    types = await db.scalars(
        select(GroupTypePermission)
        .where(GroupTypePermission.group_id == g.id)
        .order_by(GroupTypePermission.object_type)
    )
    links = await db.scalars(
        select(GroupLinkPermission.link_type)
        .where(GroupLinkPermission.group_id == g.id, GroupLinkPermission.can_link.is_(True))
        .order_by(GroupLinkPermission.link_type)
    )
    hidden: dict[str, list[str]] = {}
    for h in await db.scalars(
        select(GroupPropertyHide)
        .where(GroupPropertyHide.group_id == g.id)
        .order_by(GroupPropertyHide.property)
    ):
        hidden.setdefault(h.object_type, []).append(h.property)
    policies: dict[tuple[str, str], tuple[dict[str, Any], ...]] = {
        (r.object_type, r.applies_to): tuple(r.filters)
        for r in await db.scalars(select(GroupRowPolicy).where(GroupRowPolicy.group_id == g.id))
    }
    grants = [
        TypeGrant(
            t.object_type,
            t.can_read,
            t.can_create,
            t.can_edit,
            t.can_delete,
            tuple(hidden.get(t.object_type, ())),
            policies.get((t.object_type, "read")),
            policies.get((t.object_type, "write")),
        )
        for t in types
    ]
    return GroupView(
        g.id,
        g.name,
        g.display_name,
        g.description,
        g.external_id,
        list(members),
        [x for x in grants if x.flags()],
        list(links),
    )


async def list_groups(db: AsyncSession) -> list[GroupView]:
    return [
        await _group_view(db, g)
        for g in await db.scalars(select(DataGroup).order_by(DataGroup.name))
    ]


async def create_group(
    db: AsyncSession,
    *,
    actor_id: int | None,
    name: str,
    display_name: str,
    description: str | None = None,
    external_id: str | None = None,
) -> GroupView:
    if not IDENT_MEMBER_RE.match(name):
        raise AdminError(
            "그룹 이름은 영문 소문자로 시작하는 a-z0-9_ 입니다", code="invalid_group_name"
        )
    if await db.scalar(select(DataGroup.id).where(DataGroup.name == name)):
        raise AdminError("이미 있는 그룹 이름입니다", code="group_taken", status=409)
    await _check_external_id(db, external_id, None)
    g = DataGroup(
        name=name, display_name=display_name, description=description, external_id=external_id
    )
    db.add(g)
    await db.flush()
    audit.record(
        db,
        audit.GROUP_CHANGED,
        actor_id=actor_id,
        target=f"group:{name}",
        payload={"op": "create", "display_name": display_name, "external_id": external_id},
    )
    return await _group_view(db, g)


async def _check_external_id(
    db: AsyncSession, external_id: str | None, group_id: int | None
) -> None:
    if external_id is None:
        return
    other = await db.scalar(select(DataGroup.id).where(DataGroup.external_id == external_id))
    if other is not None and other != group_id:
        raise AdminError(
            "다른 그룹이 같은 SSO 그룹 ID 를 씁니다", code="external_id_taken", status=409
        )


async def update_group(
    db: AsyncSession,
    *,
    actor_id: int,
    group_id: int,
    display_name: str,
    description: str | None,
    external_id: str | None,
) -> GroupView:
    g = await _group(db, group_id)
    await _check_external_id(db, external_id, g.id)
    before = {"display_name": g.display_name, "external_id": g.external_id}
    g.display_name, g.description, g.external_id = display_name, description, external_id
    await db.flush()
    audit.record(
        db,
        audit.GROUP_CHANGED,
        actor_id=actor_id,
        target=f"group:{g.name}",
        payload={
            "op": "update",
            "before": before,
            "after": {"display_name": display_name, "external_id": external_id},
        },
    )
    return await _group_view(db, g)


async def delete_group(db: AsyncSession, *, actor_id: int, group_id: int) -> None:
    g = await _group(db, group_id)
    view = await _group_view(db, g)
    await db.delete(g)
    await db.flush()
    audit.record(
        db,
        audit.GROUP_CHANGED,
        actor_id=actor_id,
        target=f"group:{g.name}",
        payload={
            "op": "delete",
            "members": view.member_ids,
            "types": {t.object_type: t.flags() for t in view.types},
        },
    )


async def set_members(
    db: AsyncSession, *, actor_id: int | None, group_id: int, user_ids: Sequence[int], via: str
) -> GroupView:
    """구성원을 주어진 사용자 집합으로 **바꾼다**(교체)."""
    g = await _group(db, group_id)
    wanted = set(user_ids)
    known = set(await db.scalars(select(AppUser.id).where(AppUser.id.in_(list(wanted)))))
    if wanted - known:
        raise AdminError(f"알 수 없는 사용자: {sorted(wanted - known)}", code="user_not_found")
    current = set(await db.scalars(select(GroupMember.user_id).where(GroupMember.group_id == g.id)))
    if current != wanted:
        await db.execute(
            delete(GroupMember).where(
                GroupMember.group_id == g.id, GroupMember.user_id.in_(list(current - wanted))
            )
        )
        for uid in sorted(wanted - current):
            db.add(GroupMember(group_id=g.id, user_id=uid))
        await db.flush()
        audit.record(
            db,
            audit.GROUP_MEMBERS_CHANGED,
            actor_id=actor_id,
            target=f"group:{g.name}",
            payload={
                "added": sorted(wanted - current),
                "removed": sorted(current - wanted),
                "via": via,
            },
        )
    return await _group_view(db, g)


async def set_permissions(
    db: AsyncSession,
    *,
    actor_id: int | None,
    group_id: int,
    types: Sequence[TypeGrant],
    links: Sequence[str],
) -> GroupView:
    """그룹의 타입·링크 권한을 주어진 것으로 **바꾼다**(교체).

    이름 형식만 보고, 게시본에 없는 이름도 받는다 — 권한은 스키마 버전과 따로 산다(게시 전 draft 의
    타입에 미리 줄 수 있다).
    """
    g = await _group(db, group_id)
    bad = [t.object_type for t in types if not IDENT_TYPE_RE.match(t.object_type)]
    bad += [lk for lk in links if not IDENT_MEMBER_RE.match(lk)]
    if bad:
        raise AdminError(f"이름 형식이 아닙니다: {bad}", code="invalid_type")
    _check_restrictions(types)
    before = await _group_view(db, g)
    for model in (GroupTypePermission, GroupLinkPermission, GroupPropertyHide, GroupRowPolicy):
        await db.execute(delete(model).where(model.group_id == g.id))
    for t in {x.object_type: x for x in types}.values():
        if not t.flags():
            continue
        db.add(
            GroupTypePermission(
                group_id=g.id,
                object_type=t.object_type,
                can_read=t.can_read,
                can_create=t.can_create,
                can_edit=t.can_edit,
                can_delete=t.can_delete,
            )
        )
        for prop in sorted(set(t.hidden)):
            db.add(GroupPropertyHide(group_id=g.id, object_type=t.object_type, property=prop))
        for applies_to, filters in (("read", t.read_filters), ("write", t.write_filters)):
            if filters is not None:
                db.add(
                    GroupRowPolicy(
                        group_id=g.id,
                        object_type=t.object_type,
                        applies_to=applies_to,
                        filters=list(filters),
                    )
                )
    for lk in sorted(set(links)):
        db.add(GroupLinkPermission(group_id=g.id, link_type=lk, can_link=True))
    await db.flush()
    after = await _group_view(db, g)
    if (before.types, before.links) != (after.types, after.links):
        audit.record(
            db,
            audit.PERMISSION_CHANGED,
            actor_id=actor_id,
            target=f"group:{g.name}",
            payload={
                "before": {t.object_type: t.summary() for t in before.types},
                "after": {t.object_type: t.summary() for t in after.types},
                "links_before": before.links,
                "links_after": after.links,
            },
        )
    return after


def _check_restrictions(types: Sequence[TypeGrant]) -> None:
    """숨김·행 정책 검사 — 게시본이 있으면 그 정의로 본다(없는 속성·pk·제목 숨김·잘못된 조건은
    거부).

    정책이 잘못되면 실행 때는 그 정책을 빼고 닫힌 쪽(볼 행 없음)으로 판정하지만, 저장 때 먼저
    막는다.
    """
    schema = registry.current()
    errors: list[str] = []
    for t in types:
        if not (t.hidden or t.read_filters is not None or t.write_filters is not None):
            continue
        td = schema.document.object_type(t.object_type) if schema is not None else None
        bad_names = [p for p in t.hidden if not IDENT_MEMBER_RE.match(p)]
        if bad_names:
            errors.append(f"{t.object_type}: 속성 이름 형식이 아닙니다 {bad_names}")
        if td is not None:
            frozen = {td.pk_property, td.title_property}
            if frozen & set(t.hidden):
                errors.append(f"{t.object_type}: pk·제목 속성은 숨길 수 없습니다")
            unknown = [p for p in t.hidden if td.property(p) is None]
            if unknown:
                errors.append(f"{t.object_type}: 없는 속성 {unknown}")
        for label, filters in (("읽기", t.read_filters), ("쓰기", t.write_filters)):
            if filters is None:
                continue
            if schema is None or td is None:
                continue  # 게시 전 타입 — 형식만(실행 때 검증되지 않으면 닫힌 쪽)
            scope = check_scopes(schema.document, {t.object_type: [list(filters)]})
            if scope[t.object_type] == ():
                errors.append(f"{t.object_type}: {label} 행 정책 조건이 올바르지 않습니다")
    if errors:
        raise AdminError("; ".join(errors), code="invalid_restriction")


async def grant_read(
    db: AsyncSession, group_name: str, object_type: str, *, via: str, edit: bool = False
) -> None:
    """설치·시드 스크립트용 — 그룹이 없으면 만들고 그 타입 읽기 권한을 더한다(있던 권한 유지)."""
    gid = await db.scalar(select(DataGroup.id).where(DataGroup.name == group_name))
    if gid is None:
        gid = (await create_group(db, actor_id=None, name=group_name, display_name=group_name)).id
    g = await _group(db, gid)
    row = await db.scalar(
        select(GroupTypePermission).where(
            GroupTypePermission.group_id == g.id, GroupTypePermission.object_type == object_type
        )
    )
    if row is None:
        db.add(
            GroupTypePermission(
                group_id=g.id, object_type=object_type, can_read=True, can_edit=edit
            )
        )
    elif not row.can_read or (edit and not row.can_edit):
        row.can_read = True
        row.can_edit = row.can_edit or edit
    else:
        return
    await db.flush()
    audit.record(
        db,
        audit.PERMISSION_CHANGED,
        actor_id=None,
        target=f"group:{group_name}",
        payload={"object_type": object_type, "can_read": True, "can_edit": edit, "via": via},
    )


async def sync_external_groups(db: AsyncSession, user_id: int, claim: Sequence[str]) -> None:
    """SSO 로그인 — ``external_id`` 가 있는 그룹의 소속을 클레임 값과 **정확히** 맞춘다.

    ``external_id`` 가 없는 그룹(관리 화면에서 직접 넣은 소속)은 건드리지 않는다.
    """
    wanted = set(claim)
    managed = {
        g.id: g
        for g in await db.scalars(select(DataGroup).where(DataGroup.external_id.is_not(None)))
    }
    current = set(
        await db.scalars(
            select(GroupMember.group_id).where(
                GroupMember.user_id == user_id, GroupMember.group_id.in_(list(managed))
            )
        )
    )
    should = {gid for gid, g in managed.items() if g.external_id in wanted}
    for gid in should - current:
        db.add(GroupMember(group_id=gid, user_id=user_id))
    if current - should:
        await db.execute(
            delete(GroupMember).where(
                GroupMember.user_id == user_id, GroupMember.group_id.in_(list(current - should))
            )
        )
    if should != current:
        await db.flush()
        audit.record(
            db,
            audit.GROUP_MEMBERS_CHANGED,
            actor_id=user_id,
            target=f"app_user:{user_id}",
            payload={
                "added": sorted(managed[g].name for g in should - current),
                "removed": sorted(managed[g].name for g in current - should),
                "via": "oidc",
            },
        )


async def audit_logs(
    db: AsyncSession,
    *,
    actor_id: int,
    event: str | None = None,
    user_id: int | None = None,
    before_id: int | None = None,
    limit: int = 100,
) -> list[AuditLog]:
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(max(limit, 1), 500))
    if event:
        q = q.where(AuditLog.event == event)
    if user_id is not None:
        q = q.where(AuditLog.actor_id == user_id)
    if before_id is not None:
        q = q.where(AuditLog.id < before_id)
    rows = list(await db.scalars(q))
    # 조회 기록은 결과를 읽은 **뒤에** 넣는다 — 자기 자신이 결과에 섞이지 않게.
    audit.record(
        db,
        audit.AUDIT_VIEWED,
        actor_id=actor_id,
        payload={"event": event, "user_id": user_id, "before_id": before_id, "rows": len(rows)},
    )
    return rows
