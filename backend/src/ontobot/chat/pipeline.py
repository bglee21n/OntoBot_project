"""질의 파이프라인(TRD 7.1) — 계획 → 검증 → 컴파일·실행 → 조회 조건 → 답변 → 대조·등급.

이 함수는 저장하지 않는다. 대화·메시지·트레이스·맥락 기록은 호출부(``api.chat``)가
``PipelineResult`` 로 한다 — 평가(Phase 7)가 같은 함수를 인프로세스로 돌리기 위해서다.
진행 상황(``stage``·``conditions``)은 ``emit`` 으로 바로 알린다. 답변 본문은 대조를 마친 결과에만
있다(TRD 8.3).

상태와 등급은 다른 축이다(TRD 7.3): ``failed``·``not_query`` 는 등급이 없고, ``empty`` 는 ①이다.

Action 요청 말(플래너 ``kind: action``)은 조회하지 않는다. 제안을 ``actions.params`` 로 대조해
``action_preview`` 상태로 돌려준다(등급 없음). 실행 기록(미리보기)을 만드는 것은 저장하는 호출부다
(``api.chat`` → ``actions.runner.preview``) — 승인·실행은 Action API 가 한다(TRD 17.1-5).

Function 을 부르는 말(``kind: function``)은 ``functions.binding`` 으로 인자·내부 조회를 검증하고
(호출자 권한), 내부 조회를 **같은 실행기**로 돌려 식을 계산한다. 결과는 집계와 같이 ``q:N`` 근거다.

문서 질문(``kind: document``, Phase 16d)은 보는 사람의 문서 그룹(``documents``) 안에서 질문
문장 그대로 벡터 검색한다(``query.retrievers.vector``). 찾은 청크가 ``d:N/M`` 근거이고, 대조는
청크 원문과의 정확 일치다(``grounding.document``). 읽을 수 있는 색인된 문서가 없으면 플래너 요약에
``documents`` 를 싣지 않는다.
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
from ontobot.functions import binding as fn_binding
from ontobot.functions import evaluator as fn_evaluator
from ontobot.functions import runner as fn_runner
from ontobot.graph import client as graph
from ontobot.graph.projector import LAG_WARN_SECONDS, lag
from ontobot.grounding.grader import GradeResult, grade
from ontobot.grounding.types import Evidence, GroundingContext
from ontobot.llm.gateway import LLMGateway, LlmUnavailable
from ontobot.llm.structured import StructuredOutputError
from ontobot.ontology.registry import PublishedSchema
from ontobot.ontology.summary import schema_summary
from ontobot.query import conditions, planner, records
from ontobot.query.coverage import coverage_errors, uncovered_in
from ontobot.query.executor import ExecResult, execute_plan
from ontobot.query.plan import FullPlannerOutput, FunctionCall
from ontobot.query.references import (
    check_raw_references,
    check_references,
    rewrite_fk_names,
)
from ontobot.query.retrievers import vector
from ontobot.query.validator import (
    CheckedFilter,
    PlanInvalid,
    Scope,
    ValidatedPlan,
    check_scopes,
    validate,
)
from ontobot.security import rbac

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
    # 실행이 돌려준 행 수(객체 행 또는 집계 행). 실행 전에 끝났거나 실패면 None — 트레이스 화면용.
    row_count: int | None = None
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


def _all_filters(vp: ValidatedPlan) -> list[CheckedFilter]:
    return [*vp.filters, *(f for h in vp.hops for f in h.filters)]


def _condition_terms(vp: ValidatedPlan) -> frozenset[str]:
    terms: set[str] = set()
    for f in _all_filters(vp):
        values = f.value if isinstance(f.value, tuple) else (f.value,)
        for v in values:
            if isinstance(v, bool):
                continue
            terms.update({str(v), records.display(f.prop, v)})
            if hasattr(v, "year"):  # 날짜 조건은 "2015년 이전"처럼 연도만 되풀이하는 경우가 흔하다
                terms.add(str(v.year))
    return frozenset(terms)


def _function_terms(vf: fn_binding.ValidatedFunction) -> frozenset[str]:
    """Function 의 조건 값 — 내부 조회 조건 값과 인자(조건 문장에 있는 값은 사실이 아니다)."""
    terms: set[str] = set()
    for b in vf.queries:
        terms |= _condition_terms(b.plan)
    for v in vf.args.values():
        if v is not None and not isinstance(v, bool):
            terms.update({str(v), records.display(None, v)})
    return frozenset(terms)


def _function_coverage(
    question: str, call: FunctionCall, vf: fn_binding.ValidatedFunction
) -> list[str]:
    """인자 ↔ 질문 대조(결정론적). 두 방향을 본다.

    - 질문의 숫자가 인자·내부 조건 어디에도 없다 → 그 값을 쓰는 인자가 빠졌다
      (``query.coverage``).
    - 넣은 인자 값이 질문에 없다 → 지어낸 값이다(Action 값 파라미터와 같은 규칙,
      ``actions.params``). enum·bool 은 닫힌 집합이라 보지 않는다. 기본값을 그대로 적어 넣은
      것도 막는다 — 측정에서 플래너가 "대구 지역의 고위험 비율"(지역 인자 없음)에 기본값 4 를
      채워 지역 조건을 뺀 채 불렀다.
    """
    errors: list[str] = []
    for a in call.args:
        p = vf.fn.param(a.name)
        if p is None or p.data_type in {"enum", "bool"} or a.value is None:
            continue
        if not action_params.in_question(question, a.value):
            errors.append(
                f"인자 '{a.name}' 의 값 {a.value} 이 질문에 없습니다 — 질문에 없는 값은 넣지 "
                "마세요(선택 인자는 기본값이 쓰입니다). 질문의 조건 중 이 Function 의 params 로 "
                "받을 수 없는 것이 있으면 cannot_answer 를 내세요"
            )
    missing = uncovered_in(question, fn_binding.input_values(vf))
    if missing:
        errors.append(
            f"질문의 값 {missing} 이 Function 인자 어디에도 없습니다. 그 값을 쓰는 인자를 "
            "빼지 말고 args 에 넣으세요. 이 Function 의 인자로 표현할 수 없으면 cannot_answer 를 "
            "내세요"
        )
    return errors


def _pii_values(vp: ValidatedPlan, rs: records.RecordSet | None = None) -> list[str]:
    """이번 요청에서 PII 인 값 — ``pii_kind`` 속성에 건 조건 값과 결과 레코드의 그 속성 값."""
    out: list[str] = []
    for f in _all_filters(vp):
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


EMBEDDER = "embedder"


@dataclass(frozen=True)
class DocumentSearch:
    """플래너가 문서 질문이라고 한 것 — 검색어는 질문 그대로다."""


async def _document_titles(db: AsyncSession, llm: LLMGateway, groups: Set[int]) -> list[str]:
    if not groups or EMBEDDER not in llm.config.roles:
        return []
    return await vector.has_documents(db, groups, llm.model_for(EMBEDDER))


async def _plan_and_validate(
    db: AsyncSession,
    llm: LLMGateway,
    schema: PublishedSchema,
    readable: Set[str],
    question: str,
    context: list[dict[str, str]],
    res: PipelineResult,
    actions: Set[str],
    access: rbac.Access | None,
    documents: Set[int],
) -> (
    ValidatedPlan
    | fn_binding.ValidatedFunction
    | action_params.ProposalCheck
    | DocumentSearch
    | PipelineResult
):
    # 계획은 폐기되지 않은 요소로만 세운다 — 폐기 타입은 '없는 것'이라 재계획 대상이다
    # (권한 없음이 아니다). 숨긴 속성(Phase 14b)도 이 사용자에게는 없는 것이다 — 요약·검증·데이터
    # 블록·Function 이 모두 이 "보이는 문서"를 본다.
    doc = schema.document.active()
    scopes: dict[str, Scope] = {}
    if access is not None:
        doc = doc.without_properties(access.hidden())
        scopes = check_scopes(schema.document, access.read_scopes())
    summary = schema_summary(doc, readable, actions)
    titles = await _document_titles(db, llm, documents)
    if titles:
        summary = {**summary, "documents": titles}
    allowed = frozenset((c["type"], c["pk"]) for c in context)
    errors: list[str] | None = None
    proposal: action_params.ProposalCheck | None = None
    for _ in range(2):  # 최초 + 검증 오류를 붙인 재계획 1회(TRD 7.1 3단계)
        proposal = None
        try:
            out: FullPlannerOutput = await planner.plan(llm, summary, question, errors, context)
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
        if out.kind == "document":
            if not titles:  # 요약에 없던 종류 — 스키마가 막지만 한 번 더
                errors = [
                    "문서 검색을 쓸 수 없습니다 — query 로 계획하거나 cannot_answer 를 내세요"
                ]
                res.plan_attempts.append({"structured_ok": True, "errors": errors})
                continue
            res.plan_attempts.append({"structured_ok": True, "document": True, "errors": []})
            res.plan = {"document": {"query": question}}
            return DocumentSearch()
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
        if out.kind == "function":
            if out.function is None:
                errors = ["kind 가 function 이면 function 이 있어야 합니다"]
                res.plan_attempts.append({"structured_ok": True, "errors": errors})
                continue
            res.plan = {"function": out.function.model_dump(mode="json")}
            try:
                vf = fn_binding.bind(doc, readable, out.function, scopes)
            except PlanInvalid as e:
                res.plan_attempts.append(
                    {"structured_ok": True, "function": True, "errors": e.errors}
                )
                if e.forbidden:
                    return _failed(res, "forbidden")
                errors = e.errors
                continue
            ref_errors = [
                f"Function '{vf.fn.api_name}' 의 조회 '{b.query.api_name}' 에서 참조 값이 실재하지 "
                "않습니다 — 인자에는 질문에 적힌 값(코드·식별자)을 그대로 넣으세요"
                for b in vf.queries
                if await check_references(db, doc, b.plan)
            ] + _function_coverage(question, out.function, vf)
            res.plan_attempts.append(
                {"structured_ok": True, "function": True, "errors": ref_errors}
            )
            if ref_errors:
                errors = ref_errors
                continue
            return vf
        if out.plan is None:
            errors = ["kind 가 query 이면 plan 이 있어야 합니다"]
            res.plan_attempts.append({"structured_ok": True, "errors": errors})
            continue
        plan, rewrites = await rewrite_fk_names(db, doc, out.plan)
        res.plan = plan.model_dump(mode="json")
        try:
            vp = validate(doc, readable, plan, allowed, scopes)
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
    access: rbac.Access | None = None,
    documents: Set[int] = frozenset(),
) -> PipelineResult:
    """``context`` 는 대화 맥락(플래너용 제목이 붙은 식별자, ``chat.context.with_titles``).

    ``actions`` 는 이 사용자가 요청할 수 있는 Action 이름(``actions.params.requestable``). 비면
    플래너는 Action 을 모른다. ``access`` 는 속성 숨김·행 정책(``security.rbac.access``) — None 이면
    타입 단위(``readable``)만 본다. ``documents`` 는 읽을 수 있는 문서 그룹
    (``rbac.document_groups``) — 비면 문서 검색을 하지 않는다.
    """
    res = PipelineResult(status="failed", answer="")
    timer = _Timer(res.stage_latency_ms)
    if schema is None:
        return _failed(res, "no_schema")
    try:
        await emit("stage", {"stage": "planning"})
        with timer.stage("planning"):
            planned = await _plan_and_validate(
                db,
                llm,
                schema,
                readable,
                question,
                context or [],
                res,
                actions,
                access,
                documents,
            )
        if isinstance(planned, PipelineResult):
            return planned
        if isinstance(planned, action_params.ProposalCheck):
            return _action(res, planned)
        if isinstance(planned, DocumentSearch):
            return await _run_documents(
                db, llm, documents, question, res, timer, emit, grade2_enabled=grade2_enabled
            )
        if isinstance(planned, fn_binding.ValidatedFunction):
            return await _run_function(
                db,
                llm,
                planned,
                readable,
                question,
                res,
                timer,
                emit,
                executor,
                graph_driver,
                grade2_enabled=grade2_enabled,
            )
        vp = planned
        llm.protect(_pii_values(vp))
        res.conditions = conditions.describe(vp)

        await emit("stage", {"stage": "querying"})
        with timer.stage("querying"):
            ex = await executor(db, graph_driver, vp)
        res.sql, res.cypher = ex.sql or None, ex.cypher
        res.row_count = None if ex.status == "failed" else len(ex.rows)
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

    _finish(res, g, rs)
    if vp.aggregate is None:
        res.context_refs = [
            {"type": r.type, "pk": r.ref.split("/", 1)[1]} for r in rs.records if r.type
        ]
    return res


def _snapshot(r: records.Record | None) -> list[dict[str, str]] | None:
    """집계·Function 결과 행(``q:N``)의 값 — 근거 카드가 보여 준다.

    객체 근거는 카드가 열 때마다 지금 값을 다시 읽지만, 집계 행은 다시 읽을 곳이 없다. 그래서
    답을 만들 때 대조한 그 값(표시 문자열)을 인용에 함께 남긴다. 객체 근거는 None.
    """
    if r is None or r.type is not None:
        return None
    return [{"name": f.name, "label": f.label, "display": f.display} for f in r.fields]


def _finish(res: PipelineResult, g: GradeResult, rs: records.RecordSet) -> None:
    res.status, res.grade = "ok", g.grade
    res.answer = g.text if g.text is not None else templates.ungrounded(res.conditions or "")
    res.citations = [
        {
            "ref": f"{c.ref}#{c.field}",
            "title": (r.title if (r := rs.by_ref(c.ref)) else c.ref),
            "synced_at": r.synced_at.isoformat() if r and r.synced_at else None,
            "values": _snapshot(r),
        }
        for c in g.citations
    ]
    res.removed_citations = [r.__dict__ for r in g.removed]
    res.estimated_sentences = g.estimated_sentences
    res.fact_sentences, res.verified_sentences = g.fact_sentences, g.verified_sentences


async def _run_function(
    db: AsyncSession,
    llm: LLMGateway,
    vf: fn_binding.ValidatedFunction,
    readable: Set[str],
    question: str,
    res: PipelineResult,
    timer: _Timer,
    emit: Emit,
    executor: Executor,
    graph_driver: graph.AsyncDriver | None,
    *,
    grade2_enabled: bool,
) -> PipelineResult:
    """Function 결과 — 조회와 같은 단계(조건 문장 → 실행 → 답변 → 대조)를 탄다.

    LLM 오류(``LlmUnavailable``)는 호출부의 ``try`` 가 받는다.
    대화 맥락은 바꾸지 않는다(집계와 같다).
    """
    llm.protect([v for b in vf.queries for v in _pii_values(b.plan)])
    res.conditions = fn_evaluator.describe(vf)
    await emit("stage", {"stage": "querying"})
    with timer.stage("querying"):
        ex = await fn_runner.run(db, graph_driver, vf, executor)
    res.sql, res.cypher = ex.sql or None, ex.cypher
    res.row_count = None if ex.status == "failed" else len(ex.rows)
    if any(b.plan.hops for b in vf.queries) and ex.failure_reason != "graph_unavailable":
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
    rs = fn_evaluator.build(vf, ex.rows)
    await emit("stage", {"stage": "answering"})
    with timer.stage("answering"):
        raw = await answerer.answer(llm, question, res.conditions, records.render_for_llm(rs))
    res.raw_answer = raw
    await emit("stage", {"stage": "verifying"})
    with timer.stage("verifying"):
        g = grade(raw, _evidence(rs, readable, _function_terms(vf)), grade2_enabled=grade2_enabled)
    _finish(res, g, rs)
    return res


def _document_block(hits: list[vector.Hit]) -> str:
    """문서 청크 데이터 블록 — 줄마다 ``인용키 | 이름 | 값``.

    ADR-0004 와 같은 모양이고 값은 청크 원문이다.
    """
    lines = ["<data>"]
    for h in hits:
        lines.append(f"## {h.ref} — {h.title} (조각 {h.chunk_no})")
        lines.append(f"{h.ref} | {h.title} | {' '.join(h.text.split())}")
    lines.append("</data>")
    return "\n".join(lines)


async def _run_documents(
    db: AsyncSession,
    llm: LLMGateway,
    groups: Set[int],
    question: str,
    res: PipelineResult,
    timer: _Timer,
    emit: Emit,
    *,
    grade2_enabled: bool,
) -> PipelineResult:
    """문서 검색 → 답변 → 청크 원문 대조. LLM 오류는 호출부의 ``try`` 가 받는다.

    대화 맥락은 바꾸지 않는다(객체 답이 아니다).
    """
    res.conditions = templates.DOCUMENT_CONDITIONS
    await emit("stage", {"stage": "querying"})
    with timer.stage("querying"):
        (qv,) = await llm.embed(EMBEDDER, [question])
        hits = await vector.search(db, groups, llm.model_for(EMBEDDER), qv)
    res.row_count = len(hits)
    await emit("conditions", {"text": res.conditions, "graph_as_of": None})
    if not hits:
        res.status, res.grade, res.answer = "empty", 1, templates.DOCUMENT_EMPTY
        return res
    await emit("stage", {"stage": "answering"})
    with timer.stage("answering"):
        raw = await answerer.answer(
            llm, question, res.conditions, _document_block(hits), documents=True
        )
    res.raw_answer = raw
    await emit("stage", {"stage": "verifying"})
    ctx = GroundingContext(
        evidence={h.ref: Evidence(h.ref, None, h.title, {}, text=h.text) for h in hits},
        readable=frozenset(),
    )
    with timer.stage("verifying"):
        g = grade(raw, ctx, grade2_enabled=grade2_enabled)
    by_ref = {h.ref: h for h in hits}
    res.status, res.grade = "ok", g.grade
    res.answer = g.text if g.text is not None else templates.DOCUMENT_UNGROUNDED
    res.citations = [
        {
            "ref": f"{c.ref}#chunk",
            "title": f"{h.title} · 조각 {h.chunk_no}",
            "synced_at": None,
            # 대조한 그 청크 원문을 남긴다 — 문서가 지워져도 답의 근거를 보여 줄 수 있다.
            "values": [{"name": "chunk", "label": h.title, "display": h.text}],
        }
        for c in g.citations
        if (h := by_ref.get(c.ref)) is not None
    ]
    res.removed_citations = [r.__dict__ for r in g.removed]
    res.estimated_sentences = g.estimated_sentences
    res.fact_sentences, res.verified_sentences = g.fact_sentences, g.verified_sentences
    return res
