"""Action 요청·승인·롤백과 알림(PRD 5.3, TRD 17.1) — 검증과 위임만 한다.

상태 전이·재검증·발송은 ``actions.runner``, 권한 판정은 ``security.rbac`` 에 있다. 읽을 수
없는 타입의 Action·실행 기록은 없는 것과 같은 404 다. 수신자 연락처는 마스킹해서만 내보낸다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ontobot.actions import runner
from ontobot.api.deps import CurrentUser, Db, User
from ontobot.api.errors import ProblemError
from ontobot.config import get_settings
from ontobot.db.models.actions import ActionRun, Notification, NotificationDelivery
from ontobot.db.models.security import AppUser
from ontobot.notify import config as notify_config
from ontobot.ontology import registry
from ontobot.ontology.models import ActionTypeDef
from ontobot.security import rbac
from ontobot.security.pii import mask_contact

router = APIRouter(tags=["actions"])

RunStatus = Literal["previewed", "blocked", "rejected", "executed", "failed", "rolled_back"]


def _actor(user: CurrentUser) -> runner.Actor:
    return runner.Actor(user.id, user.roles)


def _cfg() -> notify_config.NotifyConfig:
    return notify_config.load(get_settings().notify_config_path)


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except runner.ActionError as e:
        # 파라미터 오류는 게시 전 검사와 같은 모양(issues)으로 — 화면이 한 목록으로 보여 준다.
        extra = {"issues": [{"path": "params", "message": m} for m in e.errors]}
        raise ProblemError(e.status, str(e), code=e.code, extra=extra if e.errors else None) from e


# ---------------------------------------------------------------- Action 목록


class ActionTypeView(ActionTypeDef):
    target_display_name: str
    can_request: bool


@router.get("/action-types", response_model=list[ActionTypeView])
async def list_action_types(db: Db, user: User) -> list[ActionTypeView]:
    """지금 게시본의 Action 중 대상 타입을 읽을 수 있는 것."""
    schema = registry.current()
    if schema is None:
        return []
    readable = await rbac.readable_types(db, user.id)
    out = []
    for a in schema.document.action_types:
        t = schema.document.object_type(a.target_type)
        if t is None or t.api_name not in readable:
            continue
        out.append(
            ActionTypeView(
                **a.model_dump(),
                target_display_name=t.display_name,
                can_request=rbac.can_request_action(user.roles, a.requester_roles),
            )
        )
    return out


# ---------------------------------------------------------------- 실행 기록


class PreviewRequest(BaseModel):
    target_pk: str = Field(min_length=1, max_length=255)
    params: dict[str, Any] = Field(default_factory=dict)
    # 클라이언트가 만든 요청 ID — 같은 요청을 다시 보내도 한 번만 받는다(PRD 5.3).
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class DeliveryOut(BaseModel):
    channel: Literal["email", "sms"]
    kind: Literal["notify", "compensation"]
    recipient: str  # 마스킹
    status: Literal["pending", "sent", "failed", "cancelled"]
    attempts: int
    sent_at: datetime | None
    error: str | None


class PreviewTarget(BaseModel):
    type: str
    pk: str
    title: str


class PreviewCondition(BaseModel):
    property: str
    display_name: str
    op: str
    expected: str
    actual: str
    ok: bool


class PreviewChange(BaseModel):
    property: str
    display_name: str
    before: str
    after: str


class PreviewNotify(BaseModel):
    channel: Literal["app", "email", "sms"]
    count: int
    recipients: list[str]  # 마스킹
    body: str
    irreversible: bool


class PreviewOut(BaseModel):
    """승인자가 보는 그대로(``actions.runner._preview_json``)."""

    target: PreviewTarget
    conditions: list[PreviewCondition]
    changes: list[PreviewChange]
    notify: list[PreviewNotify]
    blocked: list[str]
    warnings: list[str]


class ActionRunOut(BaseModel):
    id: int
    request_id: str
    action_type: str
    action_display_name: str
    target_type: str
    target_pk: str
    params: dict[str, Any]
    status: RunStatus
    origin: str
    preview: PreviewOut
    requested_by: str | None
    decided_by: str | None
    decided_at: datetime | None
    reason: str | None
    executed_at: datetime | None
    rolled_back_at: datetime | None
    error: str | None
    created_at: datetime
    # 지금 사용자가 할 수 있는 일(화면 단추)
    can_approve: bool
    can_reject: bool
    can_rollback: bool
    deliveries: list[DeliveryOut]


async def _names(db: Db, ids: set[int | None]) -> dict[int, str]:
    wanted = [i for i in ids if i is not None]
    if not wanted:
        return {}
    rows = await db.execute(select(AppUser.id, AppUser.display_name).where(AppUser.id.in_(wanted)))
    return {r.id: r.display_name for r in rows}


async def _out(db: Db, user: CurrentUser, runs: list[ActionRun]) -> list[ActionRunOut]:
    schema = registry.current()
    ids: set[int | None] = {r.decided_by for r in runs}
    ids.update(r.requested_by for r in runs)
    names = await _names(db, ids)
    deliveries: dict[int, list[NotificationDelivery]] = {}
    if runs:
        for d in await db.scalars(
            select(NotificationDelivery)
            .where(NotificationDelivery.action_run_id.in_([r.id for r in runs]))
            .order_by(NotificationDelivery.id)
        ):
            deliveries.setdefault(d.action_run_id, []).append(d)
    out = []
    for r in runs:
        a = schema.document.action_type(r.action_type) if schema is not None else None
        is_requester = r.requested_by == user.id
        approval_roles = a.approval.roles if a is not None else []
        out.append(
            ActionRunOut(
                id=r.id,
                request_id=r.request_id,
                action_type=r.action_type,
                action_display_name=a.display_name if a is not None else r.action_type,
                target_type=r.target_type,
                target_pk=r.target_pk,
                params=r.params,
                status=r.status,  # type: ignore[arg-type]
                origin=r.origin,
                preview=PreviewOut.model_validate(r.preview),
                requested_by=names.get(r.requested_by),
                decided_by=names.get(r.decided_by) if r.decided_by else None,
                decided_at=r.decided_at,
                reason=r.reason,
                executed_at=r.executed_at,
                rolled_back_at=r.rolled_back_at,
                error=r.error,
                created_at=r.created_at,
                can_approve=r.status == "previewed"
                and a is not None
                and rbac.can_decide_action(
                    user.roles,
                    a.approval.roles,
                    allow_self=a.approval.allow_self,
                    is_requester=is_requester,
                ),
                can_reject=r.status in {"previewed", "blocked"}
                and (
                    rbac.can_withdraw_action(is_requester=is_requester)
                    or (
                        a is not None
                        and rbac.can_decide_action(
                            user.roles, a.approval.roles, allow_self=False, is_requester=False
                        )
                    )
                ),
                can_rollback=r.status == "executed"
                and rbac.can_rollback_action(user.roles, approval_roles),
                deliveries=[
                    DeliveryOut(
                        channel=d.channel,  # type: ignore[arg-type]
                        kind=d.kind,  # type: ignore[arg-type]
                        recipient=mask_contact(d.recipient),
                        status=d.status,  # type: ignore[arg-type]
                        attempts=d.attempts,
                        sent_at=d.sent_at,
                        error=d.error,
                    )
                    for d in deliveries.get(r.id, [])
                ],
            )
        )
    return out


async def _one(db: Db, user: CurrentUser, run_id: int) -> ActionRunOut:
    run = await db.get(ActionRun, run_id, populate_existing=True)
    if run is None or run.target_type not in await rbac.readable_types(db, user.id):
        raise ProblemError(404, "실행 기록을 찾을 수 없습니다", code="action_not_found")
    return (await _out(db, user, [run]))[0]


@router.post("/action-types/{name}/preview", response_model=ActionRunOut, status_code=201)
async def preview_action(name: str, body: PreviewRequest, db: Db, user: User) -> ActionRunOut:
    """미리보기 — 실행하지 않는다. 바뀔 값·사전 조건·발송 요약을 기록으로 남긴다."""
    with _errors():
        run = await runner.preview(
            db,
            _actor(user),
            action=name,
            target_pk=body.target_pk,
            params=body.params,
            request_id=body.request_id,
            cfg=_cfg(),
        )
    return await _one(db, user, run.id)


@router.get("/action-runs", response_model=list[ActionRunOut])
async def list_runs(
    db: Db,
    user: User,
    scope: Annotated[Literal["mine", "to_decide", "all"], Query()] = "mine",
    status: Annotated[RunStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ActionRunOut]:
    """``mine`` 내가 요청한 것, ``to_decide`` 내가 승인할 대기 건, ``all`` 읽을 수 있는 전부."""
    readable = await rbac.readable_types(db, user.id)
    q = select(ActionRun).where(ActionRun.target_type.in_(readable))
    if scope == "mine":
        q = q.where(ActionRun.requested_by == user.id)
    if scope == "to_decide":
        q = q.where(ActionRun.status == "previewed")
    if status is not None:
        q = q.where(ActionRun.status == status)
    runs = list(await db.scalars(q.order_by(ActionRun.id.desc()).limit(limit)))
    out = await _out(db, user, runs)
    if scope == "to_decide":
        out = [r for r in out if r.can_approve]
    return out


@router.get("/action-runs/{run_id}", response_model=ActionRunOut)
async def get_run(run_id: int, db: Db, user: User) -> ActionRunOut:
    return await _one(db, user, run_id)


@router.post("/action-runs/{run_id}/approve", response_model=ActionRunOut)
async def approve_run(run_id: int, db: Db, user: User) -> ActionRunOut:
    """승인 = 실행. 실행 직전 재검증에 실패하면 ``failed`` 기록이 돌아온다(200)."""
    with _errors():
        await runner.approve(db, _actor(user), run_id, _cfg())
    return await _one(db, user, run_id)


class RejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.post("/action-runs/{run_id}/reject", response_model=ActionRunOut)
async def reject_run(run_id: int, body: RejectRequest, db: Db, user: User) -> ActionRunOut:
    with _errors():
        await runner.reject(db, _actor(user), run_id, body.reason)
    return await _one(db, user, run_id)


@router.post("/action-runs/{run_id}/rollback", response_model=ActionRunOut)
async def rollback_run(run_id: int, db: Db, user: User) -> ActionRunOut:
    """편집을 해제하고, 안 나간 발송은 취소, 나간 발송에는 정정 문구를 보낸다."""
    with _errors():
        await runner.rollback(db, _actor(user), run_id, _cfg())
    return await _one(db, user, run_id)


# ---------------------------------------------------------------- 앱 안 알림


class NotificationOut(BaseModel):
    id: int
    kind: Literal["notify", "compensation"]
    body: str
    action_run_id: int | None
    created_at: datetime
    read_at: datetime | None


class NotificationList(BaseModel):
    unread: int
    items: list[NotificationOut]


@router.get("/notifications", response_model=NotificationList)
async def list_notifications(db: Db, user: User) -> NotificationList:
    unread = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )
    rows = await db.scalars(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.id.desc())
        .limit(50)
    )
    return NotificationList(
        unread=int(unread or 0),
        items=[
            NotificationOut(
                id=n.id,
                kind=n.kind,  # type: ignore[arg-type]
                body=n.body,
                action_run_id=n.action_run_id,
                created_at=n.created_at,
                read_at=n.read_at,
            )
            for n in rows
        ],
    )


@router.post("/notifications/read", status_code=204)
async def mark_read(db: Db, user: User) -> None:
    """내 알림을 모두 읽음으로."""
    await db.execute(
        Notification.__table__.update()  # type: ignore[attr-defined]
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=func.now())
    )
