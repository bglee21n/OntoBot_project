"""관리(TRD 14.1 관리 묶음) — 사용자·역할·타입 권한·감사 로그·트레이스. 관리자만.

검증과 위임만 한다. 규칙(자기 잠금 방지, 세션 폐기, 감사 기록)은 ``security.admin`` 에 있다.
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ontobot.api.deps import CurrentUser, Db, require_role
from ontobot.api.errors import ProblemError
from ontobot.db.models.chat import Trace
from ontobot.ontology import registry
from ontobot.security import admin, audit
from ontobot.security.rbac import ROLE_ADMIN

router = APIRouter(tags=["admin"])
Admin = Annotated[CurrentUser, Depends(require_role(ROLE_ADMIN))]


async def _call[R](work: Awaitable[R]) -> R:
    try:
        return await work
    except admin.AdminError as e:
        raise ProblemError(e.status, str(e), code=e.code) from e


class UserOut(BaseModel):
    id: int
    username: str | None
    display_name: str
    status: Literal["active", "disabled"]
    auth: Literal["local", "oidc"]
    roles: list[str]
    created_at: datetime


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,64}$")
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    roles: list[str] = Field(default_factory=list)


class UserPatch(BaseModel):
    status: Literal["active", "disabled"] | None = None
    roles: list[str] | None = None


class RoleOut(BaseModel):
    id: int
    name: str
    description: str | None
    readable_types: list[str]


class PermissionsIn(BaseModel):
    readable_types: list[str] = Field(max_length=500)


class AuditLogOut(BaseModel):
    id: int
    ts: datetime
    actor_id: int | None
    event: str
    target: str | None
    payload: dict[str, Any]


class TraceOut(BaseModel):
    id: int
    created_at: datetime
    user_id: int | None
    message_id: int | None
    status: str | None
    failure_reason: str | None
    grade: int | None
    plan: dict[str, Any] | None
    sql_masked: str | None
    cypher_masked: str | None
    fact_sentences: int | None
    verified_sentences: int | None
    removed_citations: list[dict[str, Any]]
    stage_latency_ms: dict[str, int]
    llm_calls: list[dict[str, Any]]


def _user(v: admin.UserView) -> UserOut:
    return UserOut.model_validate(v, from_attributes=True)


@router.get("/admin/users", response_model=list[UserOut])
async def users(db: Db, _: Admin) -> list[UserOut]:
    return [_user(v) for v in await admin.list_users(db)]


@router.post("/admin/users", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, db: Db, me: Admin) -> UserOut:
    return _user(
        await _call(
            admin.create_local_user(
                db,
                actor_id=me.id,
                username=body.username,
                display_name=body.display_name,
                password=body.password,
                roles=body.roles,
            )
        )
    )


@router.patch("/admin/users/{user_id}", response_model=UserOut)
async def update_user(user_id: int, body: UserPatch, db: Db, me: Admin) -> UserOut:
    return _user(
        await _call(
            admin.update_user(
                db, actor_id=me.id, user_id=user_id, status=body.status, roles=body.roles
            )
        )
    )


class TypeOut(BaseModel):
    api_name: str
    display_name: str


@router.get("/admin/object-types", response_model=list[TypeOut])
async def object_types(_: Admin) -> list[TypeOut]:
    """권한 표의 열 — 게시된 버전의 타입. 게시본이 없으면 빈 목록."""
    schema = registry.current()
    if schema is None:
        return []
    return [
        TypeOut(api_name=t.api_name, display_name=t.display_name)
        for t in schema.document.object_types
    ]


@router.get("/admin/roles", response_model=list[RoleOut])
async def roles(db: Db, _: Admin) -> list[RoleOut]:
    return [RoleOut.model_validate(r, from_attributes=True) for r in await admin.list_roles(db)]


@router.put("/admin/roles/{role_id}/permissions", response_model=RoleOut)
async def set_permissions(role_id: int, body: PermissionsIn, db: Db, me: Admin) -> RoleOut:
    view = await _call(
        admin.set_readable_types(
            db, actor_id=me.id, role_id=role_id, object_types=body.readable_types
        )
    )
    return RoleOut.model_validate(view, from_attributes=True)


@router.get("/audit-logs", response_model=list[AuditLogOut])
async def audit_logs(
    db: Db,
    me: Admin,
    event: str | None = None,
    user_id: int | None = None,
    before_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuditLogOut]:
    rows = await admin.audit_logs(
        db, actor_id=me.id, event=event, user_id=user_id, before_id=before_id, limit=limit
    )
    return [AuditLogOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/traces/{trace_id}", response_model=TraceOut)
async def trace(trace_id: int, db: Db, me: Admin) -> TraceOut:
    t = await db.get(Trace, trace_id)
    if t is None:
        raise ProblemError(404, "트레이스를 찾을 수 없습니다", code="trace_not_found")
    # 트레이스는 마스킹돼 있지만 질문·계획을 담는다 — 누가 열어 봤는지 남긴다.
    audit.record(db, audit.TRACE_VIEWED, actor_id=me.id, target=f"trace:{trace_id}")
    return TraceOut.model_validate(t, from_attributes=True)
