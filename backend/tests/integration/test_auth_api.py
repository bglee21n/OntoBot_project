"""로컬 로그인·세션·로그아웃·출처 검사(TRD 9.1)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from ontobot.db.models.security import AuditLog, UserSession
from ontobot.db.session import session_scope
from tests.integration.conftest import MakeUser, login

pytestmark = pytest.mark.integration


async def _audit_events(actor_id: int | None, since_id: int) -> list[str]:
    async with session_scope() as db:
        q = select(AuditLog.event).where(AuditLog.id > since_id)
        if actor_id is not None:
            q = q.where(AuditLog.actor_id == actor_id)
        return list(await db.scalars(q.order_by(AuditLog.id)))
    return []


async def _last_audit_id() -> int:
    async with session_scope() as db:
        return int(await db.scalar(select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1)) or 0)
    return 0


async def test_login_sets_httponly_cookie_and_me_returns_roles(client, make_user: MakeUser) -> None:
    username, password, uid = await make_user("modeler", "user")
    since = await _last_audit_id()

    res = await login(client, username, password)
    assert res.status_code == 200
    assert sorted(res.json()["roles"]) == ["modeler", "user"]
    cookie = res.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["id"] == uid
    assert await _audit_events(uid, since) == ["auth.login"]


async def test_wrong_password_is_problem_json_and_audited(client, make_user: MakeUser) -> None:
    username, _, _ = await make_user("user")
    since = await _last_audit_id()

    res = await login(client, username, "not-the-password")
    assert res.status_code == 401
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["code"] == "invalid_credentials"
    # 실패 기록은 응답이 오류여도 커밋돼 있어야 한다.
    assert "auth.login_failed" in await _audit_events(None, since)


async def test_unknown_user_and_disabled_user_get_the_same_answer(
    client, make_user: MakeUser
) -> None:
    username, password, _ = await make_user("user", status="disabled")
    disabled = await login(client, username, password)
    unknown = await login(client, "no_such_user_xyz", password)
    assert disabled.status_code == unknown.status_code == 401
    assert disabled.json()["title"] == unknown.json()["title"]


async def test_logout_revokes_session_immediately(client, make_user: MakeUser) -> None:
    username, password, _ = await make_user("user")
    await login(client, username, password)
    token = client.cookies.get("ontobot_session")
    assert token

    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    # 쿠키를 다시 실어 보내도(가로챈 토큰을 재사용해도) 세션은 이미 죽었다.
    client.cookies.set("ontobot_session", token)
    res = await client.get("/api/v1/auth/me")
    assert res.status_code == 401
    assert res.json()["code"] == "unauthenticated"


async def test_expired_session_is_rejected(client, make_user: MakeUser) -> None:
    username, password, uid = await make_user("user")
    await login(client, username, password)
    async with session_scope() as db:
        await db.execute(
            update(UserSession)
            .where(UserSession.user_id == uid)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_unsafe_request_from_foreign_origin_is_rejected(client, make_user: MakeUser) -> None:
    username, password, _ = await make_user("user")
    res = await client.post(
        "/api/v1/auth/local/login",
        json={"username": username, "password": password},
        headers={"Origin": "http://evil.example"},
    )
    assert res.status_code == 403
    assert res.json()["code"] == "origin_rejected"


async def test_session_token_is_not_stored_in_plaintext(client, make_user: MakeUser) -> None:
    username, password, uid = await make_user("user")
    await login(client, username, password)
    token = client.cookies.get("ontobot_session")
    async with session_scope() as db:
        hashes = list(
            await db.scalars(select(UserSession.token_hash).where(UserSession.user_id == uid))
        )
    assert token not in hashes
    assert all(len(h) == 64 for h in hashes)
