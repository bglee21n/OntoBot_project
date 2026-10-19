"""대사·재구축(TRD 5.5) — PostgreSQL 이 원본, Neo4j 는 언제든 다시 만들 수 있는 투영이다.

대사: 타입·링크 타입마다 **건수와 키 해시**를 양쪽에서 계산해 비교한다. 건수만 보면 "하나 빠지고
하나 더 생긴" 어긋남을 놓친다. 키는 바이트 순서로 정렬해 이어 붙인다(양쪽 정렬 규칙을 맞춘다).

재구축: Neo4j 를 비우고 PostgreSQL 의 살아 있는 객체·링크를 다시 넣은 뒤, 시작 시점까지의
아웃박스 이벤트를 처리됨으로 표시한다. 재구축 중에 쌓인 이벤트는 그 뒤에 투영기가 반영한다
(MERGE 라 겹쳐도 결과가 같다).
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.graph.client import AsyncDriver, label, read, rel, write
from ontobot.graph.projector import ensure_constraints
from ontobot.objects import reader
from ontobot.ontology.models import OntologyDocument

SEP = "\x1f"
CHUNK = 2000


def digest(keys: list[str]) -> str:
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()


@dataclass
class Row:
    kind: str  # object_type / link_type
    name: str
    pg_count: int
    graph_count: int
    pg_hash: str
    graph_hash: str

    @property
    def ok(self) -> bool:
        return self.pg_count == self.graph_count and self.pg_hash == self.graph_hash


@dataclass
class Report:
    checked_at: str
    rows: list[Row] = field(default_factory=list)
    stub_nodes: int = 0  # 링크가 가리키는데 객체가 없는 노드(참조 대상 없음)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.rows)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "ok": self.ok,
            "stub_nodes": self.stub_nodes,
            "rows": [{**asdict(r), "ok": r.ok} for r in self.rows],
        }


async def reconcile(db: AsyncSession, driver: AsyncDriver, doc: OntologyDocument) -> Report:
    report = Report(checked_at=datetime.now(UTC).isoformat())
    for t in doc.object_types:
        pg = await reader.object_keys(db, t.api_name)
        g = sorted(
            r["pk"]
            for r in await read(
                driver, f"MATCH (n:{label(t.api_name)}) WHERE n.stub = false RETURN n.pk AS pk"
            )
        )
        report.rows.append(Row("object_type", t.api_name, len(pg), len(g), digest(pg), digest(g)))
    for lt in doc.link_types:
        pg = await reader.link_keys(db, lt.api_name)
        g = sorted(
            f"{r['f']}{SEP}{r['t']}"
            for r in await read(
                driver,
                f"MATCH (a:{label(lt.from_type)})-[:{rel(lt.api_name)}]->(b:{label(lt.to_type)}) "
                "RETURN a.pk AS f, b.pk AS t",
            )
        )
        report.rows.append(Row("link_type", lt.api_name, len(pg), len(g), digest(pg), digest(g)))
    stubs = await read(driver, "MATCH (n) WHERE n.stub = true RETURN count(n) AS c")
    report.stub_nodes = int(stubs[0]["c"]) if stubs else 0
    return report


async def rebuild(db: AsyncSession, driver: AsyncDriver, doc: OntologyDocument) -> dict[str, int]:
    upto = int(await db.scalar(text("SELECT coalesce(max(id), 0) FROM graph_outbox")) or 0)
    # 1) 비우기 — 큰 그래프에서 한 트랜잭션으로 지우면 메모리가 터지므로 나눠 지운다.
    while int((await read(driver, "MATCH (n) RETURN count(n) AS c"))[0]["c"]) > 0:
        await write(driver, [("MATCH (n) WITH n LIMIT 10000 DETACH DELETE n", {})])
    await ensure_constraints(driver, doc)

    counts = {"nodes": 0, "links": 0}
    # 2) 노드
    for t in doc.object_types:
        title_prop = t.property(t.title_property)
        title = t.title_property if title_prop is not None and title_prop.pii_kind is None else None
        async for chunk in reader.iter_titles(db, t.api_name, title, chunk=CHUNK):
            await write(
                driver,
                [
                    (
                        f"UNWIND $rows AS r MERGE (n:{label(t.api_name)} {{pk: r.pk}}) "
                        "SET n.title = r.title, n.stub = false",
                        {"rows": [{"pk": pk, "title": tt} for pk, tt in chunk]},
                    )
                ],
            )
            counts["nodes"] += len(chunk)
    # 3) 링크
    for lt in doc.link_types:
        async for links in reader.iter_links(db, lt.api_name, chunk=CHUNK):
            await write(
                driver,
                [
                    (
                        f"UNWIND $rows AS r "
                        f"MERGE (a:{label(lt.from_type)} {{pk: r.f}}) ON CREATE SET a.stub = true "
                        f"MERGE (b:{label(lt.to_type)} {{pk: r.t}}) ON CREATE SET b.stub = true "
                        f"MERGE (a)-[:{rel(lt.api_name)}]->(b)",
                        {"rows": [{"f": f, "t": t} for f, t in links]},
                    )
                ],
            )
            counts["links"] += len(links)
    # 4) 시작 시점까지의 이벤트는 반영된 것으로 본다
    await db.execute(
        text(
            "UPDATE graph_outbox SET processed_at = :now WHERE id <= :upto AND processed_at IS NULL"
        ),
        {"now": datetime.now(UTC), "upto": upto},
    )
    await db.commit()
    return counts
