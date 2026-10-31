"""문서(Phase 16a, PRD 5.1 P1) — 등록·목록·상세·삭제·다시 색인. 검증과 위임만 한다.

문서는 데이터 그룹 하나에 속하고 그 구성원만 본다(읽을 수 없는 문서는 없는 문서와 같은 404).
등록·삭제·다시 색인은 모델러·관리자이면서 그 그룹 구성원이어야 한다.

객체 링크(Phase 16c): 후보 찾기·확정·거부는 같은 조건. 목록은 보는 사람이 읽을 수 있는 객체만 —
모델러·관리자는 후보·거부까지, 그 밖의 사용자는 확정된 링크만 본다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from ontobot.api.deps import CurrentUser, Db, SettingsDep, User, require_role
from ontobot.api.errors import ProblemError
from ontobot.db.models.documents import Document
from ontobot.db.models.security import DataGroup
from ontobot.documents import extract, links, store
from ontobot.documents.indexing import ROLE as EMBEDDER_ROLE
from ontobot.llm.config import load as load_llm_config
from ontobot.ontology import registry
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


def _require_embedder(settings: SettingsDep) -> None:
    """임베딩 역할이 없으면 색인할 수 없다 — 넣고 나서 작업이 조용히 실패하지 않게 먼저 막는다.

    업그레이드는 운영자의 ``config/llm.yaml`` 을 덮지 않아 새 역할이 빠질 수 있다(Phase 19 발견,
    ``ontobotctl`` 이 알린다).
    """
    if EMBEDDER_ROLE not in load_llm_config(settings.llm_config_path).roles:
        raise ProblemError(
            409,
            "임베딩 모델이 설정되지 않아 문서를 색인할 수 없습니다"
            "(config/llm.yaml 의 embedder 역할)",
            code="embedder_not_configured",
        )


@router.post("", response_model=DocumentOut, status_code=201)
async def upload(
    db: Db,
    user: Curator,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
    group_id: Annotated[int, Form()],
    title: Annotated[str | None, Form(max_length=255)] = None,
) -> DocumentOut:
    if group_id not in await rbac.document_groups(db, user.id):
        raise ProblemError(403, "속한 그룹에만 문서를 넣을 수 있습니다", code="forbidden_group")
    _require_embedder(settings)
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
async def reindex(doc_id: int, db: Db, user: Curator, settings: SettingsDep) -> DocumentOut:
    doc = await _readable(db, user, doc_id)
    _require_embedder(settings)
    await store.reindex(db, doc, actor_id=user.id)
    return _out(await store.view(db, doc))


class DocumentLinkOut(BaseModel):
    id: int
    object_type: str
    type_display_name: str
    object_pk: str
    title: str
    match_kind: Literal["title", "pk"]
    match_text: str
    occurrences: int
    first_chunk_no: int
    status: Literal["candidate", "confirmed", "rejected"]
    decided_at: datetime | None


class LinkScanOut(BaseModel):
    found: int
    added: int
    removed: int
    scanned_objects: int
    truncated: bool


class LinkDecisionIn(BaseModel):
    status: Literal["candidate", "confirmed", "rejected"]


def _schema() -> registry.PublishedSchema:
    schema = registry.current()
    if schema is None:
        raise ProblemError(409, "게시된 온톨로지가 없습니다", code="no_published_ontology")
    return schema


@router.get("/{doc_id}/links", response_model=list[DocumentLinkOut])
async def document_links(doc_id: int, db: Db, user: User) -> list[DocumentLinkOut]:
    doc = await _readable(db, user, doc_id)
    schema = registry.current()
    if schema is None:
        return []
    rows = await links.listing(db, doc.id, schema.document, await rbac.access(db, user.id))
    curator = rbac.has_role(user.roles, ROLE_MODELER, ROLE_ADMIN)
    return [
        DocumentLinkOut.model_validate(r, from_attributes=True)
        for r in rows
        if curator or r.status == "confirmed"
    ]


@router.post("/{doc_id}/links/scan", response_model=LinkScanOut)
async def scan_links(doc_id: int, db: Db, user: Curator) -> LinkScanOut:
    """본문에서 객체 제목·pk 를 그대로 찾아 후보로 둔다(찾는 사람의 권한으로)."""
    doc = await _readable(db, user, doc_id)
    schema = _schema()
    access = await rbac.access(db, user.id)
    res = await links.scan(db, doc, schema.document.active(), access, actor_id=user.id)
    return LinkScanOut.model_validate(res, from_attributes=True)


@router.put("/{doc_id}/links/{link_id}", status_code=204)
async def decide_link(
    doc_id: int, link_id: int, body: LinkDecisionIn, db: Db, user: Curator
) -> Response:
    doc = await _readable(db, user, doc_id)
    schema = _schema()
    try:
        await links.decide(
            db,
            doc.id,
            link_id,
            body.status,
            schema.document,
            await rbac.access(db, user.id),
            actor_id=user.id,
        )
    except links.NotFound as e:
        raise ProblemError(404, "링크를 찾을 수 없습니다", code="link_not_found") from e
    return Response(status_code=204)
