"""라우터 공용 의존성 — DB 세션, 현재 사용자, 역할 요구(TRD 16.1)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.api.errors import ProblemError
from ontobot.config import Settings, get_settings
from ontobot.db.session import session_scope
from ontobot.security import rbac
from ontobot.security.auth import resolve_session


async def get_db() -> AsyncIterator[AsyncSession]:
    """요청 하나 = 트랜잭션 하나. 예외가 나면 롤백한다(라우터가 직접 commit 하지 않는다)."""
    async for session in session_scope():
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


Db = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@dataclass(frozen=True)
class CurrentUser:
    id: int
    display_name: str
    roles: frozenset[str]


async def current_user(request: Request, db: Db, settings: SettingsDep) -> CurrentUser:
    token = request.cookies.get(settings.session_cookie_name)
    user = await resolve_session(db, token) if token else None
    if user is None:
        raise ProblemError(401, "로그인이 필요합니다", code="unauthenticated")
    return CurrentUser(
        id=user.id, display_name=user.display_name, roles=await rbac.role_names(db, user.id)
    )


User = Annotated[CurrentUser, Depends(current_user)]


def require_role(*roles: str) -> Callable[..., Coroutine[Any, Any, CurrentUser]]:
    """역할 중 하나를 요구한다. 판정은 ``rbac.has_role`` 하나뿐이다(16.3-6)."""

    async def _dep(user: User) -> CurrentUser:
        if not rbac.has_role(user.roles, *roles):
            # 403 은 로그아웃시키지 않는다 — 다시 로그인해도 같은 결과다.
            raise ProblemError(
                403,
                "이 기능을 쓸 권한이 없습니다",
                code="forbidden",
                extra={"required_roles": list(roles)},
            )
        return user

    return _dep
