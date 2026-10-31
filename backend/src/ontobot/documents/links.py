"""문서 ↔ 객체 링크 후보 찾기·확정(Phase 16c, PRD 5.1 P1).

- **후보 찾기는 찾는 사람의 권한으로** 한다: 읽을 수 있는 타입, 행 정책 안의 객체만 이름표에 넣는다
  (``rbac.access`` 결과). 제목 속성이 PII 인 타입은 제목으로 찾지 않는다(pk 로만).
- 다시 찾으면 확정·거부한 링크의 상태는 그대로 두고(거부한 것은 되살리지 않는다), 더는 맞지
  않는 **후보**만 지운다 — 찾은 사람이 읽을 수 있는 타입 안에서만(다른 사람이 볼 수 있는 후보를
  지우지 않는다).
- 보여 줄 때도 보는 사람의 권한으로 거른다: 읽을 수 없는 타입·행 정책 밖·지금 없는 객체의 링크는
  목록에 나오지 않는다(존재도 정보다). 제목은 ``objects.reader`` 로 다시 읽는다.
- 감사 기록은 오래 걸리는 찾기가 끝난 뒤에 넣는다(체인 잠금).
"""

from __future__ import annotations

from collections.abc import Set
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.documents import Document, DocumentChunk, DocumentLink
from ontobot.db.models.security import DataGroup
from ontobot.documents import matching
from ontobot.objects.reader import iter_titles, resolve
from ontobot.ontology.models import OntologyDocument
from ontobot.query.executor import in_scope
from ontobot.query.validator import Scope, check_scopes
from ontobot.security import audit
from ontobot.security.rbac import Access

# 한 번 찾기에서 이름표에 넣는 객체 수 상한. 넘으면 결과에 ``truncated`` 로 밝힌다.
MAX_NAMES = 50_000

Status = Literal["candidate", "confirmed", "rejected"]


class NotFound(Exception):
    pass


@dataclass(frozen=True)
class ScanResult:
    found: int
    added: int
    removed: int
    scanned_objects: int
    truncated: bool


@dataclass(frozen=True)
class LinkView:
    id: int
    object_type: str
    type_display_name: str
    object_pk: str
    title: str
    match_kind: str
    match_text: str
    occurrences: int
    first_chunk_no: int
    status: str
    decided_at: datetime | None


@dataclass(frozen=True)
class RelatedDocument:
    document_id: int
    title: str
    group_name: str
    match_text: str
    first_chunk_no: int


def _scopes(doc: OntologyDocument, access: Access) -> dict[str, Scope]:
    return check_scopes(doc, access.read_scopes())


async def _names(
    db: AsyncSession, doc: OntologyDocument, access: Access
) -> tuple[list[matching.Name], bool]:
    scopes = _scopes(doc, access)
    names: list[matching.Name] = []
    for t in doc.object_types:
        if t.api_name not in access.readable:
            continue
        title_prop = t.property(t.title_property)
        title_key = None if title_prop is None or title_prop.pii_kind else t.title_property
        async for batch in iter_titles(db, t.api_name, title_key):
            allowed = await in_scope(
                db, t.api_name, [pk for pk, _ in batch], scopes.get(t.api_name)
            )
            for pk, title in batch:
                if pk in allowed:
                    names.append(matching.Name(t.api_name, pk, title))
                    if len(names) >= MAX_NAMES:
                        return names, True
    return names, False


async def scan(
    db: AsyncSession, document: Document, doc: OntologyDocument, access: Access, *, actor_id: int
) -> ScanResult:
    names, truncated = await _names(db, doc, access)
    matches = matching.find(document.text, names)
    starts = list(
        await db.scalars(
            select(DocumentChunk.start_char)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_no)
        )
    )
    existing = {
        (link.object_type, link.object_pk): link
        for link in await db.scalars(
            select(DocumentLink).where(DocumentLink.document_id == document.id)
        )
    }
    added = 0
    seen: set[tuple[str, str]] = set()
    for m in matches:
        key = (m.object_type, m.pk)
        seen.add(key)
        fields = {
            "match_kind": m.kind,
            "match_text": m.text,
            "occurrences": m.occurrences,
            "first_chunk_no": matching.chunk_of(starts, m.first_start),
        }
        link = existing.get(key)
        if link is None:
            db.add(
                DocumentLink(
                    document_id=document.id, object_type=key[0], object_pk=key[1], **fields
                )
            )
            added += 1
        else:
            for k, v in fields.items():
                setattr(link, k, v)
    scanned = {t.api_name for t in doc.object_types if t.api_name in access.readable}
    stale = [
        link.id
        for key, link in existing.items()
        if key not in seen and link.status == "candidate" and key[0] in scanned
    ]
    if stale and not truncated:  # 다 못 봤으면 "더는 안 맞는다"고 말할 수 없다
        await db.execute(delete(DocumentLink).where(DocumentLink.id.in_(stale)))
    result = ScanResult(
        found=len(matches),
        added=added,
        removed=0 if truncated else len(stale),
        scanned_objects=len(names),
        truncated=truncated,
    )
    audit.record(
        db,
        audit.DOCUMENT_LINKS_SCANNED,
        actor_id=actor_id,
        target=f"document:{document.id}",
        payload={"found": result.found, "added": added, "removed": result.removed},
    )
    await db.flush()
    return result


async def _visible(
    db: AsyncSession, doc: OntologyDocument, access: Access, links: list[DocumentLink]
) -> dict[int, str]:
    """보는 사람이 볼 수 있는 링크 id → 객체 제목."""
    scopes = _scopes(doc, access)
    by_type: dict[str, list[DocumentLink]] = {}
    for link in links:
        if link.object_type in access.readable and doc.object_type(link.object_type) is not None:
            by_type.setdefault(link.object_type, []).append(link)
    out: dict[int, str] = {}
    for object_type, mine in by_type.items():
        t = doc.object_type(object_type)
        if t is None:  # 위에서 걸렀다
            continue
        pks = [link.object_pk for link in mine]
        allowed = await in_scope(db, object_type, pks, scopes.get(object_type))
        found = await resolve(
            db, object_type, [p for p in pks if p in allowed], declared=[t.title_property]
        )
        for link in mine:
            row = found.get(link.object_pk)
            if row is not None:
                out[link.id] = str(row.properties.get(t.title_property) or link.object_pk)
    return out


async def listing(
    db: AsyncSession, document_id: int, doc: OntologyDocument, access: Access
) -> list[LinkView]:
    links = list(
        await db.scalars(
            select(DocumentLink)
            .where(DocumentLink.document_id == document_id)
            .order_by(DocumentLink.first_chunk_no, DocumentLink.id)
        )
    )
    titles = await _visible(db, doc, access, links)
    out = []
    for link in links:
        if link.id not in titles:
            continue
        t = doc.object_type(link.object_type)
        out.append(
            LinkView(
                id=link.id,
                object_type=link.object_type,
                type_display_name=t.display_name if t else link.object_type,
                object_pk=link.object_pk,
                title=titles[link.id],
                match_kind=link.match_kind,
                match_text=link.match_text,
                occurrences=link.occurrences,
                first_chunk_no=link.first_chunk_no,
                status=link.status,
                decided_at=link.decided_at,
            )
        )
    return out


async def decide(
    db: AsyncSession,
    document_id: int,
    link_id: int,
    status: Status,
    doc: OntologyDocument,
    access: Access,
    *,
    actor_id: int,
) -> None:
    """확정·거부·후보로 되돌리기. 볼 수 없는 링크는 없는 링크와 같다."""
    link = await db.get(DocumentLink, link_id)
    if link is None or link.document_id != document_id:
        raise NotFound(link_id)
    if link.id not in await _visible(db, doc, access, [link]):
        raise NotFound(link_id)
    if link.status == status:
        return
    before = link.status
    link.status = status
    link.decided_by, link.decided_at = (
        (None, None) if status == "candidate" else (actor_id, datetime.now(UTC))
    )
    audit.record(
        db,
        audit.DOCUMENT_LINK_DECIDED,
        actor_id=actor_id,
        target=f"document:{document_id}",
        payload={
            "link_id": link.id,
            "object": f"{link.object_type}/{link.object_pk}",
            "from": before,
            "to": status,
        },
    )
    await db.flush()


async def related_documents(
    db: AsyncSession, object_type: str, pk: str, groups: Set[int]
) -> list[RelatedDocument]:
    """객체에 **확정된** 링크가 있는 문서 중 보는 사람이 속한 그룹의 것.

    객체를 볼 수 있는지는 호출부가 본다."""
    if not groups:
        return []
    rows = await db.execute(
        select(
            Document.id,
            Document.title,
            DataGroup.display_name,
            DocumentLink.match_text,
            DocumentLink.first_chunk_no,
        )
        .join(Document, Document.id == DocumentLink.document_id)
        .join(DataGroup, DataGroup.id == Document.group_id)
        .where(
            DocumentLink.object_type == object_type,
            DocumentLink.object_pk == pk,
            DocumentLink.status == "confirmed",
            Document.group_id.in_(list(groups)),
        )
        .order_by(Document.id.desc())
    )
    return [RelatedDocument(i, t, g, m, c) for i, t, g, m, c in rows]
