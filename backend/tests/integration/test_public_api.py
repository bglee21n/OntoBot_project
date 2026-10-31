"""Phase 17b — 공개 REST API: 서비스 계정·API 키·스코프.

질의(JSON)·객체 읽기·Action 요청 → 사람 승인.

완료 기준(Phase 17): API 키로 질의·Action 요청 → 승인 흐름.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from ontobot.db.models.actions import ActionRun
from ontobot.db.session import session_scope
from ontobot.llm.adapters import ollama
from ontobot.objects.store import upsert_objects
from ontobot.security import admin
from tests.integration.conftest import MakeUser, add_group, login

pytestmark = pytest.mark.integration


async def _issue(client: httpx.AsyncClient, user_id: int, scopes: list[str]) -> str:
    r = await client.post(
        "/api/v1/admin/api-keys", json={"user_id": user_id, "name": "연동 시험", "scopes": scopes}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["key"].startswith(f"obk_{body['prefix']}_") and body["scopes"] == sorted(scopes)
    return str(body["key"])


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def test_api_key_query_object_and_action_request_then_human_approves(
    monkeypatch: pytest.MonkeyPatch,
    sample_published: None,
    client: httpx.AsyncClient,
    make_user: MakeUser,
) -> None:
    tag = uuid.uuid4().hex[:8]
    pk = f"API-{tag}"
    async with session_scope() as db:
        await upsert_objects(
            db,
            "Facility",
            [(pk, {"facility_id": pk, "name": f"연동 시설 {tag}", "risk_grade": 2})],
        )
        await db.commit()
    admin_name, admin_pw, admin_id = await make_user("admin")
    assert (await login(client, admin_name, admin_pw)).status_code == 200

    # 서비스 계정 — 로그인하지 않는 계정. 역할·데이터 그룹은 사람과 같은 관리로 준다.
    r = await client.post(
        "/api/v1/admin/service-accounts",
        json={"username": f"svc-{tag}", "display_name": f"연동 {tag}"},
    )
    assert r.status_code == 201 and r.json()["auth"] == "service", r.text
    svc_id = r.json()["id"]
    assert (
        await client.patch(f"/api/v1/admin/users/{svc_id}", json={"roles": ["user"]})
    ).status_code == 200
    async with session_scope() as db:
        gid = await add_group(db, f"api_{tag}", ["Facility"], edit=True)
        await admin.set_members(
            db, actor_id=None, group_id=gid, user_ids=[svc_id, admin_id], via="test"
        )
        await db.commit()
    # 사람 계정에는 키를 주지 않는다.
    r = await client.post(
        "/api/v1/admin/api-keys", json={"user_id": admin_id, "name": "x", "scopes": ["query"]}
    )
    assert (r.status_code, r.json()["code"]) == (422, "not_service_account")
    r = await client.post(
        "/api/v1/admin/api-keys", json={"user_id": svc_id, "name": "x", "scopes": ["write"]}
    )
    assert (r.status_code, r.json()["code"]) == (422, "invalid_scope")
    read_key = await _issue(client, svc_id, ["objects:read", "query"])
    act_key = await _issue(client, svc_id, ["actions:request"])

    # 세션 쿠키(로그인한 관리자)로는 공개 API 를 부를 수 없다 — 키만 받는다.
    assert (await client.get(f"/api/v1/public/objects/Facility/{pk}")).status_code == 401
    bad = "obk_00000000_" + "a" * 43
    r = await client.get(f"/api/v1/public/objects/Facility/{pk}", headers=_bearer(bad))
    assert (r.status_code, r.json()["code"]) == (401, "invalid_api_key")

    # 객체 읽기 — 근거 카드와 같은 읽기(권한 밖 타입은 없는 것).
    r = await client.get(f"/api/v1/public/objects/Facility/{pk}", headers=_bearer(read_key))
    assert r.status_code == 200 and r.json()["title"] == f"연동 시설 {tag}", r.text
    r = await client.get("/api/v1/public/objects/Organization/ORG-01", headers=_bearer(read_key))
    assert r.status_code == 404

    # 질의 — 화면과 같은 파이프라인, JSON 한 번(가짜 Ollama: 플래너·답변).
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "format" in body:
            plan = {
                "kind": "query",
                "plan": {
                    "target": "Facility",
                    "filters": [{"property": "facility_id", "op": "eq", "value": pk}],
                },
            }
            content = json.dumps(plan)
        else:
            content = f"연동 시설 {tag}의 위험등급은 2입니다. [[o:Facility/{pk}#risk_grade]]"
        return httpx.Response(200, json={"message": {"content": content}})

    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(handler))
    r = await client.post(
        "/api/v1/public/query", json={"question": f"{pk} 위험등급은?"}, headers=_bearer(read_key)
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["status"], out["grade"]) == ("ok", 1) and out["trace_id"] > 0
    assert [c["ref"] for c in out["citations"]] == [f"o:Facility/{pk}#risk_grade"]
    assert out["action"] is None

    # 스코프 — 읽기 키로는 Action 요청을, Action 키로는 질의를 못 한다.
    req = {"target_pk": pk, "params": {"risk_grade": 4}, "request_id": f"api-test-{tag}"}
    r = await client.post(
        "/api/v1/public/actions/adjust_risk_grade/requests", json=req, headers=_bearer(read_key)
    )
    assert (r.status_code, r.json()["code"]) == (403, "missing_scope")
    r = await client.post("/api/v1/public/query", json={"question": "x"}, headers=_bearer(act_key))
    assert r.status_code == 403

    # Action 요청 = 미리보기. 같은 request_id 는 한 번만.
    r = await client.post(
        "/api/v1/public/actions/adjust_risk_grade/requests", json=req, headers=_bearer(act_key)
    )
    assert r.status_code == 201 and r.json()["status"] == "previewed", r.text
    run_id = r.json()["id"]
    again = await client.post(
        "/api/v1/public/actions/adjust_risk_grade/requests", json=req, headers=_bearer(act_key)
    )
    assert again.json()["id"] == run_id
    async with session_scope() as db:
        run = await db.get(ActionRun, run_id)
        assert run is not None and run.origin == "api" and run.requested_by == svc_id

    # 승인은 사람이 화면(세션)에서 — 승인 = 실행.
    r = await client.post(f"/api/v1/action-runs/{run_id}/approve")
    assert r.status_code == 200 and r.json()["status"] == "executed", r.text
    r = await client.get(f"/api/v1/public/actions/requests/{run_id}", headers=_bearer(act_key))
    assert r.json()["status"] == "executed"
    r = await client.get(f"/api/v1/public/objects/Facility/{pk}", headers=_bearer(read_key))
    grade = next(f for f in r.json()["fields"] if f["name"] == "risk_grade")
    assert grade["value"] == "4"

    # 폐기하면 그 키는 없는 키다. 목록에는 원문이 없다.
    keys = (await client.get("/api/v1/admin/api-keys")).json()
    mine = [k for k in keys if k["user_id"] == svc_id]
    assert len(mine) == 2 and all("key" not in k for k in mine)
    assert all(k["last_used_at"] for k in mine)
    read_id = next(k["id"] for k in mine if "query" in k["scopes"])
    assert (await client.delete(f"/api/v1/admin/api-keys/{read_id}")).status_code == 204
    r = await client.get(f"/api/v1/public/objects/Facility/{pk}", headers=_bearer(read_key))
    assert r.status_code == 401

    # 공개 OpenAPI 는 공개 경로만 담는다.
    doc = (await client.get("/api/v1/public/openapi.json")).json()
    assert doc["paths"] and all(p.startswith("/api/v1/public/") for p in doc["paths"])
    assert "bearer" in json.dumps(doc["components"]["securitySchemes"]).lower()
