"""온톨로지 — 버전·객체 타입·속성·링크 타입(TRD 6.1)·Action 타입(17.1-2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ontobot.db.models.base import Base, IdMixin, tz

VERSION_STATUSES = ("draft", "published", "archived")
DATA_TYPES = ("string", "int", "decimal", "bool", "date", "datetime", "enum")
CARDINALITIES = ("one", "many")


class OntologyVersion(IdMixin, Base):
    __tablename__ = "ontology_version"
    __table_args__ = (
        CheckConstraint("status IN ('draft','published','archived')", name="status"),
        # 게시 버전은 언제나 하나뿐이다 — '현재 게시 버전'이 둘이면 읽기 경로가 갈라진다.
        Index(
            "uq_ontology_version_single_published",
            "status",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
    )

    version_no: Mapped[int] = mapped_column(Integer, unique=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ontology_version.id"))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    published_at: Mapped[datetime | None] = mapped_column(tz())
    # 게시 시점 문서의 정규화 해시 — 권한 지문(9.1)과 버전 비교에 쓴다.
    content_hash: Mapped[str | None] = mapped_column(String(64))

    object_types: Mapped[list[ObjectType]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="ObjectType.id"
    )
    link_types: Mapped[list[LinkType]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="LinkType.id"
    )
    action_types: Mapped[list[ActionType]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="ActionType.id"
    )
    functions: Mapped[list[FunctionType]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="FunctionType.id"
    )


class ObjectType(IdMixin, Base):
    __tablename__ = "object_type"
    __table_args__ = (Index("uq_object_type_version_api", "version_id", "api_name", unique=True),)

    version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ontology_version.id", ondelete="CASCADE")
    )
    api_name: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    pk_property: Mapped[str] = mapped_column(String(64))
    title_property: Mapped[str] = mapped_column(String(64))
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    version: Mapped[OntologyVersion] = relationship(back_populates="object_types")
    properties: Mapped[list[PropertyType]] = relationship(
        back_populates="object_type", cascade="all, delete-orphan", order_by="PropertyType.id"
    )


class PropertyType(IdMixin, Base):
    __tablename__ = "property_type"
    __table_args__ = (
        Index("uq_property_type_type_api", "object_type_id", "api_name", unique=True),
        CheckConstraint(
            "data_type IN ('string','int','decimal','bool','date','datetime','enum')",
            name="data_type",
        ),
    )

    object_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("object_type.id", ondelete="CASCADE")
    )
    api_name: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    data_type: Mapped[str] = mapped_column(String(16))
    enum_values: Mapped[list[str] | None] = mapped_column(JSONB)
    nullable: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    indexed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    pii_kind: Mapped[str | None] = mapped_column(String(32))

    object_type: Mapped[ObjectType] = relationship(back_populates="properties")


class LinkType(IdMixin, Base):
    __tablename__ = "link_type"
    __table_args__ = (
        Index("uq_link_type_version_api", "version_id", "api_name", unique=True),
        CheckConstraint("cardinality IN ('one','many')", name="cardinality"),
    )

    version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ontology_version.id", ondelete="CASCADE")
    )
    api_name: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(128))
    from_type: Mapped[str] = mapped_column(String(64))
    to_type: Mapped[str] = mapped_column(String(64))
    cardinality: Mapped[str] = mapped_column(String(8))
    # {"kind": "fk_property", "property": "owner_org_id"} | {"kind": "join_mapping", ...}
    source: Mapped[dict[str, Any]] = mapped_column(JSONB)
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    version: Mapped[OntologyVersion] = relationship(back_populates="link_types")


class ActionType(IdMixin, Base):
    """Action 정의(PRD 5.3). 버전에 묶여 함께 게시되고 게시본은 불변이다(TRD 17.1-2).

    정의 본문은 ``ontology.models.ActionTypeDef`` 를 그대로 담는다(이름·표시 이름 밖의 전부).
    """

    __tablename__ = "action_type"
    __table_args__ = (Index("uq_action_type_version_api", "version_id", "api_name", unique=True),)

    version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ontology_version.id", ondelete="CASCADE")
    )
    api_name: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(128))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)

    version: Mapped[OntologyVersion] = relationship(back_populates="action_types")


class FunctionType(IdMixin, Base):
    """Function 정의(PRD 5.10). Action 과 같이 버전에 묶여 함께 게시되고 게시본은 불변이다.

    정의 본문은 ``ontology.models.FunctionDef`` 를 그대로 담는다(이름·표시 이름 밖의 전부).
    """

    __tablename__ = "function_type"
    __table_args__ = (Index("uq_function_type_version_api", "version_id", "api_name", unique=True),)

    version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ontology_version.id", ondelete="CASCADE")
    )
    api_name: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(128))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)

    version: Mapped[OntologyVersion] = relationship(back_populates="functions")


class OntologyLayout(IdMixin, Base):
    """스키마 그래프 화면의 노드 위치. 정의가 아니라 화면 상태다.

    정의 문서 밖에 두어 ``content_hash``·게시 불변성과 무관하다 — 게시본의 배치도 고칠 수 있고,
    감사 기록도 남기지 않는다. draft 를 만들 때 부모 버전의 배치를 복사한다.
    """

    __tablename__ = "ontology_layout"
    __table_args__ = (
        Index(
            "uq_ontology_layout_element",
            "version_id",
            "element_kind",
            "element_name",
            unique=True,
        ),
        CheckConstraint("element_kind IN ('object_type')", name="element_kind"),
    )

    version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ontology_version.id", ondelete="CASCADE")
    )
    element_kind: Mapped[str] = mapped_column(String(16))
    element_name: Mapped[str] = mapped_column(String(64))
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    updated_at: Mapped[datetime] = mapped_column(tz(), server_default=func.now())
