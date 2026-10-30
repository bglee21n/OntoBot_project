"""관리(TRD 14.1 관리 묶음) — 사용자·역할·타입 권한·감사 로그·트레이스. 관리자만.

검증과 위임만 한다. 규칙(자기 잠금 방지, 세션 폐기, 감사 기록)은 ``security.admin`` 에 있다.
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ontobot.actions import metrics as action_metrics
from ontobot.api.deps import CurrentUser, Db, require_role
from ontobot.api.errors import ProblemError
from ontobot.config import app_version, get_settings
from ontobot.db.models.chat import Trace
from ontobot.evaluation import targets
from ontobot.explorer import limits as explorer_limits
from ontobot.ontology import registry
from ontobot.security import admin, audit, audit_chain
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
    groups: list[str]
    created_at: datetime
    email: str | None  # 마스킹
    phone: str | None  # 마스킹


class ContactsIn(BaseModel):
    """Action 알림 연락처. 비우면 지운다."""

    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,64}$")
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    roles: list[str] = Field(default_factory=list)
    # 데이터 그룹(Phase 14) — 역할은 화면·기능, 데이터 권한은 그룹이 준다.
    groups: list[str] = Field(default_factory=list)


class UserPatch(BaseModel):
    status: Literal["active", "disabled"] | None = None
    roles: list[str] | None = None


class RoleOut(BaseModel):
    id: int
    name: str
    description: str | None


class TypeGrantIO(BaseModel):
    object_type: str = Field(max_length=64)
    can_read: bool = False
    can_create: bool = False
    can_edit: bool = False
    can_delete: bool = False
    # Phase 14b — 숨길 속성(기본은 모두 보임), 행 정책(QueryPlan Filter 문법의 조건 AND, null =
    # 제한 없음)
    hidden: list[str] = Field(default_factory=list, max_length=200)
    read_filters: list[dict[str, Any]] | None = Field(default=None, max_length=20)
    write_filters: list[dict[str, Any]] | None = Field(default=None, max_length=20)


class GroupOut(BaseModel):
    id: int
    name: str
    display_name: str
    description: str | None
    # SSO(OIDC) 그룹 클레임 값 — 있으면 로그인 때 소속을 이 값으로 맞춘다(정확 일치).
    external_id: str | None
    member_ids: list[int]
    types: list[TypeGrantIO]
    links: list[str]


class GroupCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    display_name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=255)
    external_id: str | None = Field(default=None, min_length=1, max_length=255)


class GroupPatch(BaseModel):
    display_name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=255)
    external_id: str | None = Field(default=None, min_length=1, max_length=255)


class MembersIn(BaseModel):
    user_ids: list[int] = Field(max_length=10_000)


class GroupPermissionsIn(BaseModel):
    types: list[TypeGrantIO] = Field(max_length=500)
    links: list[str] = Field(default_factory=list, max_length=500)


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
                groups=body.groups,
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


@router.put("/admin/users/{user_id}/contacts", response_model=UserOut)
async def set_contacts(user_id: int, body: ContactsIn, db: Db, me: Admin) -> UserOut:
    return _user(
        await _call(
            admin.set_contacts(
                db, actor_id=me.id, user_id=user_id, email=body.email, phone=body.phone
            )
        )
    )


class TypeOut(BaseModel):
    api_name: str
    display_name: str
    # 객체 타입이면 속성 api_name(속성 숨김 표의 선택지), 링크 타입이면 비어 있다.
    properties: list[str] = Field(default_factory=list)


class SystemOut(BaseModel):
    version: str
    deployer_url: str | None


@router.get("/admin/system", response_model=SystemOut)
async def system(_: Admin) -> SystemOut:
    """지금 버전(이미지의 VERSION)과 배포 콘솔 주소 — 배포는 앱 밖의 콘솔이 한다(ADR-0014)."""
    return SystemOut(version=app_version(), deployer_url=get_settings().deployer_url or None)


class V1MetricsOut(BaseModel):
    days: int
    # 상태별 Action 요청 수(요청 시각 기준). withdrawn = 요청자가 거둔 것
    counts: dict[str, int]
    success_rate: float | None
    rejection_rate: float | None
    open_conflicts: int


@router.get("/admin/metrics", response_model=V1MetricsOut)
async def metrics(db: Db, _: Admin, days: Annotated[int, Query(ge=1, le=365)] = 30) -> V1MetricsOut:
    """PRD 7절 v1 지표 — Action 실행 성공률·승인 거부율, 확인되지 않은 편집 충돌."""
    m = await action_metrics.v1_metrics(db, days)
    return V1MetricsOut(**m.__dict__)


class TargetOut(BaseModel):
    key: str
    label: str
    unit: Literal["ratio", "seconds", "minutes", "days", "count"]
    direction: Literal["min", "max"]
    # 지금 목표 — 관리자 설정값, 없으면 제안값(근거 없는 지표는 null = 미정)
    value: float | None
    default: float | None
    custom: bool
    source: str
    measured: float | None
    met: bool | None


class TargetsIn(BaseModel):
    # 지표 이름 → 목표. null 은 설정을 지워 제안값으로 되돌린다.
    values: dict[str, Annotated[Decimal, Field(ge=0, max_digits=12)] | None]


def _target_out(t: targets.TargetView) -> TargetOut:
    s = t.spec
    return TargetOut(
        key=s.key,
        label=s.label,
        unit=s.unit,
        direction=s.direction,
        value=float(t.value) if t.value is not None else None,
        default=float(s.default) if s.default is not None else None,
        custom=t.custom,
        source=s.source,
        measured=t.measured,
        met=t.met,
    )


@router.get("/admin/targets", response_model=list[TargetOut])
async def get_targets(db: Db, _: Admin) -> list[TargetOut]:
    """PRD 7절 성공 지표 — 목표(관리자 설정, 없으면 제안값)와 지금 측정값."""
    return [_target_out(t) for t in await targets.load(db)]


@router.put("/admin/targets", response_model=list[TargetOut])
async def put_targets(body: TargetsIn, db: Db, user: Admin) -> list[TargetOut]:
    try:
        await targets.update(db, body.values, actor_id=user.id)
    except targets.UnknownTarget as e:
        raise ProblemError(422, str(e), code="invalid_target") from e
    return [_target_out(t) for t in await targets.load(db)]


class ExplorerLimitsIn(BaseModel):
    max_depth: int = Field(ge=explorer_limits.DEPTH_RANGE[0], le=explorer_limits.DEPTH_RANGE[1])
    max_nodes: int = Field(ge=explorer_limits.NODES_RANGE[0], le=explorer_limits.NODES_RANGE[1])


@router.put("/admin/explorer-limits", response_model=ExplorerLimitsIn)
async def put_explorer_limits(body: ExplorerLimitsIn, db: Db, user: Admin) -> ExplorerLimitsIn:
    """객체 탐색기 확장 한도(최대 깊이·화면 노드 수). 서버도 이웃 조회를 같은 값으로 자른다."""
    lim = await explorer_limits.put(
        db, explorer_limits.Limits(body.max_depth, body.max_nodes), actor_id=user.id
    )
    return ExplorerLimitsIn(max_depth=lim.max_depth, max_nodes=lim.max_nodes)


@router.get("/admin/object-types", response_model=list[TypeOut])
async def object_types(_: Admin) -> list[TypeOut]:
    """권한 표의 열 — 게시된 버전의 타입. 게시본이 없으면 빈 목록."""
    schema = registry.current()
    if schema is None:
        return []
    return [
        TypeOut(
            api_name=t.api_name,
            display_name=t.display_name,
            properties=[
                p.api_name
                for p in t.properties
                if p.api_name not in {t.pk_property, t.title_property}
            ],
        )
        for t in schema.document.object_types
    ]


@router.get("/admin/link-types", response_model=list[TypeOut])
async def link_types(_: Admin) -> list[TypeOut]:
    """그룹 '연결' 권한 표의 열 — 게시된 버전의 링크 타입. 게시본이 없으면 빈 목록."""
    schema = registry.current()
    if schema is None:
        return []
    return [
        TypeOut(api_name=lt.api_name, display_name=lt.display_name)
        for lt in schema.document.link_types
    ]


@router.get("/admin/roles", response_model=list[RoleOut])
async def roles(db: Db, _: Admin) -> list[RoleOut]:
    return [RoleOut.model_validate(r, from_attributes=True) for r in await admin.list_roles(db)]


# ---------------------------------------------------------------- 데이터 그룹(Phase 14)


def _group(v: admin.GroupView) -> GroupOut:
    return GroupOut.model_validate(v, from_attributes=True)


@router.get("/admin/groups", response_model=list[GroupOut])
async def groups(db: Db, _: Admin) -> list[GroupOut]:
    return [_group(g) for g in await admin.list_groups(db)]


@router.post("/admin/groups", response_model=GroupOut, status_code=201)
async def create_group(body: GroupCreate, db: Db, me: Admin) -> GroupOut:
    return _group(
        await _call(
            admin.create_group(
                db,
                actor_id=me.id,
                name=body.name,
                display_name=body.display_name,
                description=body.description,
                external_id=body.external_id,
            )
        )
    )


@router.patch("/admin/groups/{group_id}", response_model=GroupOut)
async def update_group(group_id: int, body: GroupPatch, db: Db, me: Admin) -> GroupOut:
    return _group(
        await _call(
            admin.update_group(
                db,
                actor_id=me.id,
                group_id=group_id,
                display_name=body.display_name,
                description=body.description,
                external_id=body.external_id,
            )
        )
    )


@router.delete("/admin/groups/{group_id}", status_code=204)
async def delete_group(group_id: int, db: Db, me: Admin) -> None:
    await _call(admin.delete_group(db, actor_id=me.id, group_id=group_id))


@router.put("/admin/groups/{group_id}/members", response_model=GroupOut)
async def set_members(group_id: int, body: MembersIn, db: Db, me: Admin) -> GroupOut:
    return _group(
        await _call(
            admin.set_members(
                db, actor_id=me.id, group_id=group_id, user_ids=body.user_ids, via="admin"
            )
        )
    )


@router.put("/admin/groups/{group_id}/permissions", response_model=GroupOut)
async def set_group_permissions(
    group_id: int, body: GroupPermissionsIn, db: Db, me: Admin
) -> GroupOut:
    grants = [
        admin.TypeGrant(
            t.object_type,
            t.can_read,
            t.can_create,
            t.can_edit,
            t.can_delete,
            tuple(t.hidden),
            tuple(t.read_filters) if t.read_filters is not None else None,
            tuple(t.write_filters) if t.write_filters is not None else None,
        )
        for t in body.types
    ]
    return _group(
        await _call(
            admin.set_permissions(
                db, actor_id=me.id, group_id=group_id, types=grants, links=body.links
            )
        )
    )


class AuditChainOut(BaseModel):
    ok: bool
    rows: int
    last_seq: int
    broken_seq: int | None
    broken_id: int | None
    # hash_mismatch / link_broken / gap / head_missing / unchained / tail_missing
    reason: str | None


@router.get("/admin/audit-chain", response_model=AuditChainOut)
async def audit_chain_check(db: Db, me: Admin) -> AuditChainOut:
    """감사 로그 해시 체인을 지금 검증한다(PRD 5.5). 매일 작업(``audit_verify``)도 같은 검증."""
    report = await audit_chain.verify(db)
    audit.record(
        db,
        audit.AUDIT_VIEWED,
        actor_id=me.id,
        target="audit_chain",
        payload={"ok": report.ok, "reason": report.reason},
    )
    return AuditChainOut(**report.__dict__)


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
