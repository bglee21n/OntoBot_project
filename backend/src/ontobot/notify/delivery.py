"""발송 아웃박스 처리(워커) — ``notification_delivery`` 를 **커밋 뒤에** 보낸다.

1. 기한이 된 ``pending`` 묶음을 한 문장으로 집는다: 시도 수를 올리고 ``next_attempt_at`` 을
   임대 시각으로 민 뒤 커밋한다(``FOR UPDATE SKIP LOCKED`` — 워커가 둘이어도 같은 행을 두 번
   집지 않는다).
2. 트랜잭션 밖에서 보낸다(네트워크를 기다리는 동안 DB 를 쥐지 않는다).
3. 결과를 적는다: ``sent`` / 다시 ``pending``(시도 수 × 재시도 간격 뒤) / 상한이면 ``failed``.

워커가 보낸 뒤 결과를 적기 전에 죽으면 임대가 끝난 뒤 다시 보낸다 — **최소 한 번** 발송이다
(중복 가능성은 게이트웨이 쪽 중복 제거에 맡긴다. 잃어버리는 것보다 낫다).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from ontobot.db.session import session_scope
from ontobot.notify import channels
from ontobot.notify.config import NotifyConfig

LEASE = timedelta(minutes=5)
BATCH = 50


async def deliver_due(cfg: NotifyConfig, *, now: datetime | None = None) -> dict[str, int]:
    """기한이 된 발송을 모두 처리한다. 처리 건수를 돌려준다."""
    counts = {"sent": 0, "retry": 0, "failed": 0}
    while True:
        claimed = await _claim(now or datetime.now(UTC))
        if not claimed:
            return counts
        for row in claimed:
            msg = channels.OutMessage(row["channel"], row["recipient"], row["subject"], row["body"])
            try:
                await channels.send(cfg, msg)
            except channels.ChannelError as e:
                final = row["attempts"] >= cfg.max_attempts
                await _record_failure(row["id"], str(e), final, row["attempts"], cfg)
                counts["failed" if final else "retry"] += 1
            else:
                await _record_sent(row["id"])
                counts["sent"] += 1
        if len(claimed) < BATCH:
            return counts


async def _claim(now: datetime) -> list[dict[str, Any]]:
    async with session_scope() as db:
        rows = await db.execute(
            text(
                "UPDATE notification_delivery d SET attempts = d.attempts + 1, "
                "next_attempt_at = :lease WHERE d.id IN ("
                "SELECT id FROM notification_delivery WHERE status = 'pending' "
                "AND next_attempt_at <= :now ORDER BY id LIMIT :n FOR UPDATE SKIP LOCKED) "
                "RETURNING d.id, d.channel, d.recipient, d.subject, d.body, d.attempts"
            ),
            {"now": now, "lease": now + LEASE, "n": BATCH},
        )
        claimed = [dict(r._mapping) for r in rows]
        await db.commit()
        return claimed
    return []


async def _record_sent(delivery_id: int) -> None:
    async with session_scope() as db:
        await db.execute(
            text(
                "UPDATE notification_delivery SET status = 'sent', sent_at = now(), error = NULL "
                "WHERE id = :id AND status = 'pending'"
            ),
            {"id": delivery_id},
        )
        await db.commit()


async def _record_failure(
    delivery_id: int, error: str, final: bool, attempts: int, cfg: NotifyConfig
) -> None:
    async with session_scope() as db:
        await db.execute(
            text(
                "UPDATE notification_delivery SET status = :status, error = :error, "
                "next_attempt_at = :next WHERE id = :id AND status = 'pending'"
            ),
            {
                "id": delivery_id,
                "status": "failed" if final else "pending",
                "error": error[:1000],
                "next": datetime.now(UTC) + timedelta(seconds=cfg.retry_seconds * attempts),
            },
        )
        await db.commit()


async def has_due(db: Any, now: datetime) -> bool:
    """주기 발행이 발송 작업을 넣을지 — 기한이 된 대기 발송이 있는가."""
    return bool(
        await db.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM notification_delivery "
                "WHERE status = 'pending' AND next_attempt_at <= :now)"
            ),
            {"now": now},
        )
    )
