"""관리 API(TRD 14.1 관리 묶음, 9.3 감사) — 실제 DB."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select

from ontobot.db.models.security import AuditLog
from ontobot.db.session import session_scope
from tests.integration.conftest import MakeUser, login

pytestmark = pytest.mark.integration


@pytest.fixture
async def other() -> AsyncIterator[httpx.AsyncClient]:
    """두 번째 브라우저 — 세션 폐기를 다른 쿠키로 확인한다."""
    from ontobot.main import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def _admin(client: httpx.AsyncClient, make_user: MakeUser) -> int:
    username, password, uid = await make_user("admin")
    assert (await login(client, username, password)).status_code == 200
    return uid


async def _events(target: str) -> list[tuple[str, dict]]:
    async with session_scope() as db:
        rows = await db.scalars(
            select(AuditLog).where(AuditLog.target == target).order_by(AuditLog.id)
        )
        return [(r.event, r.payload) for r in rows]


async def test_admin_api_requires_admin(client, make_user: MakeUser) -> None:
    username, password, _ = await make_user("modeler")
    assert (await login(client, username, password)).status_code == 200
    for path in ("/api/v1/admin/users", "/api/v1/admin/roles", "/api/v1/audit-logs"):
        assert (await client.get(path)).status_code == 403


async def test_create_user_then_login(client, other, make_user: MakeUser) -> None:
    await _admin(client, make_user)
    name = f"n_{uuid.uuid4().hex[:8]}"
    body = {
        "username": name,
        "display_name": "새 사용자",
        "password": "pw-" + "x" * 12,
        "roles": ["user"],
    }
    r = await client.post("/api/v1/admin/users", json=body)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["roles"] == ["user"] and created["auth"] == "local"
    assert (await client.post("/api/v1/admin/users", json=body)).status_code == 409
    weak = {**body, "username": name + "w", "password": "short"}
    r = await client.post("/api/v1/admin/users", json=weak)
    assert (r.status_code, r.json()["code"]) == (400, "weak_password")
    unknown = {**body, "username": name + "u", "roles": ["root"]}
    assert (await client.post("/api/v1/admin/users", json=unknown)).json()["code"] == "unknown_role"
    assert (await login(other, name, body["password"])).status_code == 200
    events = await _events(f"app_user:{created['id']}")
    assert events[0][0] == "security.user_created" and "password" not in events[0][1]


async def test_disable_and_role_change_revoke_sessions(client, other, make_user: MakeUser) -> None:
    await _admin(client, make_user)
    username, password, uid = await make_user("user")
    assert (await login(other, username, password)).status_code == 200
    assert (await other.get("/api/v1/auth/me")).status_code == 200

    r = await client.patch(f"/api/v1/admin/users/{uid}", json={"roles": ["user", "modeler"]})
    assert r.json()["roles"] == ["modeler", "user"]
    assert (await other.get("/api/v1/auth/me")).status_code == 401  # 세션 폐기

    assert (await login(other, username, password)).status_code == 200
    r = await client.patch(f"/api/v1/admin/users/{uid}", json={"status": "disabled"})
    assert r.json()["status"] == "disabled"
    assert (await other.get("/api/v1/auth/me")).status_code == 401
    assert (await login(other, username, password)).status_code == 401  # 정지 계정은 못 들어온다

    events = await _events(f"app_user:{uid}")
    assert events == [
        ("security.role_changed", {"before": ["user"], "after": ["modeler", "user"]}),
        ("security.user_status_changed", {"before": "active", "after": "disabled"}),
    ]


async def test_admin_cannot_lock_themself_out(client, make_user: MakeUser) -> None:
    me = await _admin(client, make_user)
    r = await client.patch(f"/api/v1/admin/users/{me}", json={"status": "disabled"})
    assert (r.status_code, r.json()["code"]) == (409, "self_lockout")
    r = await client.patch(f"/api/v1/admin/users/{me}", json={"roles": ["user"]})
    assert (r.status_code, r.json()["code"]) == (409, "self_lockout")
    # 역할을 더하는 것은 된다(관리자 역할을 유지한 채).
    r = await client.patch(f"/api/v1/admin/users/{me}", json={"roles": ["admin", "modeler"]})
    assert r.status_code == 200 and (await client.get("/api/v1/auth/me")).status_code == 200


async def test_permissions_are_replaced_and_audited(client, make_user: MakeUser) -> None:
    await _admin(client, make_user)
    roles = (await client.get("/api/v1/admin/roles")).json()
    domain = next(r for r in roles if r["name"] == "domain_expert")
    url = f"/api/v1/admin/roles/{domain['id']}/permissions"
    r = await client.put(url, json={"readable_types": ["Region", "Facility"]})
    assert r.json()["readable_types"] == ["Facility", "Region"]
    r = await client.put(url, json={"readable_types": ["Region"]})
    assert r.json()["readable_types"] == ["Region"]
    assert (await client.put(url, json={"readable_types": ["bad name"]})).status_code == 400
    events = [
        e for e in await _events("role:domain_expert") if e[0] == "security.permission_changed"
    ]
    assert events[-1][1] == {"before": ["Facility", "Region"], "after": ["Region"]}
    # 원상태로(다른 테스트가 같은 시드 역할을 본다)
    await client.put(url, json={"readable_types": domain["readable_types"]})


async def test_viewing_audit_logs_is_itself_audited(client, make_user: MakeUser) -> None:
    me = await _admin(client, make_user)
    r = await client.get("/api/v1/audit-logs", params={"user_id": me})
    rows = r.json()
    assert r.status_code == 200 and rows[0]["event"] == "auth.login"
    assert all(x["event"] != "security.audit_viewed" for x in rows)  # 조회 기록은 결과 뒤에 남는다
    again = (await client.get("/api/v1/audit-logs", params={"user_id": me})).json()
    assert again[0]["event"] == "security.audit_viewed"
    assert again[0]["payload"]["user_id"] == me
    assert (await client.get("/api/v1/traces/999999999")).status_code == 404


async def test_v1_metrics_count_action_outcomes(client, make_user: MakeUser) -> None:
    # PRD 7절 v1 지표. 다른 테스트의 실행 기록과 섞이므로 넣기 전후의 차이로 본다.
    from ontobot.db.models.actions import ActionRun
    from ontobot.db.models.ontology import OntologyVersion

    uid = await _admin(client, make_user)
    _, _, approver = await make_user("admin")
    before = (await client.get("/api/v1/admin/metrics")).json()
    async with session_scope() as db:
        version = await db.scalar(select(OntologyVersion.id).limit(1))
        if version is None:
            v = OntologyVersion(version_no=900_000 + uuid.uuid4().int % 99_999, status="draft")
            db.add(v)
            await db.flush()
            version = v.id
        for status, decider in [
            ("executed", approver),
            ("rolled_back", approver),
            ("failed", approver),
            ("rejected", approver),
            ("rejected", uid),  # 요청자가 거둠 — 거부율 분모에 넣지 않는다
            ("previewed", None),
        ]:
            db.add(
                ActionRun(
                    request_id=f"metrics-{uuid.uuid4().hex}",
                    action_type="adjust_risk_grade",
                    ontology_version_id=version,
                    target_type="Facility",
                    target_pk="F-0001",
                    params={},
                    status=status,
                    preview={},
                    requested_by=uid,
                    decided_by=decider,
                )
            )
        await db.commit()
    after = (await client.get("/api/v1/admin/metrics", params={"days": 7})).json()
    delta = {k: after["counts"][k] - before["counts"][k] for k in after["counts"]}
    assert delta == {
        "previewed": 1,
        "blocked": 0,
        "rejected": 1,
        "withdrawn": 1,
        "executed": 1,
        "failed": 1,
        "rolled_back": 1,
    }
    c = after["counts"]
    attempted = c["executed"] + c["rolled_back"] + c["failed"]
    assert after["success_rate"] == round((c["executed"] + c["rolled_back"]) / attempted, 4)
    assert after["rejection_rate"] == round(c["rejected"] / (attempted + c["rejected"]), 4)
    assert isinstance(after["open_conflicts"], int) and after["days"] == 7


async def test_v1_metrics_are_admin_only(client, make_user: MakeUser) -> None:
    username, password, _ = await make_user("modeler")
    await login(client, username, password)
    assert (await client.get("/api/v1/admin/metrics")).status_code == 403


async def test_targets_default_then_admin_override_and_reset(client, make_user: MakeUser) -> None:
    # PRD 7절 목표 수치 — 행이 없으면 제안값, 관리자가 정하면 그 값, null 이면 제안값으로 되돌린다.
    await _admin(client, make_user)
    got = {t["key"]: t for t in (await client.get("/api/v1/admin/targets")).json()}
    assert got["accuracy"]["default"] == 0.9 and got["accuracy"]["direction"] == "min"
    assert got["freshness_minutes"]["default"] is None  # 근거 없는 지표는 미정
    res = await client.put(
        "/api/v1/admin/targets", json={"values": {"accuracy": 0.95, "freshness_minutes": 30}}
    )
    assert res.status_code == 200, res.text
    got = {t["key"]: t for t in res.json()}
    assert (got["accuracy"]["value"], got["accuracy"]["custom"]) == (0.95, True)
    assert got["freshness_minutes"]["value"] == 30
    res = await client.put("/api/v1/admin/targets", json={"values": {"accuracy": None}})
    got = {t["key"]: t for t in res.json()}
    assert (got["accuracy"]["value"], got["accuracy"]["custom"]) == (0.9, False)
    bad = await client.put("/api/v1/admin/targets", json={"values": {"nope": 1}})
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_target"
    bad = await client.put("/api/v1/admin/targets", json={"values": {"accuracy": 1.5}})
    assert bad.status_code == 422
    assert (
        "ops.targets_changed",
        {"accuracy": "0.95", "freshness_minutes": "30"},
    ) in await _events("ops_target")
    # 남이 다음 테스트에 영향 주지 않게 되돌린다.
    await client.put("/api/v1/admin/targets", json={"values": {"freshness_minutes": None}})
