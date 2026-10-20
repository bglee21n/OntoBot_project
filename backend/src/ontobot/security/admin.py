"""관리 기능의 업무 규칙(TRD 14.1 관리 묶음) — 사용자·역할·타입 권한·감사 로그 조회.

라우터(``api/admin.py``)는 검증과 위임만 한다. 여기 규칙:

- 모든 변경은 같은 트랜잭션에 감사 기록을 남긴다(``audit.record``). 전후 값을 함께 남긴다.
- **자기 잠금 방지**: 관리자가 자기 자신을 정지하거나 자기 관리자 역할을 뺄 수 없다. 마지막 관리자가
  사라지면 관리 화면에 들어올 사람이 없다 — 그때는 CLI(``security.cli``)로만 되살릴 수 있다.
- 계정 정지·역할 변경은 그 사용자의 세션을 폐기한다. 역할은 요청마다 다시 읽지만, 정지된 계정이
  열어 둔 세션이 남아 있으면 안 된다.
- 감사 로그 조회도 감사 대상이다(TRD 9.3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.security import AppUser, AuditLog, Role, TypePermission, UserRole
from ontobot.ontology.models import IDENT_TYPE_RE
from ontobot.security import audit
from ontobot.security.auth import hash_password, revoke_user_sessions
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


async def _roles_of(db: AsyncSession, user_id: int) -> list[str]:
    rows = await db.scalars(
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
        .order_by(Role.name)
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
    )


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
) -> UserView:
    if await db.scalar(select(AppUser.id).where(AppUser.username == username)):
        raise AdminError("이미 있는 사용자 이름입니다", code="username_taken", status=409)
    try:
        password_hash = hash_password(password)
    except ValueError as e:
        raise AdminError(str(e), code="weak_password") from e
    ids = await _role_ids(db, roles)
    user = AppUser(username=username, display_name=display_name, password_hash=password_hash)
    db.add(user)
    await db.flush()
    for rid in ids.values():
        db.add(UserRole(user_id=user.id, role_id=rid))
    audit.record(
        db,
        audit.USER_CREATED,
        actor_id=actor_id,
        target=f"app_user:{user.id}",
        payload={"username": username, "roles": sorted(roles), "via": "admin"},
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
    id: int
    name: str
    description: str | None
    readable_types: list[str]


async def list_roles(db: AsyncSession) -> list[RoleView]:
    roles = list(await db.scalars(select(Role).order_by(Role.id)))
    perms = await db.execute(
        select(TypePermission.role_id, TypePermission.object_type).where(
            TypePermission.can_read.is_(True)
        )
    )
    by_role: dict[int, list[str]] = {}
    for p in perms:
        by_role.setdefault(p.role_id, []).append(p.object_type)
    return [RoleView(r.id, r.name, r.description, sorted(by_role.get(r.id, []))) for r in roles]


async def set_readable_types(
    db: AsyncSession, *, actor_id: int | None, role_id: int, object_types: Sequence[str]
) -> RoleView:
    """역할의 읽기 권한을 주어진 타입 집합으로 **바꾼다**(더하기가 아니라 교체).

    ``actor_id`` 가 None 이면 설치·시드 스크립트가 바꾼 것이다(감사 로그에 행위자 없이 남는다).
    """
    role = await db.get(Role, role_id)
    if role is None:
        raise AdminError("역할을 찾을 수 없습니다", code="role_not_found", status=404)
    bad = [t for t in object_types if not IDENT_TYPE_RE.match(t)]
    if bad:
        raise AdminError(f"타입 이름 형식이 아닙니다: {bad}", code="invalid_type")
    current = {
        p.object_type: p
        for p in await db.scalars(select(TypePermission).where(TypePermission.role_id == role_id))
    }
    before = sorted(t for t, p in current.items() if p.can_read)
    after = sorted(set(object_types))
    if before != after:
        for t, p in current.items():
            p.can_read = t in after
        for t in after:
            if t not in current:
                db.add(TypePermission(role_id=role_id, object_type=t, can_read=True))
        audit.record(
            db,
            audit.PERMISSION_CHANGED,
            actor_id=actor_id,
            target=f"role:{role.name}",
            payload={"before": before, "after": after},
        )
        await db.flush()
    return RoleView(role.id, role.name, role.description, after)


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
