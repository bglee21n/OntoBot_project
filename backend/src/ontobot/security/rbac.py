"""권한 판정 — 권한을 계산하는 코드는 이 파일에만 둔다(TRD 16.3-6).

MVP는 타입 단위 읽기 권한이다(PRD 5.5). 판정 지점 셋(플래너 컨텍스트·검증기·실행기, TRD 9.1)이
모두 ``readable_types`` 의 결과를 받아 쓴다 — 각자 계산하면 조용히 갈라진다.

역할 판정(``has_role``)은 API 접근 제어용이고, 데이터 읽기 권한과는 다른 축이다.
관리자라도 ``type_permission`` 이 없으면 객체를 읽지 못한다.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.security import Role, TypePermission, UserRole

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


async def readable_types(db: AsyncSession, user_id: int) -> frozenset[str]:
    rows = await db.scalars(
        select(TypePermission.object_type)
        .join(UserRole, UserRole.role_id == TypePermission.role_id)
        .where(UserRole.user_id == user_id, TypePermission.can_read.is_(True))
        .distinct()
    )
    return frozenset(rows)


async def readable_types_for_role(db: AsyncSession, role: str) -> frozenset[str]:
    """역할 하나의 읽기 권한 — 평가 문항의 ``test_role`` 로 파이프라인을 돌릴 때 쓴다(TRD 13.1)."""
    rows = await db.scalars(
        select(TypePermission.object_type)
        .join(Role, Role.id == TypePermission.role_id)
        .where(Role.name == role, TypePermission.can_read.is_(True))
    )
    return frozenset(rows)


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
