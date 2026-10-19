"""OIDC(Authorization Code + PKCE) — 가짜 IdP 로 전 흐름과 거부 경로를 확인한다.

가짜 IdP 는 httpx.MockTransport 로 discovery·JWKS·토큰 엔드포인트를 흉내 내고, ID 토큰을
실제 RSA 키로 서명한다. 토큰 엔드포인트는 code_verifier 가 인가 요청의 code_challenge 와 맞는지
직접 확인한다 — PKCE 가 실제로 이어져 있는지를 여기서 본다.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from ontobot.config import get_settings
from ontobot.db.models.security import AuditLog
from ontobot.db.session import session_scope
from ontobot.security import oidc

pytestmark = pytest.mark.integration

ISSUER = "https://idp.test"
CLIENT_ID = "ontobot-test"
FRONT = "http://localhost:5173"


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


SIGNING_KEY = _key()
OTHER_KEY = _key()


def _jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    jwk: dict[str, Any] = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**jwk, "kid": kid, "use": "sig", "alg": "RS256"}


class FakeIdp:
    def __init__(self) -> None:
        self.challenges: dict[str, str] = {}  # code → code_challenge
        self.nonce = ""
        # 토큰을 어떻게 비틀지 — 기본은 정상 토큰
        self.tamper: Callable[[dict[str, Any]], dict[str, Any]] = lambda c: c
        self.sign_with = SIGNING_KEY
        self.alg = "RS256"
        self.sub = "alice"

    def authorize(self, location: str, code: str = "code-1") -> str:
        q = parse_qs(urlparse(location).query)
        assert q["code_challenge_method"] == ["S256"]
        self.challenges[code] = q["code_challenge"][0]
        self.nonce = q["nonce"][0]
        return q["state"][0]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
        if path == "/jwks":
            return httpx.Response(200, json={"keys": [_jwk(SIGNING_KEY, "k1")]})
        if path == "/token":
            form = parse_qs(request.content.decode())
            code, verifier = form["code"][0], form["code_verifier"][0]
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            if digest.rstrip(b"=").decode() != self.challenges.get(code):
                return httpx.Response(400, json={"error": "invalid_grant"})
            now = int(time.time())
            claims = self.tamper(
                {
                    "iss": ISSUER,
                    "aud": CLIENT_ID,
                    "sub": self.sub,
                    "nonce": self.nonce,
                    "iat": now,
                    "exp": now + 300,
                    "name": "앨리스",
                }
            )
            if self.alg == "none":
                token = jwt.encode(claims, key=None, algorithm="none")
            else:
                token = jwt.encode(
                    claims, self.sign_with, algorithm=self.alg, headers={"kid": "k1"}
                )
            return httpx.Response(200, json={"id_token": token, "token_type": "Bearer"})
        return httpx.Response(404)


@pytest.fixture
def idp(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeIdp]:
    fake = FakeIdp()
    monkeypatch.setenv("ONTOBOT_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("ONTOBOT_OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("ONTOBOT_FRONTEND_BASE_URL", FRONT)
    get_settings.cache_clear()
    oidc.reset_caches()
    monkeypatch.setattr(oidc, "transport", httpx.MockTransport(fake.handler))
    yield fake
    monkeypatch.undo()
    get_settings.cache_clear()
    oidc.reset_caches()


async def _start(client: httpx.AsyncClient, idp: FakeIdp, return_to: str = "/ontology") -> str:
    res = await client.get("/api/v1/auth/login", params={"return_to": return_to})
    assert res.status_code == 302
    assert res.headers["location"].startswith(f"{ISSUER}/authorize?")
    return idp.authorize(res.headers["location"])


async def _callback(client: httpx.AsyncClient, state: str, code: str = "code-1") -> httpx.Response:
    return await client.get("/api/v1/auth/callback", params={"code": code, "state": state})


async def test_providers_reflect_configuration(client, idp: FakeIdp) -> None:
    assert (await client.get("/api/v1/auth/providers")).json() == {"local": True, "oidc": True}


async def test_full_login_creates_user_with_default_role(client, idp: FakeIdp) -> None:
    idp.sub = "alice-full"
    state = await _start(client, idp)
    res = await _callback(client, state)
    assert res.status_code == 302
    assert res.headers["location"] == f"{FRONT}/ontology"
    assert "httponly" in res.headers["set-cookie"].lower()

    me = (await client.get("/api/v1/auth/me")).json()
    assert me["display_name"] == "앨리스" and me["roles"] == ["user"]

    async for db in session_scope():
        payload = await db.scalar(
            select(AuditLog.payload).where(
                AuditLog.actor_id == me["id"], AuditLog.event == "auth.login"
            )
        )
    assert payload == {"method": "oidc", "first_login": True}


async def test_second_login_reuses_the_same_user(client, idp: FakeIdp) -> None:
    idp.sub = "alice-repeat"
    await _callback(client, await _start(client, idp))
    first = (await client.get("/api/v1/auth/me")).json()["id"]
    await client.post("/api/v1/auth/logout")
    await _callback(client, await _start(client, idp))
    assert (await client.get("/api/v1/auth/me")).json()["id"] == first


async def test_state_cannot_be_replayed(client, idp: FakeIdp) -> None:
    idp.sub = "alice-replay"
    state = await _start(client, idp)
    assert (await _callback(client, state)).headers["location"] == f"{FRONT}/ontology"
    await client.post("/api/v1/auth/logout")
    again = await _callback(client, state)
    assert again.headers["location"] == f"{FRONT}/login?error=sso_failed"
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_state_survives_a_failed_attempt_as_consumed(client, idp: FakeIdp) -> None:
    # 검증이 실패해 요청이 롤백돼도 state 소비는 남아야 한다(그렇지 않으면 재시도로 우회 가능).
    idp.sub = "alice-consumed"
    state = await _start(client, idp)
    idp.tamper = lambda c: {**c, "aud": "someone-else"}
    assert "error=sso_failed" in (await _callback(client, state)).headers["location"]
    idp.tamper = lambda c: c
    assert "error=sso_failed" in (await _callback(client, state)).headers["location"]


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("wrong audience", lambda f: setattr(f, "tamper", lambda c: {**c, "aud": "other-app"})),
        (
            "wrong issuer",
            lambda f: setattr(f, "tamper", lambda c: {**c, "iss": "https://evil.test"}),
        ),
        ("wrong nonce", lambda f: setattr(f, "tamper", lambda c: {**c, "nonce": "x"})),
        ("expired", lambda f: setattr(f, "tamper", lambda c: {**c, "exp": int(time.time()) - 10})),
        ("unsigned alg none", lambda f: setattr(f, "alg", "none")),
        ("signed by unknown key", lambda f: setattr(f, "sign_with", OTHER_KEY)),
    ],
)
async def test_bad_id_tokens_are_rejected(client, idp: FakeIdp, label: str, mutate) -> None:
    idp.sub = f"mallory-{label.replace(' ', '-')}"
    state = await _start(client, idp)
    mutate(idp)
    res = await _callback(client, state)
    assert res.headers["location"] == f"{FRONT}/login?error=sso_failed", label
    assert "set-cookie" not in res.headers
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_wrong_pkce_verifier_is_rejected(client, idp: FakeIdp) -> None:
    state = await _start(client, idp)
    idp.challenges["code-1"] = "not-the-challenge"
    assert "error=sso_failed" in (await _callback(client, state)).headers["location"]


@pytest.mark.parametrize(
    "target", ["//evil.example/x", "https://evil.example", "\\\\evil", "ontology"]
)
async def test_return_to_cannot_leave_the_site(client, idp: FakeIdp, target: str) -> None:
    idp.sub = f"alice-redirect-{abs(hash(target))}"
    state = await _start(client, idp, return_to=target)
    assert (await _callback(client, state)).headers["location"] == f"{FRONT}/"


async def test_idp_error_parameter_is_a_failed_login(client, idp: FakeIdp) -> None:
    res = await client.get("/api/v1/auth/callback", params={"error": "access_denied"})
    assert res.headers["location"] == f"{FRONT}/login?error=sso_failed"
