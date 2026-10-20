"""운영 점검(Phase 8) — 준비 확인의 실제 접속과 매일 주기 작업(대사·보존 기간) 발행."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from ontobot import health
from ontobot.config import get_settings
from ontobot.db.models.jobs import Job
from ontobot.db.session import session_scope
from ontobot.graph.client import create_driver
from ontobot.jobs import handlers, schedules

pytestmark = pytest.mark.integration

DAILY_KINDS = (handlers.GRAPH_RECONCILE, handlers.RETENTION)


async def test_readiness_reaches_real_postgres() -> None:
    assert await health._bounded(health._postgres()) == health.Component("ok")


async def test_readiness_reaches_real_neo4j_and_reports_dead_one_as_down() -> None:
    s = get_settings()
    password = s.test_neo4j_password.get_secret_value()
    if not password:
        pytest.skip("테스트 Neo4j 비밀번호 미설정 (.env)")
    alive = create_driver(s.test_neo4j_uri, s.test_neo4j_user, password)
    # 아무도 듣지 않는 포트 — 드라이버 재시도(5초)보다 먼저 확인 시간 상한(3초)에 걸린다.
    dead = create_driver("bolt://127.0.0.1:1", "neo4j", "x")
    try:
        up = await health._bounded(health._neo4j(alive))
        if up.status != "ok":
            pytest.skip(f"테스트 Neo4j 가 내려가 있음({up.detail})")
        down = await health._bounded(health._neo4j(dead))
    finally:
        await alive.close()
        await dead.close()
    assert down.status == "down"


async def test_daily_reconcile_and_retention_are_issued_once_a_day() -> None:
    # 다른 테스트가 남긴 대기 작업이 중복 방지에 걸리지 않게 닫아 둔다.
    async with session_scope() as db:
        await db.execute(
            update(Job)
            .where(Job.kind.in_(DAILY_KINDS), Job.status.in_(("queued", "running")))
            .values(status="succeeded")
        )
        await db.commit()
        # 시각을 미래로 옮겨 이 테스트만의 "하루"를 만든다(실제 시각의 작업과 섞이지 않는다).
        day0 = datetime.now(UTC) + timedelta(days=400)
        issued = await schedules.enqueue_due(db, now=day0)
        rows = list(await db.scalars(select(Job).where(Job.id.in_(issued))))
        assert sorted(j.kind for j in rows if j.kind in DAILY_KINDS) == sorted(DAILY_KINDS)

        daily_ids = [j.id for j in rows if j.kind in DAILY_KINDS]
        await db.execute(
            update(Job).where(Job.id.in_(daily_ids)).values(status="succeeded", created_at=day0)
        )
        await db.commit()

        same_day = await schedules.enqueue_due(db, now=day0 + timedelta(hours=23))
        kinds = set(await db.scalars(select(Job.kind).where(Job.id.in_(same_day))))
        assert not kinds & set(DAILY_KINDS)

        next_day = await schedules.enqueue_due(db, now=day0 + timedelta(hours=25))
        kinds = set(await db.scalars(select(Job.kind).where(Job.id.in_(next_day))))
        assert set(DAILY_KINDS) <= kinds

        # 뒷정리 — 이번에 발행한 작업(예약 동기화 포함)을 닫는다. 남기면 뒤 테스트의 워커가 집는다.
        # 미래 시각의 작업은 과거로 돌린다(남기면 이후 실제 발행이 400일 동안 멈춘다).
        await db.execute(
            update(Job)
            .where(Job.id.in_([*issued, *same_day, *next_day]))
            .values(status="succeeded")
        )
        await db.execute(
            update(Job)
            .where(Job.created_at > datetime.now(UTC))
            .values(created_at=datetime.now(UTC) - timedelta(days=2))
        )
        await db.commit()
