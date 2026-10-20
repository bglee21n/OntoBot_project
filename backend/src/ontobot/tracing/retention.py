"""보존 기간 만료분 삭제(TRD 9.3) — 워커의 매일 작업(``retention_purge``).

- ``trace``: 만든 지 N 일 지난 행
- ``message``: 만든 지 N 일 지난 메시지. 메시지가 하나도 남지 않은 대화는 맥락과 함께 지운다
  (``conversation_context`` 는 ``ON DELETE CASCADE``)
- ``interview_utterance``: 만든 지 N 일 지난 발언(이미 마스킹 저장이지만 오래 둘 이유가 없다)

설정이 0 이면 그 표는 지우지 않는다. 감사 로그는 DB 트리거로 추가 전용이라 여기서 다루지 않는다.
지운 건수는 감사 로그에 남긴다 — 무엇이 언제 사라졌는지는 남아야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.chat import Conversation, Message, Trace
from ontobot.db.models.workshop import InterviewUtterance
from ontobot.security import audit


@dataclass(frozen=True)
class Policy:
    trace_days: int
    message_days: int
    utterance_days: int


async def purge(db: AsyncSession, policy: Policy, *, now: datetime | None = None) -> dict[str, int]:
    """만료분을 지우고 표별 건수를 돌려준다. 호출부가 커밋한다(감사 기록과 함께)."""
    now = now or datetime.now(UTC)
    counts = {"trace": 0, "message": 0, "conversation": 0, "interview_utterance": 0}

    async def _delete(
        model: type[Trace] | type[Message] | type[InterviewUtterance], days: int
    ) -> int:
        if days <= 0:
            return 0
        result = await db.execute(
            delete(model).where(model.created_at < now - timedelta(days=days)).returning(model.id)
        )
        return len(result.all())

    counts["trace"] = await _delete(Trace, policy.trace_days)
    counts["message"] = await _delete(Message, policy.message_days)
    if counts["message"]:
        result = await db.execute(
            delete(Conversation)
            .where(~exists(select(Message.id).where(Message.conversation_id == Conversation.id)))
            .returning(Conversation.id)
        )
        counts["conversation"] = len(result.all())
    counts["interview_utterance"] = await _delete(InterviewUtterance, policy.utterance_days)
    if any(counts.values()):
        audit.record(
            db,
            audit.RETENTION_PURGED,
            actor_id=None,
            payload={
                "deleted": counts,
                "policy_days": {
                    "trace": policy.trace_days,
                    "message": policy.message_days,
                    "interview_utterance": policy.utterance_days,
                },
            },
        )
    return counts
