"""객체·링크 쓰기 — upsert · 삭제 표시 · 스테이징 병합(TRD 6.2, 11절).

서비스용 읽기는 하지 않는다(그것은 ``reader`` 의 일 — 투영·편집 레이어가 거기서 덮인다). 여기서
``object``·``link`` 를 읽는 것은 **병합 내부의 변경 판별**뿐이다(row_hash 비교, 바뀐 링크 찾기).

스테이징 병합은 ``sync.runner`` 가 한 트랜잭션 안에서 순서대로 부른다:
``stage`` → ``changed_pks`` → ``upsert_staged`` → (전체 모드) ``mark_missing_deleted`` ·
``delete_links_touching`` → 링크 규칙마다 ``sync_fk_links``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row, text
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


# ---------------------------------------------------------------- 스테이징 병합


async def stage(db: AsyncSession, rows: list[tuple[str, dict[str, Any], str | None]]) -> None:
    """``(pk, 속성, 그래프 제목)`` 을 트랜잭션 임시 표에 올린다(커밋·롤백 때 사라진다)."""
    await db.execute(
        text(
            "CREATE TEMP TABLE sync_stage (pk text PRIMARY KEY, properties jsonb NOT NULL, "
            "row_hash text NOT NULL, title text) ON COMMIT DROP"
        )
    )
    if rows:
        await db.execute(
            text(
                "INSERT INTO sync_stage (pk, properties, row_hash, title) "
                "VALUES (:pk, CAST(:props AS jsonb), :h, :title)"
            ),
            [
                {
                    "pk": pk,
                    "props": json.dumps(props, ensure_ascii=False),
                    "h": row_hash(props),
                    "title": title,
                }
                for pk, props, title in rows
            ],
        )


async def changed_pks(db: AsyncSession, object_type: str) -> list[str]:
    """새로 생겼거나, 내용이 달라졌거나, 삭제 표시였다가 되살아난 행."""
    return list(
        await db.scalars(
            text(
                "SELECT s.pk FROM sync_stage s LEFT JOIN object o ON o.type = :t AND o.pk = s.pk "
                "WHERE o.pk IS NULL OR o.row_hash IS DISTINCT FROM s.row_hash "
                "OR o.deleted_at IS NOT NULL ORDER BY s.pk"
            ),
            {"t": object_type},
        )
    )


async def staged_titles(db: AsyncSession, pks: list[str]) -> list[Row[Any]]:
    result = await db.execute(
        text("SELECT pk, title FROM sync_stage WHERE pk = ANY(:pks) ORDER BY pk"), {"pks": pks}
    )
    return list(result.all())


async def upsert_staged(db: AsyncSession, object_type: str, source_id: int, now: datetime) -> None:
    """모든 스테이징 행을 반영한다.

    바뀌지 않은 행도 synced_at 은 갱신한다 — 근거 카드의 '마지막 동기화 시각'은 마지막으로
    **확인한** 시각이어야 한다.
    """
    await db.execute(
        text(
            "INSERT INTO object (type, pk, properties, row_hash, source_id, synced_at, deleted_at) "
            "SELECT :t, pk, properties, row_hash, :sid, :now, NULL FROM sync_stage "
            "ON CONFLICT (type, pk) DO UPDATE SET properties = EXCLUDED.properties, "
            "row_hash = EXCLUDED.row_hash, source_id = EXCLUDED.source_id, "
            "synced_at = EXCLUDED.synced_at, deleted_at = NULL"
        ),
        {"t": object_type, "sid": source_id, "now": now},
    )


async def mark_missing_deleted(
    db: AsyncSession, object_type: str, source_id: int, now: datetime
) -> list[str]:
    """전체 동기화: 이 원천이 넣었는데 이번에 오지 않은 객체를 삭제 표시한다.

    다른 원천·시드가 넣은 객체는 건드리지 않는다(``source_id`` 로 가른다).
    """
    return list(
        await db.scalars(
            text(
                "UPDATE object SET deleted_at = :now WHERE type = :t AND source_id = :sid "
                "AND deleted_at IS NULL AND pk NOT IN (SELECT pk FROM sync_stage) RETURNING pk"
            ),
            {"t": object_type, "sid": source_id, "now": now},
        )
    )


async def delete_links_touching(
    db: AsyncSession, outgoing: list[str], incoming: list[str], pks: list[str], now: datetime
) -> list[Row[Any]]:
    """삭제된 객체에서 나가거나 들어오는 링크를 삭제 표시한다."""
    result = await db.execute(
        text(
            "UPDATE link SET deleted_at = :now WHERE deleted_at IS NULL AND ("
            "(link_type = ANY(:out) AND from_pk = ANY(:pks)) OR "
            "(link_type = ANY(:inc) AND to_pk = ANY(:pks))) RETURNING link_type, from_pk, to_pk"
        ),
        {"now": now, "out": outgoing, "inc": incoming, "pks": pks},
    )
    return list(result.all())


async def sync_fk_links(
    db: AsyncSession, link_type: str, fk_property: str, source_id: int, now: datetime
) -> tuple[list[Row[Any]], list[Row[Any]]]:
    """스테이징 행의 FK 속성 값으로 링크를 맞춘다. (지운 링크, 새로 만든 링크)."""
    p = {"l": link_type, "p": fk_property, "sid": source_id, "now": now}
    stale = await db.execute(
        text(
            "UPDATE link l SET deleted_at = :now FROM sync_stage s "
            "WHERE l.link_type = :l AND l.deleted_at IS NULL AND l.from_pk = s.pk "
            "AND (s.properties ->> CAST(:p AS text) IS NULL "
            "OR l.to_pk <> s.properties ->> CAST(:p AS text)) "
            "RETURNING l.link_type, l.from_pk, l.to_pk"
        ),
        p,
    )
    stale_rows = list(stale.all())
    added = await db.execute(
        text(
            "INSERT INTO link (link_type, from_pk, to_pk, source_id, synced_at) "
            "SELECT :l, s.pk, s.properties ->> CAST(:p AS text), :sid, :now FROM sync_stage s "
            "WHERE s.properties ->> CAST(:p AS text) IS NOT NULL "
            "ON CONFLICT (link_type, from_pk, to_pk) WHERE deleted_at IS NULL DO NOTHING "
            "RETURNING link_type, from_pk, to_pk"
        ),
        p,
    )
    return stale_rows, list(added.all())
