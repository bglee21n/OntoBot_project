"""PRD 7절 성공 지표의 목표 수치 — 관리자 설정(``ops_target``)과 지금 측정값.

목표는 관리자가 화면에서 정한다. 행이 없으면 **제안 기본값**(측정 근거가 있는 것 — 평가 게이트
가안·ADR-0011)을 쓰고, 근거가 없는 지표는 기본값도 없다(미정 — 0 이나 100% 로 꾸미지 않는다).

평가 게이트(``gate.yaml``)는 이 값을 읽지 않는다. 게이트는 CI 에서 DB 없이도 같은 기준으로 돌아야
하고, 불변식은 설정으로 느슨하게 할 수 없어야 한다. 여기 값은 운영 화면에서 측정값과 견주는 목표다.

측정값의 출처:

- 품질(정답률·추정 비율·Action 파라미터 정확도) — 마지막으로 끝난 **녹화 재생이 아닌** 평가 실행
  (``eval_run.metrics``). 재생은 녹화 시점 모델의 답을 되풀이할 뿐이고 지연은 0 에 가깝다.
- 운영(조회 실패율·P95·주간 활성 사용자) — 실제 질문의 트레이스. 실패율의 분자는 **장애성 실패**
  (실행 오류·시간 초과·그래프·LLM 불가)뿐이다. 질문 해석 실패·권한 없음은 정상 동작이라 넣지 않는다
  — 평가의 ``failure_rate`` 는 실패가 정답인 문항까지 세므로 이 지표가 아니다.
- Action 실행 성공률 — v1 운영 지표(``actions.metrics``, 30일).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Numeric, delete, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.actions.metrics import v1_metrics
from ontobot.db.models.chat import Trace
from ontobot.db.models.evaluation import EvalRun, OpsTarget
from ontobot.security import audit

Direction = Literal["min", "max"]
Unit = Literal["ratio", "seconds", "minutes", "days", "count"]


@dataclass(frozen=True)
class TargetSpec:
    key: str
    label: str
    unit: Unit
    direction: Direction  # min = 이 값 이상이어야, max = 이 값 이하여야
    default: Decimal | None  # 제안값(근거가 있는 것만)
    source: str  # 제안값 또는 측정값의 근거


_GATE = "평가 게이트 가안"
SPECS: tuple[TargetSpec, ...] = (
    TargetSpec(
        "accuracy", "골든셋 질의 정답률", "ratio", "min", Decimal("0.9"), "평가 게이트 가안"
    ),
    TargetSpec("estimate_ratio", "추정 비율", "ratio", "max", Decimal("0.2"), _GATE),
    TargetSpec("failure_rate", "조회 실패율", "ratio", "max", None, "트레이스 30일(장애성 실패)"),
    TargetSpec(
        "p95_seconds",
        "P95 응답 시간(초)",
        "seconds",
        "max",
        Decimal(25),
        "ADR-0011 · 측정은 트레이스 30일",
    ),
    TargetSpec("concurrent_users", "동시 사용자", "count", "min", Decimal(8), "ADR-0011"),
    TargetSpec(
        "action_param_accuracy",
        "Action 파라미터 정확도",
        "ratio",
        "min",
        Decimal("0.9"),
        _GATE,
    ),
    TargetSpec("action_success_rate", "Action 실행 성공률", "ratio", "min", None, "운영 지표 30일"),
    TargetSpec("freshness_minutes", "데이터 신선도(분)", "minutes", "max", None, "동기화 지연"),
    TargetSpec("weekly_active_users", "주간 활성 사용자", "count", "min", None, "트레이스 7일"),
    TargetSpec("new_type_days", "신규 객체 타입 구성 소요(일)", "days", "max", None, "—"),
)
BY_KEY = {s.key: s for s in SPECS}


class UnknownTarget(ValueError):
    pass


@dataclass(frozen=True)
class TargetView:
    spec: TargetSpec
    value: Decimal | None  # 지금 목표(설정값, 없으면 제안값)
    custom: bool  # 관리자가 정한 값인가
    measured: float | None
    met: bool | None  # 측정값과 목표가 모두 있을 때만


def _met(spec: TargetSpec, value: Decimal | None, measured: float | None) -> bool | None:
    if value is None or measured is None:
        return None
    m = Decimal(str(measured))
    return m >= value if spec.direction == "min" else m <= value


# 장애성 실패 — "자료 없음"으로 보이면 안 되는 실패(TRD 7.3). 해석 실패·권한 없음은 넣지 않는다.
OUTAGE_REASONS = ("execution_error", "timeout", "graph_unavailable", "llm_unavailable", "too_broad")


async def _traces(db: AsyncSession) -> dict[str, float | None]:
    # 한 질문의 지연 = 단계별 지연의 합(트레이스에 단계별로 남는다).
    latency = literal_column(
        "(SELECT coalesce(sum(value::numeric), 0) FROM jsonb_each_text(trace.stage_latency_ms))",
        Numeric,
    )
    total, outages, p95_ms = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(Trace.failure_reason.in_(OUTAGE_REASONS)),
                func.percentile_cont(0.95).within_group(latency),
            ).where(Trace.created_at >= func.now() - timedelta(days=30))
        )
    ).one()
    wau = await db.scalar(
        select(func.count(func.distinct(Trace.user_id))).where(
            Trace.created_at >= func.now() - timedelta(days=7)
        )
    )
    return {
        "failure_rate": round(outages / total, 4) if total else None,
        "p95_seconds": round(float(p95_ms) / 1000, 1) if total and p95_ms is not None else None,
        "weekly_active_users": float(wau) if wau else None,
    }


async def _measured(db: AsyncSession) -> dict[str, float | None]:
    runs = await db.scalars(
        select(EvalRun)
        .where(EvalRun.status == "succeeded", EvalRun.metrics.is_not(None))
        .order_by(EvalRun.id.desc())
        .limit(20)
    )
    run = next((r for r in runs if (r.llm_config or {}).get("mode") != "replay"), None)
    m: dict[str, Any] = (run.metrics or {}) if run is not None else {}
    v1 = await v1_metrics(db, 30)
    return {
        "accuracy": m.get("accuracy"),
        "estimate_ratio": m.get("estimate_ratio"),
        "action_param_accuracy": m.get("action_param_accuracy"),
        "action_success_rate": v1.success_rate,
        **await _traces(db),
    }


async def load(db: AsyncSession) -> list[TargetView]:
    rows = {r.key: r.value for r in await db.scalars(select(OpsTarget))}
    measured = await _measured(db)
    out = []
    for s in SPECS:
        value = rows.get(s.key, s.default)
        m = measured.get(s.key)
        out.append(TargetView(s, value, s.key in rows, m, _met(s, value, m)))
    return out


async def update(db: AsyncSession, values: dict[str, Decimal | None], *, actor_id: int) -> None:
    """``None`` 은 설정을 지워 제안값으로 되돌린다. 호출자 트랜잭션 안에서 감사 기록과 함께."""
    unknown = sorted(set(values) - BY_KEY.keys())
    if unknown:
        raise UnknownTarget(f"모르는 지표입니다: {', '.join(unknown)}")
    for key, value in values.items():
        if value is None:
            await db.execute(delete(OpsTarget).where(OpsTarget.key == key))
            continue
        if value < 0 or (BY_KEY[key].unit == "ratio" and value > 1):
            raise UnknownTarget(f"'{key}' 값이 범위를 벗어났습니다: {value}")
        await db.execute(
            insert(OpsTarget)
            .values(key=key, value=value, updated_by=actor_id)
            .on_conflict_do_update(
                index_elements=[OpsTarget.key],
                set_={"value": value, "updated_by": actor_id, "updated_at": func.now()},
            )
        )
    audit.record(
        db,
        audit.TARGETS_CHANGED,
        actor_id=actor_id,
        target="ops_target",
        payload={k: (None if v is None else str(v)) for k, v in values.items()},
    )
