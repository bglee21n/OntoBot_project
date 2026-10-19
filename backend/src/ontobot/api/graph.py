"""그래프 상태·재구축(TRD 14.1 그래프 묶음, 5.2·5.5)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from ontobot.api.deps import CurrentUser, Db, require_role
from ontobot.api.errors import ProblemError
from ontobot.db.models.jobs import Job
from ontobot.graph import client as graph
from ontobot.graph.projector import lag
from ontobot.jobs import handlers, queue
from ontobot.security import audit
from ontobot.security.rbac import ROLE_ADMIN, ROLE_MODELER

router = APIRouter(prefix="/graph", tags=["graph"])
Admin = Annotated[CurrentUser, Depends(require_role(ROLE_ADMIN))]
Viewer = Annotated[CurrentUser, Depends(require_role(ROLE_MODELER, ROLE_ADMIN))]

# 이 지연을 넘으면 조회 조건 문장에 "관계 정보는 HH:MM 기준"을 덧붙인다(TRD 5.5).
LAG_WARN_SECONDS = 600


class GraphStatus(BaseModel):
    neo4j_available: bool
    neo4j_version: str | None
    pending_events: int
    projection_lag_seconds: int
    lag_warning: bool
    oldest_pending_at: str | None
    last_processed_at: str | None
    last_reconcile: dict[str, Any] | None
    last_reconcile_at: datetime | None


class JobOut(BaseModel):
    id: int
    kind: str
    status: str
    attempts: int
    error: str | None
    result: dict[str, Any] | None
    created_at: datetime


@router.get("/status", response_model=GraphStatus)
async def status(db: Db, _: Viewer) -> GraphStatus:
    lagged = await lag(db)
    version: str | None = None
    driver = handlers.graph_driver()
    available = False
    if driver is not None:
        try:
            version = (await graph.server_info(driver)).version
            available = True
        except graph.GraphUnavailable:
            available = False
        finally:
            await driver.close()
    last = await db.scalar(
        select(Job)
        .where(Job.kind == handlers.GRAPH_RECONCILE, Job.status == "succeeded")
        .order_by(Job.id.desc())
        .limit(1)
    )
    return GraphStatus(
        neo4j_available=available,
        neo4j_version=version,
        lag_warning=lagged["projection_lag_seconds"] > LAG_WARN_SECONDS,
        last_reconcile=(last.payload or {}).get("result") if last else None,
        last_reconcile_at=last.created_at if last else None,
        **lagged,
    )


@router.post("/rebuild", response_model=JobOut, status_code=202)
async def rebuild(db: Db, user: Admin) -> JobOut:
    job_id = await queue.enqueue(db, handlers.GRAPH_REBUILD, {}, dedupe=True)
    audit.record(db, audit.GRAPH_REBUILD, actor_id=user.id, payload={"job_id": job_id})
    return await _job(db, job_id) if job_id else await _latest(db, handlers.GRAPH_REBUILD)


@router.post("/reconcile", response_model=JobOut, status_code=202)
async def reconcile(db: Db, _: Admin) -> JobOut:
    job_id = await queue.enqueue(db, handlers.GRAPH_RECONCILE, {}, dedupe=True)
    return await _job(db, job_id) if job_id else await _latest(db, handlers.GRAPH_RECONCILE)


async def _job(db: Db, job_id: int) -> JobOut:
    j = await db.get(Job, job_id)
    if j is None:
        raise ProblemError(404, "작업을 찾을 수 없습니다", code="job_not_found")
    return JobOut(
        id=j.id,
        kind=j.kind,
        status=j.status,
        attempts=j.attempts,
        error=j.error,
        result=(j.payload or {}).get("result"),
        created_at=j.created_at,
    )


async def _latest(db: Db, kind: str) -> JobOut:
    j = await db.scalar(select(Job).where(Job.kind == kind).order_by(Job.id.desc()).limit(1))
    if j is None:
        raise ProblemError(404, "작업을 찾을 수 없습니다", code="job_not_found")
    return await _job(db, j.id)


jobs_router = APIRouter(prefix="/jobs", tags=["jobs"])


@jobs_router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: int, db: Db, _: Viewer) -> JobOut:
    return await _job(db, job_id)
