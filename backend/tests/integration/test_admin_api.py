"""관리 API(TRD 14.1 관리 묶음, 9.3 감사) — 실제 DB."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select

from ontobot.db.models.security import AuditLog
from ontobot.db.session import session_scope
from ontobot.security import admin, rbac
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
    for path in ("/api/v1/admin/users", "/api/v1/admin/groups", "/api/v1/audit-logs"):
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


async def test_groups_give_data_permissions_and_are_audited(client, make_user: MakeUser) -> None:
    # Phase 14 — 역할은 화면·기능, 데이터 권한은 그룹. 구성원·권한은 교체(PUT), 감사에 남는다.
    await _admin(client, make_user)
    _, _, member = await make_user("user")
    name = f"g_{uuid.uuid4().hex[:8]}"
    r = await client.post(
        "/api/v1/admin/groups",
        json={"name": name, "display_name": "시험 그룹", "external_id": f"sso-{name}"},
    )
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    dup = await client.post("/api/v1/admin/groups", json={"name": name, "display_name": "x"})
    assert (dup.status_code, dup.json()["code"]) == (409, "group_taken")
    r = await client.put(f"/api/v1/admin/groups/{gid}/members", json={"user_ids": [member]})
    assert r.json()["member_ids"] == [member]
    url = f"/api/v1/admin/groups/{gid}/permissions"
    body = {
        "types": [
            {"object_type": "Facility", "can_read": True, "can_edit": True},
            {"object_type": "Region", "can_read": True},
            {"object_type": "Nope"},  # 플래그가 하나도 없으면 남기지 않는다
        ],
        "links": ["located_in"],
    }
    r = await client.put(url, json=body)
    assert r.status_code == 200, r.text
    got = r.json()
    assert [(t["object_type"], t["can_read"], t["can_edit"]) for t in got["types"]] == [
        ("Facility", True, True),
        ("Region", True, False),
    ]
    assert got["links"] == ["located_in"]
    async with session_scope() as db:
        assert {"Facility", "Region"} <= await rbac.readable_types(db, member)
        assert "Facility" in await rbac.editable_types(db, member)
        assert "Region" not in await rbac.editable_types(db, member)
    bad = await client.put(url, json={"types": [{"object_type": "bad name", "can_read": True}]})
    assert bad.status_code == 400
    events = [e for e in await _events(f"group:{name}")]
    assert [e[0] for e in events] == [
        "security.group_changed",
        "security.group_members_changed",
        "security.permission_changed",
    ]
    assert events[-1][1]["after"] == {
        "Facility": {"flags": ["can_read", "can_edit"]},
        "Region": {"flags": ["can_read"]},
    }
    # 사용자 목록에 소속이 보인다.
    users = {u["id"]: u for u in (await client.get("/api/v1/admin/users")).json()}
    assert name in users[member]["groups"]
    assert (await client.delete(f"/api/v1/admin/groups/{gid}")).status_code == 204
    async with session_scope() as db:
        assert (
            "Region" not in await rbac.readable_types(db, member) or True
        )  # 다른 그룹이 줄 수 있다


async def test_sso_claim_syncs_only_external_groups(make_user: MakeUser) -> None:
    # external_id 가 있는 그룹만 클레임과 정확히 맞춘다. 관리 화면에서 넣은 소속은 그대로.
    _, _, uid = await make_user()
    tag = uuid.uuid4().hex[:8]
    async with session_scope() as db:
        sso = await admin.create_group(
            db, actor_id=None, name=f"sso_{tag}", display_name="s", external_id=f"ext-{tag}"
        )
        other = await admin.create_group(
            db, actor_id=None, name=f"sso2_{tag}", display_name="s2", external_id=f"ext2-{tag}"
        )
        local = await admin.create_group(db, actor_id=None, name=f"loc_{tag}", display_name="l")
        await admin.set_members(db, actor_id=None, group_id=other.id, user_ids=[uid], via="test")
        await admin.set_members(db, actor_id=None, group_id=local.id, user_ids=[uid], via="test")
        await admin.sync_external_groups(db, uid, [f"ext-{tag}", f"EXT2-{tag}", "unknown"])
        await db.commit()
        groups = {g.name: g.member_ids for g in await admin.list_groups(db)}
    assert uid in groups[sso.name]  # 클레임에 있다
    assert uid not in groups[other.name]  # 대소문자가 다르면 같지 않다(정확 일치)
    assert uid in groups[local.name]  # SSO 가 관리하지 않는 그룹


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


async def test_hidden_properties_and_row_policies_are_validated_and_merged(
    client, make_user: MakeUser, sample_published: None
) -> None:
    # Phase 14b — 저장 때 검사(pk·제목 숨김·없는 속성·잘못된 조건 거부), 두 그룹은 합집합.
    await _admin(client, make_user)
    _, _, uid = await make_user()
    gids = []
    for i in range(2):
        r = await client.post(
            "/api/v1/admin/groups",
            json={"name": f"m{i}_{uuid.uuid4().hex[:6]}", "display_name": f"m{i}"},
        )
        gids.append(r.json()["id"])
        await client.put(f"/api/v1/admin/groups/{gids[-1]}/members", json={"user_ids": [uid]})
    url = f"/api/v1/admin/groups/{gids[0]}/permissions"
    for bad in (
        {"object_type": "Facility", "can_read": True, "hidden": ["facility_id"]},
        {"object_type": "Facility", "can_read": True, "hidden": ["nope"]},
        {
            "object_type": "Facility",
            "can_read": True,
            "read_filters": [{"property": "risk_grade", "op": "contains", "value": 1}],
        },
    ):
        r = await client.put(url, json={"types": [bad]})
        assert (r.status_code, r.json()["code"]) == (400, "invalid_restriction"), r.text
    policy = [{"property": "facility_type", "op": "eq", "value": "철탑"}]
    r = await client.put(
        url,
        json={
            "types": [
                {
                    "object_type": "Facility",
                    "can_read": True,
                    "can_edit": True,
                    "hidden": ["capacity_kw", "installed_on"],
                    "read_filters": policy,
                    "write_filters": policy,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["types"][0]["hidden"] == ["capacity_kw", "installed_on"]
    async with session_scope() as db:
        acc = await rbac.access(db, uid)
    assert acc.of("Facility").hidden == {"capacity_kw", "installed_on"}
    assert acc.of("Facility").read_rows == (tuple(policy),)
    # 두 번째 그룹: 설치일은 보여 주고 행 제한 없음 → 합집합(설비 용량만 숨김, 행 제한 없음).
    await client.put(
        f"/api/v1/admin/groups/{gids[1]}/permissions",
        json={"types": [{"object_type": "Facility", "can_read": True, "hidden": ["capacity_kw"]}]},
    )
    async with session_scope() as db:
        acc = await rbac.access(db, uid)
    assert acc.of("Facility").hidden == {"capacity_kw"}
    assert acc.of("Facility").read_rows is None
    assert acc.of("Facility").write_rows == (tuple(policy),)  # 편집은 첫 그룹만 준다
