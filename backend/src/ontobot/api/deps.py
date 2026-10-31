"""라우터 공용 의존성 — DB 세션, 현재 사용자, 역할 요구(TRD 16.1)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.api.errors import ProblemError
from ontobot.config import Settings, get_settings
from ontobot.db.session import session_scope
from ontobot.security import api_keys, rbac
from ontobot.security.auth import resolve_session


async def get_db() -> AsyncIterator[AsyncSession]:
    """요청 하나 = 트랜잭션 하나. 예외가 나면 롤백한다(라우터가 직접 commit 하지 않는다)."""
    async with session_scope() as session:
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


# ---------------------------------------------------------------- 공개 API(Phase 17b)

_bearer = HTTPBearer(auto_error=False, description="API 키 — `obk_<prefix>_<secret>`")


async def api_caller(
    db: Db,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> tuple[CurrentUser, frozenset[str]]:
    """``Authorization: Bearer <API 키>`` 만 받는다 — 세션 쿠키로는 공개 API 를 부를 수 없다
    (다른 사이트가 로그인한 사람의 브라우저로 부르는 길을 만들지 않는다)."""
    caller = await api_keys.resolve(db, credentials.credentials) if credentials else None
    if caller is None:
        raise ProblemError(401, "유효한 API 키가 필요합니다", code="invalid_api_key")
    user = CurrentUser(
        id=caller.user.id,
        display_name=caller.user.display_name,
        roles=await rbac.role_names(db, caller.user.id),
    )
    return user, caller.scopes


def require_scope(scope: str) -> Callable[..., Coroutine[Any, Any, CurrentUser]]:
    """키에 그 스코프가 있어야 한다. 판정은 ``rbac.has_scope`` 하나뿐이다."""

    async def _dep(
        caller: Annotated[tuple[CurrentUser, frozenset[str]], Depends(api_caller)],
    ) -> CurrentUser:
        user, scopes = caller
        if not rbac.has_scope(scopes, scope):
            raise ProblemError(
                403,
                "이 API 키에 없는 스코프입니다",
                code="missing_scope",
                extra={"required_scope": scope},
            )
        return user

    return _dep
