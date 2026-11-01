"""36.14 파일럿 판단(TRD 17.17, Phase 22) — 판단 근거 모으기와 고를 수 있는 판단.

근거는 마지막 **실제 모델** 평가 실행(재생 제외)의 게이트·불변식, 성공 지표 목표 대비 지금 값,
양식의 업무 지표다. 판단(계속 / 보완 뒤 계속 / 보류)과 사유는 사람이 고른다. 불변식 위반은 평균
점수로 상쇄할 수 없다(매뉴얼 20.6) — 불변식 위반이 있거나 게이트를 통과하지 못했으면(또는 실제
모델 평가가 없으면) 보류만 고를 수 있고, 이 검사는 서버가 한다(``allowed``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.adoption import EngagementDecision
from ontobot.db.models.evaluation import EvalRun, GoldenSet
from ontobot.evaluation import targets
from ontobot.security import audit

Decision = Literal["go", "go_after_fix", "no_go"]
DECISIONS: tuple[Decision, ...] = ("go", "go_after_fix", "no_go")

# 36.14 성공 지표 표에서 업무 지표인 행(시스템 지표는 평가·목표에서 온다)
BUSINESS_ROWS = (4, 5)


@dataclass(frozen=True)
class GateCheck:
    metric: str
    kind: str  # invariant / threshold
    op: str
    limit: float
    value: float | None
    passed: bool


@dataclass(frozen=True)
class RunEvidence:
    run_id: int
    golden_set: str
    finished_at: datetime | None
    gate_passed: bool | None
    checks: list[GateCheck]

    @property
    def invariant_violations(self) -> list[str]:
        return [c.metric for c in self.checks if c.kind == "invariant" and not c.passed]


def allowed(run: RunEvidence | None) -> dict[Decision, str | None]:
    """판단마다 고를 수 없는 까닭(None = 고를 수 있다). 순수 함수."""
    why: str | None = None
    if run is None:
        why = "실제 모델 평가 실행이 없습니다 — 평가(20장)를 먼저 돌리세요"
    elif run.invariant_violations:
        names = ", ".join(run.invariant_violations)
        why = f"불변식 위반이 있습니다({names}) — 보류만 고를 수 있습니다"
    elif not run.gate_passed:
        why = "평가 게이트를 통과하지 못했습니다 — 보류만 고를 수 있습니다"
    return {"go": why, "go_after_fix": why, "no_go": None}


async def last_real_run(db: AsyncSession) -> RunEvidence | None:
    """끝난 실제 모델 평가 실행 중 마지막 것(replay 제외). "마지막"은 ``targets._measured`` 와 같이
    id 순서다 — 판단 패널과 성공 지표 목표가 서로 다른 실행을 보면 안 된다."""
    row = (
        await db.execute(
            select(EvalRun, GoldenSet.name)
            .join(GoldenSet, GoldenSet.id == EvalRun.golden_set_id)
            .where(
                EvalRun.status == "succeeded",
                EvalRun.llm_config["mode"].astext.is_distinct_from("replay"),
            )
            .order_by(EvalRun.id.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    run, name = row
    gate: dict[str, Any] = (run.metrics or {}).get("gate") or {}
    checks = [
        GateCheck(
            metric=str(c["metric"]),
            kind=str(c["kind"]),
            op=str(c["op"]),
            limit=float(c["limit"]),
            value=None if c.get("value") is None else float(c["value"]),
            passed=bool(c["passed"]),
        )
        for c in gate.get("checks", [])
    ]
    return RunEvidence(run.id, name, run.finished_at, run.gate_passed, checks)


@dataclass(frozen=True)
class BusinessMetric:
    label: str
    current: str | None
    target: str | None


def business_metrics(
    payloads: dict[str, dict[str, Any]], labels: list[str]
) -> list[BusinessMetric]:
    """양식의 업무 지표 행 — 지금 값·목표를 적힌 그대로(계산하지 않는다)."""
    rows = (payloads.get("pilot") or {}).get("metrics") or []
    out = []
    for i in BUSINESS_ROWS:
        row = rows[i] if i < len(rows) and isinstance(rows[i], dict) else {}
        out.append(BusinessMetric(labels[i], row.get("current"), row.get("target")))
    return out


@dataclass(frozen=True)
class Evidence:
    run: RunEvidence | None
    targets: list[targets.TargetView]
    business: list[BusinessMetric]
    allowed: dict[Decision, str | None]


class DecisionBlocked(Exception):
    pass


async def decide(
    db: AsyncSession,
    engagement_id: int,
    *,
    actor_id: int,
    decision: Decision,
    reason: str,
) -> EngagementDecision:
    """판단을 남긴다. 고를 수 없는 판단이면 ``DecisionBlocked``(근거를 지금 다시 읽어 본다)."""
    run = await last_real_run(db)
    why = allowed(run)[decision]
    if why:
        raise DecisionBlocked(why)
    row = EngagementDecision(
        engagement_id=engagement_id,
        decision=decision,
        reason=reason,
        eval_run_id=run.run_id if run else None,
        gate_passed=run.gate_passed if run else None,
        invariant_violations=len(run.invariant_violations) if run else 0,
        decided_by=actor_id,
    )
    db.add(row)
    await db.flush()
    audit.record(
        db,
        audit.ENGAGEMENT_DECIDED,
        actor_id=actor_id,
        target=f"engagement:{engagement_id}",
        payload={
            "decision": decision,
            "eval_run_id": row.eval_run_id,
            "gate_passed": row.gate_passed,
        },
    )
    return row


async def decisions(db: AsyncSession, engagement_id: int) -> list[EngagementDecision]:
    return list(
        await db.scalars(
            select(EngagementDecision)
            .where(EngagementDecision.engagement_id == engagement_id)
            .order_by(EngagementDecision.id.desc())
        )
    )
