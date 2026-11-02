"""Phase 20 도입 작업 공간 — 만들기·구성원 권한·판 비교 저장·감사(실제 DB)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from ontobot.db.models.security import AuditLog
from ontobot.db.session import session_scope
from tests.integration.conftest import MakeUser, login

pytestmark = pytest.mark.integration

BASE = "/api/v1/adoption"


async def _as(client: httpx.AsyncClient, user: tuple[str, str, int]) -> None:
    assert (await login(client, user[0], user[1])).status_code == 200


async def test_create_needs_modeler_or_admin_and_creator_is_owner(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    await _as(client, await make_user("user"))
    r = await client.post(f"{BASE}/engagements", json={"name": "시설 점검"})
    assert r.status_code == 403

    modeler = await make_user("modeler")
    await _as(client, modeler)
    r = await client.post(
        f"{BASE}/engagements", json={"name": " 시설 점검 ", "description": "샘플"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "시설 점검"
    assert body["my_role"] == "owner" and body["can_manage"]
    assert [m["user_id"] for m in body["members"]] == [modeler[2]]
    assert [s["stage"] for s in body["stages"]] == [1, 2, 3, 4, 5]
    assert body["sheets"] == []

    listed = (await client.get(f"{BASE}/engagements")).json()
    assert body["id"] in [e["id"] for e in listed]

    kinds = (await client.get(f"{BASE}/sheet-kinds")).json()
    assert len(kinds) == 16 and kinds[0]["number"] == "36.1"
    assert kinds[0]["json_schema"]["type"] == "object"


async def test_members_roles_and_non_member_is_404(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    owner, editor, viewer, outsider = (
        await make_user("modeler"),
        await make_user("user"),
        await make_user("user"),
        await make_user("admin"),
    )
    await _as(client, owner)
    eid = (await client.post(f"{BASE}/engagements", json={"name": "도입"})).json()["id"]
    for who, role in ((editor, "editor"), (viewer, "viewer")):
        r = await client.post(
            f"{BASE}/engagements/{eid}/members", json={"username": who[0], "role": role}
        )
        assert r.status_code == 200, r.text
    r = await client.post(
        f"{BASE}/engagements/{eid}/members", json={"username": "nobody-x", "role": "viewer"}
    )
    assert r.status_code == 404

    # 마지막 owner 는 낮추거나 뺄 수 없다.
    r = await client.post(
        f"{BASE}/engagements/{eid}/members", json={"username": owner[0], "role": "editor"}
    )
    assert r.status_code == 409 and r.json()["code"] == "last_owner"
    assert (await client.delete(f"{BASE}/engagements/{eid}/members/{owner[2]}")).status_code == 409

    # 관리자라도 구성원이 아니면 없는 도입과 같다.
    await _as(client, outsider)
    r = await client.get(f"{BASE}/engagements/{eid}")
    assert r.status_code == 404 and r.json()["code"] == "engagement_not_found"
    assert (await client.get(f"{BASE}/engagements/{eid}/sheets/fit")).status_code == 404
    assert eid not in [e["id"] for e in (await client.get(f"{BASE}/engagements")).json()]

    await _as(client, viewer)
    detail = (await client.get(f"{BASE}/engagements/{eid}")).json()
    assert detail["my_role"] == "viewer" and not detail["can_edit"]
    r = await client.put(
        f"{BASE}/engagements/{eid}/sheets/fit", json={"revision": 0, "payload": {}}
    )
    assert r.status_code == 403
    r = await client.patch(f"{BASE}/engagements/{eid}", json={"name": "바꿈"})
    assert r.status_code == 403

    await _as(client, editor)
    r = await client.put(
        f"{BASE}/engagements/{eid}/sheets/fit",
        json={"revision": 0, "payload": {"overview": {"task": "시설 점검"}}},
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"{BASE}/engagements/{eid}/members", json={"username": viewer[0], "role": "owner"}
    )
    assert r.status_code == 403


async def test_save_with_revision_conflict_and_validation(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    await _as(client, await make_user("modeler"))
    eid = (await client.post(f"{BASE}/engagements", json={"name": "도입"})).json()["id"]
    url = f"{BASE}/engagements/{eid}/sheets/stakeholders"

    first = (await client.get(url)).json()
    assert first == {
        "kind": "stakeholders",
        "revision": 0,
        "payload": {},
        "updated_at": None,
        "updated_by": None,
    }

    payload = {
        "people": [{"name": "홍길동 과장", "role": "user", "group": "점검팀", "influence": "상"}]
    }
    r = await client.put(url, json={"revision": 0, "payload": payload})
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 1

    # 같은 판으로 다시 저장하면(다른 사람이 먼저 저장한 경우) 덮어쓰지 않는다.
    r = await client.put(url, json={"revision": 0, "payload": {"people": []}})
    assert r.status_code == 409 and r.json()["current_revision"] == 1
    assert (await client.get(url)).json()["payload"]["people"][0]["name"] == "홍길동 과장"

    r = await client.put(
        url, json={"revision": 1, "payload": {"people": [{"influence": "매우 높음"}]}}
    )
    assert r.status_code == 422
    assert [i["path"] for i in r.json()["issues"]] == ["people.0.influence"]

    assert (await client.get(f"{BASE}/engagements/{eid}/sheets/nope")).status_code == 404

    r = await client.put(
        url, json={"revision": 1, "payload": {**payload, "comms": [{"plan": "주간 회의"}]}}
    )
    assert r.json()["revision"] == 2

    detail = (await client.get(f"{BASE}/engagements/{eid}")).json()
    assert {s["kind"]: s["revision"] for s in detail["sheets"]} == {"stakeholders": 2}
    stage2 = {h["label"]: h["filled"] for h in detail["stages"][1]["handoffs"]}
    assert stage2["계정·데이터 그룹 초안"] is True

    # 감사: 종류·판·바뀐 묶음만 — 사람 이름 같은 양식 값은 남기지 않는다.
    async with session_scope() as db:
        logs = list(
            await db.scalars(
                select(AuditLog).where(AuditLog.target == f"engagement:{eid}").order_by(AuditLog.id)
            )
        )
    assert [log.event for log in logs] == [
        "adoption.engagement_created",
        "adoption.sheet_saved",
        "adoption.sheet_saved",
    ]
    assert logs[1].payload == {"kind": "stakeholders", "revision": 1, "sections": ["people"]}
    assert logs[2].payload["sections"] == ["comms"]
    assert all("홍길동" not in str(log.payload) for log in logs)
