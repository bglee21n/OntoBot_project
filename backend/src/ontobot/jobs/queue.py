"""작업 큐 — PostgreSQL ``job`` 표 + ``FOR UPDATE SKIP LOCKED``(TRD 3절). Redis 를 들이지 않는다.

- 집기(claim)는 **한 문장**이다(서브쿼리로 잠그고 바로 running 으로 바꾼다). 두 문장으로 나누면
  사이에 다른 워커가 같은 작업을 집을 수 있다.
- 실패하면 ``MAX_ATTEMPTS`` 까지 지수 백오프로 다시 넣고, 그 뒤에는 ``failed`` 로 남긴다.
  프로세스를 죽이는 작업이 워커가 뜰 때마다 다시 돌며 죽는 고리를 끊는다.
- 워커가 죽으면 ``running`` 이 고아로 남는다. ``recover_stale`` 이 오래 잠긴 것을 되돌린다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.jobs import Job

MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=30)


async def enqueue(
    db: AsyncSession,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    run_after: datetime | None = None,
    dedupe: bool = False,
) -> int | None:
    """작업을 넣는다.

    ``dedupe`` 면 같은 종류·payload 의 대기·실행 중 작업이 있을 때 넣지 않는다(None).
    """
    body = payload or {}
    if dedupe:
        exists = await db.scalar(
            text(
                "SELECT id FROM job WHERE kind = :k AND status IN ('queued','running') "
                "AND payload @> CAST(:p AS jsonb) AND CAST(:p AS jsonb) @> payload LIMIT 1"
            ),
            {"k": kind, "p": json.dumps(body, ensure_ascii=False)},
        )
        if exists:
            return None
    job = Job(kind=kind, payload=body, status="queued", run_after=run_after or datetime.now(UTC))
    db.add(job)
    await db.flush()
    return job.id


@dataclass(frozen=True)
class Claimed:
    """집은 작업. ORM 객체가 아니라 값이다 — 처리기를 도는 동안 세션·트랜잭션을 붙잡지 않는다."""

    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int


async def claim(db: AsyncSession, worker_id: str) -> Claimed | None:
    row = (
        await db.execute(
            text(
                "UPDATE job SET status = 'running', locked_by = :w, locked_at = now(), "
                "attempts = attempts + 1 WHERE id = ("
                "  SELECT id FROM job WHERE status = 'queued' AND run_after <= now() "
                "  ORDER BY run_after, id FOR UPDATE SKIP LOCKED LIMIT 1"
                ") RETURNING id, kind, payload, attempts"
            ),
            {"w": worker_id},
        )
    ).one_or_none()
    await db.commit()
    return None if row is None else Claimed(row.id, row.kind, dict(row.payload), row.attempts)


async def complete(db: AsyncSession, job_id: int, result: dict[str, Any] | None = None) -> None:
    await db.execute(
        text(
            "UPDATE job SET status = 'succeeded', locked_by = NULL, error = NULL, "
            "payload = payload || jsonb_build_object('result', CAST(:r AS jsonb)) WHERE id = :id"
        ),
        {"id": job_id, "r": json.dumps(result or {}, ensure_ascii=False, default=str)},
    )
    await db.commit()


async def fail(db: AsyncSession, job_id: int, error: str) -> None:
    job = await db.get(Job, job_id, populate_existing=True)
    if job is None:
        return
    job.error = error[:2000]
    job.locked_by = None
    if job.attempts < MAX_ATTEMPTS:
        job.status = "queued"
        job.run_after = datetime.now(UTC) + timedelta(seconds=30 * 2 ** (job.attempts - 1))
    else:
        job.status = "failed"
    await db.commit()


async def recover_stale(db: AsyncSession, *, older_than: timedelta = STALE_AFTER) -> int:
    """오래 잠긴 running 작업을 되돌린다(워커가 죽은 경우). 시도 상한을 넘었으면 failed."""
    result = await db.execute(
        text(
            "UPDATE job SET status = CASE WHEN attempts >= :max THEN 'failed' ELSE 'queued' END, "
            "locked_by = NULL, error = coalesce(error, '워커가 응답 없이 사라졌습니다') "
            "WHERE status = 'running' AND locked_at < :cutoff"
        ),
        {"max": MAX_ATTEMPTS, "cutoff": datetime.now(UTC) - older_than},
    )
    await db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
