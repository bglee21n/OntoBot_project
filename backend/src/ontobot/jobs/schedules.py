"""주기 작업 발행 — 매핑의 ``schedule``(``every:15m``), 매일 대사(TRD 5.5)·보존 기간
정리(9.3)·감사 체인 검증,
재시도 기한이 된 메일·SMS 발송(17.1-4).

발행만 한다(실행은 워커가 큐에서 집는다). 같은 작업이 대기·실행 중이면 다시 넣지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.jobs import Job
from ontobot.db.models.sync import Mapping, SyncRun
from ontobot.jobs import handlers, queue
from ontobot.notify import delivery
from ontobot.sync.mapping import schedule_seconds

DAILY = timedelta(days=1)


async def enqueue_due(db: AsyncSession, *, now: datetime | None = None) -> list[int]:
    now = now or datetime.now(UTC)
    issued: list[int] = []
    for m in await db.scalars(select(Mapping).where(Mapping.schedule.is_not(None))):
        try:
            every = schedule_seconds(m.schedule)
        except ValueError:
            continue  # 저장 시 검사를 통과했다면 오지 않는다
        if every is None:
            continue
        last = await db.scalar(
            select(func.max(SyncRun.started_at)).where(SyncRun.mapping_id == m.id)
        )
        if last is None or last + timedelta(seconds=every) <= now:
            job_id = await queue.enqueue(db, handlers.SYNC, {"mapping_id": m.id}, dedupe=True)
            if job_id is not None:
                issued.append(job_id)

    for kind in (handlers.GRAPH_RECONCILE, handlers.RETENTION, handlers.AUDIT_VERIFY):
        last = await db.scalar(select(func.max(Job.created_at)).where(Job.kind == kind))
        if last is None or last + DAILY <= now:
            job_id = await queue.enqueue(db, kind, {}, dedupe=True)
            if job_id is not None:
                issued.append(job_id)
    # 재시도 기한이 된 메일·SMS 발송(실행 때 넣은 작업이 끝난 뒤 남은 것).
    if await delivery.has_due(db, now):
        job_id = await queue.enqueue(db, handlers.NOTIFY, {}, dedupe=True)
        if job_id is not None:
            issued.append(job_id)
    await db.commit()
    return issued
