"""문서 등록·목록·삭제·다시 색인(Phase 16a).

권한(어느 그룹의 문서를 보나)은 호출부가 ``rbac`` 로 구해 넘긴다.

등록은 한 트랜잭션에서 글 추출 → 청크 → 임베딩 작업 넣기 → 감사까지 한다. 임베딩은 워커가
(``documents.indexing``) — 모델 호출이 길어 요청 안에서 하지 않는다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Set
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.documents import Document, DocumentChunk
from ontobot.db.models.security import DataGroup
from ontobot.documents import chunking, extract
from ontobot.jobs import queue
from ontobot.security import audit

EMBED_JOB = "document_embed"  # jobs.handlers 와 같은 이름


class NotFound(Exception):
    pass


class Duplicate(Exception):
    def __init__(self, existing_id: int) -> None:
        super().__init__(existing_id)
        self.existing_id = existing_id


@dataclass(frozen=True)
class DocumentView:
    id: int
    title: str
    filename: str
    media_type: str
    size_bytes: int
    group_id: int
    group_name: str
    status: str
    error: str | None
    chars: int
    chunks: int
    embedded: int
    embedding_model: str | None
    created_at: datetime
    indexed_at: datetime | None


async def create(
    db: AsyncSession,
    *,
    title: str | None,
    filename: str,
    data: bytes,
    group_id: int,
    actor_id: int,
) -> Document:
    kind, text = extract.extract(filename, data)  # ExtractError 는 호출부가 422 로
    digest = hashlib.sha256(data).hexdigest()
    existing = await db.scalar(
        select(Document.id).where(Document.group_id == group_id, Document.sha256 == digest)
    )
    if existing is not None:
        raise Duplicate(existing)
    doc = Document(
        title=(title or "").strip() or filename,
        filename=filename,
        media_type=kind,
        size_bytes=len(data),
        sha256=digest,
        group_id=group_id,
        text=text,
        status="pending",
        created_by=actor_id,
    )
    db.add(doc)
    await db.flush()
    pieces = chunking.chunk(text)
    db.add_all(
        DocumentChunk(document_id=doc.id, chunk_no=c.no, start_char=c.start, text=c.text)
        for c in pieces
    )
    await queue.enqueue(db, EMBED_JOB, {"document_id": doc.id})
    audit.record(
        db,
        audit.DOCUMENT_ADDED,
        actor_id=actor_id,
        target=f"document:{doc.id}",
        payload={
            "group_id": group_id,
            "media_type": kind,
            "chars": len(text),
            "chunks": len(pieces),
        },
    )
    await db.flush()
    return doc


async def _views(db: AsyncSession, where: object) -> list[DocumentView]:
    chunks = (
        select(
            DocumentChunk.document_id,
            func.count().label("n"),
            func.count(DocumentChunk.embedding).label("embedded"),
        )
        .group_by(DocumentChunk.document_id)
        .subquery()
    )
    rows = await db.execute(
        select(Document, DataGroup.display_name, chunks.c.n, chunks.c.embedded)
        .join(DataGroup, DataGroup.id == Document.group_id)
        .outerjoin(chunks, chunks.c.document_id == Document.id)
        .where(where)  # type: ignore[arg-type]
        .order_by(Document.id.desc())
    )
    return [
        DocumentView(
            id=d.id,
            title=d.title,
            filename=d.filename,
            media_type=d.media_type,
            size_bytes=d.size_bytes,
            group_id=d.group_id,
            group_name=g,
            status=d.status,
            error=d.error,
            chars=len(d.text),
            chunks=int(n or 0),
            embedded=int(e or 0),
            embedding_model=d.embedding_model,
            created_at=d.created_at,
            indexed_at=d.indexed_at,
        )
        for d, g, n, e in rows
    ]


async def listing(db: AsyncSession, groups: Set[int]) -> list[DocumentView]:
    if not groups:
        return []
    return await _views(db, Document.group_id.in_(list(groups)))


async def get(db: AsyncSession, doc_id: int, groups: Set[int]) -> Document:
    """읽을 수 없는 그룹의 문서는 없는 문서와 같다."""
    doc = await db.get(Document, doc_id)
    if doc is None or doc.group_id not in groups:
        raise NotFound(doc_id)
    return doc


async def view(db: AsyncSession, doc: Document) -> DocumentView:
    return (await _views(db, Document.id == doc.id))[0]


@dataclass(frozen=True)
class ChunkView:
    no: int
    start_char: int
    text: str
    embedded: bool


async def chunks(db: AsyncSession, doc_id: int) -> list[ChunkView]:
    """화면용 — 벡터는 읽지 않는다(있는지만)."""
    rows = await db.execute(
        select(
            DocumentChunk.chunk_no,
            DocumentChunk.start_char,
            DocumentChunk.text,
            DocumentChunk.embedding.is_not(None),
        )
        .where(DocumentChunk.document_id == doc_id)
        .order_by(DocumentChunk.chunk_no)
    )
    return [ChunkView(n, s, t, bool(e)) for n, s, t, e in rows]


async def remove(db: AsyncSession, doc: Document, *, actor_id: int) -> None:
    audit.record(
        db,
        audit.DOCUMENT_DELETED,
        actor_id=actor_id,
        target=f"document:{doc.id}",
        payload={"group_id": doc.group_id, "title": doc.title},
    )
    await db.delete(doc)
    await db.flush()


async def reindex(db: AsyncSession, doc: Document, *, actor_id: int) -> None:
    """임베딩을 지우고 다시 만든다(모델을 바꿨을 때·실패했을 때)."""
    await db.execute(
        update(DocumentChunk)
        .where(DocumentChunk.document_id == doc.id)
        .values(embedding=None, embedding_model=None)
    )
    doc.status, doc.error, doc.embedding_model, doc.indexed_at = "pending", None, None, None
    await queue.enqueue(db, EMBED_JOB, {"document_id": doc.id}, dedupe=True)
    audit.record(db, audit.DOCUMENT_REINDEXED, actor_id=actor_id, target=f"document:{doc.id}")
    await db.flush()


async def group_has_documents(db: AsyncSession, group_id: int) -> bool:
    return bool(
        await db.scalar(
            select(func.count()).select_from(Document).where(Document.group_id == group_id)
        )
    )
