"""36.15 합성 예시 데이터(TRD 17.17 결정 A10, Phase 22) — 타입마다 CSV 하나를 도입에 올려 둔다.

올린 CSV 는 게시본 설계도로 검사한다 — 워크숍 시연(``workshop.demo.parse_rows``)과 같은 규칙이다:
첫 줄은 속성 이름(``api_name``), 정의에 없는 열·pk 빈칸·겹침·자료형이 안 맞는 값이 하나라도 있으면
그 표 전체를 받지 않는다. 운영 ``object`` 표에는 넣지 않는다(자산화 zip 의
``seed/<타입 소문자>.csv`` — ``scripts/seed_domain_pack.py`` 가 읽는 이름이다).

실제 고객 데이터가 아님을 확인하는 것은 사람이다. 올리는 사람이 "실제 값을 바꾼 합성 데이터"라고
밝혀야 하고, 사람 정보(``pii_kind``) 속성 열이 있으면 결과에 그 열을 알려 경고한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.adoption import EngagementSeed
from ontobot.ontology import versioning
from ontobot.ontology.models import OntologyDocument
from ontobot.security import audit
from ontobot.workshop.demo import RowsInvalid, parse_rows


class SeedError(Exception):
    def __init__(self, message: str, *, code: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.errors = errors or []


@dataclass(frozen=True)
class SeedView:
    object_type: str
    rows: int
    pii_columns: list[str]
    updated_at: datetime | None


def file_name(object_type: str) -> str:
    """``seed_domain_pack.py`` 의 이름 규칙(Facility → facility.csv)."""
    return f"{object_type.lower()}.csv"


async def published_document(db: AsyncSession) -> OntologyDocument:
    vid = await versioning.published_version_id(db)
    if vid is None:
        raise SeedError("게시된 설계도가 없습니다", code="no_published_ontology")
    return versioning.to_document(await versioning.get_version(db, vid))


def check(doc: OntologyDocument, object_type: str, csv_text: str) -> tuple[int, list[str]]:
    """→ (행 수, 사람 정보 열). 정의와 맞지 않으면 ``SeedError``(문제를 모두 모아)."""
    try:
        rows = parse_rows(doc, object_type, csv_text)
    except RowsInvalid as e:
        raise SeedError(
            "CSV 가 설계도와 맞지 않습니다", code="seed_invalid", errors=e.errors
        ) from e
    t = doc.object_type(object_type)
    header = set(rows[0][1]) if rows else set()
    pii = [p.api_name for p in (t.properties if t else []) if p.pii_kind and p.api_name in header]
    return len(rows), pii


async def upload(
    db: AsyncSession,
    engagement_id: int,
    object_type: str,
    csv_text: str,
    *,
    actor_id: int,
    synthetic: bool,
) -> SeedView:
    if not synthetic:
        raise SeedError(
            "실제 값을 바꾼 합성 데이터라는 확인이 필요합니다 — 실제 고객 데이터는 올리지 않습니다",
            code="synthetic_not_confirmed",
        )
    doc = await published_document(db)
    rows, pii = check(doc, object_type, csv_text)
    text = csv_text.strip() + "\n"
    await db.execute(
        insert(EngagementSeed)
        .values(
            engagement_id=engagement_id,
            object_type=object_type,
            content=text,
            rows=rows,
            uploaded_by=actor_id,
        )
        .on_conflict_do_update(
            index_elements=["engagement_id", "object_type"],
            set_={"content": text, "rows": rows, "uploaded_by": actor_id, "updated_at": func.now()},
        )
    )
    audit.record(
        db,
        audit.ENGAGEMENT_SEED_CHANGED,
        actor_id=actor_id,
        target=f"engagement:{engagement_id}",
        payload={"object_type": object_type, "rows": rows, "pii_columns": pii},
    )
    await db.flush()
    return SeedView(object_type, rows, pii, None)


async def remove(db: AsyncSession, engagement_id: int, object_type: str, *, actor_id: int) -> bool:
    result = await db.execute(
        delete(EngagementSeed).where(
            EngagementSeed.engagement_id == engagement_id, EngagementSeed.object_type == object_type
        )
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        return False
    audit.record(
        db,
        audit.ENGAGEMENT_SEED_CHANGED,
        actor_id=actor_id,
        target=f"engagement:{engagement_id}",
        payload={"object_type": object_type, "rows": None},
    )
    await db.flush()
    return True


async def stored(db: AsyncSession, engagement_id: int) -> list[EngagementSeed]:
    return list(
        await db.scalars(
            select(EngagementSeed)
            .where(EngagementSeed.engagement_id == engagement_id)
            .order_by(EngagementSeed.object_type)
        )
    )


async def views(db: AsyncSession, engagement_id: int) -> list[SeedView]:
    """올린 CSV — 지금 게시본으로 다시 본 사람 정보 열과 함께(설계도가 바뀌었을 수 있다)."""
    out = []
    try:
        doc: OntologyDocument | None = await published_document(db)
    except SeedError:
        doc = None
    for s in await stored(db, engagement_id):
        t = doc.object_type(s.object_type) if doc else None
        header = s.content.splitlines()[0].split(",") if s.content else []
        pii = [
            p.api_name
            for p in (t.properties if t else [])
            if p.pii_kind and p.api_name in {h.strip() for h in header}
        ]
        out.append(SeedView(s.object_type, s.rows, pii, s.updated_at))
    return out
