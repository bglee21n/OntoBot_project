"""데이터 신선도(TRD 11절, 13.2) — 매핑마다 마지막 성공 실행 시각과 그로부터 지난 시간."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.sync import Mapping, SyncRun


@dataclass(frozen=True)
class Freshness:
    mapping_id: int
    object_type: str
    last_success_at: datetime | None
    age_seconds: int | None
    last_status: str | None


async def by_mapping(db: AsyncSession) -> list[Freshness]:
    now = datetime.now(UTC)
    last_ok = (
        select(SyncRun.mapping_id, func.max(SyncRun.finished_at).label("at"))
        .where(SyncRun.status == "succeeded")
        .group_by(SyncRun.mapping_id)
        .subquery()
    )
    latest = (
        select(SyncRun.mapping_id, func.max(SyncRun.id).label("id"))
        .group_by(SyncRun.mapping_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(Mapping.id, Mapping.object_type, last_ok.c.at, SyncRun.status)
            .outerjoin(last_ok, last_ok.c.mapping_id == Mapping.id)
            .outerjoin(latest, latest.c.mapping_id == Mapping.id)
            .outerjoin(SyncRun, SyncRun.id == latest.c.id)
            .order_by(Mapping.id)
        )
    ).all()
    return [
        Freshness(
            r.id, r.object_type, r.at, int((now - r.at).total_seconds()) if r.at else None, r.status
        )
        for r in rows
    ]
