"""OIDC 로그인 — Authorization Code + PKCE(TRD 9.1, ADR-0005).

흐름: ``begin()`` 이 state·nonce·PKCE verifier 를 만들어 ``auth_request`` 에 두고 인가 URL 을
돌려준다 → IdP 가 ``/auth/callback?code&state`` 로 돌려보낸다 → ``complete()`` 가 state 를 한 번만
꺼내 지우고, 코드를 토큰으로 바꾸고, ID 토큰을 검증하고, 사용자를 찾거나 만든다.

ID 토큰 검증은 서명(JWKS)·iss·aud·exp·nonce 를 모두 본다. 하나라도 빠지면 다른 앱용 토큰이나
재사용된 토큰으로 로그인할 수 있다. 사용자 식별은 ``iss|sub`` 다 — sub 는 IdP 안에서만 유일하다.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.config import Settings
from ontobot.db.models.security import AppUser, AuthRequest, Role, UserRole
from ontobot.security import admin

_ALLOWED_ALGS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]
_CACHE_TTL_S = 3600


class OidcError(Exception):
    """로그인을 끝낼 수 없다. 메시지는 로그용이고 화면에는 일반 문구만 보인다."""


@dataclass(frozen=True)
class Provider:
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    issuer: str


# 테스트가 가짜 IdP 로 바꿔 끼운다(httpx.MockTransport). 운영에서는 기본 전송을 쓴다.
transport: httpx.AsyncBaseTransport | None = None

_provider_cache: tuple[float, str, Provider] | None = None
_jwks_cache: tuple[float, str, dict[str, Any]] | None = None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport, timeout=10.0)


def reset_caches() -> None:
    global _provider_cache, _jwks_cache
    _provider_cache = None
    _jwks_cache = None


async def provider(settings: Settings) -> Provider:
    global _provider_cache
    issuer = settings.oidc_issuer.rstrip("/")
    if _provider_cache and _provider_cache[1] == issuer and time.monotonic() < _provider_cache[0]:
        return _provider_cache[2]
    async with _client() as c:
        res = await c.get(f"{issuer}/.well-known/openid-configuration")
        res.raise_for_status()
        meta = res.json()
    if meta.get("issuer", "").rstrip("/") != issuer:
        # 발견 문서가 다른 issuer 를 말하면 설정 오류이거나 가로챈 응답이다.
        raise OidcError(f"discovery issuer mismatch: {meta.get('issuer')!r} != {issuer!r}")
    p = Provider(
        authorization_endpoint=meta["authorization_endpoint"],
        token_endpoint=meta["token_endpoint"],
        jwks_uri=meta["jwks_uri"],
        issuer=meta["issuer"],
    )
    _provider_cache = (time.monotonic() + _CACHE_TTL_S, issuer, p)
    return p


async def _jwks(p: Provider, *, force: bool = False) -> dict[str, Any]:
    global _jwks_cache
    if (
        not force
        and _jwks_cache
        and _jwks_cache[1] == p.jwks_uri
        and time.monotonic() < _jwks_cache[0]
    ):
        return _jwks_cache[2]
    async with _client() as c:
        res = await c.get(p.jwks_uri)
        res.raise_for_status()
        keys: dict[str, Any] = res.json()
    _jwks_cache = (time.monotonic() + _CACHE_TTL_S, p.jwks_uri, keys)
    return keys


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_return_to(value: str | None) -> str:
    """로그인 뒤 돌아갈 경로. 같은 사이트의 상대 경로만 받는다(열린 리다이렉트 방지)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


async def begin(db: AsyncSession, settings: Settings, return_to: str | None) -> str:
    p = await provider(settings)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    db.add(
        AuthRequest(
            state_hash=_hash(state),
            nonce=nonce,
            code_verifier=verifier,
            return_to=safe_return_to(return_to),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.oidc_login_ttl_seconds),
        )
    )
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": settings.oidc_redirect_uri,
            "scope": settings.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": _b64(hashlib.sha256(verifier.encode("ascii")).digest()),
            "code_challenge_method": "S256",
        }
    )
    return f"{p.authorization_endpoint}?{query}"


@dataclass(frozen=True)
class Completed:
    user: AppUser
    return_to: str
    created: bool


async def complete(db: AsyncSession, settings: Settings, code: str, state: str) -> Completed:
    # state 는 한 번만 쓴다 — 지우면서 꺼낸다. 만료된 것은 없는 것과 같다.
    row = (
        await db.execute(
            delete(AuthRequest).where(AuthRequest.state_hash == _hash(state)).returning(AuthRequest)
        )
    ).scalar_one_or_none()
    if row is None:
        raise OidcError("unknown or already used state")
    # 소비를 바로 확정한다. 뒤에서 검증이 실패하면 요청 트랜잭션이 롤백되는데, 그때 state 행이
    # 되살아나면 같은 state 로 다시 시도(replay)할 수 있다. 이 함수에서만 예외적으로 커밋한다.
    await db.commit()
    if row.expires_at <= datetime.now(UTC):
        raise OidcError("login request expired")

    p = await provider(settings)
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oidc_redirect_uri,
        "client_id": settings.oidc_client_id,
        "code_verifier": row.code_verifier,
    }
    secret = settings.oidc_client_secret.get_secret_value()
    if secret:
        form["client_secret"] = secret
    async with _client() as c:
        res = await c.post(p.token_endpoint, data=form)
    if res.status_code != 200:
        raise OidcError(f"token endpoint returned {res.status_code}")
    id_token = res.json().get("id_token")
    if not id_token:
        raise OidcError("no id_token in token response")

    claims = await _verify_id_token(p, settings, id_token, nonce=row.nonce)
    user, created = await _find_or_create_user(db, settings, claims)
    if user.status != "active":
        raise OidcError("user disabled")
    if settings.oidc_groups_claim:
        raw = claims.get(settings.oidc_groups_claim)
        values = [raw] if isinstance(raw, str) else [str(v) for v in raw or [] if v is not None]
        await admin.sync_external_groups(db, user.id, values)
    return Completed(user=user, return_to=row.return_to, created=created)


async def _verify_id_token(
    p: Provider, settings: Settings, token: str, *, nonce: str
) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise OidcError(f"malformed id_token: {e}") from e
    if header.get("alg") not in _ALLOWED_ALGS:  # 'none'·HS* 를 막는다
        raise OidcError(f"disallowed alg {header.get('alg')!r}")
    key = await _signing_key(p, header.get("kid"))
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key=key,
            algorithms=_ALLOWED_ALGS,
            audience=settings.oidc_client_id,
            issuer=p.issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise OidcError(f"id_token rejected: {e}") from e
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OidcError("nonce mismatch")
    return claims


async def _signing_key(p: Provider, kid: str | None) -> Any:
    for force in (False, True):  # 키 교체 직후를 위해 캐시를 한 번은 새로 읽는다
        for jwk in (await _jwks(p, force=force)).get("keys", []):
            if kid is None or jwk.get("kid") == kid:
                return jwt.PyJWK(jwk).key
    raise OidcError(f"no signing key for kid {kid!r}")


async def _find_or_create_user(
    db: AsyncSession, settings: Settings, claims: dict[str, Any]
) -> tuple[AppUser, bool]:
    subject = f"{claims['iss']}|{claims['sub']}"
    user = await db.scalar(select(AppUser).where(AppUser.oidc_sub == subject))
    name = str(claims.get("name") or claims.get("preferred_username") or claims["sub"])[:128]
    if user is not None:
        user.display_name = name
        return user, False
    user = AppUser(oidc_sub=subject, display_name=name)
    db.add(user)
    await db.flush()
    for role_name in settings.oidc_default_roles:
        role_id = await db.scalar(select(Role.id).where(Role.name == role_name))
        if role_id is None:
            raise OidcError(f"default role {role_name!r} does not exist")
        db.add(UserRole(user_id=user.id, role_id=role_id))
    await db.flush()
    return user, True
