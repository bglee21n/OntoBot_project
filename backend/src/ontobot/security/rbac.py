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
