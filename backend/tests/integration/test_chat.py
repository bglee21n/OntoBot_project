"""Phase 4 — 링크 탐색(PG + Neo4j 왕복)·대화 맥락·채팅 SSE·대화 기록·근거 카드.

LLM 은 ``test_pipeline`` 과 같은 대본 가짜를 쓴다(파이프라인 계약을 고정하려는 것이다).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from ontobot.chat import context as chat_context
from ontobot.chat import pipeline, templates
from ontobot.config import get_settings
from ontobot.db.models.chat import Trace
from ontobot.db.models.security import Role, TypePermission
from ontobot.db.session import session_scope
from ontobot.graph import client as graph
from ontobot.graph.projector import statement
from ontobot.llm.adapters import ollama
from ontobot.llm.gateway import LLMGateway
from ontobot.objects.store import upsert_objects
from ontobot.ontology import importer, registry, versioning
from tests.integration.conftest import MakeUser, login
from tests.integration.test_pipeline import CFG, FACILITIES, SAMPLE, Script, _plan

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
async def schema() -> AsyncIterator[registry.PublishedSchema]:
    async with session_scope() as db:
        doc = importer.parse(SAMPLE.read_text(encoding="utf-8"), "yaml")
        draft = await versioning.create_draft(db, actor_id=None, parent_id=None)
        await versioning.replace_document(db, draft.id, doc, actor_id=None)
        await versioning.publish(db, draft.id, actor_id=None)
        await upsert_objects(db, "Facility", FACILITIES)
        await db.commit()
        published = await registry.reload(db)
        assert published is not None
        yield published


@pytest.fixture
async def neo4j() -> AsyncIterator[graph.AsyncDriver]:
    s = get_settings()
    if not s.test_neo4j_password.get_secret_value():
        pytest.skip("테스트 Neo4j 비밀번호 미설정")
    driver = graph.create_driver(
        s.test_neo4j_uri, s.test_neo4j_user, s.test_neo4j_password.get_secret_value()
    )
    try:
        await graph.server_info(driver)
    except graph.GraphUnavailable:
        await driver.close()
        pytest.skip("테스트 Neo4j 인스턴스가 떠 있지 않음 (Start-Service neo4j-ontobot-test)")
    yield driver
    await driver.close()


async def _org(driver: graph.AsyncDriver | None, facility_pk: str) -> str:
    """고유 이름의 조직을 PG 에 만들고, 드라이버가 있으면 시설 → 조직 관계를 그래프에 둔다."""
    tag = uuid.uuid4().hex[:8]
    # pk 와 이름을 다르게 둔다 — 이름 조건이 pk 로도 맞아떨어지면 재작성·참조 대조를 시험할 수 없다.
    pk, name = f"PO-{tag}", f"파이프조직-{tag}"
    async with session_scope() as db:
        await upsert_objects(db, "Organization", [(pk, {"org_id": pk, "name": name})])
        await db.commit()
    if driver is not None:
        await graph.write(
            driver,
            [
                statement("node_upsert", {"type": "Facility", "pk": facility_pk, "title": "t"}),
                statement("node_upsert", {"type": "Organization", "pk": pk, "title": name}),
                statement(
                    "link_upsert",
                    {
                        "from_type": "Facility",
                        "to_type": "Organization",
                        "link_type": "owned_by",
                        "from_pk": facility_pk,
                        "to_pk": pk,
                    },
                ),
            ],
        )
    return name


def _owned_by(name: str) -> dict[str, Any]:
    return {
        "link": "owned_by",
        "direction": "out",
        "to": "Organization",
        "filters": [{"property": "name", "op": "eq", "value": name}],
    }


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    published: registry.PublishedSchema,
    script: Script,
    driver: graph.AsyncDriver | None,
    **kw: Any,
) -> pipeline.PipelineResult:
    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(script.handler))
    async with session_scope() as db:
        return await pipeline.run(
            db,
            LLMGateway(CFG),
            published,
            {"Facility", "Organization", "Region"},
            "질문",
            graph_driver=driver,
            **kw,
        )
    raise AssertionError


# ---------------------------------------------------------------- 탐색·맥락


async def test_traverse_round_trip_through_neo4j(monkeypatch, schema, neo4j) -> None:
    name = await _org(neo4j, "PT-1")
    script = Script(
        [_plan(traverse=[_owned_by(name)])], "파이프 변전소 1입니다. [[o:Facility/PT-1#name]]"
    )
    events: list[str] = []

    async def emit(event: str, data: dict[str, Any]) -> None:
        events.append(event if event != "stage" else f"stage:{data['stage']}")

    res = await _run(monkeypatch, schema, script, neo4j, emit=emit)
    assert (res.status, res.grade) == ("ok", 1), res
    assert res.cypher is not None and name not in res.cypher  # 값은 파라미터다
    assert f"소유 조직[조직: 조직명 = {name}]" in (res.conditions or "")
    assert res.context_refs == [{"type": "Facility", "pk": "PT-1"}]
    assert events == [
        "stage:planning",
        "stage:querying",
        "conditions",
        "stage:answering",
        "stage:verifying",
    ]


async def test_traverse_without_a_linked_object_is_empty(monkeypatch, schema, neo4j) -> None:
    name = await _org(None, "PT-1")  # 조직은 있지만 그래프에 관계가 없다
    res = await _run(monkeypatch, schema, Script([_plan(traverse=[_owned_by(name)])]), neo4j)
    assert (res.status, res.grade) == ("empty", 1)


async def test_far_side_condition_matching_nothing_is_empty_without_graph(
    monkeypatch, schema
) -> None:
    # 링크 끝 조건에 맞는 객체가 없으면 그래프 없이도 0건이다(실패가 아니다).
    res = await _run(
        monkeypatch, schema, Script([_plan(traverse=[_owned_by("없는 조직 이름")])]), None
    )
    assert (res.status, res.grade) == ("empty", 1)


async def test_neo4j_down_is_graph_unavailable_not_empty(monkeypatch, schema) -> None:
    name = await _org(None, "PT-1")
    dead = graph.create_driver("bolt://127.0.0.1:1", "neo4j", "x")
    try:
        for driver in (None, dead):
            res = await _run(
                monkeypatch, schema, Script([_plan(traverse=[_owned_by(name)])]), driver
            )
            assert (res.status, res.failure_reason, res.grade) == (
                "failed",
                "graph_unavailable",
                None,
            )
            assert res.answer == templates.FAILED["graph_unavailable"]
    finally:
        await dead.close()


async def test_name_in_fk_property_is_rewritten_to_traverse(monkeypatch, schema, neo4j) -> None:
    # 측정 실측: 작은 모델은 "X 지사 소유 시설"에 owner_org_id = 'X 지사' 를 낸다. 값이 모두 조직
    # 이름과 정확히 같으면 같은 뜻의 traverse 로 옮긴다(조건을 빼지 않는다).
    name = await _org(neo4j, "PT-1")
    script = Script(
        [_plan(filters=[{"property": "owner_org_id", "op": "in", "value": name}])],
        "파이프 변전소 1입니다. [[o:Facility/PT-1#name]]",
    )
    res = await _run(monkeypatch, schema, script, neo4j)
    assert (res.status, res.grade) == ("ok", 1), res.plan_attempts
    assert f"소유 조직[조직: 조직명 = {name}]" in (res.conditions or "")
    assert res.plan is not None and res.plan["filters"] == [
        {"property": "name", "op": "contains", "value": "파이프"}
    ]
    assert res.plan_attempts[0]["rewrites"]


async def test_fk_value_that_is_neither_pk_nor_exact_name_is_not_rewritten(
    monkeypatch, schema
) -> None:
    name = await _org(None, "PT-1")
    bad = _plan(filters=[{"property": "owner_org_id", "op": "eq", "value": name[:-1]}])
    res = await _run(monkeypatch, schema, Script([bad, bad]), None)
    assert (res.status, res.failure_reason) == ("failed", "plan_invalid")
    assert "식별자(pk)" in res.plan_attempts[0]["errors"][0]
    assert res.plan_attempts[0]["rewrites"] == []


async def test_context_refs_narrow_to_previous_answer(monkeypatch, schema) -> None:
    script = Script(
        [_plan(context_refs=[{"type": "Facility", "pk": "PT-2"}])],
        "파이프 철탑 2입니다. [[o:Facility/PT-2#name]]",
    )
    ctx = [{"type": "Facility", "pk": "PT-2", "title": "파이프 철탑 2"}]
    res = await _run(monkeypatch, schema, script, None, context=ctx)
    assert (res.status, res.grade) == ("ok", 1)
    assert "직전 결과 1건 중" in (res.conditions or "")
    # 맥락에 없는 식별자는 지어낸 것으로 보고 재계획시킨다.
    bad = _plan(context_refs=[{"type": "Facility", "pk": "PT-1"}])
    res = await _run(monkeypatch, schema, Script([bad, bad]), None, context=ctx)
    assert (res.status, res.failure_reason) == ("failed", "plan_invalid")


# ---------------------------------------------------------------- 채팅 API


async def _reader(make_user: MakeUser, *types: str) -> tuple[str, str, int]:
    # 이름의 test_ 접두어: 시드 역할 검사(test_schema)가 테스트가 만든 역할을 구분한다.
    role = f"test_reader_{uuid.uuid4().hex[:8]}"
    async with session_scope() as db:
        r = Role(name=role, description="테스트")
        db.add(r)
        await db.flush()
        for t in types:
            db.add(TypePermission(role_id=r.id, object_type=t, can_read=True))
        await db.commit()
    return await make_user(role)


def _sse(body: str) -> list[tuple[str, Any]]:
    out = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n"))
        out.append((fields["event"], json.loads(fields["data"])))
    return out


async def test_chat_streams_events_and_records_history(
    monkeypatch, schema, client, make_user
) -> None:
    username, password, user_id = await _reader(make_user, "Facility")
    assert (await login(client, username, password)).status_code == 200
    script = Script([_plan()], "파이프 변전소 1입니다. [[o:Facility/PT-1#name]]")
    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(script.handler))

    r = await client.post("/api/v1/chat", json={"question": "파이프 시설 알려줘"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _sse(r.text)
    assert [e for e, _ in events] == [
        "stage",
        "stage",
        "conditions",
        "stage",
        "stage",
        "answer",
        "citations",
        "grade",
        "done",
    ]
    data = dict(events)
    assert data["grade"] == {"status": "ok", "grade": 1}
    assert data["citations"][0]["ref"] == "o:Facility/PT-1#name"
    conv_id = data["done"]["conversation_id"]

    msgs = (await client.get(f"/api/v1/conversations/{conv_id}/messages")).json()
    assert [(m["role"], m["status"]) for m in msgs] == [("user", None), ("assistant", "ok")]
    assert msgs[1]["meta"]["conditions"].startswith("시설 · 시설명에 '파이프' 포함")
    assert msgs[1]["meta"]["citations"][0]["title"] == "파이프 변전소 1"
    convs = (await client.get("/api/v1/conversations")).json()
    assert convs[0]["id"] == conv_id and convs[0]["title"] == "파이프 시설 알려줘"

    async with session_scope() as db:
        trace = await db.scalar(select(Trace).where(Trace.id == data["done"]["trace_id"]))
        assert trace is not None and trace.user_id == user_id and trace.grade == 1
        assert trace.message_id == data["done"]["message_id"]
        assert {c["role"] for c in trace.llm_calls} == {"planner", "answerer"}
        assert all("messages" not in c for c in trace.llm_calls)  # 프롬프트 본문은 남기지 않는다
        ctx = await chat_context.load(db, conv_id)
    assert {"type": "Facility", "pk": "PT-1"} in ctx

    # 이어지는 질문은 같은 대화에 쌓이고, 플래너가 맥락(제목 포함)을 받는다.
    script2 = Script(
        [_plan(context_refs=[{"type": "Facility", "pk": "PT-1"}])],
        "파이프 변전소 1입니다. [[o:Facility/PT-1#name]]",
    )
    seen: list[str] = []

    def spy(request: httpx.Request) -> httpx.Response:
        seen.append(request.content.decode())
        return script2.handler(request)

    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(spy))
    r = await client.post("/api/v1/chat", json={"question": "그 시설", "conversation_id": conv_id})
    assert dict(_sse(r.text))["grade"] == {"status": "ok", "grade": 1}
    assert "Facility/PT-1" in seen[0] and "<context>" in seen[0]


async def test_failed_chat_is_an_error_event(monkeypatch, schema, client, make_user) -> None:
    username, password, _ = await _reader(make_user)  # 읽을 수 있는 타입이 없다
    assert (await login(client, username, password)).status_code == 200
    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(Script([_plan()]).handler))
    events = _sse((await client.post("/api/v1/chat", json={"question": "시설"})).text)
    assert [e for e, _ in events][-2:] == ["error", "done"]
    assert dict(events)["error"]["reason"] == "forbidden"


async def test_conversations_are_private(monkeypatch, schema, client, make_user) -> None:
    owner = await _reader(make_user, "Facility")
    assert (await login(client, owner[0], owner[1])).status_code == 200
    script = Script([{"kind": "not_query"}])
    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(script.handler))
    events = dict(_sse((await client.post("/api/v1/chat", json={"question": "안녕"})).text))
    assert events["grade"] == {"status": "not_query", "grade": None}
    conv_id = events["done"]["conversation_id"]

    other = await _reader(make_user, "Facility")
    assert (await login(client, other[0], other[1])).status_code == 200
    assert (await client.get(f"/api/v1/conversations/{conv_id}/messages")).status_code == 404
    r = await client.post("/api/v1/chat", json={"question": "x", "conversation_id": conv_id})
    assert r.status_code == 404
    assert conv_id not in [c["id"] for c in (await client.get("/api/v1/conversations")).json()]


async def test_object_card_respects_type_permission(schema, client, make_user) -> None:
    reader = await _reader(make_user, "Facility")
    assert (await login(client, reader[0], reader[1])).status_code == 200
    r = await client.get("/api/v1/objects/Facility/PT-1")
    assert r.status_code == 200
    card = r.json()
    assert card["title"] == "파이프 변전소 1" and card["synced_at"] is not None
    assert {"name": "capacity_kw", "display_name": "설비 용량(kW)", "value": "2,400"} in card[
        "fields"
    ]
    assert (await client.get("/api/v1/objects/Facility/NOPE")).status_code == 404
    # 읽을 수 없는 타입은 없는 객체와 같은 404 다.
    blind = await _reader(make_user, "Region")
    assert (await login(client, blind[0], blind[1])).status_code == 200
    assert (await client.get("/api/v1/objects/Facility/PT-1")).status_code == 404
