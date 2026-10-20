"""Action 실행·알림·발송(PRD 5.3, TRD 17.1-4·5·6).

- ``action_run``: 미리보기부터 실행·거부·롤백까지 한 행. ``request_id`` 로 같은 요청을
  한 번만 받는다.
- ``notification``: 앱 안 알림(받는 사용자별 한 행).
- ``notification_delivery``: 메일·SMS 발송 아웃박스. 실행 트랜잭션에서 쓰고 **커밋 뒤 워커가**
  보낸다 — 트랜잭션 안에서 보내면 롤백돼도 문자가 나간다. ``(실행, 채널, 수신자, 종류)`` 로
  중복 발송을 막는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ontobot.db.models.base import Base, IdMixin, tz

RUN_STATUSES = ("previewed", "blocked", "rejected", "executed", "failed", "rolled_back")


class ActionRun(IdMixin, Base):
    __tablename__ = "action_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('previewed','blocked','rejected','executed','failed','rolled_back')",
            name="status",
        ),
        Index("uq_action_run_request_id", "request_id", unique=True),
        Index("ix_action_run_status", "status"),
    )

    request_id: Mapped[str] = mapped_column(String(64))
    action_type: Mapped[str] = mapped_column(String(64))
    ontology_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ontology_version.id"))
    target_type: Mapped[str] = mapped_column(String(64))
    target_pk: Mapped[str] = mapped_column(String(255))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16))
    # 요청 경로: 화면(ui) · 채팅(chat, Phase 11) · 공개 API(api, Phase 17)
    origin: Mapped[str] = mapped_column(String(16), default="ui", server_default="ui")
    # 미리보기: 대상·바뀔 값·사전 조건 결과·발송 요약(수신자는 마스킹). 승인자가 보는 그대로다.
    preview: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    requested_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    decided_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    decided_at: Mapped[datetime | None] = mapped_column(tz())
    reason: Mapped[str | None] = mapped_column(Text)  # 거부 사유
    executed_at: Mapped[datetime | None] = mapped_column(tz())
    rolled_back_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    rolled_back_at: Mapped[datetime | None] = mapped_column(tz())
    error: Mapped[str | None] = mapped_column(Text)


class Notification(IdMixin, Base):
    """앱 안 알림."""

    __tablename__ = "notification"
    __table_args__ = (Index("ix_notification_user_unread", "user_id", "read_at"),)

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("app_user.id", ondelete="CASCADE"))
    action_run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("action_run.id"))
    kind: Mapped[str] = mapped_column(String(16), default="notify")  # notify | compensation
    body: Mapped[str] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(tz())


class NotificationDelivery(IdMixin, Base):
    """메일·SMS 발송 하나. 수신자는 원값이다(보내야 하므로) — 화면·로그·감사는 마스킹한다."""

    __tablename__ = "notification_delivery"
    __table_args__ = (
        CheckConstraint("channel IN ('email','sms')", name="channel"),
        CheckConstraint("kind IN ('notify','compensation')", name="kind"),
        CheckConstraint("status IN ('pending','sent','failed','cancelled')", name="status"),
        Index(
            "uq_notification_delivery_once",
            "action_run_id",
            "channel",
            "recipient",
            "kind",
            unique=True,
        ),
        Index("ix_notification_delivery_due", "status", "next_attempt_at"),
    )

    action_run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("action_run.id"))
    channel: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(16))
    recipient: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(tz())
    sent_at: Mapped[datetime | None] = mapped_column(tz())
    error: Mapped[str | None] = mapped_column(Text)
