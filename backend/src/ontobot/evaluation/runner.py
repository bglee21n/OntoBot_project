"""평가 러너(TRD 13.1) — 운영과 같은 ``chat.pipeline`` 을 인프로세스로 돈다.

문항의 ``test_role`` 권한으로 돈다.

- 문항마다 세션을 따로 연다. 파이프라인은 읽기만 하고(조회는 savepoint 안), 끝나면 롤백한다.
  결과(``eval_result``)는 문항마다 다른 세션에 커밋한다 — 도중에 멈춰도 진행분이 남는다.
- 권한: ``test_role`` 이 없으면 게시 버전의 모든 타입을 읽는다(정답률을 재는 기본 문항). 골든셋의
  ``roles`` 에 정의된 평가 전용 역할이면 그 목록, 아니면 DB 역할의 권한(``rbac``)으로 돈다.
- 장애 주입은 ``evaluation.faults`` 가 실행기·드라이버를 바꿔 끼운다.
- 문항에서 예외가 나면 평가 장치 오류(``harness_errors``)로 남긴다 — 파이프라인 실패(``failed``)와
  다르다. 게이트는 장치 오류가 하나라도 있으면 실패한다(결과를 믿을 수 없다).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.actions import params as action_params
from ontobot.chat import context as chat_context
from ontobot.chat import pipeline
from ontobot.db.models.evaluation import EvalResult, EvalRun, GoldenItem, GoldenSet
from ontobot.db.models.jobs import Job
from ontobot.db.session import session_scope
from ontobot.evaluation import faults, gate, golden, metrics
from ontobot.graph import client as graph
from ontobot.llm.config import LlmConfig
from ontobot.llm.gateway import LLMGateway
from ontobot.ontology import registry
from ontobot.ontology.registry import PublishedSchema
from ontobot.security import rbac

log = logging.getLogger(__name__)


class RunNotFound(Exception):
    pass


@dataclass
class RunConfig:
    llm: LlmConfig
    overrides: dict[str, str] = field(default_factory=dict)
    grade2_enabled: bool = True
    mode: str = "live"  # live / record / replay — 기록용(전송층은 호출부가 replay.use 로 바꾼다)

    def snapshot(self) -> dict[str, Any]:
        """``eval_run.llm_config`` — 역할별 공급자·모델(덮어쓰기 반영)과 실행 모드."""
        return {
            "mode": self.mode,
            "roles": {
                name: {"provider": r.provider, "model": self.overrides.get(name, r.model)}
                for name, r in self.llm.roles.items()
                if name in ("planner", "answerer")
            },
        }


def spec_of(item: GoldenItem) -> metrics.Spec:
    return metrics.Spec(
        key=item.key or str(item.id),
        answerable=item.answerable,
        expected_status=item.expected_status,
        expected_failure=item.expected_failure,
        expected_grade=item.expected_grade,
        required_values=tuple(item.required_values or ()),
        absent_values=tuple(item.absent_values or ()),
        required_conditions=tuple(item.required_conditions or ()),
        forbidden_values=tuple(item.forbidden_values or ()),
        test_role=item.test_role,
        fault=item.fault,
        expected_action=item.expected_action,
    )


def outcome_of(res: pipeline.PipelineResult) -> metrics.Outcome:
    return metrics.Outcome(
        status=res.status,
        answer=res.answer,
        failure_reason=res.failure_reason,
        grade=res.grade,
        conditions=res.conditions,
        citations=tuple(res.citations),
        fact_sentences=res.fact_sentences,
        verified_sentences=res.verified_sentences,
        estimated_sentences=len(res.estimated_sentences),
        removed_citations=len(res.removed_citations),
        latency_ms=sum(res.stage_latency_ms.values()),
        action=res.action,
    )


async def readable_for(
    db: AsyncSession, schema: PublishedSchema, roles: dict[str, list[str]], test_role: str | None
) -> frozenset[str]:
    published = frozenset(t.api_name for t in schema.document.object_types)
    if test_role is None:
        return published
    if test_role in roles:
        return frozenset(roles[test_role]) & published
    return await rbac.readable_types_for_role(db, test_role) & published


def actions_for(
    schema: PublishedSchema,
    readable: frozenset[str],
    roles: dict[str, list[str]],
    test_role: str | None,
) -> frozenset[str]:
    """문항이 요청할 수 있는 Action. 기본 문항은 역할을 따지지 않고, 평가 전용 역할은 요청 역할이
    정해지지 않은 Action 만, DB 역할은 그 역할로 판정한다(``rbac``)."""
    if test_role is None:
        return action_params.requestable(schema.document, readable, None)
    held = frozenset() if test_role in roles else frozenset({test_role})
    return action_params.requestable(schema.document, readable, held)


async def create_run(db: AsyncSession, golden_set_id: int, cfg: RunConfig) -> EvalRun:
    gs = await db.get(GoldenSet, golden_set_id)
    if gs is None:
        raise RunNotFound(golden_set_id)
    schema = registry.current() or await registry.reload(db)
    run = EvalRun(
        golden_set_id=golden_set_id,
        ontology_version_id=schema.version_id if schema else None,
        llm_config=cfg.snapshot(),
        status="running",
        started_at=datetime.now(UTC),
    )
    db.add(run)
    await db.flush()
    return run


async def reap_orphans(db: AsyncSession, golden_set_id: int) -> list[int]:
    """작업이 끝났는데(실패·포기) ``running`` 으로 남은 실행을 ``failed`` 로 닫는다.

    워커가 작업을 끝내 실패하면 러너가 실행을 닫지 못한다 — 그대로 두면 같은 골든셋의 새 실행이
    '이미 돌고 있다'로 영영 막힌다. 작업이 있었는데 대기·실행 중인 것이 없는 실행만 닫는다.
    """
    runs = list(
        await db.scalars(
            select(EvalRun).where(
                EvalRun.golden_set_id == golden_set_id, EvalRun.status == "running"
            )
        )
    )
    closed: list[int] = []
    for run in runs:
        jobs = list(
            await db.scalars(
                select(Job.status).where(
                    Job.kind == "eval_run", Job.payload["run_id"].as_integer() == run.id
                )
            )
        )
        # 작업 없이 만든 실행(CLI — scripts/run_eval.py 가 직접 돈다)은 건드리지 않는다.
        if jobs and not any(st in ("queued", "running") for st in jobs):
            run.status, run.finished_at = "failed", datetime.now(UTC)
            closed.append(run.id)
    return closed


async def _one(
    item: GoldenItem,
    roles: dict[str, list[str]],
    cfg: RunConfig,
    driver: graph.AsyncDriver | None,
) -> tuple[metrics.Outcome, frozenset[str], dict[str, Any]]:
    async with session_scope() as db:
        schema = registry.current() or await registry.reload(db)
        if schema is None:
            res = await pipeline.run(db, LLMGateway(cfg.llm), None, frozenset(), item.question)
            return outcome_of(res), frozenset(), {}
        readable = await readable_for(db, schema, roles, item.test_role)
        actions = actions_for(schema, readable, roles, item.test_role)
        ctx = (
            await chat_context.with_titles(db, schema.document, readable, item.context)
            if item.context
            else []
        )
        llm = LLMGateway(cfg.llm, overrides=cfg.overrides)
        async with faults.injected(item.fault, driver) as (executor, drv):
            res = await pipeline.run(
                db,
                llm,
                schema,
                readable,
                item.question,
                grade2_enabled=cfg.grade2_enabled,
                context=ctx,
                graph_driver=drv,
                executor=executor,
                actions=actions,
            )
        await db.rollback()
    stats = {
        "stage_latency_ms": res.stage_latency_ms,
        "fact_sentences": res.fact_sentences,
        "verified_sentences": res.verified_sentences,
        "estimated_sentences": len(res.estimated_sentences),
        "removed_citations": res.removed_citations,
        "plan_attempts": len(res.plan_attempts),
        "action": res.action,
        "llm_calls": [
            {"role": c.role, "model": c.model, "ok": c.ok, "latency_ms": c.latency_ms}
            for c in llm.calls
        ],
    }
    return outcome_of(res), readable, stats


async def execute(
    run_id: int,
    cfg: RunConfig,
    *,
    graph_driver: graph.AsyncDriver | None,
    gate_cfg: dict[str, Any] | None = None,
    skip: tuple[str, ...] = (),
    extra: Callable[[], dict[str, Any]] | None = None,
) -> EvalRun:
    """실행을 끝까지 돌리고 지표·게이트를 기록한다.

    ``extra`` 는 문항을 다 돈 뒤 지표에 덧붙일 값(카세트 놓침 수 등)을 돌려준다.
    """
    async with session_scope() as db:
        run = await db.get(EvalRun, run_id)
        if run is None:
            raise RunNotFound(run_id)
        gs = await db.get(GoldenSet, run.golden_set_id)
        roles = dict(gs.roles) if gs else {}
        todo = await golden.items(db, run.golden_set_id)
        done = set(await db.scalars(select(EvalResult.item_id).where(EvalResult.run_id == run_id)))
    rows: list[tuple[metrics.Spec, metrics.Outcome, metrics.Verdict]] = []
    try:
        for item in todo:
            spec = spec_of(item)
            if item.id in done:
                continue  # 작업 재시도 — 이미 기록한 문항은 다시 돌지 않는다
            readable: frozenset[str] = frozenset()
            stats: dict[str, Any] = {}
            try:
                out, readable, stats = await _one(item, roles, cfg, graph_driver)
            except Exception as e:
                log.exception("평가 문항 %s 실행 오류", spec.key)
                out = metrics.Outcome(status=None, error=f"{type(e).__name__}: {e}")
            verdict = metrics.judge(spec, out, readable)
            async with session_scope() as db:
                db.add(
                    EvalResult(
                        run_id=run_id,
                        item_id=item.id,
                        status=out.status,
                        failure_reason=out.failure_reason,
                        grade=out.grade,
                        answer=out.answer,
                        citations=list(out.citations),
                        verdict=asdict(verdict),
                        conditions=out.conditions,
                        stats={**stats, "latency_ms": out.latency_ms},
                    )
                )
                await db.commit()
        # 재시도로 이어 돈 경우까지 포함해 기록된 결과 전체로 지표를 낸다.
        rows = await _recorded(run_id)
        computed = {**metrics.compute(rows), **(extra() if extra else {})}
        result = gate.evaluate(computed, gate_cfg or gate.load(), skip=skip)
        status = "succeeded"
    except Exception:
        log.exception("평가 실행 %s 실패", run_id)
        computed, result, status = None, None, "failed"
    async with session_scope() as db:
        run = await db.get(EvalRun, run_id)
        if run is None:
            raise RunNotFound(run_id)
        run.status = status
        run.metrics = None if computed is None else {**computed, "gate": result.as_dict()}  # type: ignore[union-attr]
        run.gate_passed = None if result is None else result.passed
        run.finished_at = datetime.now(UTC)
        await db.commit()
        return run


async def _recorded(run_id: int) -> list[tuple[metrics.Spec, metrics.Outcome, metrics.Verdict]]:
    async with session_scope() as db:
        pairs = await db.execute(
            select(EvalResult, GoldenItem)
            .join(GoldenItem, GoldenItem.id == EvalResult.item_id)
            .where(EvalResult.run_id == run_id)
            .order_by(EvalResult.id)
        )
        out = []
        for r, item in pairs.all():
            s = r.stats or {}
            o = metrics.Outcome(
                status=r.status,
                answer=r.answer or "",
                failure_reason=r.failure_reason,
                grade=r.grade,
                conditions=r.conditions,
                citations=tuple(r.citations or ()),
                fact_sentences=int(s.get("fact_sentences", 0)),
                verified_sentences=int(s.get("verified_sentences", 0)),
                estimated_sentences=int(s.get("estimated_sentences", 0)),
                removed_citations=len(s.get("removed_citations", [])),
                latency_ms=int(s.get("latency_ms", 0)),
                error=(r.verdict or {}).get("error"),
                action=s.get("action"),
            )
            v = metrics.Verdict(**r.verdict)
            out.append((spec_of(item), o, v))
        return out
