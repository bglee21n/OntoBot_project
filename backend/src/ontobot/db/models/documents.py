"""문서·청크(Phase 16, PRD 5.1·5.2 P1, TRD 17.1 결정 11·12).

원본 파일은 두지 않고 **추출한 글**만 둔다(정규화: NFC·줄바꿈 ``\\n``). 청크는 그 글을 겹침 없이
자른 조각이다 — 문서 인용(``[[d:<문서 id>/<청크 번호>]]``)의 근거 대조가 청크 원문에서 값을
**그대로** 찾으므로, 청크 글은 원문과 한 글자도 달라지면 안 된다.

문서 하나는 데이터 그룹 하나에 속한다(필수). 읽을 수 있는 사람은 그 그룹 구성원이다
(``security.rbac``).
임베딩 차원은 모델마다 다르므로 열은 차원 없는 ``vector`` 이고 모델 이름을 함께 남긴다 — 다른 모델의
벡터와 섞어 비교하지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from ontobot.db.models.base import Base, IdMixin, tz


class Vector(UserDefinedType[list[float]]):
    """pgvector ``vector``(차원 없음). 값은 ``[1,2,3]`` 글로 오간다 — 드라이버 확장 없이."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "vector"

    def bind_processor(self, dialect: Any) -> Any:
        def process(value: list[float] | None) -> str | None:
            return None if value is None else "[" + ",".join(repr(float(v)) for v in value) + "]"

        return process

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        def process(value: str | None) -> list[float] | None:
            if value is None:
                return None
            return [float(v) for v in value.strip("[]").split(",") if v]

        return process


class Document(IdMixin, Base):
    __tablename__ = "document"
    __table_args__ = (
        CheckConstraint("status IN ('pending','indexed','failed')", name="status"),
        UniqueConstraint("group_id", "sha256", name="uq_document_group_sha256"),
    )

    title: Mapped[str] = mapped_column(String(255))
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    # 원본 파일 바이트의 SHA-256 — 같은 그룹에 같은 파일을 두 번 넣지 않는다.
    sha256: Mapped[str] = mapped_column(String(64))
    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("data_group.id", ondelete="RESTRICT"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    indexed_at: Mapped[datetime | None] = mapped_column(tz())


class DocumentChunk(IdMixin, Base):
    __tablename__ = "document_chunk"
    __table_args__ = (UniqueConstraint("document_id", "chunk_no", name="uq_document_chunk_no"),)

    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("document.id", ondelete="CASCADE"), index=True
    )
    chunk_no: Mapped[int] = mapped_column(Integer)
    # 문서 글에서의 시작 위치(글자) — 화면이 원문 속 위치를 보여 줄 때 쓴다.
    start_char: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    embedding_model: Mapped[str | None] = mapped_column(String(128))


class DocumentLink(IdMixin, Base):
    """문서 ↔ 객체 링크(Phase 16c). 후보는 본문에 객체 제목·pk 가 **그대로** 있을 때만 생기고
    모델러가 확정·거부한다. 거부한 후보는 다시 찾아도 되살리지 않는다.
    객체는 이름(타입·pk)으로만 가리킨다 — 객체가 삭제·권한 밖이면 읽을 때 걸러진다."""

    __tablename__ = "document_link"
    __table_args__ = (
        CheckConstraint("status IN ('candidate','confirmed','rejected')", name="status"),
        CheckConstraint("match_kind IN ('title','pk')", name="match_kind"),
        UniqueConstraint("document_id", "object_type", "object_pk", name="uq_document_link_object"),
        Index("ix_document_link_object", "object_type", "object_pk"),
    )

    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("document.id", ondelete="CASCADE"), index=True
    )
    object_type: Mapped[str] = mapped_column(String(64))
    object_pk: Mapped[str] = mapped_column(String(255))
    match_kind: Mapped[str] = mapped_column(String(8))
    match_text: Mapped[str] = mapped_column(Text)
    occurrences: Mapped[int] = mapped_column(Integer)
    # 처음 나온 청크 — 화면이 근거 위치를 보여 준다.
    first_chunk_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="candidate", server_default="candidate")
    decided_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    decided_at: Mapped[datetime | None] = mapped_column(tz())
