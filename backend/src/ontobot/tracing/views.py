"""트레이스 화면(Phase 15, PRD 5.6 P1) — 목록과 질문 붙이기. 관리자만 본다(호출부가 막는다).

트레이스에는 질문 원문이 없다. 질문은 답 메시지 바로 앞의 사용자 메시지에서 가져와
**패턴으로 가린다**(주민번호·전화·이메일). 트레이스의 다른 칸은 기록할 때 이미 마스킹됐다
(``tracing.recorder``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.chat import history
from ontobot.db.models.chat import Trace
from ontobot.db.models.security import AppUser
from ontobot.security.pii import mask_text

LIST_LIMIT = 100


@dataclass(frozen=True)
class TraceSummary:
    id: int
    created_at: datetime
    user_name: str | None
    question: str | None
    status: str | None
    failure_reason: str | None
    grade: int | None
    row_count: int | None
    fact_sentences: int | None
    verified_sentences: int | None
    removed_citations: int
    total_ms: int


async def question_of(db: AsyncSession, message_id: int | None) -> str | None:
    if message_id is None:
        return None
    q = (await history.questions_for(db, [message_id])).get(message_id)
    return mask_text(q) if q is not None else None


async def listing(
    db: AsyncSession,
    *,
    status: str | None,
    failure_reason: str | None,
    before_id: int | None,
    limit: int = LIST_LIMIT,
) -> list[TraceSummary]:
    """최근 것부터. ``before_id`` 로 다음 쪽을 읽는다."""
    q = (
        select(Trace, AppUser.display_name)
        .outerjoin(AppUser, AppUser.id == Trace.user_id)
        .order_by(Trace.id.desc())
        .limit(limit)
    )
    if status is not None:
        q = q.where(Trace.status == status)
    if failure_reason is not None:
        q = q.where(Trace.failure_reason == failure_reason)
    if before_id is not None:
        q = q.where(Trace.id < before_id)
    rows = (await db.execute(q)).all()
    questions = await history.questions_for(
        db, [t.message_id for t, _ in rows if t.message_id is not None]
    )
    return [
        TraceSummary(
            id=t.id,
            created_at=t.created_at,
            user_name=name,
            question=mask_text(questions[t.message_id]) if t.message_id in questions else None,
            status=t.status,
            failure_reason=t.failure_reason,
            grade=t.grade,
            row_count=t.row_count,
            fact_sentences=t.fact_sentences,
            verified_sentences=t.verified_sentences,
            removed_citations=len(t.removed_citations or []),
            total_ms=sum((t.stage_latency_ms or {}).values()),
        )
        for t, name in rows
    ]
