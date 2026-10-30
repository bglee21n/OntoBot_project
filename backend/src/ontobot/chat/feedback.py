"""답에 대한 피드백(PRD 5.6 P1, Phase 15).

사용자는 자기 대화의 답에만 좋아요·싫어요를 남긴다(같은 답에 다시 누르면 고친다). 의견은 마스킹해
저장한다. 싫어요는 골든 문항 후보다 — 옮기기는 ``evaluation.golden.promote_feedback`` 가 하고,
기대값은 사람이 적는다(피드백은 답이 틀렸다는 신호일 뿐 맞는 답을 모른다).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ontobot.db.models.chat import Conversation, Feedback, Message
from ontobot.security import audit
from ontobot.security.pii import mask_text

Rating = Literal["up", "down"]
FeedbackStatus = Literal["open", "promoted", "dismissed"]
LIST_LIMIT = 200


class MessageNotFound(Exception):
    pass


class FeedbackNotFound(Exception):
    pass


class AlreadyHandled(Exception):
    """이미 옮겼거나 넘긴 피드백이다."""


async def submit(
    db: AsyncSession, *, user_id: int, message_id: int, rating: Rating, comment: str | None
) -> Feedback:
    msg = await db.get(Message, message_id)
    conv = await db.get(Conversation, msg.conversation_id) if msg is not None else None
    if msg is None or conv is None or conv.user_id != user_id or msg.role != "assistant":
        # 남의 대화의 답은 없는 답과 같은 말로 거른다.
        raise MessageNotFound(message_id)
    text = mask_text(comment.strip()) if comment and comment.strip() else None
    fb = await db.scalar(
        select(Feedback).where(Feedback.message_id == message_id, Feedback.user_id == user_id)
    )
    if fb is None:
        fb = Feedback(message_id=message_id, user_id=user_id, rating=rating, comment=text)
        db.add(fb)
    else:
        fb.rating = rating
        fb.comment = text
    await db.flush()
    return fb


async def mine(db: AsyncSession, user_id: int, message_ids: list[int]) -> dict[int, str]:
    """메시지 id → 내가 남긴 평가."""
    if not message_ids:
        return {}
    rows = await db.execute(
        select(Feedback.message_id, Feedback.rating).where(
            Feedback.user_id == user_id, Feedback.message_id.in_(message_ids)
        )
    )
    return {m: r for m, r in rows.all()}


@dataclass(frozen=True)
class FeedbackView:
    id: int
    message_id: int
    rating: str
    comment: str | None
    status: str
    golden_item_id: int | None
    question: str | None
    answer: str
    answer_status: str | None
    grade: int | None
    conditions: str | None
    trace_id: int | None
    created_at: datetime


async def listing(
    db: AsyncSession, *, status: FeedbackStatus | None, rating: Rating | None
) -> list[FeedbackView]:
    asked = aliased(Message)
    # 답 바로 앞의 사용자 메시지가 그 답의 질문이다(채팅 API 가 질문 → 답 순서로 넣는다).
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
    q = (
        select(Feedback, Message, question_id.label("question_id"))
        .join(Message, Message.id == Feedback.message_id)
        .order_by(Feedback.id.desc())
        .limit(LIST_LIMIT)
    )
    if status is not None:
        q = q.where(Feedback.status == status)
    if rating is not None:
        q = q.where(Feedback.rating == rating)
    rows = (await db.execute(q)).all()
    qids = [r.question_id for r in rows if r.question_id is not None]
    questions = (
        dict(
            (
                await db.execute(select(Message.id, Message.content).where(Message.id.in_(qids)))
            ).all()
        )
        if qids
        else {}
    )
    return [
        FeedbackView(
            id=fb.id,
            message_id=fb.message_id,
            rating=fb.rating,
            comment=fb.comment,
            status=fb.status,
            golden_item_id=fb.golden_item_id,
            question=questions.get(qid) if qid is not None else None,
            answer=msg.content,
            answer_status=msg.status,
            grade=msg.grade,
            conditions=(msg.meta or {}).get("conditions"),
            trace_id=msg.trace_id,
            created_at=fb.created_at,
        )
        for fb, msg, qid in rows
    ]


async def get_open(db: AsyncSession, feedback_id: int) -> Feedback:
    fb = await db.get(Feedback, feedback_id)
    if fb is None:
        raise FeedbackNotFound(feedback_id)
    if fb.status != "open":
        raise AlreadyHandled(feedback_id)
    return fb


def mark(
    db: AsyncSession,
    fb: Feedback,
    status: FeedbackStatus,
    *,
    actor_id: int,
    golden_item_id: int | None = None,
) -> None:
    fb.status = status
    fb.golden_item_id = golden_item_id
    fb.handled_by = actor_id
    fb.handled_at = datetime.now(UTC)
    audit.record(
        db,
        audit.FEEDBACK_HANDLED,
        actor_id=actor_id,
        target=f"feedback:{fb.id}",
        payload={"status": status, "golden_item_id": golden_item_id},
    )


async def dismiss(db: AsyncSession, feedback_id: int, *, actor_id: int) -> Feedback:
    fb = await get_open(db, feedback_id)
    mark(db, fb, "dismissed", actor_id=actor_id)
    await db.flush()
    return fb
