"""질의 파이프라인(TRD 7.1) — 계획 → 검증 → 컴파일·실행 → 조회 조건 → 답변 → 대조·등급.

Phase 1.5 스파이크판이다. 단일 타입 질의만 다룬다(탐색·맥락은 Phase 4). SSE 와 트레이스 저장은
Phase 4 에서 이 함수의 결과(``PipelineResult``)를 그대로 받아 붙인다 — 단계 구분과 상태·등급
규칙은 여기서 이미 최종 모양이다.

상태와 등급은 다른 축이다(TRD 7.3): ``failed``·``not_query`` 는 등급이 없고, ``empty`` 는 ①이다.
"""

from __future__ import annotations

import time
from collections.abc import Set
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.chat import answerer, templates
from ontobot.grounding.grader import grade
from ontobot.grounding.types import Evidence, GroundingContext
from ontobot.llm.gateway import LLMGateway, LlmUnavailable
from ontobot.llm.structured import StructuredOutputError
from ontobot.ontology.registry import PublishedSchema
from ontobot.ontology.summary import schema_summary
from ontobot.query import conditions, planner, records
from ontobot.query.compiler import compile_plan
from ontobot.query.executor import execute
from ontobot.query.plan import PlannerOutput
from ontobot.query.references import check_references
from ontobot.query.validator import PlanInvalid, ValidatedPlan, validate

Status = Literal["ok", "empty", "failed", "not_query"]


@dataclass
class PipelineResult:
    status: Status
    answer: str
    failure_reason: str | None = None
    grade: int | None = None
    conditions: str | None = None
    plan: dict[str, Any] | None = None
    # 계획 시도마다 (구조화 출력 성공 여부, 검증 오류). 스파이크의 plan_invalid 측정 근거.
    plan_attempts: list[dict[str, Any]] = field(default_factory=list)
    sql: str | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    removed_citations: list[dict[str, Any]] = field(default_factory=list)
    estimated_sentences: list[int] = field(default_factory=list)
    fact_sentences: int = 0
    verified_sentences: int = 0
    raw_answer: str | None = None
    stage_latency_ms: dict[str, int] = field(default_factory=dict)


class _Timer:
    def __init__(self, sink: dict[str, int]) -> None:
        self.sink = sink

    def stage(self, name: str) -> _Stage:
        return _Stage(self.sink, name)


class _Stage:
    def __init__(self, sink: dict[str, int], name: str) -> None:
        self.sink, self.name = sink, name

    def __enter__(self) -> None:
        self.started = time.perf_counter()

    def __exit__(self, *_: object) -> None:
        elapsed = int((time.perf_counter() - self.started) * 1000)
        self.sink[self.name] = self.sink.get(self.name, 0) + elapsed


def _failed(res: PipelineResult, reason: str) -> PipelineResult:
    res.status, res.failure_reason, res.grade = "failed", reason, None
    res.answer = templates.FAILED[reason]
    return res


def _condition_terms(vp: ValidatedPlan) -> frozenset[str]:
    terms: set[str] = set()
    for f in vp.filters:
        values = f.value if isinstance(f.value, tuple) else (f.value,)
        for v in values:
            if isinstance(v, bool):
                continue
            terms.update({str(v), records.display(f.prop, v)})
            if hasattr(v, "year"):  # 날짜 조건은 "2015년 이전"처럼 연도만 되풀이하는 경우가 흔하다
                terms.add(str(v.year))
    return frozenset(terms)


def _evidence(
    rs: records.RecordSet, readable: Set[str], condition_terms: frozenset[str] = frozenset()
) -> GroundingContext:
    ev = {
        r.ref: Evidence(
            ref=r.ref,
            object_type=r.type,
            title=r.title,
            values={f.name: ("" if f.raw is None else str(f.raw), f.display) for f in r.fields},
        )
        for r in rs.records
    }
    return GroundingContext(
        evidence=ev,
        readable=readable,
        titles=frozenset(rs.titles),
        enum_values=frozenset(rs.enum_values),
        condition_terms=condition_terms,
    )


async def _plan_and_validate(
    db: AsyncSession,
    llm: LLMGateway,
    schema: PublishedSchema,
    readable: Set[str],
    question: str,
    res: PipelineResult,
) -> ValidatedPlan | PipelineResult:
    summary = schema_summary(schema.document, readable)
    errors: list[str] | None = None
    for _ in range(2):  # 최초 + 검증 오류를 붙인 재계획 1회(TRD 7.1 3단계)
        try:
            out: PlannerOutput = await planner.plan(llm, summary, question, errors)
        except StructuredOutputError as e:
            res.plan_attempts.append({"structured_ok": False, "errors": e.errors})
            return _failed(res, "plan_invalid")
        if out.kind == "cannot_answer":
            res.plan_attempts.append(
                {"structured_ok": True, "cannot_answer": True, "reason": out.reason}
            )
            return _failed(res, "plan_invalid")
        if out.kind == "not_query":
            res.plan_attempts.append({"structured_ok": True, "not_query": True})
            res.status, res.answer = "not_query", templates.NOT_QUERY
            return res
        if out.plan is None:
            errors = ["kind 가 query 이면 plan 이 있어야 합니다"]
            res.plan_attempts.append({"structured_ok": True, "errors": errors})
            continue
        res.plan = out.plan.model_dump(mode="json")
        try:
            vp = validate(schema.document, readable, out.plan)
        except PlanInvalid as e:
            res.plan_attempts.append({"structured_ok": True, "errors": e.errors})
            if e.forbidden:
                return _failed(res, "forbidden")
            errors = e.errors
            continue
        ref_errors = await check_references(db, schema.document, vp)
        if ref_errors:
            res.plan_attempts.append({"structured_ok": True, "errors": ref_errors})
            errors = ref_errors
            continue
        res.plan_attempts.append({"structured_ok": True, "errors": []})
        return vp
    return _failed(res, "plan_invalid")


async def run(
    db: AsyncSession,
    llm: LLMGateway,
    schema: PublishedSchema | None,
    readable: Set[str],
    question: str,
    *,
    grade2_enabled: bool = True,
) -> PipelineResult:
    res = PipelineResult(status="failed", answer="")
    timer = _Timer(res.stage_latency_ms)
    if schema is None:
        return _failed(res, "no_schema")
    try:
        with timer.stage("planning"):
            planned = await _plan_and_validate(db, llm, schema, readable, question, res)
        if isinstance(planned, PipelineResult):
            return planned
        vp = planned
        res.conditions = conditions.describe(vp)

        with timer.stage("querying"):
            compiled = compile_plan(vp)
            ex = await execute(db, compiled)
        res.sql = ex.sql
        if ex.status == "failed":
            return _failed(res, ex.failure_reason or "execution_error")
        if ex.status == "empty":
            res.status, res.grade, res.answer = "empty", 1, templates.empty(res.conditions)
            return res

        rs = records.build(vp, ex.rows)
        with timer.stage("answering"):
            raw = await answerer.answer(llm, question, res.conditions, records.render_for_llm(rs))
        res.raw_answer = raw
        with timer.stage("verifying"):
            g = grade(
                raw, _evidence(rs, readable, _condition_terms(vp)), grade2_enabled=grade2_enabled
            )
    except LlmUnavailable:
        return _failed(res, "llm_unavailable")

    res.status, res.grade = "ok", g.grade
    res.answer = g.text if g.text is not None else templates.ungrounded(res.conditions)
    res.citations = [
        {
            "ref": f"{c.ref}#{c.field}",
            "title": (r.title if (r := rs.by_ref(c.ref)) else c.ref),
            "synced_at": r.synced_at.isoformat() if r and r.synced_at else None,
        }
        for c in g.citations
    ]
    res.removed_citations = [r.__dict__ for r in g.removed]
    res.estimated_sentences = g.estimated_sentences
    res.fact_sentences, res.verified_sentences = g.fact_sentences, g.verified_sentences
    return res
