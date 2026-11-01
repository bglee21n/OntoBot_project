"""도입 작업 공간 라우터(TRD 17.17, Phase 20) — 검증과 위임만 한다.

규칙은 ``ontobot.adoption`` 에, 권한 판정은 ``rbac.engagement_access`` 에 있다. 구성원이 아니면
그 도입은 없는 도입과 같은 404 다(워크숍 세션과 같은 말 — 존재를 알리지 않는다).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ontobot.adoption import bridges, progress, send, sheets, store
from ontobot.api.deps import CurrentUser, Db, User
from ontobot.api.errors import ProblemError
from ontobot.db.models.adoption import Engagement
from ontobot.security import rbac

router = APIRouter(prefix="/adoption", tags=["adoption"])

MemberRole = Literal["owner", "editor", "viewer"]


# ---------------------------------------------------------------- 양식 선언


class ColumnOut(BaseModel):
    key: str  # 고정 열은 ""
    label: str
    kind: Literal["text", "long", "choice", "check"]
    options: list[str]
    pii: bool
    fixed: bool


class SectionOut(BaseModel):
    key: str
    title: str | None
    layout: Literal["fields", "table"]
    columns: list[ColumnOut]
    rows: list[list[str]]  # 행이 정해진 표: 행마다 열 값(입력 열은 자리 표시)
    blank_rows: int
    max_rows: int
    note: str | None


class SheetKindOut(BaseModel):
    kind: str
    number: str
    title: str
    stage: int
    intro: str
    sections: list[SectionOut]
    json_schema: dict[str, Any]


def _kind_out(s: sheets.Sheet) -> SheetKindOut:
    return SheetKindOut(
        kind=s.kind,
        number=s.number,
        title=s.title,
        stage=s.stage,
        intro=s.intro,
        sections=[
            SectionOut(
                key=sec.key,
                title=sec.title,
                layout=sec.layout,
                columns=[
                    ColumnOut(
                        key=c.key,
                        label=c.label,
                        kind=c.kind,
                        options=list(c.options),
                        pii=c.pii,
                        fixed=c.fixed,
                    )
                    for c in sec.columns
                ],
                rows=[list(r) for r in sec.rows],
                blank_rows=sec.blank_rows,
                max_rows=len(sec.rows) if sec.rows else sheets.MAX_ROWS,
                note=sec.note,
            )
            for sec in s.sections
        ],
        json_schema=sheets.model(s.kind).model_json_schema(),
    )


@router.get("/sheet-kinds", response_model=list[SheetKindOut])
async def sheet_kinds(_: User) -> list[SheetKindOut]:
    return [_kind_out(s) for s in sheets.SHEETS]


# ---------------------------------------------------------------- 도입


class EngagementCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)


class EngagementPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)


class EngagementSummary(BaseModel):
    id: int
    name: str
    description: str | None
    my_role: MemberRole
    created_at: datetime


class MemberOut(BaseModel):
    user_id: int
    username: str | None
    display_name: str
    role: MemberRole


class SheetIndexOut(BaseModel):
    kind: str
    revision: int
    updated_at: datetime | None


class HandoffOut(BaseModel):
    label: str
    filled: bool
    sheets: list[str]


class StageOut(BaseModel):
    stage: int
    title: str
    done: bool
    handoffs: list[HandoffOut]


class LinkedSessionOut(BaseModel):
    id: int
    title: str | None
    status: Literal["open", "submitted", "published"]


class EngagementDetail(EngagementSummary):
    can_edit: bool
    can_manage: bool
    # 보내기마다 이 사람이 할 수 있는가(구성원 역할 + 받는 곳 역할, rbac.can_send)
    can_send: dict[str, bool]
    members: list[MemberOut]
    sheets: list[SheetIndexOut]
    stages: list[StageOut]
    sessions: list[LinkedSessionOut]


def _summary(e: Engagement, role: str) -> EngagementSummary:
    return EngagementSummary(
        id=e.id,
        name=e.name,
        description=e.description,
        my_role=role,  # type: ignore[arg-type]
        created_at=e.created_at,
    )


async def _engagement(
    db: Db, engagement_id: int, user: CurrentUser
) -> tuple[Engagement, str, rbac.EngagementAccess]:
    """구성원이 아니면 없는 도입과 같은 404."""
    role = await store.member_role(db, engagement_id, user.id)
    access = rbac.engagement_access(role)
    if not access.view or role is None:
        raise ProblemError(404, "도입을 찾을 수 없습니다", code="engagement_not_found")
    return await store.get(db, engagement_id), role, access


def _need(allowed: bool) -> None:
    if not allowed:
        raise ProblemError(403, "이 도입에서 할 수 없는 일입니다", code="engagement_forbidden")


async def _detail(
    db: Db, e: Engagement, role: str, access: rbac.EngagementAccess, user: CurrentUser
) -> EngagementDetail:
    states = await store.sheet_states(db, e.id)
    stages = progress.evaluate({s.kind: s.payload for s in states})
    return EngagementDetail(
        **_summary(e, role).model_dump(),
        can_edit=access.edit,
        can_manage=access.manage,
        can_send={b: rbac.can_send(b, user.roles, role) for b in bridges.BRIDGES},
        members=[
            MemberOut(
                user_id=m.user_id,
                username=m.username,
                display_name=m.display_name,
                role=m.role,  # type: ignore[arg-type]
            )
            for m in await store.members(db, e.id)
        ],
        sheets=[
            SheetIndexOut(kind=s.kind, revision=s.revision, updated_at=s.updated_at) for s in states
        ],
        stages=[
            StageOut(
                stage=st.stage,
                title=st.title,
                done=st.done,
                handoffs=[
                    HandoffOut(label=h.label, filled=h.filled, sheets=list(h.sheets))
                    for h in st.handoffs
                ],
            )
            for st in stages
        ],
        sessions=[
            LinkedSessionOut(id=s.id, title=s.title, status=s.status)  # type: ignore[arg-type]
            for s in await store.linked_sessions(db, e.id)
        ],
    )


@router.get("/engagements", response_model=list[EngagementSummary])
async def list_engagements(db: Db, user: User) -> list[EngagementSummary]:
    return [_summary(e, role) for e, role in await store.list_for_user(db, user.id)]


@router.post("/engagements", response_model=EngagementDetail, status_code=201)
async def create_engagement(body: EngagementCreate, db: Db, user: User) -> EngagementDetail:
    if not rbac.can_create_engagement(user.roles):
        raise ProblemError(
            403,
            "도입을 만들 권한이 없습니다",
            code="forbidden",
            extra={"required_roles": [rbac.ROLE_MODELER, rbac.ROLE_ADMIN]},
        )
    e = await store.create(
        db, actor_id=user.id, name=body.name.strip(), description=body.description
    )
    return await _detail(
        db, e, rbac.ENGAGEMENT_OWNER, rbac.engagement_access(rbac.ENGAGEMENT_OWNER), user
    )


@router.get("/engagements/{engagement_id}", response_model=EngagementDetail)
async def get_engagement(engagement_id: int, db: Db, user: User) -> EngagementDetail:
    e, role, access = await _engagement(db, engagement_id, user)
    return await _detail(db, e, role, access, user)


@router.patch("/engagements/{engagement_id}", response_model=EngagementDetail)
async def patch_engagement(
    engagement_id: int, body: EngagementPatch, db: Db, user: User
) -> EngagementDetail:
    e, role, access = await _engagement(db, engagement_id, user)
    _need(access.manage)
    await store.update_info(
        db,
        e,
        actor_id=user.id,
        name=body.name.strip() if body.name is not None else None,
        description=body.description,
    )
    return await _detail(db, e, role, access, user)


# ---------------------------------------------------------------- 구성원


class MemberIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    role: MemberRole


def _last_owner() -> ProblemError:
    return ProblemError(409, "마지막 owner 는 빼거나 낮출 수 없습니다", code="last_owner")


@router.post("/engagements/{engagement_id}/members", response_model=MemberOut)
async def set_member(engagement_id: int, body: MemberIn, db: Db, user: User) -> MemberOut:
    _, _, access = await _engagement(db, engagement_id, user)
    _need(access.manage)
    try:
        m = await store.set_member(
            db, engagement_id, actor_id=user.id, username=body.username, role=body.role
        )
    except store.UserNotFound as e:
        raise ProblemError(404, "그 아이디의 사용자가 없습니다", code="user_not_found") from e
    except store.LastOwner as e:
        raise _last_owner() from e
    return MemberOut(
        user_id=m.user_id, username=m.username, display_name=m.display_name, role=body.role
    )


@router.delete("/engagements/{engagement_id}/members/{user_id}", status_code=204)
async def remove_member(engagement_id: int, user_id: int, db: Db, user: User) -> None:
    _, _, access = await _engagement(db, engagement_id, user)
    _need(access.manage)
    try:
        await store.remove_member(db, engagement_id, actor_id=user.id, user_id=user_id)
    except store.UserNotFound as e:
        raise ProblemError(404, "구성원이 아닙니다", code="member_not_found") from e
    except store.LastOwner as e:
        raise _last_owner() from e


# ---------------------------------------------------------------- 양식


class SheetOut(BaseModel):
    kind: str
    revision: int  # 0 = 아직 저장한 적 없음
    payload: dict[str, Any]
    updated_at: datetime | None
    updated_by: int | None


class SheetIn(BaseModel):
    revision: int = Field(ge=0)  # 읽을 때 받은 판
    payload: dict[str, Any]


def _sheet_kind(kind: str) -> sheets.Sheet:
    s = sheets.get(kind)
    if s is None:
        raise ProblemError(404, "그런 양식이 없습니다", code="sheet_kind_not_found")
    return s


def _sheet_out(s: store.SheetState) -> SheetOut:
    return SheetOut(
        kind=s.kind,
        revision=s.revision,
        payload=s.payload,
        updated_at=s.updated_at,
        updated_by=s.updated_by,
    )


@router.get("/engagements/{engagement_id}/sheets/{kind}", response_model=SheetOut)
async def read_sheet(engagement_id: int, kind: str, db: Db, user: User) -> SheetOut:
    await _engagement(db, engagement_id, user)
    _sheet_kind(kind)
    return _sheet_out(await store.read_sheet(db, engagement_id, kind))


@router.put("/engagements/{engagement_id}/sheets/{kind}", response_model=SheetOut)
async def save_sheet(engagement_id: int, kind: str, body: SheetIn, db: Db, user: User) -> SheetOut:
    _, _, access = await _engagement(db, engagement_id, user)
    _need(access.edit)
    _sheet_kind(kind)
    try:
        saved = await store.save_sheet(
            db, engagement_id, kind, actor_id=user.id, revision=body.revision, payload=body.payload
        )
    except sheets.SheetInvalid as e:
        raise ProblemError(
            422, "양식 값이 올바르지 않습니다", code="sheet_invalid", extra={"issues": e.issues}
        ) from e
    except store.RevisionConflict as e:
        raise ProblemError(
            409,
            "다른 사람이 먼저 저장했습니다 — 다시 읽고 고쳐 주세요",
            code="revision_conflict",
            extra={"current_revision": e.current},
        ) from e
    return _sheet_out(saved)


# ---------------------------------------------------------------- 보내기(Phase 21, 결정 A5)


class SendChangeOut(BaseModel):
    op: str
    target: str
    field: str | None
    before: str | None
    after: str | None
    source: str
    # null = 적용한다, "" = 이미 같다, 그 밖 = 막힌 까닭
    blocked: str | None


class SendIn(BaseModel):
    version_id: int | None = None  # draft 보내기: 넣을 draft(없으면 게시본으로 새 draft)


class SendApplyIn(SendIn):
    fingerprint: str = Field(min_length=64, max_length=64)  # 미리보기에서 받은 지문


class SendPreviewOut(BaseModel):
    bridge: bridges.Bridge
    changes: list[SendChangeOut]
    applicable: int
    fingerprint: str
    version_id: int | None
    golden_set_id: int | None
    golden_set_name: str | None


class SendAppliedOut(BaseModel):
    bridge: bridges.Bridge
    applied: int
    version_id: int | None
    golden_set_id: int | None
    # 새 계정의 임시 비밀번호 — 이 응답에서 한 번만 보인다
    passwords: dict[str, str]


async def _sender(db: Db, engagement_id: int, bridge: str, user: CurrentUser) -> bridges.Bridge:
    _, role, _ = await _engagement(db, engagement_id, user)
    if bridge not in bridges.BRIDGES:
        raise ProblemError(404, "그런 보내기가 없습니다", code="bridge_not_found")
    if not rbac.can_send(bridge, user.roles, role):
        raise ProblemError(
            403,
            "이 보내기를 할 권한이 없습니다",
            code="forbidden",
            extra={"required_roles": list(rbac.SEND_ROLES[bridge])},
        )
    return bridge


def _send_error(e: send.SendError) -> ProblemError:
    return ProblemError(e.status, str(e), code=e.code)


@router.post("/engagements/{engagement_id}/send/{bridge}/preview", response_model=SendPreviewOut)
async def send_preview(
    engagement_id: int, bridge: str, body: SendIn, db: Db, user: User
) -> SendPreviewOut:
    b = await _sender(db, engagement_id, bridge, user)
    try:
        p = await send.preview(db, engagement_id, b, version_id=body.version_id)
    except send.SendError as e:
        raise _send_error(e) from e
    return SendPreviewOut(
        bridge=p.bridge,
        changes=[SendChangeOut(**asdict(c)) for c in p.changes],
        applicable=len(bridges.applicable(p.changes)),
        fingerprint=p.fingerprint,
        version_id=p.version_id,
        golden_set_id=p.golden_set_id,
        golden_set_name=p.golden_set_name,
    )


@router.post("/engagements/{engagement_id}/send/{bridge}/apply", response_model=SendAppliedOut)
async def send_apply(
    engagement_id: int, bridge: str, body: SendApplyIn, db: Db, user: User
) -> SendAppliedOut:
    b = await _sender(db, engagement_id, bridge, user)
    try:
        r = await send.apply(
            db,
            engagement_id,
            b,
            actor_id=user.id,
            fingerprint=body.fingerprint,
            version_id=body.version_id,
        )
    except send.SendError as e:
        raise _send_error(e) from e
    return SendAppliedOut(
        bridge=b,
        applied=r.applied,
        version_id=r.version_id,
        golden_set_id=r.golden_set_id,
        passwords=r.passwords,
    )


# ---------------------------------------------------------------- 인터뷰 잇기(21e, 결정 A11)


class SessionLinkIn(BaseModel):
    session_id: int


@router.post("/engagements/{engagement_id}/sessions", response_model=EngagementDetail)
async def link_session(
    engagement_id: int, body: SessionLinkIn, db: Db, user: User
) -> EngagementDetail:
    e, role, access = await _engagement(db, engagement_id, user)
    _need(access.edit)
    try:
        await store.link_session(
            db, engagement_id, body.session_id, actor_id=user.id, roles=user.roles
        )
    except store.NotFound as err:
        # 볼 수 없는 인터뷰는 없는 인터뷰와 같은 말(워크숍과 같다)
        raise ProblemError(404, "인터뷰를 찾을 수 없습니다", code="session_not_found") from err
    return await _detail(db, e, role, access, user)


@router.delete("/engagements/{engagement_id}/sessions/{session_id}", status_code=204)
async def unlink_session(engagement_id: int, session_id: int, db: Db, user: User) -> None:
    _, _, access = await _engagement(db, engagement_id, user)
    _need(access.edit)
    try:
        await store.unlink_session(db, engagement_id, session_id, actor_id=user.id)
    except store.NotFound as err:
        raise ProblemError(
            404, "이 도입에 이은 인터뷰가 아닙니다", code="session_not_found"
        ) from err
