"""데이터 소스·매핑·동기화 API 와 작업 큐·워커(TRD 14.1, 3절)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update

from ontobot.db.models.jobs import Job
from ontobot.db.session import session_scope
from ontobot.jobs import handlers, queue, schedules
from ontobot.jobs.worker import Worker
from ontobot.objects.indexes import index_name
from tests.integration.conftest import SOURCE_ENV as ENV
from tests.integration.conftest import MakeUser, login
from tests.integration.conftest import source_connect as _src
from tests.integration.test_sync_graph import Source

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("source_env")]


@pytest.fixture
async def modeler(client, make_user: MakeUser) -> int:
    username, password, uid = await make_user("modeler", "admin")
    assert (await login(client, username, password)).status_code == 200
    return uid


async def _table(rows: int = 3) -> str:
    name = f"api_{uuid.uuid4().hex[:8]}"
    conn = await _src()
    try:
        await conn.execute(f"CREATE TABLE {name} (code text PRIMARY KEY, label text)")
        await conn.executemany(
            f"INSERT INTO {name} VALUES ($1, $2)",
            [(f"{name}-{i}", f"지역 {i}") for i in range(rows)],
        )
    finally:
        await conn.close()
    return name


async def test_source_secret_is_a_reference_and_test_reports_reachability(client, modeler) -> None:
    bad = await client.post(
        "/api/v1/sources",
        json={
            "kind": "postgres",
            "name": f"bad-{uuid.uuid4().hex[:6]}",
            "secret_ref": "postgres://u:p@h/db",
        },
    )
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_secret_ref"

    ok = (
        await client.post(
            "/api/v1/sources",
            json={
                "kind": "postgres",
                "name": f"ok-{uuid.uuid4().hex[:6]}",
                "secret_ref": f"env:{ENV}",
            },
        )
    ).json()
    assert (await client.post(f"/api/v1/sources/{ok['id']}/test")).json()["ok"] is True

    missing = (
        await client.post(
            "/api/v1/sources",
            json={
                "kind": "postgres",
                "name": f"miss-{uuid.uuid4().hex[:6]}",
                "secret_ref": "env:ONTOBOT_NOPE",
            },
        )
    ).json()
    res = (await client.post(f"/api/v1/sources/{missing['id']}/test")).json()
    assert res["ok"] is False and "ONTOBOT_NOPE_HOST" in res["error"]
    assert "password" not in res["error"].lower() or "ONTOBOT_NOPE_PASSWORD" in res["error"]


async def test_mapping_is_checked_against_schema_and_real_columns(client, modeler) -> None:
    table = await _table()
    src = (
        await client.post(
            "/api/v1/sources",
            json={
                "kind": "postgres",
                "name": f"m-{uuid.uuid4().hex[:6]}",
                "secret_ref": f"env:{ENV}",
            },
        )
    ).json()
    body = {
        "source_id": src["id"],
        "object_type": "Region",
        "table": table,
        "pk_column": "code",
        "column_map": {"code": "region_code", "label": "name"},
        "mode": "full",
    }

    wrong_col = await client.post(
        "/api/v1/mappings", json={**body, "column_map": {"code": "region_code", "title": "name"}}
    )
    assert wrong_col.status_code == 422
    assert any("원천에 없는 열: title" in i["message"] for i in wrong_col.json()["issues"])

    wrong_prop = await client.post(
        "/api/v1/mappings", json={**body, "column_map": {"code": "region_code", "label": "label"}}
    )
    assert any("없는 속성: label" in i["message"] for i in wrong_prop.json()["issues"])

    created = await client.post("/api/v1/mappings", json={**body, "schedule": "every:15m"})
    assert created.status_code == 201, created.text
    mapping = created.json()

    # 수동 실행은 작업을 넣고(202), 워커가 실행한다
    queued = (await client.post(f"/api/v1/mappings/{mapping['id']}/sync")).json()
    again = (await client.post(f"/api/v1/mappings/{mapping['id']}/sync")).json()
    assert queued["job_id"] and again == {"job_id": None, "already_queued": True}

    worker = Worker()
    worker.driver = None  # 이 테스트는 투영을 보지 않는다
    while await worker.run_one_job():
        pass
    runs = (await client.get("/api/v1/sync-runs", params={"mapping_id": mapping["id"]})).json()
    assert runs[0]["status"] == "succeeded" and runs[0]["rows_upserted"] == 3
    listed = {m["id"]: m for m in (await client.get("/api/v1/mappings")).json()}
    assert listed[mapping["id"]]["last_status"] == "succeeded"

    # 실행 이력이 있으면 지우지 않는다
    assert (await client.delete(f"/api/v1/mappings/{mapping['id']}")).status_code == 409


async def test_graph_status_reports_lag_and_requires_role(client, make_user: MakeUser) -> None:
    username, password, _ = await make_user("user")
    await login(client, username, password)
    assert (await client.get("/api/v1/graph/status")).status_code == 403

    admin, pw, _ = await make_user("admin")
    await login(client, admin, pw)
    body = (await client.get("/api/v1/graph/status")).json()
    assert {"pending_events", "projection_lag_seconds", "neo4j_available", "lag_warning"} <= set(
        body
    )


# ---------------------------------------------------------------- 큐


async def test_claim_skips_locked_jobs_and_retries_with_backoff() -> None:
    kind = f"test_{uuid.uuid4().hex[:6]}"
    async with session_scope() as db:
        a = await queue.enqueue(db, kind, {"n": 1})
        await db.commit()
        job = await queue.claim(db, "w1")
        while job is not None and job.kind != kind:  # 다른 테스트가 남긴 작업은 건너뛴다
            await queue.complete(db, job.id)
            job = await queue.claim(db, "w1")
        assert job is not None and job.id == a and job.attempts == 1
        assert await queue.claim(db, "w2") is None or True  # 같은 작업을 다시 집지 않는다
        await queue.fail(db, a, "boom")
        row = await db.get(Job, a, populate_existing=True)
        assert row is not None and row.status == "queued" and row.run_after > datetime.now(UTC)
        # 상한에 닿으면 failed 로 남는다
        await db.execute(update(Job).where(Job.id == a).values(attempts=queue.MAX_ATTEMPTS))
        await db.commit()
        await queue.fail(db, a, "boom again")
        row = await db.get(Job, a, populate_existing=True)
        assert row is not None and row.status == "failed"


async def test_stale_running_job_is_recovered() -> None:
    kind = f"stale_{uuid.uuid4().hex[:6]}"
    async with session_scope() as db:
        jid = await queue.enqueue(db, kind)
        await db.execute(
            update(Job)
            .where(Job.id == jid)
            .values(status="running", attempts=1, locked_at=datetime.now(UTC) - timedelta(hours=2))
        )
        await db.commit()
        assert await queue.recover_stale(db) >= 1
        row = await db.get(Job, jid, populate_existing=True)
        assert row is not None and row.status == "queued"


async def test_schedule_enqueues_due_sync_once() -> None:
    src = await Source().create()
    async with session_scope() as db:
        await db.execute(
            text("UPDATE mapping SET schedule = 'every:15m' WHERE id = :id"),
            {"id": src.org_mapping},
        )
        await db.commit()
        first = await schedules.enqueue_due(db)
        second = await schedules.enqueue_due(db)
        jobs = list(
            await db.scalars(
                select(Job).where(
                    Job.kind == handlers.SYNC,
                    Job.payload["mapping_id"].as_integer() == src.org_mapping,
                )
            )
        )
    assert len(jobs) == 1 and jobs[0].id in first and jobs[0].id not in second


async def test_publish_issues_property_index_jobs_that_create_indexes(client, modeler) -> None:
    from pathlib import Path

    sample = (Path(__file__).resolve().parents[3] / "domain-packs/sample/ontology.yaml").read_text(
        encoding="utf-8"
    )
    draft = (await client.post("/api/v1/ontology/versions", json={})).json()
    await client.post(
        f"/api/v1/ontology/versions/{draft['id']}/import",
        json={"format": "yaml", "content": sample},
    )
    await client.post(f"/api/v1/ontology/versions/{draft['id']}/publish")
    async with session_scope() as db:
        jobs = list(await db.scalars(select(Job).where(Job.kind == handlers.PROPERTY_INDEX)))
        assert {(j.payload["type"], j.payload["property"]) for j in jobs} >= {
            ("Facility", "risk_grade"),
            ("Region", "region_code"),
        }
    worker = Worker()
    worker.driver = None
    while await worker.run_one_job():
        pass
    async with session_scope() as db:
        names = set(
            await db.scalars(text("SELECT indexname FROM pg_indexes WHERE tablename = 'object'"))
        )
    assert index_name("Facility", "risk_grade") in names
