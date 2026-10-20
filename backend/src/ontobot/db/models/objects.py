"""객체·링크(TRD 6.2). 읽기는 ``objects.reader.resolve()`` 하나만 거친다(16.3-5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, text
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


# ---------------------------------------------------------------- 편집 레이어(TRD 6.3, P1)


class ObjectEdit(IdMixin, Base):
    """원천 값 위에 덮는 편집 하나(속성 하나). 보이는 값 = 활성 편집 값 → 없으면 원천 값.

    활성(``cleared_at IS NULL``) 편집은 (type, pk, property) 마다 하나뿐이다. 동기화는 이 표를
    건드리지 않는다 — 원천이 바뀌면 ``sync_conflict`` 만 남긴다(편집 레이어 우선, PRD 5.3).
    값은 ``object.properties`` 와 같은 JSON 표현이다(그래야 조회가 그대로 덮어 읽는다).
    """

    __tablename__ = "object_edit"
    __table_args__ = (
        Index(
            "uq_object_edit_active",
            "type",
            "pk",
            "property",
            unique=True,
            postgresql_where=text("cleared_at IS NULL"),
        ),
    )

    type: Mapped[str] = mapped_column(String(64))
    pk: Mapped[str] = mapped_column(String(255))
    property: Mapped[str] = mapped_column(String(64))
    value: Mapped[Any] = mapped_column(JSONB)
    # 편집 당시의 원천 값(없으면 NULL) — 이후 동기화 값과 비교해 충돌을 판정한다.
    base_source_value: Mapped[Any] = mapped_column(JSONB(none_as_null=True), nullable=True)
    # 편집을 만든 Action 실행(Phase 10 에서 action_run FK 를 건다).
    action_run_id: Mapped[int | None] = mapped_column(BigInteger)
    edited_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    edited_at: Mapped[datetime] = mapped_column(tz())
    cleared_at: Mapped[datetime | None] = mapped_column(tz())
    cleared_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))


class SyncConflict(IdMixin, Base):
    """편집된 속성의 원천 값이 편집 당시와 달라졌다는 기록. 편집 값은 그대로 이긴다.

    편집 하나에 살아 있는(open/acknowledged) 충돌은 하나뿐이다 — 원천이 또 바뀌면 그 행의 값을
    고치고 다시 ``open`` 으로 둔다. 원천이 편집 당시 값으로 돌아오거나 편집이 해제되면 ``cleared``.
    """

    __tablename__ = "sync_conflict"
    __table_args__ = (
        CheckConstraint("status IN ('open','acknowledged','cleared')", name="status"),
        Index(
            "uq_sync_conflict_live",
            "edit_id",
            unique=True,
            postgresql_where=text("status <> 'cleared'"),
        ),
        Index("ix_sync_conflict_status", "status"),
    )

    edit_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("object_edit.id"))
    new_source_value: Mapped[Any] = mapped_column(JSONB(none_as_null=True), nullable=True)
    source_synced_at: Mapped[datetime | None] = mapped_column(tz())
    status: Mapped[str] = mapped_column(String(16))
    resolved_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(tz())
