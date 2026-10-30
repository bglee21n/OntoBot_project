"""파이프라인 전 구간(TRD 7.1·7.3) — 실제 DB + 대본대로 답하는 가짜 LLM.

LLM 을 가짜로 두는 이유: 모델 품질이 아니라 **파이프라인 계약**(상태·등급·실패 표시·조건 문장·
바인드 실행)을 고정하려는 것이다. 모델 품질은 스파이크·평가(scripts/spike_plan.py)가 잰다.
"""

from __future__ import annotations

import dataclasses
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
from ontobot.ontology.models import FunctionDef, check_document
from ontobot.query.executor import ExecResult, execute_plan

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


# ---------------------------------------------------------------- Function(PRD 5.10, TRD 17.1-7)

PIPE_RATIO = {
    "api_name": "pipe_ratio",
    "display_name": "파이프 고위험 비율",
    "params": [
        {
            "api_name": "min_grade",
            "display_name": "기준 위험등급",
            "data_type": "int",
            "required": False,
            "default": 4,
        }
    ],
    "queries": [
        {
            "api_name": "high_risk",
            "display_name": "고위험 수",
            "target": "Facility",
            "filters": [
                {"property": "name", "op": "contains", "value": "파이프"},
                {"property": "risk_grade", "op": "gte", "value": {"param": "min_grade"}},
            ],
            "aggregate": {"fn": "count", "group_by": ["facility_type"]},
        },
        {
            "api_name": "total",
            "display_name": "전체 수",
            "target": "Facility",
            "filters": [{"property": "name", "op": "contains", "value": "파이프"}],
            "aggregate": {"fn": "count", "group_by": ["facility_type"]},
        },
    ],
    "body": {
        "op": "mul",
        "args": [
            {
                "op": "div",
                "args": [{"op": "query", "name": "high_risk"}, {"op": "query", "name": "total"}],
            },
            {"op": "const", "value": 100},
        ],
    },
    "result_label": "고위험 비율(%)",
    "decimals": 1,
}


def _with_pipe_ratio(schema: registry.PublishedSchema) -> registry.PublishedSchema:
    # 다른 테스트가 넣은 시설과 섞이지 않게, 이 파일의 시설만 세는 Function 을 문서에 더한다.
    doc = schema.document
    fns = [*doc.functions, FunctionDef.model_validate(PIPE_RATIO)]
    assert not check_document(doc.model_copy(update={"functions": fns}))
    return dataclasses.replace(schema, document=doc.model_copy(update={"functions": fns}))


def _call(name: str, **args: Any) -> dict[str, Any]:
    return {
        "kind": "function",
        "function": {"name": name, "args": [{"name": k, "value": v} for k, v in args.items()]},
    }


async def test_function_result_is_cited_like_an_aggregate(monkeypatch, schema) -> None:
    script = Script(
        [_call("pipe_ratio")],
        "변전소의 고위험 비율은 100%이고, 철탑은 0%입니다. [[q:1#value]] [[q:2#value]]",
    )
    res = await _run(monkeypatch, _with_pipe_ratio(schema), script, "파이프 시설 고위험 비율")
    assert (res.status, res.grade) == ("ok", 1)
    assert res.plan == {"function": {"name": "pipe_ratio", "args": []}}
    assert res.conditions is not None
    assert res.conditions.startswith("파이프 고위험 비율(기준 위험등급 = 4(기본값)) · 고위험 수:")
    assert [c["ref"] for c in res.citations] == ["q:1#value", "q:2#value"]
    assert res.context_refs is None  # 집계처럼 대화 맥락을 바꾸지 않는다
    # 내부 조회 둘(";" 로 이어 붙인다), 조건 값은 바인드다.
    assert res.sql is not None and len(res.sql.split(";\n")) == 2 and "파이프" not in res.sql


async def test_function_inner_values_are_citable(monkeypatch, schema) -> None:
    # 인자 1 이면 둘 다 고위험 — 계산 근거(내부 조회 값)도 칸마다 인용된다.
    script = Script(
        [_call("pipe_ratio", min_grade=1)],
        "변전소는 1곳 중 1곳으로 100%입니다. [[q:1#high_risk]] [[q:1#total]] [[q:1#value]]",
    )
    res = await _run(monkeypatch, _with_pipe_ratio(schema), script, "위험등급 1 이상 비율")
    assert (res.status, res.grade) == ("ok", 1)
    assert "(기준 위험등급 = 1) ·" in (res.conditions or "")


async def test_wrong_function_value_is_removed(monkeypatch, schema) -> None:
    script = Script([_call("pipe_ratio")], "변전소의 고위험 비율은 50%입니다. [[q:1#value]]")
    res = await _run(monkeypatch, _with_pipe_ratio(schema), script, "파이프 시설 고위험 비율")
    assert (res.status, res.grade) == ("ok", 3)


async def test_function_over_unreadable_type_is_forbidden(monkeypatch, schema) -> None:
    script = Script([_call("region_high_risk_ratio", region="서울")])
    res = await _run(monkeypatch, schema, script, "서울 고위험 비율", readable={"Facility"})
    assert (res.status, res.failure_reason) == ("failed", "forbidden")
    assert script.calls == 1  # 재계획하지 않는다


async def test_function_argument_errors_are_replanned(monkeypatch, schema) -> None:
    # 질문의 3 이 인자에 없다(기본값 4) → 재계획, 필수 인자가 없어도 재계획.
    script = Script(
        [_call("pipe_ratio"), _call("pipe_ratio", min_grade=3)],
        "철탑은 0%입니다. [[q:2#value]]",
    )
    res = await _run(monkeypatch, _with_pipe_ratio(schema), script, "위험등급 3 이상 비율")
    assert "['3']" in res.plan_attempts[0]["errors"][0]
    assert (res.status, res.grade) == ("ok", 1)
    script = Script([_call("region_high_risk_ratio"), _call("region_high_risk_ratio")])
    res = await _run(monkeypatch, schema, script, "지역 고위험 비율")
    assert (res.status, res.failure_reason) == ("failed", "plan_invalid")
    assert "필수 인자" in res.plan_attempts[0]["errors"][0]


async def test_function_inner_failure_fails_the_whole_function(monkeypatch, schema) -> None:
    calls: list[str] = []

    async def broken(db, driver, vp):  # type: ignore[no-untyped-def]
        calls.append(vp.target.api_name)
        if len(calls) == 2:
            return ExecResult("failed", failure_reason="timeout", sql="SELECT 1")
        return await execute_plan(db, driver, vp)

    monkeypatch.setattr(
        ollama, "transport", httpx.MockTransport(Script([_call("pipe_ratio")]).handler)
    )
    async with session_scope() as db:
        res = await pipeline.run(
            db, LLMGateway(CFG), _with_pipe_ratio(schema), {"Facility"}, "비율", executor=broken
        )
    # 나머지 값으로 계산해 그럴듯한 답을 만들지 않는다.
    assert (res.status, res.failure_reason, res.grade) == ("failed", "timeout", None)
    assert calls == ["Facility", "Facility"]
