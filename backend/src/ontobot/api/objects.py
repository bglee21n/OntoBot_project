"""근거 카드(TRD 14.1 객체 묶음)와 편집 레이어 관리(TRD 6.3, 17.1-1).

근거 카드는 인용을 누르면 그 객체의 **지금** 보이는 값과 값 계보를 보여 준다.

- 값은 답변 때 저장해 둔 것이 아니라 열 때 다시 읽는다. 그사이 값이 바뀌었으면 카드가 그것을
  보여 주고, 사용자는 계보(동기화 시각·편집 시각)로 언제 값인지 안다.
- 편집된 값은 "편집됨 · 원천 값 ○○ (동기화 시각)"을 함께 낸다(PRD 5.3) — 편집 값이 낡았는지 사람이
  판단할 수 있어야 한다.

읽을 수 없는 타입과 없는 객체는 같은 404 로 거른다(존재 여부도 정보다). 편집 충돌 목록·확인·편집
해제는 모델러·관리자가, 그것도 읽을 수 있는 타입에만 한다. 편집을 **만드는** API 는 없다 — 편집은
Action 실행(Phase 10)만 만든다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from ontobot.api.deps import CurrentUser, Db, User, require_role
from ontobot.api.errors import ProblemError
from ontobot.explorer import limits as explorer_limits
from ontobot.explorer import service as explorer
from ontobot.objects import edits
from ontobot.objects.lineage import object_lineage
from ontobot.objects.reader import resolve
from ontobot.ontology import registry
from ontobot.ontology.models import ObjectTypeDef
from ontobot.query.executor import in_scope
from ontobot.query.records import display
from ontobot.query.validator import check_scopes
from ontobot.security import audit, rbac
from ontobot.security.rbac import ROLE_ADMIN, ROLE_MODELER

router = APIRouter(prefix="/objects", tags=["objects"])
edits_router = APIRouter(tags=["objects"])

Curator = Annotated[CurrentUser, Depends(require_role(ROLE_MODELER, ROLE_ADMIN))]


LiveStatus = Literal["open", "acknowledged"]
ConflictStatus = Literal["open", "acknowledged", "cleared"]


class ConflictOut(BaseModel):
    id: int
    status: LiveStatus
    new_source_value: str  # 표시 문자열
    source_synced_at: datetime | None


class EditOut(BaseModel):
    edit_id: int
    edited_by: str | None
    edited_at: datetime
    action_run_id: int | None
    source_value: str  # 편집 당시 원천 값(표시 문자열)
    conflict: ConflictOut | None


class FieldOut(BaseModel):
    name: str
    display_name: str
    value: str  # 표시 문자열(답변의 데이터 블록과 같은 표기) — 편집이 있으면 편집 값
    edit: EditOut | None = None


class SourceLineageOut(BaseModel):
    data_source: str
    mapping_id: int | None
    origin: str | None
    sync_run_id: int | None
    run_finished_at: datetime | None


class ObjectCard(BaseModel):
    type: str
    type_display_name: str
    pk: str
    title: str
    fields: list[FieldOut]
    synced_at: datetime | None
    # 편집되지 않은 값의 출처. 원천이 없는 객체(시드·직접 적재)는 None.
    source: SourceLineageOut | None


def _readable_type(object_type: str, readable: frozenset[str]) -> ObjectTypeDef | None:
    schema = registry.current()
    t = schema.document.object_type(object_type) if schema is not None else None
    return t if t is not None and object_type in readable else None


def _edit_out(t: ObjectTypeDef, info: edits.EditInfo) -> EditOut:
    prop = t.property(info.property)
    c = info.conflict
    return EditOut(
        edit_id=info.id,
        edited_by=info.edited_by_name,
        edited_at=info.edited_at,
        action_run_id=info.action_run_id,
        source_value=display(prop, info.base_source_value),
        conflict=ConflictOut(
            id=c.id,
            status=cast(LiveStatus, c.status),  # 살아 있는 충돌만 온다
            new_source_value=display(prop, c.new_source_value),
            source_synced_at=c.source_synced_at,
        )
        if c is not None
        else None,
    )


@router.get("/{object_type}/{pk}", response_model=ObjectCard)
async def card(object_type: str, pk: str, db: Db, user: User) -> ObjectCard:
    access = await rbac.access(db, user.id)
    t = _readable_type(object_type, access.readable)
    if t is None:
        raise ProblemError(404, "객체를 찾을 수 없습니다", code="object_not_found")
    # 숨긴 속성은 카드에도 없다. 읽기 행 정책 밖의 객체는 없는 것과 같다(Phase 14b).
    schema = registry.current()
    if schema is not None:
        t = schema.document.without_properties(access.hidden()).object_type(t.api_name) or t
        scope = check_scopes(schema.document, access.read_scopes()).get(t.api_name)
        if not await in_scope(db, t.api_name, [pk], scope):
            raise ProblemError(404, "객체를 찾을 수 없습니다", code="object_not_found")
    found = await resolve(db, object_type, [pk], declared=[p.api_name for p in t.properties])
    row = found.get(pk)
    if row is None:
        raise ProblemError(404, "객체를 찾을 수 없습니다", code="object_not_found")
    lineage = await object_lineage(db, object_type, pk)
    src = lineage.source
    return ObjectCard(
        type=t.api_name,
        type_display_name=t.display_name,
        pk=row.pk,
        title=str(row.properties.get(t.title_property) or row.pk),
        fields=[
            FieldOut(
                name=p.api_name,
                display_name=p.display_name,
                value=display(p, row.properties.get(p.api_name)),
                edit=_edit_out(t, lineage.edits[p.api_name])
                if p.api_name in lineage.edits
                else None,
            )
            for p in t.properties
        ],
        synced_at=row.synced_at,
        source=SourceLineageOut(
            data_source=src.data_source_name,
            mapping_id=src.mapping_id,
            origin=src.origin,
            sync_run_id=src.sync_run_id,
            run_finished_at=src.run_finished_at,
        )
        if src is not None
        else None,
    )


# ---------------------------------------------------------------- 객체 탐색기(Phase 15)


class SearchHitOut(BaseModel):
    type: str
    type_display_name: str
    pk: str
    title: str


class NeighborGroupOut(BaseModel):
    link_type: str
    link_display_name: str
    direction: Literal["out", "in"]
    other_type: str
    other_display_name: str
    total: int
    truncated: bool  # 참이면 total 은 "이상"


class NeighborOut(BaseModel):
    link_type: str
    direction: Literal["out", "in"]
    type: str
    pk: str
    title: str


class NeighborsOut(BaseModel):
    groups: list[NeighborGroupOut]
    items: list[NeighborOut]


class ExplorerLimitsOut(BaseModel):
    max_depth: int
    max_nodes: int


class EditHistoryOut(BaseModel):
    edit_id: int
    property: str
    display_name: str
    value: str
    source_value: str
    edited_by: str | None
    edited_at: datetime
    action_run_id: int | None
    cleared_at: datetime | None


def _not_found() -> ProblemError:
    return ProblemError(404, "객체를 찾을 수 없습니다", code="object_not_found")


@router.get("/search", response_model=list[SearchHitOut])
async def search(
    db: Db,
    user: User,
    q: Annotated[str, Query(min_length=1, max_length=100)],
    object_type: Annotated[str | None, Query(alias="type", max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[SearchHitOut]:
    """읽을 수 있는 객체 중 pk·제목에 ``q`` 가 들어 있는 것(행 정책 안에서만)."""
    schema = registry.current()
    if schema is None:
        return []
    access = await rbac.access(db, user.id)
    hits = await explorer.search(
        db, schema.document, access, q, object_type=object_type, limit=limit
    )
    return [
        SearchHitOut(type=h.type, type_display_name=h.type_display_name, pk=h.pk, title=h.title)
        for h in hits
    ]


@router.get("/explorer-limits", response_model=ExplorerLimitsOut)
async def explorer_limits_get(db: Db, _: User) -> ExplorerLimitsOut:
    lim = await explorer_limits.get(db)
    return ExplorerLimitsOut(max_depth=lim.max_depth, max_nodes=lim.max_nodes)


@router.get("/{object_type}/{pk}/neighbors", response_model=NeighborsOut)
async def neighbors(
    object_type: str,
    pk: str,
    request: Request,
    db: Db,
    user: User,
    link_type: Annotated[str | None, Query(max_length=64)] = None,
    direction: Annotated[explorer.Direction, Query()] = "both",
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NeighborsOut:
    """1홉 이웃(그래프). 읽을 수 없는 이웃은 개수에도 없다. ``limit`` 은 관리자 노드 상한으로."""
    schema = registry.current()
    if schema is None:
        raise _not_found()
    access = await rbac.access(db, user.id)
    lim = await explorer_limits.get(db)
    try:
        res = await explorer.neighbors(
            db,
            getattr(request.app.state, "graph", None),
            schema.document,
            access,
            object_type,
            pk,
            link_type=link_type,
            direction=direction,
            limit=min(limit, lim.max_nodes),
            offset=offset,
        )
    except explorer.NotFound as e:
        raise _not_found() from e
    except explorer.GraphUnavailable as e:
        raise ProblemError(
            503,
            "관계 정보를 지금 볼 수 없습니다",
            detail="그래프 저장소에 연결할 수 없습니다. 객체 값은 그대로 볼 수 있습니다",
            code="graph_unavailable",
        ) from e
    # 대량으로 훑는 경로라 호출을 남긴다(그래프 편집 제안서 보강 — 데이터 접근 7).
    audit.record(
        db,
        audit.OBJECT_EXPLORED,
        actor_id=user.id,
        target=f"object:{object_type}",
        payload={"link_type": link_type, "direction": direction, "returned": len(res.items)},
    )
    return NeighborsOut(
        groups=[NeighborGroupOut.model_validate(g, from_attributes=True) for g in res.groups],
        items=[NeighborOut.model_validate(i, from_attributes=True) for i in res.items],
    )


@router.get("/{object_type}/{pk}/edits", response_model=list[EditHistoryOut])
async def edit_history(object_type: str, pk: str, db: Db, user: User) -> list[EditHistoryOut]:
    """편집 이력(해제된 것 포함). 숨긴 속성의 편집은 내지 않는다."""
    schema = registry.current()
    if schema is None:
        raise _not_found()
    access = await rbac.access(db, user.id)
    try:
        await explorer.check_start(db, schema.document, access, object_type, pk)
    except explorer.NotFound as e:
        raise _not_found() from e
    doc = schema.document.without_properties(access.hidden())
    t = doc.object_type(object_type)
    if t is None:  # check_start 가 확인했다 — 그사이 게시본이 바뀐 경우뿐
        raise _not_found()
    rows = await edits.history(db, object_type, pk, [p.api_name for p in t.properties])
    out = []
    for r in rows:
        prop = t.property(r.property)
        out.append(
            EditHistoryOut(
                edit_id=r.id,
                property=r.property,
                display_name=prop.display_name if prop else r.property,
                value=display(prop, r.value),
                source_value=display(prop, r.base_source_value),
                edited_by=r.edited_by_name,
                edited_at=r.edited_at,
                action_run_id=r.action_run_id,
                cleared_at=r.cleared_at,
            )
        )
    return out


# ---------------------------------------------------------------- 편집 충돌·해제


class SyncConflictOut(BaseModel):
    id: int
    status: ConflictStatus
    edit_id: int
    type: str
    pk: str
    property: str
    property_display_name: str
    edit_value: str
    base_source_value: str
    new_source_value: str
    source_synced_at: datetime | None
    edited_by: str | None
    edited_at: datetime
    resolved_by: str | None
    resolved_at: datetime | None


def _show(t: ObjectTypeDef | None, prop: str, value: Any) -> str:
    return display(t.property(prop) if t is not None else None, value)


@edits_router.get("/sync-conflicts", response_model=list[SyncConflictOut])
async def list_conflicts(
    db: Db,
    user: Curator,
    status: Annotated[ConflictStatus | None, Query()] = None,
) -> list[SyncConflictOut]:
    """편집 충돌. 상태를 주지 않으면 살아 있는 것(open·acknowledged)."""
    readable = await rbac.readable_types(db, user.id)
    schema = registry.current()
    out = []
    for c in await edits.list_conflicts(db, sorted(readable), status):
        t = schema.document.object_type(c.type) if schema is not None else None
        prop = t.property(c.property) if t is not None else None
        out.append(
            SyncConflictOut(
                id=c.id,
                status=cast(ConflictStatus, c.status),
                edit_id=c.edit_id,
                type=c.type,
                pk=c.pk,
                property=c.property,
                property_display_name=prop.display_name if prop is not None else c.property,
                edit_value=_show(t, c.property, c.edit_value),
                base_source_value=_show(t, c.property, c.base_source_value),
                new_source_value=_show(t, c.property, c.new_source_value),
                source_synced_at=c.source_synced_at,
                edited_by=c.edited_by,
                edited_at=c.edited_at,
                resolved_by=c.resolved_by,
                resolved_at=c.resolved_at,
            )
        )
    return out


@edits_router.post("/sync-conflicts/{conflict_id}/acknowledge", status_code=204)
async def acknowledge_conflict(conflict_id: int, db: Db, user: Curator) -> None:
    """충돌을 확인했다고 표시한다. 편집 값은 그대로 이긴다 — 낡았으면 편집을 해제한다."""
    readable = await rbac.readable_types(db, user.id)
    try:
        target = await edits.acknowledge_conflict(db, conflict_id, user.id)
    except edits.NotFound as e:
        raise ProblemError(404, str(e), code="conflict_not_found") from e
    except edits.EditError as e:
        raise ProblemError(409, str(e), code="conflict_not_open") from e
    if target[0] not in readable:
        # 읽을 수 없는 타입이면 없는 것과 같다 — 위 변경은 요청 트랜잭션과 함께 롤백된다.
        raise ProblemError(404, "충돌을 찾을 수 없습니다", code="conflict_not_found")
    audit.record(
        db,
        audit.EDIT_CONFLICT_ACKNOWLEDGED,
        actor_id=user.id,
        target=f"sync_conflict:{conflict_id}",
        payload={"type": target[0], "pk": target[1], "property": target[2]},
    )


@edits_router.delete("/object-edits/{edit_id}", status_code=204)
async def clear_edit(edit_id: int, db: Db, user: Curator) -> None:
    """편집을 해제한다 — 보이는 값이 원천 값으로 돌아간다(PRD 5.3)."""
    target = await edits.edit_target(db, edit_id)
    if target is None or target[0] not in await rbac.readable_types(db, user.id):
        raise ProblemError(404, "편집을 찾을 수 없습니다", code="edit_not_found")
    try:
        await edits.clear_edit(db, edit_id, user.id)
    except edits.EditError as e:
        raise ProblemError(409, str(e), code="edit_already_cleared") from e
    audit.record(
        db,
        audit.OBJECT_EDIT_CLEARED,
        actor_id=user.id,
        target=f"object_edit:{edit_id}",
        payload={"type": target[0], "pk": target[1], "property": target[2]},
    )
