"""아웃박스 → Neo4j 투영(TRD 5.2).

- 이벤트는 ``id`` 순서로 소비한다. 묶음 하나 = Neo4j 트랜잭션 하나 = PostgreSQL 처리 표시 하나.
  Neo4j 반영이 실패하면 처리 표시도 롤백되어 같은 묶음을 다음에 다시 반영한다(모든 문장은 MERGE·
  MATCH 라 두 번 반영해도 결과가 같다).
- 투영기는 한 번에 하나만 돈다(advisory lock). 둘이 돌면 순서가 섞인다.
- 노드에는 ``pk``·``title``·``stub`` 만 둔다. 속성 값·PII·권한은 두지 않는다(TRD 5.1).
- 링크 끝점 객체가 아직 없으면 ``stub`` 노드를 만든다. 그 객체가 나중에 동기화되면 실제 노드가 된다.
  남아 있는 stub 은 대사가 "참조 대상 없음"으로 드러낸다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.graph.client import AsyncDriver, Statement, label, rel, run_schema, write
from ontobot.ontology.models import OntologyDocument

log = logging.getLogger(__name__)

BATCH = 500
_PROJECTOR_LOCK = 0x0B72_0001


def statement(event: str, p: dict[str, Any]) -> Statement:
    if event == "node_upsert":
        return (
            f"MERGE (n:{label(p['type'])} {{pk: $pk}}) SET n.title = $title, n.stub = false",
            {"pk": p["pk"], "title": p.get("title")},
        )
    if event == "node_delete":
        return (f"MATCH (n:{label(p['type'])} {{pk: $pk}}) DETACH DELETE n", {"pk": p["pk"]})
    if event == "link_upsert":
        return (
            f"MERGE (a:{label(p['from_type'])} {{pk: $f}}) ON CREATE SET a.stub = true "
            f"MERGE (b:{label(p['to_type'])} {{pk: $t}}) ON CREATE SET b.stub = true "
            f"MERGE (a)-[:{rel(p['link_type'])}]->(b)",
            {"f": p["from_pk"], "t": p["to_pk"]},
        )
    if event == "link_delete":
        return (
            f"MATCH (:{label(p['from_type'])} {{pk: $f}})-[r:{rel(p['link_type'])}]->"
            f"(:{label(p['to_type'])} {{pk: $t}}) DELETE r",
            {"f": p["from_pk"], "t": p["to_pk"]},
        )
    raise ValueError(f"알 수 없는 아웃박스 이벤트: {event}")


async def ensure_constraints(driver: AsyncDriver, doc: OntologyDocument) -> None:
    """타입마다 pk 유일 제약 — MERGE 가 같은 노드를 둘로 만들지 않게 하고, pk 조회를 빠르게 한다."""
    for t in doc.object_types:
        await run_schema(
            driver,
            f"CREATE CONSTRAINT {rel('pk_' + t.api_name.lower())} IF NOT EXISTS "
            f"FOR (n:{label(t.api_name)}) REQUIRE n.pk IS UNIQUE",
        )


async def project_pending(db: AsyncSession, driver: AsyncDriver, *, batch: int = BATCH) -> int:
    """미처리 이벤트를 한 묶음 반영한다. 반영한 개수를 돌려준다(0 이면 밀린 것이 없다)."""
    got = await db.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _PROJECTOR_LOCK})
    if not got:
        return 0  # 다른 투영기가 돌고 있다
    rows = (
        await db.execute(
            text(
                "SELECT id, event, payload FROM graph_outbox WHERE processed_at IS NULL "
                "ORDER BY id LIMIT :n"
            ),
            {"n": batch},
        )
    ).all()
    if not rows:
        await db.commit()
        return 0
    await write(driver, [statement(r.event, r.payload) for r in rows])
    await db.execute(
        text("UPDATE graph_outbox SET processed_at = :now WHERE id = ANY(:ids)"),
        {"now": datetime.now(UTC), "ids": [r.id for r in rows]},
    )
    await db.commit()
    return len(rows)


async def project_all(db: AsyncSession, driver: AsyncDriver) -> int:
    total = 0
    while (n := await project_pending(db, driver)) > 0:
        total += n
    return total


async def lag(db: AsyncSession) -> dict[str, Any]:
    """반영 지연(TRD 5.2): 가장 오래된 미처리 이벤트의 나이."""
    row = (
        await db.execute(
            text(
                "SELECT count(*) AS pending, min(created_at) AS oldest, "
                "(SELECT max(processed_at) FROM graph_outbox) AS last_processed "
                "FROM graph_outbox WHERE processed_at IS NULL"
            )
        )
    ).one()
    now = datetime.now(UTC)
    return {
        "pending_events": int(row.pending),
        "projection_lag_seconds": int((now - row.oldest).total_seconds()) if row.oldest else 0,
        "oldest_pending_at": row.oldest.isoformat() if row.oldest else None,
        "last_processed_at": row.last_processed.isoformat() if row.last_processed else None,
    }
