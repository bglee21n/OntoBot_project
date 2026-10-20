"""Phase 11 — 채팅에서 Action: 제안 대조(대상·값)·재계획·미리보기 기록(TRD 17.1-5).

LLM 은 ``test_pipeline`` 의 대본 가짜다 — 모델 품질이 아니라 **막는 규칙**을 고정한다. 모델이
대상·값을 지어내도 미리보기(실행 기록)가 만들어지지 않아야 한다.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from ontobot.chat import pipeline, templates
from ontobot.db.models.actions import ActionRun
from ontobot.db.session import session_scope
from ontobot.llm.adapters import ollama
from ontobot.llm.gateway import LLMGateway
from ontobot.objects.store import upsert_objects
from ontobot.ontology import importer, registry, versioning
from tests.integration.conftest import login
from tests.integration.test_chat import _reader, _sse
from tests.integration.test_pipeline import CFG, FACILITIES, SAMPLE, Script

pytestmark = pytest.mark.integration

ACTIONS = frozenset({"adjust_risk_grade", "request_inspection"})
TWIN = "채팅액션 쌍둥이 시설"


@pytest.fixture(scope="module")
async def schema() -> AsyncIterator[registry.PublishedSchema]:
    async with session_scope() as db:
        doc = importer.parse(SAMPLE.read_text(encoding="utf-8"), "yaml")
        draft = await versioning.create_draft(db, actor_id=None, parent_id=None)
        await versioning.replace_document(db, draft.id, doc, actor_id=None)
        await versioning.publish(db, draft.id, actor_id=None)
        twins = [
            (pk, {"facility_id": pk, "name": TWIN, "facility_type": "전주", "risk_grade": 3})
            for pk in ("CA-1", "CA-2")
        ]
        await upsert_objects(db, "Facility", [*FACILITIES, *twins])
        await db.commit()
        published = await registry.reload(db)
        assert published is not None
        yield published


def _act(target: str | None, action: str = "adjust_risk_grade", **params: Any) -> dict[str, Any]:
    return {
        "kind": "action",
        "action": {
            "action_type": action,
            "target": target,
            "params": [{"name": k, "value": v} for k, v in params.items()],
        },
    }


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    published: registry.PublishedSchema,
    script: Script,
    question: str,
    *,
    actions: frozenset[str] = ACTIONS,
    context: list[dict[str, str]] | None = None,
) -> pipeline.PipelineResult:
    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(script.handler))
    async with session_scope() as db:
        return await pipeline.run(
            db,
            LLMGateway(CFG),
            published,
            {"Facility", "Organization", "Region"},
            question,
            context=context,
            actions=actions,
        )
    raise AssertionError


# ---------------------------------------------------------------- 대조를 통과하는 제안


async def test_target_by_pk_is_ready_without_querying(monkeypatch, schema) -> None:
    script = Script([_act("PT-1", risk_grade=3)])
    res = await _run(monkeypatch, schema, script, "PT-1 위험등급을 3으로 바꿔줘")
    assert (res.status, res.grade, res.conditions, res.sql) == ("action_preview", None, None, None)
    assert res.action is not None and res.action["ready"] is True
    assert res.action["target_pk"] == "PT-1" and res.action["target_title"] == "파이프 변전소 1"
    assert res.action["params"] == {"risk_grade": 3} and res.action["run_id"] is None
    assert res.answer == templates.action_proposed("위험등급 조정")
    assert script.calls == 1  # 조회·답변 모델을 부르지 않는다


async def test_target_by_exact_title(monkeypatch, schema) -> None:
    script = Script([_act("파이프 철탑 2", "request_inspection", urgency="긴급")])
    res = await _run(monkeypatch, schema, script, "파이프 철탑 2 긴급 점검 요청해줘")
    assert res.action is not None and res.action["ready"]
    assert (res.action["target_pk"], res.action["params"]) == ("PT-2", {"urgency": "긴급"})


async def test_target_from_conversation_context(monkeypatch, schema) -> None:
    ctx = [{"type": "Facility", "pk": "PT-2", "title": "파이프 철탑 2"}]
    script = Script([_act("PT-2", risk_grade=1)])
    res = await _run(monkeypatch, schema, script, "그 시설 위험등급 1로 낮춰줘", context=ctx)
    assert res.action is not None and res.action["ready"] and res.action["target_pk"] == "PT-2"


async def test_replan_fixes_an_invented_value(monkeypatch, schema) -> None:
    script = Script([_act("PT-1", risk_grade=5), _act("PT-1", risk_grade=3)])
    seen: list[str] = []
    original = script.handler

    def spy(request: httpx.Request) -> httpx.Response:
        seen.append(json.dumps(json.loads(request.content), ensure_ascii=False))
        return original(request)

    script.handler = spy  # type: ignore[method-assign]
    res = await _run(monkeypatch, schema, script, "PT-1 위험등급을 3으로 바꿔줘")
    assert (
        res.action is not None and res.action["ready"] and res.action["params"] == {"risk_grade": 3}
    )
    assert len(res.plan_attempts) == 2 and "질문에 없습니다" in seen[1]


# ---------------------------------------------------------------- 막는 것(재계획 뒤에도 남으면)


async def test_target_not_in_question_is_blocked_even_if_it_exists(monkeypatch, schema) -> None:
    # PT-1 은 실재한다. 하지만 질문·맥락에 없다 — 모델이 고른 대상은 지어낸 것이다.
    script = Script([_act("PT-1", risk_grade=3), _act("PT-1", risk_grade=3)])
    res = await _run(monkeypatch, schema, script, "위험등급을 3으로 바꿔줘")
    assert res.status == "action_preview" and res.action is not None
    assert res.action["ready"] is False and res.action["target_pk"] is None
    assert any("질문에도 맥락에도 없습니다" in i for i in res.action["issues"])
    assert res.answer == templates.action_incomplete("위험등급 조정")
    assert script.calls == 2


async def test_missing_object_is_blocked(monkeypatch, schema) -> None:
    script = Script([_act("PT-404", risk_grade=3)] * 2)
    res = await _run(monkeypatch, schema, script, "PT-404 위험등급을 3으로 바꿔줘")
    assert res.action is not None and res.action["ready"] is False
    assert any("해당하는 시설 객체가 없습니다" in i for i in res.action["issues"])
    assert res.action["candidates"] == []


async def test_missing_object_offers_context_candidates(monkeypatch, schema) -> None:
    ctx = [{"type": "Facility", "pk": "PT-2", "title": "파이프 철탑 2"}]
    script = Script([_act("파이프 철탑 9", risk_grade=1)] * 2)
    res = await _run(monkeypatch, schema, script, "파이프 철탑 9 위험등급 1로", context=ctx)
    assert res.action is not None and not res.action["ready"]
    assert res.action["candidates"] == [{"pk": "PT-2", "title": "파이프 철탑 2"}]


async def test_same_title_twice_asks_the_user_to_pick(monkeypatch, schema) -> None:
    script = Script([_act(TWIN, risk_grade=4)] * 2)
    res = await _run(monkeypatch, schema, script, f"{TWIN} 위험등급을 4로 올려줘")
    assert res.action is not None and not res.action["ready"]
    assert res.action["candidates"] == [
        {"pk": "CA-1", "title": TWIN},
        {"pk": "CA-2", "title": TWIN},
    ]


async def test_missing_required_value_is_not_invented(monkeypatch, schema) -> None:
    script = Script([_act("PT-1"), _act("PT-1")])
    res = await _run(monkeypatch, schema, script, "PT-1 위험등급 바꿔줘")
    assert res.action is not None and not res.action["ready"] and res.action["target_pk"] == "PT-1"
    assert res.action["params"] == {} and any("필요합니다" in i for i in res.action["issues"])


async def test_unknown_or_unrequestable_action_is_plan_invalid(monkeypatch, schema) -> None:
    script = Script([_act("PT-1", "delete_facility")] * 2)
    res = await _run(monkeypatch, schema, script, "PT-1 지워줘")
    assert (res.status, res.failure_reason) == ("failed", "plan_invalid")
    # 요청할 수 있는 Action 이 없으면 플래너는 Action 을 모른다 — 그래도 내면 막는다.
    script = Script([_act("PT-1", risk_grade=3)] * 2)
    res = await _run(
        monkeypatch, schema, script, "PT-1 위험등급을 3으로 바꿔줘", actions=frozenset()
    )
    assert (res.status, res.failure_reason) == ("failed", "plan_invalid")


# ---------------------------------------------------------------- 채팅 API: 미리보기 기록


async def test_chat_creates_a_preview_run(monkeypatch, schema, client, make_user) -> None:
    username, password, user_id = await _reader(make_user, "Facility")
    assert (await login(client, username, password)).status_code == 200
    script = Script([_act("PT-1", risk_grade=3)])
    prompts: list[str] = []
    original = script.handler

    def spy(request: httpx.Request) -> httpx.Response:
        prompts.append(json.loads(request.content)["messages"][0]["content"])
        return original(request)

    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(spy))
    r = await client.post("/api/v1/chat", json={"question": "PT-1 위험등급을 3으로 바꿔줘"})
    events = _sse(r.text)
    assert [e for e, _ in events] == ["stage", "action_preview", "done"]
    data = dict(events)["action_preview"]
    assert data["ready"] is True and data["run_id"] is not None
    assert "adjust_risk_grade" in prompts[0]  # 요청할 수 있는 Action 을 플래너에 보였다

    run = (await client.get(f"/api/v1/action-runs/{data['run_id']}")).json()
    assert (run["origin"], run["status"], run["target_pk"]) == ("chat", "previewed", "PT-1")
    assert run["params"]["risk_grade"] == 3
    assert run["can_approve"] is False and run["can_reject"] is True  # 요청자는 거둘 수만 있다
    async with session_scope() as db:
        row = await db.scalar(select(ActionRun).where(ActionRun.id == data["run_id"]))
        assert row is not None and row.requested_by == user_id
        assert row.request_id.startswith("chat-")

    conv_id = dict(events)["done"]["conversation_id"]
    msgs = (await client.get(f"/api/v1/conversations/{conv_id}/messages")).json()
    assert msgs[1]["status"] == "action_preview" and msgs[1]["grade"] is None
    assert msgs[1]["meta"]["action"]["run_id"] == data["run_id"]


async def test_chat_incomplete_proposal_makes_no_run(
    monkeypatch, schema, client, make_user
) -> None:
    username, password, user_id = await _reader(make_user, "Facility")
    assert (await login(client, username, password)).status_code == 200
    monkeypatch.setattr(
        ollama, "transport", httpx.MockTransport(Script([_act("PT-404", risk_grade=3)] * 2).handler)
    )
    events = dict(_sse((await client.post("/api/v1/chat", json={"question": "PT-404 3으로"})).text))
    assert events["action_preview"]["ready"] is False and events["action_preview"]["run_id"] is None
    async with session_scope() as db:
        runs = await db.scalars(select(ActionRun).where(ActionRun.requested_by == user_id))
        assert list(runs) == []


async def test_requester_role_hides_the_action(monkeypatch, schema, client, make_user) -> None:
    # 요청 역할이 정해진 Action 은 그 역할이 없는 사용자의 플래너 요약에 싣지 않는다.
    doc = schema.document.model_copy(
        update={
            "action_types": [
                a.model_copy(update={"requester_roles": [f"test_nobody_{uuid.uuid4().hex[:6]}"]})
                for a in schema.document.action_types
            ]
        }
    )
    async with session_scope() as db:
        draft = await versioning.create_draft(db, actor_id=None, parent_id=None)
        await versioning.replace_document(db, draft.id, doc, actor_id=None)
        await versioning.publish(db, draft.id, actor_id=None)
        await db.commit()
        await registry.reload(db)
    try:
        username, password, _ = await _reader(make_user, "Facility")
        assert (await login(client, username, password)).status_code == 200
        script = Script([_act("PT-1", risk_grade=3)] * 2)
        prompts: list[str] = []
        original = script.handler

        def spy(request: httpx.Request) -> httpx.Response:
            prompts.append(json.loads(request.content)["messages"][0]["content"])
            return original(request)

        monkeypatch.setattr(ollama, "transport", httpx.MockTransport(spy))
        events = dict(
            _sse((await client.post("/api/v1/chat", json={"question": "PT-1 3으로"})).text)
        )
        assert events["error"]["reason"] == "plan_invalid"
        assert "action_types" not in prompts[0]
    finally:
        # 이 모듈의 다른 테스트가 원래 게시본을 쓰도록 되돌린다.
        async with session_scope() as db:
            draft = await versioning.create_draft(db, actor_id=None, parent_id=None)
            await versioning.replace_document(db, draft.id, schema.document, actor_id=None)
            await versioning.publish(db, draft.id, actor_id=None)
            await db.commit()
            await registry.reload(db)
