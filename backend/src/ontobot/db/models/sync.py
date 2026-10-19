"""동기화 — 데이터 소스·매핑·실행 이력(TRD 6.4, 11절)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ontobot.db.models.base import Base, IdMixin, tz


class DataSource(IdMixin, Base):
    __tablename__ = "data_source"
    __table_args__ = (CheckConstraint("kind IN ('postgres','oracle','mysql','csv')", name="kind"),)

    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(128), unique=True)
    # 접속 정보는 시크릿 저장소 참조만 둔다. 평문 저장 금지(TRD 9.3).
    secret_ref: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")


class Mapping(IdMixin, Base):
    __tablename__ = "mapping"
    __table_args__ = (CheckConstraint("mode IN ('incremental','full')", name="mode"),)

    source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("data_source.id"))
    object_type: Mapped[str] = mapped_column(String(64))
    source_query: Mapped[str | None] = mapped_column(Text)
    table: Mapped[str | None] = mapped_column(String(255))
    column_map: Mapped[dict[str, Any]] = mapped_column(JSONB)
    pk_column: Mapped[str] = mapped_column(String(128))
    mode: Mapped[str] = mapped_column(String(16))
    watermark_column: Mapped[str | None] = mapped_column(String(128))
    link_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    schedule: Mapped[str | None] = mapped_column(String(64))


class SyncRun(IdMixin, Base):
    __tablename__ = "sync_run"
    __table_args__ = (CheckConstraint("status IN ('running','succeeded','failed')", name="status"),)

    mapping_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("mapping.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(tz())
    finished_at: Mapped[datetime | None] = mapped_column(tz())
    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    rows_upserted: Mapped[int] = mapped_column(Integer, default=0)
    rows_deleted: Mapped[int] = mapped_column(Integer, default=0)
    watermark: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
