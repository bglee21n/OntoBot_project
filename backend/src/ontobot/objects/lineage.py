"""값 계보(PRD 5.0 P1, TRD 17절) — 근거 카드의 값이 어디서 왔는가.

속성 값마다 둘 중 하나다:

- **편집**: 활성 편집이 있으면 그 값이다(누가·언제·어느 Action 실행, 편집 당시 원천 값, 충돌).
- **원천**: 아니면 객체를 마지막으로 넣은 원천(데이터 소스 → 매핑 → 동기화 실행)이다. 원천이 없는
  객체(시드·직접 적재)는 ``source`` 가 None 이다.

객체 행 하나가 원천 한 곳에서 오므로(MVP 단일 소스) 원천 계보는 객체 단위로 한 번 구한다.
실행은 ``synced_at`` 을 찍은 성공 실행 — 병합이 실행 시작 뒤에 시각을 찍으므로, 그 원천·타입의
매핑 실행 가운데 그 시각 이전에 시작한 가장 늦은 성공 실행이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.objects.edits import EditInfo, active_edits


@dataclass(frozen=True)
class SourceLineage:
    data_source_id: int
    data_source_name: str
    mapping_id: int | None
    origin: str | None  # 매핑의 표 이름, 또는 질의면 "query"
    sync_run_id: int | None
    run_finished_at: datetime | None
    synced_at: datetime | None


@dataclass(frozen=True)
class ObjectLineage:
    source: SourceLineage | None
    edits: dict[str, EditInfo]


async def object_lineage(db: AsyncSession, object_type: str, pk: str) -> ObjectLineage:
    row = (
        await db.execute(
            text(
                "SELECT o.source_id, o.synced_at, s.name AS source_name, x.mapping_id, x.origin, "
                "x.run_id, x.finished_at FROM object o "
                "LEFT JOIN data_source s ON s.id = o.source_id "
                "LEFT JOIN LATERAL (SELECT r.id AS run_id, r.finished_at, m.id AS mapping_id, "
                "COALESCE(m.\"table\", 'query') AS origin FROM sync_run r "
                "JOIN mapping m ON m.id = r.mapping_id "
                "WHERE m.source_id = o.source_id AND m.object_type = o.type "
                "AND r.status = 'succeeded' AND r.started_at <= o.synced_at "
                "ORDER BY r.started_at DESC LIMIT 1) x ON true "
                "WHERE o.type = :t AND o.pk = :pk"
            ),
            {"t": object_type, "pk": pk},
        )
    ).first()
    source = None
    if row is not None and row.source_id is not None:
        source = SourceLineage(
            row.source_id,
            row.source_name,
            row.mapping_id,
            row.origin,
            row.run_id,
            row.finished_at,
            row.synced_at,
        )
    edits = (await active_edits(db, object_type, [pk])).get(pk, {})
    return ObjectLineage(source, edits)
