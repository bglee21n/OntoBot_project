"""인터뷰 워크숍 — 세션·발언·요소 후보(TRD 6.7, 12절)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ontobot.db.models.base import Base, IdMixin


class InterviewSession(IdMixin, Base):
    __tablename__ = "interview_session"
    __table_args__ = (CheckConstraint("status IN ('open','submitted','published')", name="status"),)

    interviewee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    topic_state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")


class InterviewUtterance(IdMixin, Base):
    __tablename__ = "interview_utterance"
    __table_args__ = (CheckConstraint("speaker IN ('agent','interviewee')", name="speaker"),)

    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("interview_session.id", ondelete="CASCADE"), index=True
    )
    speaker: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)  # 마스킹해서 저장한다(TRD 12절)
    seq: Mapped[int] = mapped_column(Integer)


class DraftElement(IdMixin, Base):
    __tablename__ = "draft_element"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('object_type','property','link_type','action_candidate','golden_query')",
            name="kind",
        ),
        CheckConstraint("status IN ('proposed','confirmed','edited','rejected')", name="status"),
    )

    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("interview_session.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    evidence_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="proposed", server_default="proposed")
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
