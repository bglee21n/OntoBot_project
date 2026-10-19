"""현재 게시 버전 캐시(TRD 6.1 읽기 경로).

읽기는 잠금 없이 참조 하나를 읽는다. 갱신은 새 스냅샷을 다 만든 뒤 참조를 **한 번에** 바꾼다 —
읽는 쪽이 반쯤 바뀐 스키마를 보는 일이 없다.

갱신 시점은 두 곳뿐이다: 앱 기동(lifespan)과 게시 직후. 게시는 커밋 **뒤에** 다시 읽어야 한다
— 커밋 전에 읽으면 롤백된 버전이 캐시에 남는다(라우터의 publish 가 그렇게 한다).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.ontology.models import OntologyDocument
from ontobot.ontology.versioning import get_version, published_version_id, to_document


@dataclass(frozen=True)
class PublishedSchema:
    version_id: int
    version_no: int
    content_hash: str
    document: OntologyDocument


_current: PublishedSchema | None = None


def current() -> PublishedSchema | None:
    return _current


async def reload(db: AsyncSession) -> PublishedSchema | None:
    global _current
    vid = await published_version_id(db)
    if vid is None:
        _current = None
        return None
    version = await get_version(db, vid)
    doc = to_document(version)
    snapshot = PublishedSchema(
        version_id=version.id,
        version_no=version.version_no,
        content_hash=version.content_hash or doc.content_hash(),
        document=doc,
    )
    _current = snapshot
    return snapshot


def reset() -> None:
    """테스트용 — 다른 DB로 바꾼 뒤 남은 캐시를 지운다."""
    global _current
    _current = None
