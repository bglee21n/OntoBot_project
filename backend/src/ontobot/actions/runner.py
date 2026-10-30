"""Action 실행 기록의 상태 전이(PRD 5.3, TRD 17.1).

    previewed ─승인→ executed ─롤백→ rolled_back
        │  └─거부·거둠→ rejected
        └(실행 직전 재검증 실패)→ failed
    blocked(미리보기에서 사전 조건·발송 조건을 못 넘음 — 승인할 수 없다)

- **미리보기는 실행하지 않는다.** 바뀔 값·사전 조건 결과·발송 요약(수신자 마스킹)을 기록에 남긴다.
  승인자는 그것을 보고 결정한다.
- **승인 = 실행.** 같은 트랜잭션에서 게시 버전·대상·사전 조건을 다시 확인하고, 편집 레이어에
  쓰고, 앱 알림과 메일·SMS 발송(아웃박스)을 넣는다. 발송은 커밋 뒤 워커가 한다 — 롤백된 실행의
  문자가 나가지 않는다. 재검증에 실패하면 ``failed`` 로 남긴다(예외로 되돌리지 않는다 — 실패도
  기록이다).
- **멱등성**: ``request_id`` 가 같은 요청은 처음 만든 기록을 돌려준다(PRD 5.3).
- **롤백**: 이 실행이 만든 활성 편집을 해제한다. 아직 안 나간 발송은 취소하고, 이미 나간
  메일·SMS·앱 알림 수신자에게는 정정 문구를 보낸다(되돌릴 수 없으므로 정정한다).

모든 전이는 감사 로그에 같은 트랜잭션으로 남는다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.actions import rules
from ontobot.db.models.actions import ActionRun, Notification, NotificationDelivery
from ontobot.db.models.security import AppUser, Role, UserRole
from ontobot.jobs import queue
from ontobot.notify.config import NotifyConfig
from ontobot.objects import edits
from ontobot.objects.reader import ObjectRow, resolve
from ontobot.ontology import registry
from ontobot.ontology.models import ActionTypeDef, ObjectTypeDef
from ontobot.query.executor import in_scope
from ontobot.query.validator import check_scopes
from ontobot.security import audit, rbac
from ontobot.security.pii import mask_contact

NOTIFY_JOB = "notify_deliver"
DEFAULT_COMPENSATION = (
    "[정정] '{action}' 실행({target})이 취소되었습니다. 앞서 보낸 알림은 무효입니다."
)


class ActionError(Exception):
    status = 400
    code = "action_error"

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


class NotFound(ActionError):
    status, code = 404, "action_not_found"


class Forbidden(ActionError):
    status, code = 403, "forbidden"


class Invalid(ActionError):
    status, code = 422, "action_invalid"


class Conflict(ActionError):
    status, code = 409, "action_conflict"


@dataclass(frozen=True)
class Actor:
    id: int
    roles: frozenset[str]


# ---------------------------------------------------------------- 계획(미리보기·실행이 함께 쓴다)


@dataclass
class Recipients:
    channel: str
    template: str
    users: list[int]  # app: 받는 사용자
    contacts: list[str]  # email/sms: 원값(저장·발송용)


@dataclass
class Plan:
    action: ActionTypeDef
    target: ObjectTypeDef
    row: ObjectRow
    title: str
    params: dict[str, Any]
    conditions: list[rules.ConditionResult]
    changes: list[rules.Change]
    notify: list[Recipients]
    values: dict[str, str]
    blocked: list[str]  # 막는 이유(사전 조건 밖)
    warnings: list[str]


async def _role_contacts(
    db: AsyncSession, roles: list[str], channel: str
) -> tuple[list[int], list[str], int]:
    """역할의 활성 사용자 → (사용자 id, 연락처, 연락처 없는 사용자 수)."""
    if not roles:
        return [], [], 0
    rows = (
        await db.execute(
            select(AppUser.id, AppUser.email, AppUser.phone)
            .join(UserRole, UserRole.user_id == AppUser.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.name.in_(roles), AppUser.status == "active")
            .distinct()
            .order_by(AppUser.id)
        )
    ).all()
    ids = [r.id for r in rows]
    if channel == "app":
        return ids, [], 0
    contacts = [c for r in rows if (c := (r.email if channel == "email" else r.phone))]
    return ids, contacts, len(rows) - len(contacts)


async def _plan(
    db: AsyncSession,
    a: ActionTypeDef,
    t: ObjectTypeDef,
    target_pk: str,
    raw_params: dict[str, Any],
    cfg: NotifyConfig,
) -> Plan:
    params, errors = rules.convert_params(a, raw_params)
    if errors:
        raise Invalid("파라미터를 확인하세요", errors)
    found = await resolve(db, t.api_name, [target_pk], declared=[p.api_name for p in t.properties])
    row = found.get(target_pk)
    if row is None:
        raise NotFound("대상 객체를 찾을 수 없습니다")
    title = str(row.properties.get(t.title_property) or target_pk)
    values = rules.template_values(a, t, params, target_pk, title)
    blocked: list[str] = []
    warnings: list[str] = []
    notify: list[Recipients] = []
    for n in a.notify:
        users, contacts, missing = await _role_contacts(db, n.to_roles, n.channel)
        if n.to_property is not None:
            value = row.properties.get(n.to_property)
            if value:
                contacts.append(str(value))
            else:
                warnings.append(f"대상 객체의 '{n.to_property}' 연락처가 비어 있습니다")
        if missing:
            warnings.append(f"{n.channel} 연락처가 없는 사용자 {missing}명은 받지 못합니다")
        contacts = list(dict.fromkeys(contacts))  # 같은 연락처는 한 번만
        if n.channel != "app":
            if not cfg.enabled(n.channel):
                blocked.append(f"{n.channel} 발송 채널이 설정되지 않았습니다(config/notify.yaml)")
            elif cfg.is_fake(n.channel):
                warnings.append(
                    f"{n.channel} 채널은 개발용 가짜 게이트웨이입니다 — 실제로 보내지 않습니다"
                )
            if len(contacts) > cfg.max_recipients:
                blocked.append(
                    f"{n.channel} 수신자 {len(contacts)}명이 상한 {cfg.max_recipients}명을 넘습니다"
                )
        notify.append(Recipients(n.channel, n.template, users, contacts))
    return Plan(
        action=a,
        target=t,
        row=row,
        title=title,
        params=params,
        conditions=rules.check_conditions(t, a.preconditions, row.properties),
        changes=rules.planned_changes(a, t, params, row.properties),
        notify=notify,
        values=values,
        blocked=blocked,
        warnings=warnings,
    )


def _preview_json(p: Plan) -> dict[str, Any]:
    return {
        "target": {"type": p.target.api_name, "pk": p.row.pk, "title": p.title},
        "conditions": [asdict(c) for c in p.conditions],
        "changes": [
            {
                "property": c.property,
                "display_name": c.display_name,
                "before": c.before_display,
                "after": c.after_display,
            }
            for c in p.changes
        ],
        "notify": [
            {
                "channel": r.channel,
                "count": len(r.users) if r.channel == "app" else len(r.contacts),
                "recipients": [] if r.channel == "app" else [mask_contact(c) for c in r.contacts],
                "body": rules.render(r.template, p.values),
                "irreversible": r.channel != "app",
            }
            for r in p.notify
        ],
        "blocked": p.blocked
        + [
            f"사전 조건: {c.display_name} {c.op} {c.expected} (지금 {c.actual})"
            for c in p.conditions
            if not c.ok
        ],
        "warnings": p.warnings,
    }


def _schema_action(name: str) -> tuple[int, ActionTypeDef, ObjectTypeDef]:
    schema = registry.current()
    a = schema.document.action_type(name) if schema is not None else None
    t = schema.document.object_type(a.target_type) if schema is not None and a is not None else None
    if schema is None or a is None or t is None:
        raise NotFound("Action 을 찾을 수 없습니다")
    return schema.version_id, a, t


# ---------------------------------------------------------------- 미리보기


async def preview(
    db: AsyncSession,
    actor: Actor,
    *,
    action: str,
    target_pk: str,
    params: dict[str, Any],
    request_id: str,
    cfg: NotifyConfig,
    origin: str = "ui",
) -> ActionRun:
    existing = await db.scalar(select(ActionRun).where(ActionRun.request_id == request_id))
    if existing is not None:
        if existing.requested_by != actor.id:
            raise Conflict("이미 쓰인 요청 ID 입니다")
        return existing  # 같은 요청의 재전송 — 처음 기록을 돌려준다(멱등)
    version_id, a, t = _schema_action(action)
    acc = await rbac.access(db, actor.id)
    if t.api_name not in acc.readable:
        raise NotFound("Action 을 찾을 수 없습니다")  # 읽을 수 없는 타입의 Action 은 없는 것과 같다
    if not rbac.can_request_action(actor.roles, a.requester_roles):
        raise Forbidden("이 Action 을 요청할 권한이 없습니다")
    if t.api_name not in acc.editable:
        # 요청 = 대상 값을 바꾸자는 것 — 대상 타입 편집 권한(데이터 그룹)이 있어야 한다(Phase 14).
        raise Forbidden("이 타입을 편집할 권한이 없습니다")
    await _check_rows(db, acc, t.api_name, target_pk)
    plan = await _plan(db, a, t, target_pk, params, cfg)
    pv = _preview_json(plan)
    run = ActionRun(
        request_id=request_id,
        action_type=a.api_name,
        ontology_version_id=version_id,
        target_type=t.api_name,
        target_pk=target_pk,
        params=plan.params,
        status="blocked" if pv["blocked"] else "previewed",
        origin=origin,
        preview=pv,
        requested_by=actor.id,
    )
    db.add(run)
    await db.flush()
    audit.record(
        db,
        audit.ACTION_PREVIEWED,
        actor_id=actor.id,
        target=f"action_run:{run.id}",
        payload={
            "action": a.api_name,
            "target": f"{t.api_name}/{target_pk}",
            "params": plan.params,
            "status": run.status,
            "blocked": pv["blocked"],
        },
    )
    return run


# ---------------------------------------------------------------- 승인(= 실행)·거부


async def _check_rows(db: AsyncSession, acc: rbac.Access, type_name: str, pk: str) -> None:
    """행 정책(Phase 14b) — 읽기 정책 밖의 대상은 없는 것과 같고, 쓰기 정책 밖이면 요청할 수
    없다."""
    schema = registry.current()
    if schema is None:
        return
    ta = acc.of(type_name)
    raw = {k: v for k, v in (("read", ta.read_rows), ("write", ta.write_rows)) if v is not None}
    if not raw:
        return
    scopes = {k: check_scopes(schema.document, {type_name: v})[type_name] for k, v in raw.items()}
    if "read" in scopes and pk not in await in_scope(db, type_name, [pk], scopes["read"]):
        raise NotFound("대상 객체를 찾을 수 없습니다")
    if "write" in scopes and pk not in await in_scope(db, type_name, [pk], scopes["write"]):
        raise Forbidden("이 객체를 편집할 권한이 없습니다(행 정책)")


async def _locked(db: AsyncSession, run_id: int) -> ActionRun:
    run = await db.get(ActionRun, run_id, with_for_update=True)
    if run is None:
        raise NotFound("실행 기록을 찾을 수 없습니다")
    return run


async def _definition(
    db: AsyncSession, actor: Actor, run: ActionRun
) -> tuple[ActionTypeDef | None, ObjectTypeDef | None]:
    """기록의 Action 정의(지금 게시본에서). 읽을 수 없는 타입이면 기록도 없는 것과 같다."""
    if run.target_type not in await rbac.readable_types(db, actor.id):
        raise NotFound("실행 기록을 찾을 수 없습니다")
    schema = registry.current()
    a = schema.document.action_type(run.action_type) if schema is not None else None
    t = schema.document.object_type(run.target_type) if schema is not None else None
    return a, t


def _fail(db: AsyncSession, run: ActionRun, actor: Actor, error: str) -> ActionRun:
    run.status = "failed"
    run.error = error
    run.decided_by = actor.id
    run.decided_at = datetime.now(UTC)
    audit.record(
        db,
        audit.ACTION_FAILED,
        actor_id=actor.id,
        target=f"action_run:{run.id}",
        payload={"action": run.action_type, "error": error},
    )
    return run


async def approve(db: AsyncSession, actor: Actor, run_id: int, cfg: NotifyConfig) -> ActionRun:
    run = await _locked(db, run_id)
    a, t = await _definition(db, actor, run)
    if run.status != "previewed":
        raise Conflict(f"승인할 수 있는 상태가 아닙니다({run.status})")
    schema = registry.current()
    if a is None or t is None or schema is None or schema.version_id != run.ontology_version_id:
        if a is not None and not rbac.can_decide_action(
            actor.roles,
            a.approval.roles,
            allow_self=a.approval.allow_self,
            is_requester=actor.id == run.requested_by,
        ):
            raise Forbidden("이 Action 을 승인할 권한이 없습니다")
        return _fail(db, run, actor, "게시 버전이 바뀌었습니다 — 새 정의로 다시 요청하세요")
    if not rbac.can_decide_action(
        actor.roles,
        a.approval.roles,
        allow_self=a.approval.allow_self,
        is_requester=actor.id == run.requested_by,
    ):
        raise Forbidden("이 Action 을 승인할 권한이 없습니다")
    try:
        # 재검증: 요청자의 쓰기 행 정책(미리보기 뒤 대상 값이 바뀌어 정책 밖이 됐을 수 있다).
        await _check_rows(db, await rbac.access(db, run.requested_by), t.api_name, run.target_pk)
    except (NotFound, Forbidden) as e:
        return _fail(db, run, actor, f"요청자의 편집 범위 밖입니다: {e}")
    try:
        plan = await _plan(db, a, t, run.target_pk, run.params, cfg)
    except ActionError as e:
        return _fail(db, run, actor, f"{e}: {'; '.join(e.errors)}" if e.errors else str(e))
    pv = _preview_json(plan)
    if pv["blocked"]:
        return _fail(
            db, run, actor, "실행 직전 다시 확인했더니 막혔습니다: " + "; ".join(pv["blocked"])
        )

    now = datetime.now(UTC)
    for c in plan.changes:
        await edits.apply_edit(
            db,
            object_type=t.api_name,
            pk=run.target_pk,
            prop=c.property,
            value=c.after,
            edited_by=run.requested_by,
            action_run_id=run.id,
            now=now,
        )
    deliveries = 0
    for r in plan.notify:
        body = rules.render(r.template, plan.values)
        if r.channel == "app":
            db.add_all(
                Notification(user_id=u, action_run_id=run.id, kind="notify", body=body)
                for u in r.users
            )
            continue
        for contact in r.contacts:
            db.add(
                NotificationDelivery(
                    action_run_id=run.id,
                    channel=r.channel,
                    kind="notify",
                    recipient=contact,
                    subject=f"[OntoBot] {a.display_name}",
                    body=body,
                    status="pending",
                    attempts=0,
                    next_attempt_at=now,
                )
            )
            deliveries += 1
    if deliveries:
        await queue.enqueue(db, NOTIFY_JOB, {}, dedupe=True)
    run.status = "executed"
    run.preview = pv  # 실행 때의 값으로 갱신 — 기록이 실제로 한 일과 같아야 한다
    run.decided_by = actor.id
    run.decided_at = now
    run.executed_at = now
    await db.flush()
    audit.record(
        db,
        audit.ACTION_EXECUTED,
        actor_id=actor.id,
        target=f"action_run:{run.id}",
        payload={
            "action": a.api_name,
            "target": f"{t.api_name}/{run.target_pk}",
            "requested_by": run.requested_by,
            "changes": pv["changes"],
            "deliveries": deliveries,
        },
    )
    return run


async def reject(db: AsyncSession, actor: Actor, run_id: int, reason: str | None) -> ActionRun:
    """승인자가 거부하거나 요청자가 거둔다. 미리보기(또는 막힘) 상태만."""
    run = await _locked(db, run_id)
    a, _ = await _definition(db, actor, run)
    if run.status not in {"previewed", "blocked"}:
        raise Conflict(f"거부할 수 있는 상태가 아닙니다({run.status})")
    is_requester = actor.id == run.requested_by
    allowed = rbac.can_withdraw_action(is_requester=is_requester) or (
        a is not None
        and rbac.can_decide_action(
            actor.roles, a.approval.roles, allow_self=a.approval.allow_self, is_requester=False
        )
    )
    if not allowed:
        raise Forbidden("이 Action 을 거부할 권한이 없습니다")
    run.status = "rejected"
    run.reason = reason or ("요청자가 거뒀습니다" if is_requester else None)
    run.decided_by = actor.id
    run.decided_at = datetime.now(UTC)
    audit.record(
        db,
        audit.ACTION_REJECTED,
        actor_id=actor.id,
        target=f"action_run:{run.id}",
        payload={"action": run.action_type, "reason": run.reason},
    )
    return run


# ---------------------------------------------------------------- 롤백


@dataclass(frozen=True)
class RollbackResult:
    run: ActionRun
    cleared_edits: int
    superseded_edits: int  # 이미 다른 편집으로 바뀌었거나 해제돼 되돌리지 않은 것
    cancelled_deliveries: int
    compensations: int


async def rollback(
    db: AsyncSession, actor: Actor, run_id: int, cfg: NotifyConfig
) -> RollbackResult:
    run = await _locked(db, run_id)
    a, t = await _definition(db, actor, run)
    if run.status != "executed":
        raise Conflict(f"롤백할 수 있는 상태가 아닙니다({run.status})")
    approval_roles = a.approval.roles if a is not None else []
    if not rbac.can_rollback_action(actor.roles, approval_roles):
        raise Forbidden("이 Action 을 롤백할 권한이 없습니다")
    now = datetime.now(UTC)

    made = list(
        await db.execute(
            text("SELECT id, cleared_at FROM object_edit WHERE action_run_id = :r ORDER BY id"),
            {"r": run.id},
        )
    )
    cleared = 0
    for e in made:
        if e.cleared_at is None:
            await edits.clear_edit(db, e.id, actor.id, now)
            cleared += 1

    cancelled = (
        await db.execute(
            text(
                "UPDATE notification_delivery SET status = 'cancelled' "
                "WHERE action_run_id = :r AND kind = 'notify' AND status = 'pending'"
            ),
            {"r": run.id},
        )
    ).rowcount or 0  # type: ignore[attr-defined]

    title = str(run.preview.get("target", {}).get("title") or run.target_pk)
    if a is not None and t is not None:
        values = rules.template_values(a, t, run.params, run.target_pk, title)
    else:  # 정의가 게시본에서 사라졌어도 정정은 보낸다
        values = {"action": run.action_type, "target": title, "target_pk": run.target_pk}
    template = a.compensation if a is not None and a.compensation else DEFAULT_COMPENSATION
    body = rules.render(template, values)
    sent = list(
        await db.execute(
            text(
                "SELECT channel, recipient FROM notification_delivery "
                "WHERE action_run_id = :r AND kind = 'notify' AND status IN ('sent','failed')"
            ),
            {"r": run.id},
        )
    )
    # 실패한 발송도 게이트웨이까지는 갔을 수 있다 — 정정은 넉넉하게 보낸다.
    for s in sent:
        db.add(
            NotificationDelivery(
                action_run_id=run.id,
                channel=s.channel,
                kind="compensation",
                recipient=s.recipient,
                subject=f"[OntoBot 정정] {values['action']}",
                body=body,
                status="pending",
                attempts=0,
                next_attempt_at=now,
            )
        )
    app_users = list(
        await db.scalars(
            select(Notification.user_id).where(
                Notification.action_run_id == run.id, Notification.kind == "notify"
            )
        )
    )
    db.add_all(
        Notification(user_id=u, action_run_id=run.id, kind="compensation", body=body)
        for u in dict.fromkeys(app_users)
    )
    if sent:
        await queue.enqueue(db, NOTIFY_JOB, {}, dedupe=True)

    run.status = "rolled_back"
    run.rolled_back_by = actor.id
    run.rolled_back_at = now
    await db.flush()
    result = RollbackResult(run, cleared, len(made) - cleared, int(cancelled), len(sent))
    audit.record(
        db,
        audit.ACTION_ROLLED_BACK,
        actor_id=actor.id,
        target=f"action_run:{run.id}",
        payload={
            "action": run.action_type,
            "cleared_edits": result.cleared_edits,
            "superseded_edits": result.superseded_edits,
            "cancelled_deliveries": result.cancelled_deliveries,
            "compensations": result.compensations,
        },
    )
    return result
