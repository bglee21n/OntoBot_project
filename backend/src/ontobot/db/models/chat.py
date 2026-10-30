"""대화·관측 — 대화·메시지·맥락·트레이스(TRD 6.5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ontobot.db.models.base import Base, IdMixin, tz


class Conversation(IdMixin, Base):
    __tablename__ = "conversation"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("app_user.id"), index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(tz(), server_default=text("now()"))


class Message(IdMixin, Base):
    __tablename__ = "message"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')", name="role"),
        CheckConstraint(
            "status IS NULL OR status IN ('ok','empty','failed','not_query','action_preview')",
            name="status",
        ),
        CheckConstraint("grade IS NULL OR grade IN (1,2,3)", name="grade"),
    )

    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversation.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(16))
    grade: Mapped[int | None] = mapped_column(Integer)
    trace_id: Mapped[int | None] = mapped_column(BigInteger)
    # 답변을 다시 그릴 때 필요한 것: 조회 조건 문장, 인용 목록, 추정 문장 번호, 실패 사유.
    # 근거 값 자체는 담지 않는다 — 근거 카드는 열 때마다 다시 읽는다(PRD 5.2).
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ConversationContext(IdMixin, Base):
    """식별자만 담는다 — 값은 매번 다시 조회한다(PRD 5.2)."""

    __tablename__ = "conversation_context"

    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversation.id", ondelete="CASCADE"), unique=True
    )
    refs: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)


class Trace(IdMixin, Base):
    __tablename__ = "trace"

    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    sql_masked: Mapped[str | None] = mapped_column(Text)
    cypher_masked: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(16))
    failure_reason: Mapped[str | None] = mapped_column(String(32))
    grade: Mapped[int | None] = mapped_column(Integer)
    fact_sentences: Mapped[int | None] = mapped_column(Integer)
    verified_sentences: Mapped[int | None] = mapped_column(Integer)
    removed_citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    stage_latency_ms: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict)
    llm_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)


class Feedback(IdMixin, Base):
    """답에 대한 사용자 피드백(PRD 5.6 P1, Phase 15). 싫어요는 골든 문항 후보가 된다.

    후보를 골든 문항으로 옮길 때 기대값은 **사람이** 적는다(``evaluation.golden.promote_feedback``)
    — 답이 틀렸다는 신호일 뿐, 맞는 답이 무엇인지는 모른다. 의견은 마스킹해 저장한다.
    """

    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint("rating IN ('up','down')", name="rating"),
        CheckConstraint("status IN ('open','promoted','dismissed')", name="status"),
        UniqueConstraint("message_id", "user_id", name="uq_feedback_message_user"),
    )

    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("message.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    rating: Mapped[str] = mapped_column(String(8))
    comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    golden_item_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("golden_item.id", ondelete="SET NULL")
    )
    handled_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    handled_at: Mapped[datetime | None] = mapped_column(tz())
