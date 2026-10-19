"""데이터 소스·매핑·동기화 실행 라우터(TRD 14.1 동기화 묶음) — 모델러 전용.

매핑 저장은 두 가지를 확인한다: 현재 게시 버전과 맞는가, 원천에 그 열이 실제로 있는가.
원천에 닿지 못하면 **저장하지 않는다** — 열 이름을 확인하지 못한 매핑은 첫 실행에서야 실패한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ontobot.api.deps import CurrentUser, Db, require_role
from ontobot.api.errors import ProblemError
from ontobot.db.models.sync import DataSource, Mapping, SyncRun
from ontobot.jobs import handlers, queue
from ontobot.ontology import registry
from ontobot.security import audit
from ontobot.security.rbac import ROLE_ADMIN, ROLE_MODELER
from ontobot.security.secrets import SecretRefError, resolve_db, validate_ref
from ontobot.sync import freshness
from ontobot.sync.connectors.base import ConnectorError, SourceSpec
from ontobot.sync.connectors.postgres import connector_for
from ontobot.sync.mapping import MappingSpec, check

router = APIRouter(tags=["sync"])
Modeler = Annotated[CurrentUser, Depends(require_role(ROLE_MODELER))]
Viewer = Annotated[CurrentUser, Depends(require_role(ROLE_MODELER, ROLE_ADMIN))]


# ---------------------------------------------------------------- 데이터 소스


class SourceIn(BaseModel):
    kind: Literal["postgres", "oracle", "mysql", "csv"]
    name: str = Field(min_length=1, max_length=128)
    secret_ref: str = Field(min_length=1, max_length=255)


class SourcePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    secret_ref: str | None = Field(default=None, min_length=1, max_length=255)
    status: Literal["active", "disabled"] | None = None


class SourceOut(BaseModel):
    id: int
    kind: str
    name: str
    secret_ref: str  # 참조일 뿐 비밀값이 아니다
    status: str
    created_at: datetime


class TestResult(BaseModel):
    ok: bool
    server: str | None = None
    error: str | None = None


def _source_out(s: DataSource) -> SourceOut:
    return SourceOut(
        id=s.id,
        kind=s.kind,
        name=s.name,
        secret_ref=s.secret_ref,
        status=s.status,
        created_at=s.created_at,
    )


async def _source(db: Db, source_id: int) -> DataSource:
    s = await db.get(DataSource, source_id)
    if s is None:
        raise ProblemError(404, "데이터 소스를 찾을 수 없습니다", code="source_not_found")
    return s


def _check_ref(ref: str) -> None:
    try:
        validate_ref(ref)
    except SecretRefError as e:
        raise ProblemError(422, str(e), code="invalid_secret_ref") from e


@router.get("/sources", response_model=list[SourceOut])
async def list_sources(db: Db, _: Viewer) -> list[SourceOut]:
    return [_source_out(s) for s in await db.scalars(select(DataSource).order_by(DataSource.id))]


@router.post("/sources", response_model=SourceOut, status_code=201)
async def create_source(body: SourceIn, db: Db, user: Modeler) -> SourceOut:
    _check_ref(body.secret_ref)
    if await db.scalar(select(DataSource.id).where(DataSource.name == body.name)):
        raise ProblemError(409, "같은 이름의 데이터 소스가 있습니다", code="source_conflict")
    s = DataSource(kind=body.kind, name=body.name, secret_ref=body.secret_ref)
    db.add(s)
    await db.flush()
    audit.record(
        db,
        audit.SOURCE_CHANGED,
        actor_id=user.id,
        target=f"data_source:{s.id}",
        payload={"op": "create", "name": s.name, "kind": s.kind},
    )
    return _source_out(s)


@router.patch("/sources/{source_id}", response_model=SourceOut)
async def patch_source(source_id: int, body: SourcePatch, db: Db, user: Modeler) -> SourceOut:
    s = await _source(db, source_id)
    changes = body.model_dump(exclude_unset=True)
    if "secret_ref" in changes:
        _check_ref(changes["secret_ref"])
    for k, v in changes.items():
        setattr(s, k, v)
    audit.record(
        db,
        audit.SOURCE_CHANGED,
        actor_id=user.id,
        target=f"data_source:{s.id}",
        payload={"op": "update", "fields": sorted(changes)},
    )
    return _source_out(s)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: int, db: Db, user: Modeler) -> None:
    s = await _source(db, source_id)
    if await db.scalar(select(func.count()).select_from(Mapping).where(Mapping.source_id == s.id)):
        raise ProblemError(409, "이 소스를 쓰는 매핑이 있어 지울 수 없습니다", code="source_in_use")
    await db.delete(s)
    audit.record(
        db,
        audit.SOURCE_CHANGED,
        actor_id=user.id,
        target=f"data_source:{source_id}",
        payload={"op": "delete", "name": s.name},
    )


@router.post("/sources/{source_id}/test", response_model=TestResult)
async def test_source(source_id: int, db: Db, _: Modeler) -> TestResult:
    """접속 시험. 실패도 200 으로 돌려 화면이 사유를 보여 준다(오류 상태가 아니라 시험 결과다)."""
    s = await _source(db, source_id)
    try:
        info = await connector_for(s.kind, resolve_db(s.secret_ref)).test()
    except (ConnectorError, SecretRefError) as e:
        return TestResult(ok=False, error=str(e))
    return TestResult(ok=True, server=", ".join(info))


class ColumnsIn(BaseModel):
    table: str | None = None
    query: str | None = None


@router.post("/sources/{source_id}/columns", response_model=list[str])
async def source_columns(source_id: int, body: ColumnsIn, db: Db, _: Modeler) -> list[str]:
    s = await _source(db, source_id)
    try:
        return await connector_for(s.kind, resolve_db(s.secret_ref)).columns(
            SourceSpec(table=body.table, query=body.query)
        )
    except (ConnectorError, SecretRefError) as e:
        raise ProblemError(
            422, "원천 열을 읽지 못했습니다", detail=str(e), code="source_unreachable"
        ) from e


# ---------------------------------------------------------------- 매핑


class MappingIn(MappingSpec):
    source_id: int


class MappingOut(MappingIn):
    id: int
    created_at: datetime
    last_status: str | None = None
    last_success_at: datetime | None = None


def _mapping_out(m: Mapping, fresh: freshness.Freshness | None = None) -> MappingOut:
    return MappingOut(
        id=m.id,
        created_at=m.created_at,
        source_id=m.source_id,
        object_type=m.object_type,
        table=m.table,
        source_query=m.source_query,
        column_map=m.column_map,
        pk_column=m.pk_column,
        mode=m.mode,  # type: ignore[arg-type]
        watermark_column=m.watermark_column,
        link_rules=[r["link_type"] for r in m.link_rules],
        schedule=m.schedule,
        last_status=fresh.last_status if fresh else None,
        last_success_at=fresh.last_success_at if fresh else None,
    )


async def _validate_mapping(db: Db, body: MappingIn) -> None:
    schema = registry.current() or await registry.reload(db)
    if schema is None:
        raise ProblemError(409, "게시된 온톨로지가 없어 매핑을 만들 수 없습니다", code="no_schema")
    source = await _source(db, body.source_id)
    try:
        columns = await connector_for(source.kind, resolve_db(source.secret_ref)).columns(
            SourceSpec(table=body.table, query=body.source_query)
        )
    except (ConnectorError, SecretRefError) as e:
        raise ProblemError(
            422,
            "원천 열을 확인하지 못해 저장하지 않았습니다",
            detail=str(e),
            code="source_unreachable",
        ) from e
    errors = check(body, schema.document, columns)
    if errors:
        raise ProblemError(
            422,
            "매핑이 올바르지 않습니다",
            code="mapping_invalid",
            extra={"issues": [{"path": "mapping", "message": m} for m in errors]},
        )


def _apply(m: Mapping, body: MappingIn) -> None:
    m.source_id = body.source_id
    m.object_type = body.object_type
    m.table = body.table
    m.source_query = body.source_query
    m.column_map = body.column_map
    m.pk_column = body.pk_column
    m.mode = body.mode
    m.watermark_column = body.watermark_column
    m.link_rules = [{"link_type": name} for name in body.link_rules]
    m.schedule = body.schedule


@router.get("/mappings", response_model=list[MappingOut])
async def list_mappings(db: Db, _: Viewer) -> list[MappingOut]:
    fresh = {f.mapping_id: f for f in await freshness.by_mapping(db)}
    return [
        _mapping_out(m, fresh.get(m.id))
        for m in await db.scalars(select(Mapping).order_by(Mapping.id))
    ]


@router.post("/mappings", response_model=MappingOut, status_code=201)
async def create_mapping(body: MappingIn, db: Db, user: Modeler) -> MappingOut:
    await _validate_mapping(db, body)
    m = Mapping()
    _apply(m, body)
    db.add(m)
    await db.flush()
    audit.record(
        db,
        audit.MAPPING_CHANGED,
        actor_id=user.id,
        target=f"mapping:{m.id}",
        payload={"op": "create", "object_type": m.object_type, "source_id": m.source_id},
    )
    return _mapping_out(m)


@router.put("/mappings/{mapping_id}", response_model=MappingOut)
async def replace_mapping(mapping_id: int, body: MappingIn, db: Db, user: Modeler) -> MappingOut:
    m = await db.get(Mapping, mapping_id)
    if m is None:
        raise ProblemError(404, "매핑을 찾을 수 없습니다", code="mapping_not_found")
    await _validate_mapping(db, body)
    _apply(m, body)
    audit.record(
        db,
        audit.MAPPING_CHANGED,
        actor_id=user.id,
        target=f"mapping:{m.id}",
        payload={"op": "update", "object_type": m.object_type},
    )
    return _mapping_out(m)


@router.delete("/mappings/{mapping_id}", status_code=204)
async def delete_mapping(mapping_id: int, db: Db, user: Modeler) -> None:
    m = await db.get(Mapping, mapping_id)
    if m is None:
        raise ProblemError(404, "매핑을 찾을 수 없습니다", code="mapping_not_found")
    if await db.scalar(select(func.count()).select_from(SyncRun).where(SyncRun.mapping_id == m.id)):
        # 실행 이력이 있는 매핑은 지우지 않는다 — 신선도·감사의 근거가 사라진다. 일정을 비워 멈춘다.
        raise ProblemError(
            409,
            "실행 이력이 있는 매핑은 지울 수 없습니다. 일정을 비워 멈추세요",
            code="mapping_has_runs",
        )
    await db.delete(m)
    audit.record(
        db,
        audit.MAPPING_CHANGED,
        actor_id=user.id,
        target=f"mapping:{mapping_id}",
        payload={"op": "delete"},
    )


class Enqueued(BaseModel):
    job_id: int | None
    already_queued: bool


@router.post("/mappings/{mapping_id}/sync", response_model=Enqueued, status_code=202)
async def trigger_sync(mapping_id: int, db: Db, user: Modeler) -> Enqueued:
    if await db.get(Mapping, mapping_id) is None:
        raise ProblemError(404, "매핑을 찾을 수 없습니다", code="mapping_not_found")
    job_id = await queue.enqueue(db, handlers.SYNC, {"mapping_id": mapping_id}, dedupe=True)
    audit.record(
        db,
        audit.SYNC_TRIGGERED,
        actor_id=user.id,
        target=f"mapping:{mapping_id}",
        payload={"job_id": job_id},
    )
    return Enqueued(job_id=job_id, already_queued=job_id is None)


class SyncRunOut(BaseModel):
    id: int
    mapping_id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    rows_read: int
    rows_upserted: int
    rows_deleted: int
    watermark: str | None
    error: str | None


@router.get("/sync-runs", response_model=list[SyncRunOut])
async def list_sync_runs(
    db: Db,
    _: Viewer,
    mapping_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[SyncRunOut]:
    q = select(SyncRun).order_by(SyncRun.id.desc()).limit(limit)
    if mapping_id is not None:
        q = q.where(SyncRun.mapping_id == mapping_id)
    out: list[Any] = []
    for r in await db.scalars(q):
        out.append(
            SyncRunOut(
                id=r.id,
                mapping_id=r.mapping_id,
                status=r.status,
                started_at=r.started_at,
                finished_at=r.finished_at,
                rows_read=r.rows_read or 0,
                rows_upserted=r.rows_upserted or 0,
                rows_deleted=r.rows_deleted or 0,
                watermark=r.watermark,
                error=r.error,
            )
        )
    return out
