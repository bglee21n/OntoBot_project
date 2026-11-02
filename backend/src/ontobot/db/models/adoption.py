"""도입 작업 공간 — 도입·구성원·양식(TRD 17.17, Phase 20).

양식 내용은 온톨로지 정의 문서에 넣지 않는다(결정 A1 — 정의 문서의 ``content_hash``·카세트 키를
바꾸지 않는다).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ontobot.db.models.base import Base, IdMixin, tz


class Engagement(IdMixin, Base):
    __tablename__ = "engagement"

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))


class EngagementMember(IdMixin, Base):
    __tablename__ = "engagement_member"
    __table_args__ = (
        CheckConstraint("role IN ('owner','editor','viewer')", name="role"),
        UniqueConstraint("engagement_id", "user_id"),
    )

    engagement_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("engagement.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))


class EngagementSheet(IdMixin, Base):
    __tablename__ = "engagement_sheet"
    __table_args__ = (UniqueConstraint("engagement_id", "kind"),)

    engagement_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("engagement.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(32))  # adoption.sheets 의 kind
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # 판 비교 저장 — 읽을 때 받은 판과 지금 판이 다르면 덮어쓰지 않는다.
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    updated_at: Mapped[datetime] = mapped_column(tz(), server_default=func.now())


class EngagementDecision(IdMixin, Base):
    """36.14 파일럿 판단 기록(Phase 22) — 고친 적 없이 쌓인다(판단을 바꾸면 새 행)."""

    __tablename__ = "engagement_decision"
    __table_args__ = (
        CheckConstraint("decision IN ('go','go_after_fix','no_go')", name="decision"),
    )

    engagement_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("engagement.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    # 판단할 때 본 근거 — 마지막 실제 모델 평가 실행과 그 게이트
    eval_run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("eval_run.id"))
    gate_passed: Mapped[bool | None] = mapped_column(Boolean)
    invariant_violations: Mapped[int] = mapped_column(Integer, default=0)
    decided_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))


class EngagementSeed(IdMixin, Base):
    """36.15 합성 예시 데이터 — 타입마다 CSV 하나(자산화 zip 의 seed/). 운영 object 표와
    무관하다."""

    __tablename__ = "engagement_seed"
    __table_args__ = (UniqueConstraint("engagement_id", "object_type"),)

    engagement_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("engagement.id", ondelete="CASCADE")
    )
    object_type: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    rows: Mapped[int] = mapped_column(Integer)
    uploaded_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    updated_at: Mapped[datetime] = mapped_column(tz(), server_default=func.now())
