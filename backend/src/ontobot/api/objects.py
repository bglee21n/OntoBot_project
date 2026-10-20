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

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ontobot.api.deps import CurrentUser, Db, User, require_role
from ontobot.api.errors import ProblemError
from ontobot.objects import edits
from ontobot.objects.lineage import object_lineage
from ontobot.objects.reader import resolve
from ontobot.ontology import registry
from ontobot.ontology.models import ObjectTypeDef
from ontobot.query.records import display
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
    t = _readable_type(object_type, await rbac.readable_types(db, user.id))
    if t is None:
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
