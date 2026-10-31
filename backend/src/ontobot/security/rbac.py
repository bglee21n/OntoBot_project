"""권한 판정 — 권한을 계산하는 코드는 이 파일에만 둔다(TRD 16.3-6).

데이터 권한은 **데이터 그룹**이 준다(Phase 14 — 역할은 화면·기능, 그룹은 데이터).
사용자의 권한은 소속 그룹 권한의 합집합이다. 판정 지점 셋(플래너 컨텍스트·검증기·실행기,
TRD 9.1)이 모두 ``readable_types`` 의 결과를 받아 쓴다 — 각자 계산하면 조용히 갈라진다.

역할 판정(``has_role``)은 API 접근 제어용이고, 데이터 권한과는 다른 축이다.
관리자라도 그룹 권한이 없으면 객체를 읽지 못한다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.security import (
    DataGroup,
    GroupMember,
    GroupPropertyHide,
    GroupRowPolicy,
    GroupTypePermission,
    Role,
    UserRole,
)

# 역할 이름의 정본은 마이그레이션 0001 의 시드다. 코드는 이 상수로만 비교한다.
ROLE_USER = "user"
ROLE_MODELER = "modeler"
ROLE_BOT_BUILDER = "bot_builder"
ROLE_DOMAIN_EXPERT = "domain_expert"
ROLE_ADMIN = "admin"
KNOWN_ROLES = frozenset({ROLE_USER, ROLE_MODELER, ROLE_BOT_BUILDER, ROLE_DOMAIN_EXPERT, ROLE_ADMIN})


async def role_names(db: AsyncSession, user_id: int) -> frozenset[str]:
    rows = await db.scalars(
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    )
    return frozenset(rows)


def has_role(roles: Iterable[str], *required: str) -> bool:
    """``required`` 중 하나라도 가지면 참."""
    held = set(roles)
    return any(r in held for r in required)


async def document_groups(db: AsyncSession, user_id: int) -> frozenset[int]:
    """문서를 읽을 수 있는 그룹(Phase 16) — 소속 그룹 전부.

    문서는 그룹 하나에 속하고, 그 구성원만 본다.

    관리자라도 소속이 아니면 그 그룹 문서를 읽지 못한다(타입 권한과 같은 원칙).
    """
    rows = await db.scalars(select(GroupMember.group_id).where(GroupMember.user_id == user_id))
    return frozenset(rows)


async def readable_types(db: AsyncSession, user_id: int) -> frozenset[str]:
    """읽을 수 있는 객체 타입 — 소속 데이터 그룹 읽기 권한의 합집합(Phase 14)."""
    return await _types_with(db, GroupTypePermission.can_read, user_id=user_id)


async def editable_types(db: AsyncSession, user_id: int) -> frozenset[str]:
    """편집할 수 있는 객체 타입.

    Action(편집 레이어)을 요청하려면 대상 타입을 읽고 **편집**할 수 있어야 한다.
    """
    return await _types_with(db, GroupTypePermission.can_edit, user_id=user_id)


async def readable_types_for_group(db: AsyncSession, group: str) -> frozenset[str]:
    """그룹 하나의 읽기 권한.

    평가 문항의 ``test_role`` 이 팩의 평가 역할이 아니면 같은 이름의 데이터 그룹으로 파이프라인을
    돌린다(TRD 13.1). 역할 읽기 권한은 마이그레이션 0012 에서 같은 이름의 그룹으로 옮겼다.
    """
    return await _types_with(db, GroupTypePermission.can_read, group=group)


async def _types_with(
    db: AsyncSession, flag: Any, *, user_id: int | None = None, group: str | None = None
) -> frozenset[str]:
    q = (
        select(GroupTypePermission.object_type)
        .join(DataGroup, DataGroup.id == GroupTypePermission.group_id)
        .where(flag.is_(True))
        .distinct()
    )
    if user_id is not None:
        q = q.join(GroupMember, GroupMember.group_id == DataGroup.id).where(
            GroupMember.user_id == user_id
        )
    if group is not None:
        q = q.where(DataGroup.name == group)
    return frozenset(await db.scalars(q))


# ---------------------------------------------------------------- 속성·행 단위(Phase 14b)
#
# 합집합 규칙을 그대로 따른다(허용만, 거부 없음):
# - 속성은 그 타입을 **읽게 해 주는 모든 그룹**이 숨길 때만 숨겨진다(한 그룹이라도 보여 주면
# 보인다).
# - 행은 그 권한(읽기·편집)을 주는 그룹마다 정책을 OR 로 묶는다. 정책이 없는 그룹이 하나라도
# 있으면 제한 없음.
# 판정 결과(``Access``)를 호출부가 받아 쓴다 — 요약·검증·컴파일러·근거 카드·Action 이 각자
# 계산하지 않는다.

Policy = tuple[dict[str, Any], ...]  # QueryPlan Filter 문법의 조건 목록(AND)


@dataclass(frozen=True)
class TypeAccess:
    hidden: frozenset[str] = frozenset()
    # None = 제한 없음. 아니면 정책들의 OR(각 정책은 조건 AND).
    read_rows: tuple[Policy, ...] | None = None
    write_rows: tuple[Policy, ...] | None = None


@dataclass(frozen=True)
class Access:
    readable: frozenset[str]
    editable: frozenset[str]
    types: dict[str, TypeAccess] = field(default_factory=dict)

    def of(self, object_type: str) -> TypeAccess:
        return self.types.get(object_type, TypeAccess())

    def hidden(self) -> dict[str, frozenset[str]]:
        return {t: a.hidden for t, a in self.types.items() if a.hidden}

    def read_scopes(self) -> dict[str, tuple[Policy, ...]]:
        return {t: a.read_rows for t, a in self.types.items() if a.read_rows is not None}


@dataclass
class _Grant:
    read: bool = False
    edit: bool = False
    hidden: set[str] = field(default_factory=set)
    rows: dict[str, Policy] = field(default_factory=dict)  # read/write → 정책


async def access(db: AsyncSession, user_id: int) -> Access:
    """사용자의 데이터 권한 전부 — 타입 읽기·편집, 숨긴 속성, 행 정책(읽기·쓰기)."""
    groups = list(
        await db.scalars(select(GroupMember.group_id).where(GroupMember.user_id == user_id))
    )
    return await access_for_groups(db, groups)


async def access_for_groups(db: AsyncSession, group_ids: Iterable[int]) -> Access:
    ids = list(group_ids)
    grants: dict[tuple[int, str], _Grant] = {}
    if ids:
        for p in await db.scalars(
            select(GroupTypePermission).where(GroupTypePermission.group_id.in_(ids))
        ):
            grants[(p.group_id, p.object_type)] = _Grant(read=p.can_read, edit=p.can_edit)
        for h in await db.scalars(
            select(GroupPropertyHide).where(GroupPropertyHide.group_id.in_(ids))
        ):
            if (h.group_id, h.object_type) in grants:
                grants[(h.group_id, h.object_type)].hidden.add(h.property)
        for r in await db.scalars(select(GroupRowPolicy).where(GroupRowPolicy.group_id.in_(ids))):
            if (r.group_id, r.object_type) in grants:
                grants[(r.group_id, r.object_type)].rows[r.applies_to] = tuple(r.filters)
    return merge(_by_type(grants))


def _by_type(grants: dict[tuple[int, str], _Grant]) -> dict[str, list[_Grant]]:
    out: dict[str, list[_Grant]] = {}
    for (_, t), g in grants.items():
        out.setdefault(t, []).append(g)
    return out


def merge(by_type: dict[str, list[_Grant]]) -> Access:
    """타입별 그룹 권한 목록 → 사용자 권한(합집합 규칙). 순수 함수."""
    readable: set[str] = set()
    editable: set[str] = set()
    types: dict[str, TypeAccess] = {}
    for t, gs in by_type.items():
        readers = [g for g in gs if g.read]
        editors = [g for g in gs if g.edit and g.read]
        if not readers:
            continue
        readable.add(t)
        if editors:
            editable.add(t)
        hidden = frozenset(set.intersection(*(g.hidden for g in readers)))

        def rows(members: list[_Grant], key: str) -> tuple[Policy, ...] | None:
            if not members or any(key not in g.rows for g in members):
                return None
            # 같은 정책은 한 번만(조건은 사전이라 정규 JSON 으로 비교한다).
            uniq = {json.dumps(g.rows[key], sort_keys=True): g.rows[key] for g in members}
            return tuple(uniq.values())

        access_t = TypeAccess(hidden, rows(readers, "read"), rows(editors, "write"))
        if access_t != TypeAccess():
            types[t] = access_t
    return Access(frozenset(readable), frozenset(editable), types)


def grant(
    *,
    read: bool = True,
    edit: bool = False,
    hidden: Iterable[str] = (),
    read_rows: Policy | None = None,
    write_rows: Policy | None = None,
) -> _Grant:
    """평가 역할(골든 팩)·테스트가 그룹 하나의 권한을 만들 때 — ``merge`` 와 함께 쓴다."""
    rows: dict[str, Policy] = {}
    if read_rows is not None:
        rows["read"] = tuple(read_rows)
    if write_rows is not None:
        rows["write"] = tuple(write_rows)
    return _Grant(read, edit, set(hidden), rows)


# ---------------------------------------------------------------- Action(PRD 5.3, TRD 17.1)
#
# 데이터 읽기 권한(대상 타입)은 호출부가 readable_types 로 따로 확인한다 — 둘 다 통과해야 한다.


def can_request_action(roles: Iterable[str], requester_roles: Iterable[str]) -> bool:
    """요청 역할이 정해져 있지 않으면 누구나(대상 타입을 읽을 수 있으면) 요청한다."""
    wanted = list(requester_roles)
    return not wanted or has_role(roles, *wanted)


def can_decide_action(
    roles: Iterable[str], approval_roles: Iterable[str], *, allow_self: bool, is_requester: bool
) -> bool:
    """승인·거부할 수 있는가. 요청자 본인은 ``allow_self`` 일 때만, 남은 승인 역할이 있어야 한다."""
    if is_requester:
        return allow_self
    return has_role(roles, *approval_roles)


def can_withdraw_action(*, is_requester: bool) -> bool:
    """미리보기 단계의 요청은 요청자 본인이 거둘 수 있다."""
    return is_requester


def can_rollback_action(roles: Iterable[str], approval_roles: Iterable[str]) -> bool:
    """롤백은 승인 역할 또는 관리자. 요청자가 혼자 실행한 Action(allow_self)도 관리자는 되돌린다."""
    return has_role(roles, *approval_roles, ROLE_ADMIN)
