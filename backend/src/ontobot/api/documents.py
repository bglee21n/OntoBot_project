"""문서(Phase 16a, PRD 5.1 P1) — 등록·목록·상세·삭제·다시 색인. 검증과 위임만 한다.

문서는 데이터 그룹 하나에 속하고 그 구성원만 본다(읽을 수 없는 문서는 없는 문서와 같은 404).
등록·삭제·다시 색인은 모델러·관리자이면서 그 그룹 구성원이어야 한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from ontobot.api.deps import CurrentUser, Db, User, require_role
from ontobot.api.errors import ProblemError
from ontobot.db.models.documents import Document
from ontobot.db.models.security import DataGroup
from ontobot.documents import extract, store
from ontobot.security import rbac
from ontobot.security.rbac import ROLE_ADMIN, ROLE_MODELER

router = APIRouter(prefix="/documents", tags=["documents"])

Curator = Annotated[CurrentUser, Depends(require_role(ROLE_MODELER, ROLE_ADMIN))]


class DocumentOut(BaseModel):
    id: int
    title: str
    filename: str
    media_type: str
    size_bytes: int
    group_id: int
    group_name: str
    status: Literal["pending", "indexed", "failed"]
    error: str | None
    chars: int
    chunks: int
    embedded: int
    embedding_model: str | None
    created_at: datetime
    indexed_at: datetime | None


class ChunkOut(BaseModel):
    no: int
    start_char: int
    text: str
    embedded: bool


class DocumentDetail(DocumentOut):
    chunk_list: list[ChunkOut]


class GroupChoice(BaseModel):
    id: int
    display_name: str


def _out(v: store.DocumentView) -> DocumentOut:
    return DocumentOut.model_validate(v, from_attributes=True)


def _not_found() -> ProblemError:
    return ProblemError(404, "문서를 찾을 수 없습니다", code="document_not_found")


async def _readable(db: Db, user: CurrentUser, doc_id: int) -> Document:
    try:
        return await store.get(db, doc_id, await rbac.document_groups(db, user.id))
    except store.NotFound as e:
        raise _not_found() from e


@router.get("", response_model=list[DocumentOut])
async def documents(db: Db, user: User) -> list[DocumentOut]:
    return [_out(v) for v in await store.listing(db, await rbac.document_groups(db, user.id))]


@router.get("/groups", response_model=list[GroupChoice])
async def upload_groups(db: Db, user: Curator) -> list[GroupChoice]:
    """문서를 넣을 수 있는 그룹 — 내가 속한 그룹."""
    groups = await rbac.document_groups(db, user.id)
    rows = await db.execute(
        select(DataGroup.id, DataGroup.display_name)
        .where(DataGroup.id.in_(list(groups)))
        .order_by(DataGroup.display_name)
    )
    return [GroupChoice(id=i, display_name=n) for i, n in rows]


@router.post("", response_model=DocumentOut, status_code=201)
async def upload(
    db: Db,
    user: Curator,
    file: Annotated[UploadFile, File()],
    group_id: Annotated[int, Form()],
    title: Annotated[str | None, Form(max_length=255)] = None,
) -> DocumentOut:
    if group_id not in await rbac.document_groups(db, user.id):
        raise ProblemError(403, "속한 그룹에만 문서를 넣을 수 있습니다", code="forbidden_group")
    data = await file.read(extract.MAX_BYTES + 1)
    try:
        doc = await store.create(
            db,
            title=title,
            filename=(file.filename or "document")[:255],
            data=data,
            group_id=group_id,
            actor_id=user.id,
        )
    except extract.ExtractError as e:
        raise ProblemError(422, str(e), code="document_unreadable") from e
    except store.Duplicate as e:
        raise ProblemError(
            409,
            "같은 파일이 이 그룹에 이미 있습니다",
            code="document_exists",
            extra={"document_id": e.existing_id},
        ) from e
    return _out(await store.view(db, doc))


@router.get("/{doc_id}", response_model=DocumentDetail)
async def document(doc_id: int, db: Db, user: User) -> DocumentDetail:
    doc = await _readable(db, user, doc_id)
    chunks = await store.chunks(db, doc.id)
    return DocumentDetail(
        **_out(await store.view(db, doc)).model_dump(),
        chunk_list=[
            ChunkOut(no=c.no, start_char=c.start_char, text=c.text, embedded=c.embedded)
            for c in chunks
        ],
    )


@router.delete("/{doc_id}", status_code=204)
async def delete_document(doc_id: int, db: Db, user: Curator) -> Response:
    await store.remove(db, await _readable(db, user, doc_id), actor_id=user.id)
    return Response(status_code=204)


@router.post("/{doc_id}/reindex", response_model=DocumentOut)
async def reindex(doc_id: int, db: Db, user: Curator) -> DocumentOut:
    doc = await _readable(db, user, doc_id)
    await store.reindex(db, doc, actor_id=user.id)
    return _out(await store.view(db, doc))
