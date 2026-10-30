"""평가 라우터(TRD 14.1 평가 묶음) — 검증과 위임만 한다.

골든셋·문항 편집과 평가 실행. 실행은 작업 큐(``eval_run``)로 워커가 돈다 — 실제 모델로 문항마다
몇 초씩 걸리므로 요청 안에서 돌리지 않는다. 같은 골든셋의 실행이 이미 돌고 있으면 새로 넣지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ontobot.api.deps import CurrentUser, Db, SettingsDep, require_role
from ontobot.api.errors import ProblemError
from ontobot.chat import feedback as chat_feedback
from ontobot.db.models.evaluation import EvalResult, EvalRun, GoldenItem, GoldenSet
from ontobot.evaluation import golden, runner
from ontobot.evaluation.golden import ExpectedAction
from ontobot.jobs import handlers, queue
from ontobot.llm.config import load as load_llm_config
from ontobot.security import audit
from ontobot.security.rbac import ROLE_BOT_BUILDER, ROLE_MODELER

router = APIRouter(tags=["eval"])

Builder = Annotated[CurrentUser, Depends(require_role(ROLE_BOT_BUILDER, ROLE_MODELER))]

Status = golden.Status
Fault = Literal["none", "executor_error", "graph_down", "timeout"]


# ---------------------------------------------------------------- 모델


class GoldenSetIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    roles: dict[str, list[str]] = Field(default_factory=dict)


class GoldenSetOut(BaseModel):
    id: int
    name: str
    domain_pack: str | None
    ontology_version_id: int | None
    roles: dict[str, list[str]]
    items: int
    created_at: datetime


class ItemFields(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    expected_status: Status
    expected_failure: str | None = Field(default=None, max_length=32)
    expected_grade: Literal[1, 2, 3] | None = None
    answerable: bool = True
    required_values: list[str] = Field(default_factory=list)
    absent_values: list[str] = Field(default_factory=list)
    required_conditions: list[str] = Field(default_factory=list)
    forbidden_values: list[str] = Field(default_factory=list)
    context: list[dict[str, str]] = Field(default_factory=list)
    test_role: str | None = Field(default=None, max_length=64)
    fault: Fault = "none"
    expected_action: ExpectedAction | None = None
    note: str | None = None


class ItemIn(ItemFields):
    key: str | None = Field(default=None, max_length=64)


class ItemPatch(BaseModel):
    """보낸 필드만 고친다."""

    question: str | None = Field(default=None, min_length=1, max_length=1000)
    expected_status: Status | None = None
    expected_failure: str | None = Field(default=None, max_length=32)
    expected_grade: Literal[1, 2, 3] | None = None
    answerable: bool | None = None
    required_values: list[str] | None = None
    absent_values: list[str] | None = None
    required_conditions: list[str] | None = None
    forbidden_values: list[str] | None = None
    context: list[dict[str, str]] | None = None
    test_role: str | None = Field(default=None, max_length=64)
    fault: Fault | None = None
    expected_action: ExpectedAction | None = None
    note: str | None = None


class ItemOut(ItemFields):
    id: int
    key: str | None
    source: Literal["manual", "workshop", "feedback"]
    expected_answer: str | None


class FeedbackItemOut(BaseModel):
    id: int
    message_id: int
    rating: Literal["up", "down"]
    comment: str | None
    status: Literal["open", "promoted", "dismissed"]
    golden_item_id: int | None
    question: str | None
    answer: str
    answer_status: str | None
    grade: int | None
    conditions: str | None
    trace_id: int | None
    created_at: datetime


class PromoteIn(ItemIn):
    """기대값은 사람이 적는다 — 피드백은 답이 틀렸다는 신호일 뿐이다."""

    golden_set_id: int


class GoldenSetDetail(GoldenSetOut):
    item_list: list[ItemOut]


class EvalRunIn(BaseModel):
    golden_set_id: int
    # 역할 → 모델 덮어쓰기(모델 교체 시험). 비우면 config/llm.yaml 그대로.
    overrides: dict[str, str] = Field(default_factory=dict)


class GateCheckOut(BaseModel):
    metric: str
    kind: Literal["invariant", "threshold"]
    op: Literal["min", "max"]
    limit: float
    value: float | None
    passed: bool


class EvalRunOut(BaseModel):
    id: int
    golden_set_id: int
    ontology_version_id: int | None
    llm_config: dict[str, Any]
    status: Literal["running", "succeeded", "failed"]
    metrics: dict[str, Any] | None
    gate_passed: bool | None
    gate_checks: list[GateCheckOut]
    started_at: datetime
    finished_at: datetime | None
    results: int


class EvalResultOut(BaseModel):
    id: int
    item_id: int
    key: str | None
    question: str
    expected_status: str
    status: str | None
    failure_reason: str | None
    grade: int | None
    answer: str | None
    conditions: str | None
    verdict: dict[str, Any]
    latency_ms: int | None


class EvalRunDetail(EvalRunOut):
    result_list: list[EvalResultOut]


def _item(i: GoldenItem) -> ItemOut:
    return ItemOut(
        id=i.id,
        key=i.key,
        source=i.source,  # type: ignore[arg-type]
        expected_answer=i.expected_answer,
        question=i.question,
        expected_status=i.expected_status,  # type: ignore[arg-type]
        expected_failure=i.expected_failure,
        expected_grade=i.expected_grade,  # type: ignore[arg-type]
        answerable=i.answerable,
        required_values=list(i.required_values or []),
        absent_values=list(i.absent_values or []),
        required_conditions=list(i.required_conditions or []),
        forbidden_values=list(i.forbidden_values or []),
        context=list(i.context or []),
        test_role=i.test_role,
        fault=i.fault,  # type: ignore[arg-type]
        expected_action=(
            ExpectedAction.model_validate(i.expected_action) if i.expected_action else None
        ),
        note=i.note,
    )


async def _set_out(db: Db, gs: GoldenSet) -> GoldenSetOut:
    n = await db.scalar(
        select(func.count()).select_from(GoldenItem).where(GoldenItem.golden_set_id == gs.id)
    )
    return GoldenSetOut(
        id=gs.id,
        name=gs.name,
        domain_pack=gs.domain_pack,
        ontology_version_id=gs.ontology_version_id,
        roles=dict(gs.roles or {}),
        items=int(n or 0),
        created_at=gs.created_at,
    )


async def _run_out(db: Db, run: EvalRun) -> EvalRunOut:
    n = await db.scalar(
        select(func.count()).select_from(EvalResult).where(EvalResult.run_id == run.id)
    )
    checks = ((run.metrics or {}).get("gate") or {}).get("checks", [])
    return EvalRunOut(
        id=run.id,
        golden_set_id=run.golden_set_id,
        ontology_version_id=run.ontology_version_id,
        llm_config=run.llm_config,
        status=run.status,  # type: ignore[arg-type]
        metrics={k: v for k, v in (run.metrics or {}).items() if k != "gate"} or None,
        gate_passed=run.gate_passed,
        gate_checks=[GateCheckOut.model_validate(c) for c in checks],
        started_at=run.started_at,
        finished_at=run.finished_at,
        results=int(n or 0),
    )


@contextmanager
def _domain_errors() -> Iterator[None]:
    try:
        yield
    except (golden.SetNotFound, runner.RunNotFound) as e:
        raise ProblemError(404, "골든셋을 찾을 수 없습니다", code="golden_set_not_found") from e
    except golden.ItemNotFound as e:
        raise ProblemError(404, "문항을 찾을 수 없습니다", code="golden_item_not_found") from e
    except golden.NameTaken as e:
        raise ProblemError(409, "같은 이름의 골든셋이 있습니다", code="golden_set_exists") from e
    except chat_feedback.FeedbackNotFound as e:
        raise ProblemError(404, "피드백을 찾을 수 없습니다", code="feedback_not_found") from e
    except chat_feedback.AlreadyHandled as e:
        raise ProblemError(409, "이미 처리한 피드백입니다", code="feedback_handled") from e
    except golden.InUse as e:
        raise ProblemError(
            409,
            "평가 결과가 있어 지울 수 없습니다",
            detail="지난 실행 기록이 깨집니다. 새 골든셋을 만드세요",
            code="golden_in_use",
        ) from e


# ---------------------------------------------------------------- 골든셋


@router.get("/golden-sets")
async def list_sets(db: Db, _: Builder) -> list[GoldenSetOut]:
    rows = await db.scalars(select(GoldenSet).order_by(GoldenSet.id))
    return [await _set_out(db, gs) for gs in rows]


@router.post("/golden-sets", status_code=201)
async def create_set(body: GoldenSetIn, db: Db, user: Builder) -> GoldenSetOut:
    with _domain_errors():
        gs = await golden.create_set(db, body.name, body.roles, actor_id=user.id)
    return await _set_out(db, gs)


@router.get("/golden-sets/{set_id}")
async def get_set(set_id: int, db: Db, _: Builder) -> GoldenSetDetail:
    with _domain_errors():
        gs = await golden.get_set(db, set_id)
    out = await _set_out(db, gs)
    return GoldenSetDetail(
        **out.model_dump(), item_list=[_item(i) for i in await golden.items(db, gs.id)]
    )


@router.delete("/golden-sets/{set_id}", status_code=204)
async def delete_set(set_id: int, db: Db, user: Builder) -> Response:
    with _domain_errors():
        await golden.delete_set(db, await golden.get_set(db, set_id), actor_id=user.id)
    return Response(status_code=204)


@router.post("/golden-sets/{set_id}/items", status_code=201)
async def create_item(set_id: int, body: ItemIn, db: Db, user: Builder) -> ItemOut:
    with _domain_errors():
        gs = await golden.get_set(db, set_id)
        item = await golden.save_item(
            db, gs, body.model_dump(exclude={"key"}), actor_id=user.id, key=body.key
        )
    return _item(item)


@router.patch("/golden-sets/{set_id}/items/{item_id}")
async def patch_item(set_id: int, item_id: int, body: ItemPatch, db: Db, user: Builder) -> ItemOut:
    with _domain_errors():
        gs = await golden.get_set(db, set_id)
        item = await golden.get_item(db, gs, item_id)
        await golden.save_item(
            db, gs, body.model_dump(exclude_unset=True), actor_id=user.id, item=item
        )
    return _item(item)


@router.delete("/golden-sets/{set_id}/items/{item_id}", status_code=204)
async def delete_item(set_id: int, item_id: int, db: Db, user: Builder) -> Response:
    with _domain_errors():
        gs = await golden.get_set(db, set_id)
        await golden.delete_item(db, await golden.get_item(db, gs, item_id), actor_id=user.id)
    return Response(status_code=204)


# ---------------------------------------------------------------- 피드백 → 골든 후보(Phase 15)


def _feedback(v: chat_feedback.FeedbackView) -> FeedbackItemOut:
    return FeedbackItemOut.model_validate(v, from_attributes=True)


@router.get("/feedback")
async def feedback_list(
    db: Db,
    _: Builder,
    status: chat_feedback.FeedbackStatus | None = "open",
    rating: chat_feedback.Rating | None = "down",
) -> list[FeedbackItemOut]:
    return [_feedback(v) for v in await chat_feedback.listing(db, status=status, rating=rating)]


@router.post("/feedback/{feedback_id}/promote", status_code=201)
async def promote_feedback(feedback_id: int, body: PromoteIn, db: Db, user: Builder) -> ItemOut:
    with _domain_errors():
        gs = await golden.get_set(db, body.golden_set_id)
        item = await golden.promote_feedback(
            db,
            gs,
            feedback_id,
            body.model_dump(exclude={"key", "golden_set_id"}),
            actor_id=user.id,
            key=body.key,
        )
    return _item(item)


@router.post("/feedback/{feedback_id}/dismiss", status_code=204)
async def dismiss_feedback(feedback_id: int, db: Db, user: Builder) -> Response:
    with _domain_errors():
        await chat_feedback.dismiss(db, feedback_id, actor_id=user.id)
    return Response(status_code=204)


# ---------------------------------------------------------------- 실행


@router.post("/eval-runs", status_code=202)
async def start_run(body: EvalRunIn, db: Db, user: Builder, settings: SettingsDep) -> EvalRunOut:
    await runner.reap_orphans(db, body.golden_set_id)
    running = await db.scalar(
        select(EvalRun.id).where(
            EvalRun.golden_set_id == body.golden_set_id, EvalRun.status == "running"
        )
    )
    if running is not None:
        raise ProblemError(
            409,
            "이 골든셋의 평가가 이미 돌고 있습니다",
            code="eval_running",
            extra={"run_id": running},
        )
    cfg = runner.RunConfig(
        llm=load_llm_config(settings.llm_config_path),
        overrides=body.overrides,
        grade2_enabled=settings.grade2_enabled,
    )
    with _domain_errors():
        run = await runner.create_run(db, body.golden_set_id, cfg)
    await queue.enqueue(
        db, handlers.EVAL_RUN, {"run_id": run.id, "overrides": body.overrides}, dedupe=False
    )
    audit.record(
        db,
        audit.EVAL_RUN_STARTED,
        actor_id=user.id,
        target=f"eval_run:{run.id}",
        payload={"golden_set_id": body.golden_set_id, "llm_config": run.llm_config},
    )
    return await _run_out(db, run)


@router.get("/eval-runs")
async def list_runs(
    db: Db, _: Builder, golden_set_id: Annotated[int | None, Query()] = None
) -> list[EvalRunOut]:
    stmt = select(EvalRun).order_by(EvalRun.id.desc()).limit(50)
    if golden_set_id is not None:
        stmt = stmt.where(EvalRun.golden_set_id == golden_set_id)
    return [await _run_out(db, r) for r in await db.scalars(stmt)]


@router.get("/eval-runs/{run_id}")
async def get_run(run_id: int, db: Db, _: Builder) -> EvalRunDetail:
    run = await db.get(EvalRun, run_id)
    if run is None:
        raise ProblemError(404, "평가 실행을 찾을 수 없습니다", code="eval_run_not_found")
    pairs = await db.execute(
        select(EvalResult, GoldenItem)
        .join(GoldenItem, GoldenItem.id == EvalResult.item_id)
        .where(EvalResult.run_id == run_id)
        .order_by(GoldenItem.key.nulls_last(), GoldenItem.id)
    )
    results = [
        EvalResultOut(
            id=r.id,
            item_id=item.id,
            key=item.key,
            question=item.question,
            expected_status=item.expected_status,
            status=r.status,
            failure_reason=r.failure_reason,
            grade=r.grade,
            answer=r.answer,
            conditions=r.conditions,
            verdict=r.verdict,
            latency_ms=(r.stats or {}).get("latency_ms"),
        )
        for r, item in pairs.all()
    ]
    out = await _run_out(db, run)
    return EvalRunDetail(**out.model_dump(), result_list=results)
