"""대화 기록에서 답 → 그 답의 질문 찾기(피드백 후보·트레이스 화면이 함께 쓴다).

채팅 API 가 질문 → 답 순서로 넣으므로, 답 바로 앞의 사용자 메시지가 그 답의 질문이다.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ontobot.db.models.chat import Message


async def questions_for(db: AsyncSession, answer_ids: Sequence[int]) -> dict[int, str]:
    """답 메시지 id → 질문 원문. 질문을 못 찾은 답은 빠진다."""
    if not answer_ids:
        return {}
    asked = aliased(Message)
    question_id = (
        select(func.max(asked.id))
        .where(
            asked.conversation_id == Message.conversation_id,
            asked.role == "user",
            asked.id < Message.id,
        )
        .correlate(Message)
        .scalar_subquery()
    )
    pairs = (
        await db.execute(select(Message.id, question_id).where(Message.id.in_(list(answer_ids))))
    ).all()
    qids = {a: q for a, q in pairs if q is not None}
    if not qids:
        return {}
    texts = dict(
        (
            await db.execute(
                select(Message.id, Message.content).where(Message.id.in_(qids.values()))
            )
        ).all()
    )
    return {a: texts[q] for a, q in qids.items() if q in texts}
