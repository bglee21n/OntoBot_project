"""작업 큐와 그래프 아웃박스(TRD 3절, 5.2, v0.2 정정 4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ontobot.db.models.base import Base, IdMixin, tz


class Job(IdMixin, Base):
    __tablename__ = "job"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','succeeded','failed')", name="status"),
        # 워커가 '실행할 차례인 대기 작업'을 SKIP LOCKED 로 집어 가는 경로.
        Index(
            "ix_job_ready",
            "run_after",
            postgresql_where=text("status = 'queued'"),
        ),
    )

    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    run_after: Mapped[datetime] = mapped_column(tz(), server_default=text("now()"))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_by: Mapped[str | None] = mapped_column(String(128))
    locked_at: Mapped[datetime | None] = mapped_column(tz())
    error: Mapped[str | None] = mapped_column(String(2000))


class GraphOutbox(IdMixin, Base):
    """id(bigserial) 순서가 곧 소비 순서다. 처리 위치 = processed_at IS NULL 인 최소 id."""

    __tablename__ = "graph_outbox"
    __table_args__ = (
        CheckConstraint(
            "event IN ('link_upsert','link_delete','node_upsert','node_delete')", name="event"
        ),
        Index("ix_graph_outbox_pending", "id", postgresql_where=text("processed_at IS NULL")),
    )

    event: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    processed_at: Mapped[datetime | None] = mapped_column(tz())
