"""객체·링크(TRD 6.2). 읽기는 ``objects.reader.resolve()`` 하나만 거친다(16.3-5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ontobot.db.models.base import Base, IdMixin, tz


class ObjectRecord(IdMixin, Base):
    __tablename__ = "object"
    __table_args__ = (Index("uq_object_type_pk", "type", "pk", unique=True),)

    type: Mapped[str] = mapped_column(String(64))
    pk: Mapped[str] = mapped_column(String(255))
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("data_source.id"))
    row_hash: Mapped[str | None] = mapped_column(String(64))
    synced_at: Mapped[datetime | None] = mapped_column(tz())
    deleted_at: Mapped[datetime | None] = mapped_column(tz())


class LinkRecord(IdMixin, Base):
    __tablename__ = "link"
    __table_args__ = (
        # 증분 동기화가 링크를 중복 생성하지 않게 한다(TRD v0.2 정정 1).
        Index(
            "uq_link_live",
            "link_type",
            "from_pk",
            "to_pk",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_link_to", "link_type", "to_pk"),
    )

    link_type: Mapped[str] = mapped_column(String(64))
    from_pk: Mapped[str] = mapped_column(String(255))
    to_pk: Mapped[str] = mapped_column(String(255))
    source_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("data_source.id"))
    synced_at: Mapped[datetime | None] = mapped_column(tz())
    deleted_at: Mapped[datetime | None] = mapped_column(tz())
