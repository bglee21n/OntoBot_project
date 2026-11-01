"""Phase 21 양식 → 제품 보내기 — 미리보기 → 적용, 지문, 권한, 다시 보내면 할 일 없음(실제 DB)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from ontobot.db.models.evaluation import GoldenItem
from ontobot.db.models.workshop import InterviewSession
from ontobot.db.session import session_scope
from ontobot.evaluation import targets
from ontobot.ontology import versioning
from ontobot.security import admin
from tests.integration.conftest import MakeUser, login

pytestmark = pytest.mark.integration

BASE = "/api/v1/adoption"


async def _as(client: httpx.AsyncClient, user: tuple[str, str, int]) -> None:
    assert (await login(client, user[0], user[1])).status_code == 200


async def _engagement(client: httpx.AsyncClient) -> int:
    r = await client.post(f"{BASE}/engagements", json={"name": f"보내기 {uuid.uuid4().hex[:6]}"})
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def _save(client: httpx.AsyncClient, eid: int, kind: str, payload: dict[str, object]) -> None:
    url = f"{BASE}/engagements/{eid}/sheets/{kind}"
    revision = (await client.get(url)).json()["revision"]
    r = await client.put(url, json={"revision": revision, "payload": payload})
    assert r.status_code == 200, r.text


async def _preview(client: httpx.AsyncClient, eid: int, bridge: str, **body: object) -> dict:
    r = await client.post(f"{BASE}/engagements/{eid}/send/{bridge}/preview", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def _apply(
    client: httpx.AsyncClient, eid: int, bridge: str, fp: str, **body: object
) -> httpx.Response:
    return await client.post(
        f"{BASE}/engagements/{eid}/send/{bridge}/apply", json={"fingerprint": fp, **body}
    )


async def test_glossary_to_new_draft_then_nothing_left(
    client: httpx.AsyncClient, make_user: MakeUser, sample_published: None
) -> None:
    await _as(client, await make_user("modeler"))
    eid = await _engagement(client)
    label = f"위험등급({uuid.uuid4().hex[:4]})"
    await _save(
        client,
        eid,
        "glossary",
        {
            "terms": [
                {"api_name": "Facility.risk_grade", "display_name": label},
                {"api_name": "Nope"},
            ]
        },
    )
    p = await _preview(client, eid, "draft")
    assert p["applicable"] == 1
    published = p["version_id"]
    assert [c["blocked"] is not None for c in p["changes"]] == [False, True]

    # 지문이 다르면 적용하지 않는다.
    r = await _apply(client, eid, "draft", "0" * 64)
    assert r.status_code == 409 and r.json()["code"] == "preview_stale"

    r = await _apply(client, eid, "draft", p["fingerprint"])
    assert r.status_code == 200, r.text
    draft_id = r.json()["version_id"]
    assert draft_id != published
    async with session_scope() as db:
        v = await versioning.get_version(db, draft_id)
        assert v.status == "draft" and v.parent_id == published
        doc = versioning.to_document(v)
        assert doc.object_type("Facility").property("risk_grade").display_name == label  # type: ignore[union-attr]
        pub = versioning.to_document(await versioning.get_version(db, published))
        assert pub.object_type("Facility").property("risk_grade").display_name != label  # type: ignore[union-attr]

    again = await _preview(client, eid, "draft", version_id=draft_id)
    assert again["applicable"] == 0
    # 게시본에는 보낼 수 없다(draft 만).
    r = await client.post(
        f"{BASE}/engagements/{eid}/send/draft/preview", json={"version_id": published}
    )
    assert r.status_code == 409 and r.json()["code"] == "not_draft"


async def test_send_needs_editor_and_receiving_role(
    client: httpx.AsyncClient, make_user: MakeUser, sample_published: None
) -> None:
    owner, editor = await make_user("modeler"), await make_user("user")
    await _as(client, owner)
    eid = await _engagement(client)
    await client.post(
        f"{BASE}/engagements/{eid}/members", json={"username": editor[0], "role": "editor"}
    )
    detail = (await client.get(f"{BASE}/engagements/{eid}")).json()
    assert detail["can_send"] == {
        "draft": True,
        "golden": True,
        "targets": False,
        "accounts": False,
    }
    assert (
        await client.post(f"{BASE}/engagements/{eid}/send/targets/preview", json={})
    ).status_code == 403
    assert (
        await client.post(f"{BASE}/engagements/{eid}/send/nope/preview", json={})
    ).status_code == 404

    await _as(client, editor)
    assert (
        await client.post(f"{BASE}/engagements/{eid}/send/draft/preview", json={})
    ).status_code == 403


async def test_questions_to_adoption_golden_set(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    await _as(client, await make_user("modeler"))
    eid = await _engagement(client)
    await _save(
        client,
        eid,
        "observation",
        {"log": [{"question": "고위험 시설은 몇 곳?"}, {"question": "고위험  시설은 몇 곳?"}]},
    )
    await _save(client, eid, "concepts", {"rows": [{"golden": "강남 철탑 목록"}]})
    p = await _preview(client, eid, "golden")
    assert p["applicable"] == 2 and p["golden_set_name"] == f"adoption-engagement-{eid}"
    r = await _apply(client, eid, "golden", p["fingerprint"])
    assert r.status_code == 200 and r.json()["applied"] == 2
    gs_id = r.json()["golden_set_id"]
    async with session_scope() as db:
        rows = list(await db.scalars(select(GoldenItem).where(GoldenItem.golden_set_id == gs_id)))
    assert sorted(i.question for i in rows) == ["강남 철탑 목록", "고위험 시설은 몇 곳?"]
    assert {i.source for i in rows} == {"adoption"}
    assert all(i.required_values == [] for i in rows)  # 기대 값은 평가 화면에서 확정

    again = await _preview(client, eid, "golden")
    assert again["applicable"] == 0 and again["golden_set_id"] == gs_id


async def test_kpis_to_targets(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    await _as(client, await make_user("admin"))
    eid = await _engagement(client)
    await _save(client, eid, "pilot", {"metrics": [{"target": "93%"}, {"target": "80%"}]})
    p = await _preview(client, eid, "targets")
    assert [(c["target"], c["after"]) for c in p["changes"] if c["blocked"] is None] == [
        ("accuracy", "0.93")
    ]
    try:
        r = await _apply(client, eid, "targets", p["fingerprint"])
        assert r.status_code == 200, r.text
        async with session_scope() as db:
            view = {t.spec.key: t for t in await targets.load(db)}
        assert view["accuracy"].value == Decimal("0.93") and view["accuracy"].custom
        assert (await _preview(client, eid, "targets"))["applicable"] == 0
    finally:
        async with session_scope() as db:
            await targets.update(db, {"accuracy": None}, actor_id=None)  # type: ignore[arg-type]
            await db.commit()


async def test_stakeholders_to_accounts_once(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    existing = await make_user("user")
    await _as(client, await make_user("admin"))
    eid = await _engagement(client)
    new_user = f"u_{uuid.uuid4().hex[:10]}"
    group = f"team_{uuid.uuid4().hex[:8]}"
    await _save(
        client,
        eid,
        "stakeholders",
        {
            "people": [
                {"name": "새 담당자", "username": new_user, "role": "user", "group": group},
                {"username": existing[0], "role": "user, domain_expert", "group": group},
            ]
        },
    )
    p = await _preview(client, eid, "accounts")
    ops = [(c["op"], c["target"]) for c in p["changes"] if c["blocked"] is None]
    assert ops == [
        ("create", f"group:{group}"),
        ("create", f"user:{new_user}"),
        ("add_role", f"user:{new_user}"),
        ("add_member", f"group:{group}"),
        ("add_role", f"user:{existing[0]}"),
        ("add_member", f"group:{group}"),
    ]
    r = await _apply(client, eid, "accounts", p["fingerprint"])
    assert r.status_code == 200, r.text
    passwords = r.json()["passwords"]
    assert list(passwords) == [new_user] and len(passwords[new_user]) >= 12
    assert (await login(client, new_user, passwords[new_user])).status_code == 200

    async with session_scope() as db:
        users = {u.username: u for u in await admin.list_users(db)}
        assert users[new_user].roles == ["user"] and users[new_user].groups == [group]
        assert sorted(users[existing[0]].roles) == ["domain_expert", "user"]
        assert users[existing[0]].groups == [group]

    await _as(client, (*(await make_user("admin"))[:2], 0))
    # 다른 관리자라도 구성원이 아니면 그 도입을 보내지 못한다.
    r = await client.post(f"{BASE}/engagements/{eid}/send/accounts/preview", json={})
    assert r.status_code == 404


async def test_link_interview_session(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    modeler = await make_user("modeler")
    expert = await make_user("domain_expert")
    async with session_scope() as db:
        s = InterviewSession(interviewee_id=expert[2], title="점검팀 인터뷰", topic_state={})
        db.add(s)
        await db.commit()
        sid = s.id
    await _as(client, modeler)
    eid = await _engagement(client)
    r = await client.post(f"{BASE}/engagements/{eid}/sessions", json={"session_id": sid})
    assert r.status_code == 200, r.text
    assert [x["id"] for x in r.json()["sessions"]] == [sid]
    detail = (await client.get(f"/api/v1/workshop/sessions/{sid}")).json()
    assert detail["engagement_id"] == eid
    assert (
        await client.post(f"{BASE}/engagements/{eid}/sessions", json={"session_id": 10**9})
    ).status_code == 404

    assert (await client.delete(f"{BASE}/engagements/{eid}/sessions/{sid}")).status_code == 204
    assert (await client.get(f"{BASE}/engagements/{eid}")).json()["sessions"] == []
