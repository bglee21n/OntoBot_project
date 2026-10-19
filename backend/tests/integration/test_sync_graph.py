"""동기화 → 아웃박스 → 투영 → 대사(TRD 5·11절, 계획 Phase 2 완료 기준).

원천은 테스트 전용 PostgreSQL DB(``ontobot_source_test``), 그래프는 테스트 전용 Neo4j 인스턴스다
(ADR-0007). Neo4j 가 없으면 그래프 테스트만 건너뛴다 — 동기화 테스트는 PostgreSQL 만으로 돈다.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text

from ontobot.config import get_settings
from ontobot.db.models.jobs import GraphOutbox
from ontobot.db.models.objects import LinkRecord, ObjectRecord
from ontobot.db.models.sync import DataSource, Mapping, SyncRun
from ontobot.db.session import session_scope
from ontobot.graph import client as graph
from ontobot.graph.projector import ensure_constraints, project_all, project_pending
from ontobot.graph.reconcile import rebuild, reconcile
from ontobot.ontology import registry
from ontobot.sync.runner import run_mapping
from tests.integration.conftest import SOURCE_ENV as ENV
from tests.integration.conftest import source_connect as _src

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("source_env")]

SAMPLE = Path(__file__).resolve().parents[3] / "domain-packs" / "sample" / "ontology.yaml"


# ------------------------------------------------ 원천 DB (접속·환경은 conftest.source_env)


class Source:
    """테스트마다 고유한 원천 표 두 개(시설·조직)와 그 매핑."""

    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:8]
        self.fac = f"fac_{self.tag}"
        self.org = f"org_{self.tag}"
        self.pk_prefix = f"S{self.tag}-"
        self.source_id = 0
        self.fac_mapping = 0
        self.org_mapping = 0

    def fid(self, n: int) -> str:
        return f"{self.pk_prefix}F{n}"

    def oid(self, n: int) -> str:
        return f"{self.pk_prefix}O{n}"

    async def create(self, *, mode: str = "full") -> Source:
        conn = await _src()
        try:
            await conn.execute(
                f"CREATE TABLE {self.fac} (id text PRIMARY KEY, name text, kind text, risk int, "
                f"cap numeric, installed date, org text, updated_at timestamptz DEFAULT now())"
            )
            await conn.execute(
                f"CREATE TABLE {self.org} (id text PRIMARY KEY, name text, "
                f"manager text, phone text)"
            )
            await conn.executemany(
                f"INSERT INTO {self.org} VALUES ($1, $2, $3, $4)",
                [
                    (self.oid(1), f"{self.tag} 강남지사", "김가상", "010-0000-0001"),
                    (self.oid(2), f"{self.tag} 부산지사", "이가상", "010-0000-0002"),
                ],
            )
            await conn.executemany(
                f"INSERT INTO {self.fac} (id, name, kind, risk, cap, installed, org, updated_at) "
                f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                [
                    (
                        self.fid(i),
                        f"{self.tag} 시설 {i}",
                        ["변전소", "철탑", "전주"][i % 3],
                        i % 5 + 1,
                        1000 + i,
                        date(2015, 1, 2),
                        self.oid(1 + i % 2),
                        datetime(2026, 1, 1, 0, 0, i, tzinfo=UTC),
                    )
                    for i in range(1, 6)
                ],
            )
        finally:
            await conn.close()
        async with session_scope() as db:
            ds = DataSource(kind="postgres", name=f"src-{self.tag}", secret_ref=f"env:{ENV}")
            db.add(ds)
            await db.flush()
            self.source_id = ds.id
            fac = Mapping(
                source_id=ds.id,
                object_type="Facility",
                table=self.fac,
                pk_column="id",
                column_map={
                    "id": "facility_id",
                    "name": "name",
                    "kind": "facility_type",
                    "risk": "risk_grade",
                    "cap": "capacity_kw",
                    "installed": "installed_on",
                    "org": "owner_org_id",
                },
                mode=mode,
                watermark_column="updated_at" if mode == "incremental" else None,
                link_rules=[{"link_type": "owned_by"}],
            )
            org = Mapping(
                source_id=ds.id,
                object_type="Organization",
                table=self.org,
                pk_column="id",
                column_map={
                    "id": "org_id",
                    "name": "name",
                    "manager": "manager_name",
                    "phone": "manager_phone",
                },
                mode="full",
                link_rules=[],
            )
            db.add_all([fac, org])
            await db.commit()
            self.fac_mapping, self.org_mapping = fac.id, org.id
        return self

    async def exec(self, sql: str, *args: Any) -> None:
        conn = await _src()
        try:
            await conn.execute(sql.format(fac=self.fac, org=self.org), *args)
        finally:
            await conn.close()


async def _count(model: Any, *where: Any) -> int:
    async with session_scope() as db:
        return int(await db.scalar(select(func.count()).select_from(model).where(*where)) or 0)
    return 0


async def _outbox_since(since_id: int) -> list[tuple[str, dict[str, Any]]]:
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(GraphOutbox.event, GraphOutbox.payload)
                .where(GraphOutbox.id > since_id)
                .order_by(GraphOutbox.id)
            )
        ).all()
        return [(r.event, r.payload) for r in rows]
    return []


async def _max_outbox() -> int:
    async with session_scope() as db:
        return int(await db.scalar(select(func.coalesce(func.max(GraphOutbox.id), 0))) or 0)
    return 0


# ---------------------------------------------------------------- 동기화


async def test_full_sync_creates_objects_links_and_outbox_in_one_go() -> None:
    src = await Source().create()
    before = await _max_outbox()
    org = await run_mapping(src.org_mapping)
    fac = await run_mapping(src.fac_mapping)
    assert (org.status, fac.status) == ("succeeded", "succeeded")
    assert fac.rows_upserted == 5

    alive = ObjectRecord.deleted_at.is_(None)
    assert await _count(ObjectRecord, ObjectRecord.pk.startswith(src.pk_prefix), alive) == 7
    assert (
        await _count(
            LinkRecord,
            LinkRecord.from_pk.startswith(src.pk_prefix),
            LinkRecord.deleted_at.is_(None),
        )
        == 5
    )
    events = [e for e, _ in await _outbox_since(before)]
    assert events.count("node_upsert") == 7 and events.count("link_upsert") == 5


async def test_rerun_without_changes_writes_nothing_new() -> None:
    src = await Source().create()
    await run_mapping(src.fac_mapping)
    before = await _max_outbox()
    again = await run_mapping(src.fac_mapping)
    assert (again.status, again.rows_upserted, again.rows_deleted) == ("succeeded", 0, 0)
    assert await _outbox_since(before) == []


async def test_change_and_delete_in_source_propagate() -> None:
    src = await Source().create()
    await run_mapping(src.org_mapping)
    await run_mapping(src.fac_mapping)
    before = await _max_outbox()
    # F1 의 소유 조직을 O2 → O1 로 바꾸고 F2(소유 O1) 를 원천에서 지운다
    await src.exec("UPDATE {fac} SET org = $1, risk = 5 WHERE id = $2", src.oid(1), src.fid(1))
    await src.exec("DELETE FROM {fac} WHERE id = $1", src.fid(2))

    out = await run_mapping(src.fac_mapping)
    assert (out.status, out.rows_upserted, out.rows_deleted) == ("succeeded", 1, 1)
    events = await _outbox_since(before)
    kinds = [(e, p.get("pk") or (p["from_pk"], p["to_pk"])) for e, p in events]
    assert ("node_upsert", src.fid(1)) in kinds
    assert ("node_delete", src.fid(2)) in kinds
    assert ("link_delete", (src.fid(1), src.oid(2))) in kinds  # 옛 소유 조직
    assert ("link_upsert", (src.fid(1), src.oid(1))) in kinds  # 새 소유 조직
    assert ("link_delete", (src.fid(2), src.oid(1))) in kinds  # 삭제된 시설의 링크
    # 링크 삭제가 노드 삭제보다 먼저 온다(투영 순서)
    order = [e for e, _ in events]
    assert order.index("link_delete") < order.index("node_delete")


async def test_bad_row_fails_the_run_and_changes_nothing() -> None:
    src = await Source().create()
    await run_mapping(src.fac_mapping)
    before = await _max_outbox()
    await src.exec("UPDATE {fac} SET kind = '발전소', name = 'changed' WHERE id = $1", src.fid(3))
    await src.exec("UPDATE {fac} SET name = 'also changed' WHERE id = $1", src.fid(4))

    out = await run_mapping(src.fac_mapping)
    assert out.status == "failed"
    assert out.error is not None and src.fid(3) in out.error and "facility_type" in out.error
    # 다른 행의 변경도 반영되지 않았다(반쯤 바뀐 상태가 없다)
    async with session_scope() as db:
        name = await db.scalar(
            text("SELECT properties ->> 'name' FROM object WHERE type='Facility' AND pk = :pk"),
            {"pk": src.fid(4)},
        )
        run = await db.get(SyncRun, out.run_id)
    assert name != "also changed"
    assert run is not None and run.status == "failed" and run.finished_at is not None
    assert await _outbox_since(before) == []


async def test_incremental_reads_only_rows_after_the_watermark() -> None:
    src = await Source().create(mode="incremental")
    first = await run_mapping(src.fac_mapping)
    assert first.rows_read == 5
    await src.exec(
        "INSERT INTO {fac} (id, name, kind, risk, cap, installed, org, updated_at) "
        "VALUES ($1, 'new', '전주', 1, 10, '2020-01-01', $2, '2026-02-01T00:00:00Z')",
        src.fid(9),
        src.oid(1),
    )
    second = await run_mapping(src.fac_mapping)
    assert (second.status, second.rows_read, second.rows_upserted) == ("succeeded", 1, 1)
    third = await run_mapping(src.fac_mapping)
    assert third.rows_read == 0


async def test_mapping_that_no_longer_matches_the_schema_fails_honestly() -> None:
    src = await Source().create()
    async with session_scope() as db:
        m = await db.get(Mapping, src.fac_mapping)
        assert m is not None
        m.column_map = {**m.column_map, "risk": "risk_level"}  # 게시 버전에 없는 속성
        await db.commit()
    out = await run_mapping(src.fac_mapping)
    assert out.status == "failed" and out.error and "risk_level" in out.error


# ---------------------------------------------------------------- 그래프


@pytest.fixture
async def neo4j() -> AsyncIterator[graph.AsyncDriver]:
    s = get_settings()
    if not s.test_neo4j_password.get_secret_value():
        pytest.skip("테스트 Neo4j 비밀번호 미설정")
    driver = graph.create_driver(
        s.test_neo4j_uri, s.test_neo4j_user, s.test_neo4j_password.get_secret_value()
    )
    try:
        await graph.server_info(driver)
    except graph.GraphUnavailable:
        await driver.close()
        pytest.skip("테스트 Neo4j 인스턴스가 떠 있지 않음 (Start-Service neo4j-ontobot-test)")
    yield driver
    await driver.close()


async def _doc() -> Any:
    schema = registry.current()
    assert schema is not None
    return schema.document


async def test_projection_matches_postgres_after_rebuild_and_sync(neo4j) -> None:
    async with session_scope() as db:
        await rebuild(db, neo4j, await _doc())
    src = await Source().create()
    await run_mapping(src.org_mapping)
    await run_mapping(src.fac_mapping)
    await src.exec("DELETE FROM {fac} WHERE id = $1", src.fid(5))
    await run_mapping(src.fac_mapping)

    async with session_scope() as db:
        assert await project_all(db, neo4j) > 0
        report = await reconcile(db, neo4j, await _doc())
    mismatched = [r for r in report.rows if not r.ok]
    assert mismatched == [], mismatched


async def test_graph_holds_no_property_values(neo4j) -> None:
    src = await Source().create()
    await run_mapping(src.org_mapping)
    async with session_scope() as db:
        await ensure_constraints(neo4j, await _doc())
        await project_all(db, neo4j)
    rows = await graph.read(
        neo4j, "MATCH (n:Organization {pk: $pk}) RETURN properties(n) AS p", {"pk": src.oid(1)}
    )
    assert set(rows[0]["p"]) == {"pk", "title", "stub"}
    assert "김가상" not in repr(rows) and "010-0000-0001" not in repr(rows)


async def test_link_to_missing_object_becomes_a_reported_stub(neo4j) -> None:
    src = await Source().create()
    await src.exec("UPDATE {fac} SET org = $1 WHERE id = $2", f"{src.pk_prefix}O404", src.fid(1))
    await run_mapping(src.fac_mapping)  # 조직은 동기화하지 않았다
    async with session_scope() as db:
        await project_all(db, neo4j)
        report = await reconcile(db, neo4j, await _doc())
    assert report.stub_nodes >= 1


async def test_outbox_stays_pending_when_neo4j_is_down() -> None:
    src = await Source().create()
    await run_mapping(src.org_mapping)
    dead = graph.create_driver("bolt://127.0.0.1:1", "neo4j", "x")
    try:
        async with session_scope() as db:
            pending_before = await db.scalar(
                select(func.count())
                .select_from(GraphOutbox)
                .where(GraphOutbox.processed_at.is_(None))
            )
            with pytest.raises(graph.GraphUnavailable):
                await project_pending(db, dead)
            await db.rollback()
            pending_after = await db.scalar(
                select(func.count())
                .select_from(GraphOutbox)
                .where(GraphOutbox.processed_at.is_(None))
            )
    finally:
        await dead.close()
    assert pending_after == pending_before
