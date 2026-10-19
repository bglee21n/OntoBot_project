"""파이프라인 전 구간(TRD 7.1·7.3) — 실제 DB + 대본대로 답하는 가짜 LLM.

LLM 을 가짜로 두는 이유: 모델 품질이 아니라 **파이프라인 계약**(상태·등급·실패 표시·조건 문장·
바인드 실행)을 고정하려는 것이다. 모델 품질은 스파이크·평가(scripts/spike_plan.py)가 잰다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from ontobot.chat import pipeline, templates
from ontobot.db.session import session_scope
from ontobot.llm.adapters import ollama
from ontobot.llm.config import LlmConfig
from ontobot.llm.gateway import LLMGateway
from ontobot.objects.store import upsert_objects
from ontobot.ontology import importer, registry, versioning

pytestmark = pytest.mark.integration

SAMPLE = Path(__file__).resolve().parents[3] / "domain-packs" / "sample" / "ontology.yaml"
CFG = LlmConfig.model_validate(
    {
        "providers": {"local": {"adapter": "ollama", "base_url": "http://fake-ollama"}},
        "roles": {
            "planner": {"provider": "local", "model": "fake"},
            "answerer": {"provider": "local", "model": "fake"},
        },
    }
)
FACILITIES = [
    (
        "PT-1",
        {
            "facility_id": "PT-1",
            "name": "파이프 변전소 1",
            "facility_type": "변전소",
            "risk_grade": 4,
            "capacity_kw": 2400,
            "installed_on": "2010-01-02",
        },
    ),
    (
        "PT-2",
        {
            "facility_id": "PT-2",
            "name": "파이프 철탑 2",
            "facility_type": "철탑",
            "risk_grade": 1,
            "capacity_kw": 150,
            "installed_on": "2020-05-06",
        },
    ),
]


class Script:
    """역할별 응답 대본. 형식 요청(format)이 있으면 플래너, 없으면 답변이다."""

    def __init__(self, planner: list[Any], answer: str = "") -> None:
        self.planner = list(planner)
        self.answer = answer
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls += 1
        if "format" in body:
            item = self.planner.pop(0)
            content = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        else:
            content = self.answer
        return httpx.Response(200, json={"message": {"content": content}})


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


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    schema: registry.PublishedSchema,
    script: Script,
    question: str = "질문",
    readable: set[str] | None = None,
) -> pipeline.PipelineResult:
    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(script.handler))
    async with session_scope() as db:
        # 다른 테스트가 넣은 시설과 섞이지 않게, 이 파일의 시설만 보이는 조건을 플래너 대본에 둔다.
        return await pipeline.run(
            db,
            LLMGateway(CFG),
            schema,
            readable if readable is not None else {"Facility", "Region"},
            question,
        )
    raise AssertionError


def _plan(**kw: Any) -> dict[str, Any]:
    filters = [{"property": "name", "op": "contains", "value": "파이프"}, *kw.pop("filters", [])]
    return {"kind": "query", "plan": {"target": "Facility", "filters": filters, **kw}}


async def test_ok_answer_is_grounded_and_conditions_are_shown(monkeypatch, schema) -> None:
    script = Script(
        [_plan(filters=[{"property": "risk_grade", "op": "gte", "value": 3}])],
        "파이프 변전소 1의 위험등급은 4입니다. [[o:Facility/PT-1#risk_grade]]",
    )
    res = await _run(monkeypatch, schema, script)
    assert (res.status, res.grade) == ("ok", 1)
    assert res.answer == "파이프 변전소 1의 위험등급은 4입니다."
    assert res.conditions == "시설 · 시설명에 '파이프' 포함 · 위험등급 ≥ 3 · 최대 20건"
    assert res.citations[0]["ref"] == "o:Facility/PT-1#risk_grade"
    assert res.citations[0]["synced_at"] is not None


async def test_invented_number_is_removed_and_answer_refused(monkeypatch, schema) -> None:
    script = Script(
        [_plan()], "파이프 변전소 1의 위험등급은 5입니다. [[o:Facility/PT-1#risk_grade]]"
    )
    res = await _run(monkeypatch, schema, script)
    assert (res.status, res.grade) == ("ok", 3)
    assert res.answer == templates.ungrounded(res.conditions or "")
    assert res.removed_citations == [
        {"sentence": 0, "citation": "o:Facility/PT-1#risk_grade", "reason": "value_mismatch"}
    ]


async def test_zero_rows_is_empty_not_failed(monkeypatch, schema) -> None:
    script = Script([_plan(filters=[{"property": "risk_grade", "op": "eq", "value": 9}])])
    res = await _run(monkeypatch, schema, script)
    assert (res.status, res.grade) == ("empty", 1)
    assert res.answer.startswith("조회 조건(") and script.calls == 1  # 답변 LLM 을 부르지 않는다


async def test_aggregate_with_rounded_average(monkeypatch, schema) -> None:
    script = Script(
        [_plan(aggregate={"fn": "avg", "property": "risk_grade", "group_by": []})],
        "평균 위험등급은 2.5입니다. [[q:1#value]]",
    )
    res = await _run(monkeypatch, schema, script)
    assert (res.status, res.grade) == ("ok", 1)


async def test_invalid_plan_is_replanned_once_then_fails(monkeypatch, schema) -> None:
    bad = _plan(filters=[{"property": "budget", "op": "eq", "value": 1}])
    script = Script([bad, bad])
    res = await _run(monkeypatch, schema, script)
    assert (res.status, res.failure_reason, res.grade) == ("failed", "plan_invalid", None)
    assert len(res.plan_attempts) == 2 and "budget" in res.plan_attempts[0]["errors"][0]
    assert res.answer == templates.FAILED["plan_invalid"]


async def test_replan_with_errors_can_succeed(monkeypatch, schema) -> None:
    script = Script(
        [
            _plan(filters=[{"property": "risk_grade", "op": "eq", "value": "높음"}]),
            _plan(filters=[{"property": "risk_grade", "op": "eq", "value": 4}]),
        ],
        "파이프 변전소 1입니다. [[o:Facility/PT-1#name]]",
    )
    res = await _run(monkeypatch, schema, script)
    assert (res.status, res.grade) == ("ok", 1)
    assert [bool(a["errors"]) for a in res.plan_attempts] == [True, False]


async def test_broken_json_twice_is_plan_invalid(monkeypatch, schema) -> None:
    res = await _run(monkeypatch, schema, Script(["{not json", "{still not"]))
    assert (res.status, res.failure_reason) == ("failed", "plan_invalid")
    assert res.plan_attempts[0]["structured_ok"] is False


async def test_unreadable_type_is_forbidden_without_replanning(monkeypatch, schema) -> None:
    script = Script([_plan()])
    res = await _run(monkeypatch, schema, script, readable={"Region"})
    assert (res.status, res.failure_reason) == ("failed", "forbidden")
    assert script.calls == 1


async def test_not_query(monkeypatch, schema) -> None:
    res = await _run(monkeypatch, schema, Script([{"kind": "not_query"}]))
    assert (res.status, res.grade, res.answer) == ("not_query", None, templates.NOT_QUERY)


async def test_execution_error_is_failed_not_empty(monkeypatch, schema) -> None:
    # 원천 데이터가 자료형과 어긋나면(정수 칸에 글자) 캐스팅이 실패한다 — 0건으로 둔갑하면 안 된다.
    async with session_scope() as db:
        await upsert_objects(
            db,
            "Facility",
            [("PT-BAD", {"facility_id": "PT-BAD", "name": "파이프 불량", "risk_grade": "높음"})],
        )
        await db.commit()
    try:
        res = await _run(
            monkeypatch,
            schema,
            Script([_plan(filters=[{"property": "risk_grade", "op": "gte", "value": 1}])]),
        )
        assert (res.status, res.failure_reason) == ("failed", "execution_error")
        assert res.answer == templates.FAILED["execution_error"]
    finally:
        async with session_scope() as db:
            await db.execute(text("UPDATE object SET deleted_at = now() WHERE pk = 'PT-BAD'"))
            await db.commit()


async def test_llm_down_is_failed(monkeypatch, schema) -> None:
    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(down))
    async with session_scope() as db:
        res = await pipeline.run(db, LLMGateway(CFG), schema, {"Facility"}, "질문")
    assert (res.status, res.failure_reason) == ("failed", "llm_unavailable")


async def test_name_in_code_property_is_sent_back_for_replanning(monkeypatch, schema) -> None:
    # 스파이크 실측: "서울 지역 시설" → region_code = '서울'. 실행하면 조용히 0건이 된다.
    script = Script(
        [_plan(filters=[{"property": "region_code", "op": "eq", "value": "서울"}]), _plan()],
        "파이프 변전소 1입니다. [[o:Facility/PT-1#name]]",
    )
    res = await _run(monkeypatch, schema, script)
    assert "식별자(pk)" in res.plan_attempts[0]["errors"][0]
    assert (res.status, res.grade) == ("ok", 1)


async def test_reference_check_failing_twice_is_plan_invalid_not_empty(monkeypatch, schema) -> None:
    bad = _plan(filters=[{"property": "owner_org_id", "op": "in", "value": ["강남지사"]}])
    res = await _run(monkeypatch, schema, Script([bad, bad]))
    assert (res.status, res.failure_reason) == ("failed", "plan_invalid")


async def test_cannot_answer_is_failed_not_a_dropped_condition(monkeypatch, schema) -> None:
    script = Script([{"kind": "cannot_answer", "reason": "지역 이름 조건을 표현할 수 없음"}])
    res = await _run(monkeypatch, schema, script)
    assert (res.status, res.failure_reason) == ("failed", "plan_invalid")
    assert res.plan_attempts == [
        {"structured_ok": True, "cannot_answer": True, "reason": "지역 이름 조건을 표현할 수 없음"}
    ]
