"""질의 파이프라인(TRD 7.1) — 계획 → 검증 → 컴파일·실행 → 조회 조건 → 답변 → 대조·등급.

이 함수는 저장하지 않는다. 대화·메시지·트레이스·맥락 기록은 호출부(``api.chat``)가
``PipelineResult`` 로 한다 — 평가(Phase 7)가 같은 함수를 인프로세스로 돌리기 위해서다.
진행 상황(``stage``·``conditions``)은 ``emit`` 으로 바로 알린다. 답변 본문은 대조를 마친 결과에만
있다(TRD 8.3).

상태와 등급은 다른 축이다(TRD 7.3): ``failed``·``not_query`` 는 등급이 없고, ``empty`` 는 ①이다.

Action 요청 말(플래너 ``kind: action``)은 조회하지 않는다. 제안을 ``actions.params`` 로 대조해
``action_preview`` 상태로 돌려준다(등급 없음). 실행 기록(미리보기)을 만드는 것은 저장하는 호출부다
(``api.chat`` → ``actions.runner.preview``) — 승인·실행은 Action API 가 한다(TRD 17.1-5).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Set
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.actions import params as action_params
from ontobot.chat import answerer, templates
from ontobot.graph import client as graph
from ontobot.graph.projector import LAG_WARN_SECONDS, lag
from ontobot.grounding.grader import grade
from ontobot.grounding.types import Evidence, GroundingContext
from ontobot.llm.gateway import LLMGateway, LlmUnavailable
from ontobot.llm.structured import StructuredOutputError
from ontobot.ontology.registry import PublishedSchema
from ontobot.ontology.summary import schema_summary
from ontobot.query import conditions, planner, records
from ontobot.query.coverage import coverage_errors
from ontobot.query.executor import ExecResult, execute_plan
from ontobot.query.plan import PlannerOutput
from ontobot.query.references import (
    check_raw_references,
    check_references,
    rewrite_fk_names,
)
from ontobot.query.validator import PlanInvalid, ValidatedPlan, validate

Status = Literal["ok", "empty", "failed", "not_query", "action_preview"]
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
# 조회 실행기. 평가의 장애 주입(``evaluation.faults``)이 바꿔 끼운다.
# 운영은 언제나 ``execute_plan``.
Executor = Callable[[AsyncSession, graph.AsyncDriver | None, ValidatedPlan], Awaitable[ExecResult]]


async def _silent(_event: str, _data: dict[str, Any]) -> None:
    return None


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
    cypher: str | None = None
    # 투영 지연이 임계값을 넘었을 때만: 관계 정보가 반영된 시각(TRD 5.5, 14.2 conditions).
    graph_as_of: str | None = None
    # 이 답으로 바꿀 대화 맥락. None 이면 맥락을 그대로 둔다.
    context_refs: list[dict[str, str]] | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    removed_citations: list[dict[str, Any]] = field(default_factory=list)
    estimated_sentences: list[int] = field(default_factory=list)
    fact_sentences: int = 0
    verified_sentences: int = 0
    raw_answer: str | None = None
    # ``action_preview`` 일 때: ``actions.params.ProposalCheck.as_dict`` (+ 호출부가 채우는 run_id)
    action: dict[str, Any] | None = None
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
    for f in (*vp.filters, *(f for h in vp.hops for f in h.filters)):
        values = f.value if isinstance(f.value, tuple) else (f.value,)
        for v in values:
            if isinstance(v, bool):
                continue
            terms.update({str(v), records.display(f.prop, v)})
            if hasattr(v, "year"):  # 날짜 조건은 "2015년 이전"처럼 연도만 되풀이하는 경우가 흔하다
                terms.add(str(v.year))
    return frozenset(terms)


def _pii_values(vp: ValidatedPlan, rs: records.RecordSet | None = None) -> list[str]:
    """이번 요청에서 PII 인 값 — ``pii_kind`` 속성에 건 조건 값과 결과 레코드의 그 속성 값."""
    out: list[str] = []
    for f in (*vp.filters, *(f for h in vp.hops for f in h.filters)):
        if f.prop.pii_kind and not isinstance(f.value, bool):
            out += [str(v) for v in (f.value if isinstance(f.value, tuple) else (f.value,))]
    if rs is not None:
        pii = {p.api_name for p in vp.target.properties if p.pii_kind}
        out += [
            f.display for r in rs.records for f in r.fields if f.name in pii and f.raw is not None
        ]
        out += [
            str(f.raw) for r in rs.records for f in r.fields if f.name in pii and f.raw is not None
        ]
    return out


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
    context: list[dict[str, str]],
    res: PipelineResult,
    actions: Set[str],
) -> ValidatedPlan | action_params.ProposalCheck | PipelineResult:
    # 계획은 폐기되지 않은 요소로만 세운다 — 폐기 타입은 '없는 것'이라 재계획 대상이다
    # (권한 없음이 아니다).
    doc = schema.document.active()
    summary = schema_summary(doc, readable, actions)
    allowed = frozenset((c["type"], c["pk"]) for c in context)
    errors: list[str] | None = None
    proposal: action_params.ProposalCheck | None = None
    for _ in range(2):  # 최초 + 검증 오류를 붙인 재계획 1회(TRD 7.1 3단계)
        proposal = None
        try:
            out: PlannerOutput = await planner.plan(llm, summary, question, errors, context)
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
        if out.kind == "action":
            if out.action is None:
                errors = ["kind 가 action 이면 action 이 있어야 합니다"]
                res.plan_attempts.append({"structured_ok": True, "errors": errors})
                continue
            res.plan = {"action": out.action.model_dump(mode="json")}
            try:
                proposal = await action_params.check_proposal(
                    db, doc, actions, out.action, question, context
                )
            except action_params.UnknownAction as e:
                errors = e.errors
                res.plan_attempts.append({"structured_ok": True, "action": True, "errors": errors})
                continue
            res.plan_attempts.append(
                {"structured_ok": True, "action": True, "errors": proposal.errors}
            )
            if proposal.errors:
                errors = proposal.errors
                continue
            return proposal
        if out.plan is None:
            errors = ["kind 가 query 이면 plan 이 있어야 합니다"]
            res.plan_attempts.append({"structured_ok": True, "errors": errors})
            continue
        plan, rewrites = await rewrite_fk_names(db, doc, out.plan)
        res.plan = plan.model_dump(mode="json")
        try:
            vp = validate(doc, readable, plan, allowed)
        except PlanInvalid as e:
            if e.forbidden:
                res.plan_attempts.append({"structured_ok": True, "errors": e.errors})
                return _failed(res, "forbidden")
            errors = (
                e.errors
                + await check_raw_references(db, doc, plan)
                + coverage_errors(question, plan)
            )
            res.plan_attempts.append(
                {"structured_ok": True, "errors": errors, "rewrites": rewrites}
            )
            continue
        ref_errors = await check_references(db, doc, vp) + coverage_errors(question, plan)
        if ref_errors:
            res.plan_attempts.append(
                {"structured_ok": True, "errors": ref_errors, "rewrites": rewrites}
            )
            errors = ref_errors
            continue
        res.plan_attempts.append({"structured_ok": True, "errors": [], "rewrites": rewrites})
        return vp
    if proposal is not None:
        # 재계획 뒤에도 대조되지 않은 제안 — 실행 기록을 만들지 않고 확인 사항으로 보인다.
        return proposal
    return _failed(res, "plan_invalid")


def _action(res: PipelineResult, check: action_params.ProposalCheck) -> PipelineResult:
    res.status, res.grade = "action_preview", None
    res.action = check.as_dict()
    name = check.action.display_name
    res.answer = (
        templates.action_proposed(name) if check.ready else templates.action_incomplete(name)
    )
    return res


def _as_of(oldest_pending_at: str) -> tuple[str, str]:
    """(ISO, 'HH:MM') — 미반영 이벤트 중 가장 오래된 것의 시각. 그 전까지는 반영됐다."""
    at = datetime.fromisoformat(oldest_pending_at).astimezone()
    return at.isoformat(), at.strftime("%H:%M")


async def run(
    db: AsyncSession,
    llm: LLMGateway,
    schema: PublishedSchema | None,
    readable: Set[str],
    question: str,
    *,
    grade2_enabled: bool = True,
    context: list[dict[str, str]] | None = None,
    graph_driver: graph.AsyncDriver | None = None,
    emit: Emit = _silent,
    executor: Executor = execute_plan,
    actions: Set[str] = frozenset(),
) -> PipelineResult:
    """``context`` 는 대화 맥락(플래너용 제목이 붙은 식별자, ``chat.context.with_titles``).

    ``actions`` 는 이 사용자가 요청할 수 있는 Action 이름(``actions.params.requestable``). 비면
    플래너는 Action 을 모른다.
    """
    res = PipelineResult(status="failed", answer="")
    timer = _Timer(res.stage_latency_ms)
    if schema is None:
        return _failed(res, "no_schema")
    try:
        await emit("stage", {"stage": "planning"})
        with timer.stage("planning"):
            planned = await _plan_and_validate(
                db, llm, schema, readable, question, context or [], res, actions
            )
        if isinstance(planned, PipelineResult):
            return planned
        if isinstance(planned, action_params.ProposalCheck):
            return _action(res, planned)
        vp = planned
        llm.protect(_pii_values(vp))
        res.conditions = conditions.describe(vp)

        await emit("stage", {"stage": "querying"})
        with timer.stage("querying"):
            ex = await executor(db, graph_driver, vp)
        res.sql, res.cypher = ex.sql or None, ex.cypher
        if vp.hops and ex.failure_reason != "graph_unavailable":
            lagged = await lag(db)
            if lagged["projection_lag_seconds"] > LAG_WARN_SECONDS and lagged["oldest_pending_at"]:
                res.graph_as_of, hhmm = _as_of(lagged["oldest_pending_at"])
                res.conditions += f" · 관계 정보는 {hhmm} 기준"
        await emit("conditions", {"text": res.conditions, "graph_as_of": res.graph_as_of})
        if ex.status == "failed":
            return _failed(res, ex.failure_reason or "execution_error")
        if ex.status == "empty":
            res.status, res.grade, res.answer = "empty", 1, templates.empty(res.conditions)
            return res

        rs = records.build(vp, ex.rows)
        # 외부 공급자로 가면 토큰으로 바뀌고, 기록에는 마스킹된다(게이트웨이). 대조는 원값으로 한다.
        llm.protect(_pii_values(vp, rs))
        await emit("stage", {"stage": "answering"})
        with timer.stage("answering"):
            raw = await answerer.answer(llm, question, res.conditions, records.render_for_llm(rs))
        res.raw_answer = raw
        await emit("stage", {"stage": "verifying"})
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
    if vp.aggregate is None:
        res.context_refs = [
            {"type": r.type, "pk": r.ref.split("/", 1)[1]} for r in rs.records if r.type
        ]
    return res
