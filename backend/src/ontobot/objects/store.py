"""객체 쓰기 — upsert · 삭제 표시(TRD 6.2). 읽기는 하지 않는다(그것은 ``reader`` 의 일).

Phase 2 의 동기화 실행기(sync.runner)가 이 함수를 스테이징 병합 단계에서 부른다. 스파이크는
시드 스크립트가 직접 부른다. 링크·그래프 아웃박스는 Phase 2 에서 같은 트랜잭션에 더한다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.objects import ObjectRecord


def row_hash(properties: Mapping[str, Any]) -> str:
    """전체 동기화가 '바뀐 행'을 가리는 기준(TRD 11절). 키 순서와 무관하다."""
    canonical = json.dumps(properties, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def upsert_objects(
    db: AsyncSession,
    object_type: str,
    rows: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    source_id: int | None = None,
    synced_at: datetime | None = None,
) -> int:
    """``(pk, properties)`` 를 upsert 한다.

    내용이 같은 행은 건드리지 않는다. 바뀐 행 수를 돌려준다.
    """
    now = synced_at or datetime.now(UTC)
    values = [
        {
            "type": object_type,
            "pk": pk,
            "properties": dict(props),
            "row_hash": row_hash(props),
            "source_id": source_id,
            "synced_at": now,
            "deleted_at": None,
        }
        for pk, props in rows
    ]
    if not values:
        return 0
    stmt = insert(ObjectRecord).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[ObjectRecord.type, ObjectRecord.pk],
        set_={
            "properties": stmt.excluded.properties,
            "row_hash": stmt.excluded.row_hash,
            "source_id": stmt.excluded.source_id,
            "synced_at": stmt.excluded.synced_at,
            "deleted_at": None,
        },
        # 내용이 같고 살아 있는 행은 갱신하지 않는다 — synced_at 만 바뀌는 쓰기를 줄인다.
        where=(ObjectRecord.row_hash != stmt.excluded.row_hash)
        | ObjectRecord.deleted_at.is_not(None),
    )
    result = await db.execute(stmt)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
