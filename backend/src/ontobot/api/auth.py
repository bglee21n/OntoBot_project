"""인증 라우터(TRD 14.1). OIDC(``/auth/login``·``/auth/callback``)는 같은 세션 계층 위에 붙는다."""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ontobot.api.deps import Db, SettingsDep, User
from ontobot.api.errors import ProblemError
from ontobot.config import Settings
from ontobot.security import audit, oidc
from ontobot.security.auth import IssuedSession, authenticate_local, create_session, revoke_session
from ontobot.security.rbac import role_names

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)


class LocalLogin(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class Providers(BaseModel):
    local: bool
    oidc: bool


class Me(BaseModel):
    id: int
    display_name: str
    roles: list[str]


def set_session_cookie(response: Response, settings: Settings, issued: IssuedSession) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        issued.token,
        expires=issued.expires_at,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


@router.post("/local/login", response_model=Me)
async def local_login(body: LocalLogin, response: Response, db: Db, settings: SettingsDep) -> Me:
    if not settings.local_auth_enabled:
        raise ProblemError(404, "로컬 로그인이 꺼져 있습니다", code="local_auth_disabled")
    user = await authenticate_local(db, body.username, body.password)
    if user is None:
        audit.record(db, audit.LOGIN_FAILED, actor_id=None, payload={"username": body.username})
        await db.commit()  # 실패 기록은 응답 오류와 무관하게 남긴다
        # 계정이 없는지·비밀번호가 틀렸는지·정지됐는지 가르지 않는다.
        raise ProblemError(
            401, "아이디 또는 비밀번호가 올바르지 않습니다", code="invalid_credentials"
        )
    issued = await create_session(
        db, user, method="local", ttl=timedelta(hours=settings.session_ttl_hours)
    )
    audit.record(db, audit.LOGIN, actor_id=user.id, payload={"method": "local"})
    set_session_cookie(response, settings, issued)
    return Me(
        id=user.id, display_name=user.display_name, roles=sorted(await role_names(db, user.id))
    )


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: Db, settings: SettingsDep) -> None:
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        user_id = await revoke_session(db, token)
        if user_id is not None:
            audit.record(db, audit.LOGOUT, actor_id=user_id)
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/me", response_model=Me)
async def me(user: User) -> Me:
    return Me(id=user.id, display_name=user.display_name, roles=sorted(user.roles))


@router.get("/providers", response_model=Providers)
async def providers(settings: SettingsDep) -> Providers:
    """로그인 화면이 어떤 버튼을 그릴지 정한다(로컬 계정은 운영에서 끌 수 있다)."""
    return Providers(local=settings.local_auth_enabled, oidc=settings.oidc_enabled)


def _front(settings: Settings, path: str) -> str:
    return f"{settings.frontend_base_url.rstrip('/')}{path}"


@router.get("/login", response_class=RedirectResponse, status_code=302)
async def oidc_login(
    db: Db, settings: SettingsDep, return_to: str | None = None
) -> RedirectResponse:
    if not settings.oidc_enabled:
        raise ProblemError(404, "SSO 로그인이 설정되지 않았습니다", code="oidc_disabled")
    try:
        url = await oidc.begin(db, settings, return_to)
    except Exception as e:  # IdP 접속 불가 — 화면으로 돌려보내 실패를 밝힌다
        log.exception("OIDC 로그인 시작 실패")
        raise ProblemError(502, "SSO 서버에 연결할 수 없습니다", code="oidc_unavailable") from e
    return RedirectResponse(url, status_code=302)


@router.get("/callback", response_class=RedirectResponse, status_code=302)
async def oidc_callback(
    db: Db,
    settings: SettingsDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    failed = RedirectResponse(_front(settings, "/login?error=sso_failed"), status_code=302)
    if error or not code or not state:
        audit.record(
            db,
            audit.LOGIN_FAILED,
            actor_id=None,
            payload={"method": "oidc", "reason": error or "missing code/state"},
        )
        return failed
    try:
        done = await oidc.complete(db, settings, code, state)
    except Exception as e:  # 검증 실패든 IdP 응답 이상이든, 어떤 실패도 로그인으로 이어지지 않는다
        # 사유는 로그·감사에만 남긴다. 화면에는 일반 문구 — 검증 단계를 알려 줄 이유가 없다.
        if isinstance(e, oidc.OidcError):
            log.warning("OIDC 로그인 거부: %s", e)
        else:
            log.exception("OIDC 콜백 처리 중 오류")
        await db.rollback()
        audit.record(
            db, audit.LOGIN_FAILED, actor_id=None, payload={"method": "oidc", "reason": str(e)}
        )
        return failed
    issued = await create_session(
        db, done.user, method="oidc", ttl=timedelta(hours=settings.session_ttl_hours)
    )
    audit.record(
        db,
        audit.LOGIN,
        actor_id=done.user.id,
        payload={"method": "oidc", "first_login": done.created},
    )
    response = RedirectResponse(_front(settings, done.return_to), status_code=302)
    set_session_cookie(response, settings, issued)
    return response
