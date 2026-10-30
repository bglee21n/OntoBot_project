"""v1 운영 지표(PRD 7절) — Action 실행 성공률·승인 거부율과 확인되지 않은 편집 충돌.

정의(분모가 0 이면 None — 0% 나 100% 로 꾸미지 않는다):

- **실행 성공률** = (실행 + 롤백) ÷ (실행 + 롤백 + 실패). 롤백된 것도 한 번은 실행에 성공했다.
  실패는 승인 때 재검증에 걸린 것(미리보기 뒤 상태가 바뀜 등)이다 — 오작동 신호.
- **승인 거부율** = 거부 ÷ (거부 + 실행 + 롤백 + 실패) — 승인자가 결정한 요청 중 거부한 비율.
  요청자가 스스로 거둔 것(``rejected`` 이지만 결정한 사람 = 요청자, ``actions.runner.reject``)은
  ``withdrawn`` 으로 따로 세고 분모에 넣지 않는다. 미리보기에서 막힌 것(``blocked``)과
  대기 중(``previewed``)도 넣지 않는다.

기간은 요청 시각(``created_at``) 기준, DB 시계로 자른다. 편집 충돌은 기간과 무관한 지금의
미확인 건수다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.actions import ActionRun
from ontobot.objects.edits import open_conflict_count

STATUSES = ("previewed", "blocked", "rejected", "withdrawn", "executed", "failed", "rolled_back")


@dataclass(frozen=True)
class V1Metrics:
    days: int
    counts: dict[str, int]
    success_rate: float | None
    rejection_rate: float | None
    open_conflicts: int


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


async def v1_metrics(db: AsyncSession, days: int) -> V1Metrics:
    status = case(
        (
            (ActionRun.status == "rejected") & (ActionRun.decided_by == ActionRun.requested_by),
            "withdrawn",
        ),
        else_=ActionRun.status,
    )
    rows = await db.execute(
        select(status, func.count())
        .where(ActionRun.created_at >= func.now() - timedelta(days=days))
        .group_by(status)
    )
    counts = dict.fromkeys(STATUSES, 0) | {str(st): int(n) for st, n in rows.tuples()}
    executed = counts["executed"] + counts["rolled_back"]
    attempted = executed + counts["failed"]
    decided = attempted + counts["rejected"]
    return V1Metrics(
        days=days,
        counts=counts,
        success_rate=_ratio(executed, attempted),
        rejection_rate=_ratio(counts["rejected"], decided),
        open_conflicts=await open_conflict_count(db),
    )
