"""Action 미리보기 → 승인(실행) → 발송 → 롤백(PRD 5.3, TRD 17.1, v1 계획 Phase 10 완료 기준).

게시본을 새로 만들지 않는다 — 지금 게시본(conftest 의 샘플 팩)에 Action 하나를 더한 문서를
레지스트리 자리에 끼운다. 실행 기록의 버전 id 는 실제 게시 버전이다.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update

from ontobot.actions import runner
from ontobot.api import actions as actions_api
from ontobot.db.models.actions import ActionRun, Notification, NotificationDelivery
from ontobot.db.models.jobs import Job
from ontobot.db.models.security import AppUser, AuditLog, DataGroup, GroupTypePermission, Role
from ontobot.db.session import session_scope
from ontobot.notify import channels, delivery
from ontobot.notify.config import NotifyConfig
from ontobot.objects.reader import resolve
from ontobot.objects.store import upsert_objects
from ontobot.ontology import registry
from ontobot.ontology.models import ActionTypeDef
from tests.integration.conftest import MakeUser, add_group, login

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("source_env")]


class World:
    """테스트마다 고유한 역할 둘(요청자·승인자)과 시설 하나, 그 역할을 쓰는 Action."""

    def __init__(self, tmp: Path) -> None:
        tag = uuid.uuid4().hex[:8]
        self.tag = tag
        self.requester_role = f"test_req_{tag}"
        self.approver_role = f"test_apr_{tag}"
        self.pk = f"ACT{tag}-F1"
        self.action = f"adjust_{tag}"
        self.cfg = NotifyConfig.model_validate(
            {
                "channels": {
                    "email": {"adapter": "fake", "dir": str(tmp)},
                    "sms": {"adapter": "fake", "dir": str(tmp)},
                }
            }
        )
        self.tmp = tmp
        self.requester = runner.Actor(0, frozenset())
        self.approver = runner.Actor(0, frozenset())
        self.outsider = runner.Actor(0, frozenset())

    def definition(self, **over: Any) -> ActionTypeDef:
        base: dict[str, Any] = {
            "api_name": self.action,
            "display_name": "위험등급 조정",
            "target_type": "Facility",
            "requester_roles": [self.requester_role],
            "parameters": [{"api_name": "grade", "display_name": "새 등급", "data_type": "int"}],
            "preconditions": [{"property": "risk_grade", "op": "lte", "value": 3}],
            "changes": [{"property": "risk_grade", "param": "grade"}],
            "notify": [
                {
                    "channel": "app",
                    "to_roles": [self.approver_role],
                    "template": "{target} → {grade}",
                },
                {
                    "channel": "email",
                    "to_roles": [self.approver_role],
                    "template": "메일 {target} {grade}",
                },
                {
                    "channel": "sms",
                    "to_roles": [self.approver_role],
                    "template": "문자 {target_pk} {grade}",
                },
            ],
            "approval": {"roles": [self.approver_role]},
            "compensation": "[정정] {target} 등급 조정({grade}) 취소",
        }
        base.update(over)
        return ActionTypeDef.model_validate(base)

    async def create(self) -> World:
        async with session_scope() as db:
            for name in (self.requester_role, self.approver_role):
                r = Role(name=name, description="테스트")
                db.add(r)
                await db.flush()
                await add_group(db, name, ["Facility"])
            await upsert_objects(
                db,
                "Facility",
                [
                    (
                        self.pk,
                        {
                            "facility_id": self.pk,
                            "name": f"{self.tag} 변전소",
                            "facility_type": "변전소",
                            "risk_grade": 2,
                        },
                    )
                ],
            )
            await db.commit()
        return self


@pytest.fixture
def install(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """레지스트리 자리에 Action 을 더한 게시본을 끼운다."""
    base = registry.current()
    assert base is not None

    def _install(*actions: ActionTypeDef) -> None:
        doc = base.document.model_copy(update={"action_types": list(actions)})
        schema = replace(base, document=doc)
        monkeypatch.setattr(registry, "current", lambda: schema)

    yield _install


async def _user(
    make_user: MakeUser, *roles: str, email: str | None = None, phone: str | None = None
) -> tuple[str, str, int]:
    username, password, uid = await make_user(*roles)
    if email or phone:
        async with session_scope() as db:
            u = await db.get(AppUser, uid)
            assert u is not None
            u.email, u.phone = email, phone
            await db.commit()
    return username, password, uid


async def _world(tmp_path: Path, make_user: MakeUser, install: Any, **over: Any) -> World:
    w = await World(tmp_path).create()
    _, _, rid = await _user(make_user, w.requester_role)
    _, _, aid = await _user(
        make_user, w.approver_role, email=f"apr{w.tag}@corp.local", phone="010-5555-1234"
    )
    _, _, oid = await _user(make_user, w.requester_role)  # 승인 역할 없는 다른 요청자
    w.requester = runner.Actor(rid, frozenset({w.requester_role}))
    w.approver = runner.Actor(aid, frozenset({w.approver_role}))
    w.outsider = runner.Actor(oid, frozenset({w.requester_role}))
    install(w.definition(**over))
    return w


async def _preview(w: World, actor: runner.Actor | None = None, **params: Any) -> ActionRun:
    async with session_scope() as db:
        run = await runner.preview(
            db,
            actor or w.requester,
            action=w.action,
            target_pk=w.pk,
            params=params or {"grade": 4},
            request_id=uuid.uuid4().hex,
            cfg=w.cfg,
        )
        await db.commit()
        return run
    raise AssertionError("unreachable")


async def _risk(w: World) -> Any:
    async with session_scope() as db:
        return (await resolve(db, "Facility", [w.pk], ["risk_grade"]))[w.pk].properties[
            "risk_grade"
        ]
    return None


async def _deliveries(run_id: int) -> list[NotificationDelivery]:
    async with session_scope() as db:
        return list(
            await db.scalars(
                select(NotificationDelivery)
                .where(NotificationDelivery.action_run_id == run_id)
                .order_by(NotificationDelivery.id)
            )
        )
    return []


# ---------------------------------------------------------------- 정상 흐름


async def test_preview_approve_execute_and_deliver(tmp_path, make_user, install) -> None:
    w = await _world(tmp_path, make_user, install)
    run = await _preview(w)
    assert run.status == "previewed"
    pv = run.preview
    assert pv["changes"] == [
        {"property": "risk_grade", "display_name": "위험등급", "before": "2", "after": "4"}
    ]
    assert [c["ok"] for c in pv["conditions"]] == [True]
    sms = next(n for n in pv["notify"] if n["channel"] == "sms")
    assert (
        sms["recipients"] == ["***-1234"]
        and sms["irreversible"]
        and sms["body"] == f"문자 {w.pk} 4"
    )
    assert any("가짜 게이트웨이" in m for m in pv["warnings"])
    assert await _risk(w) == 2  # 미리보기는 실행하지 않는다

    async with session_scope() as db:
        done = await runner.approve(db, w.approver, run.id, w.cfg)
        await db.commit()
    assert done.status == "executed" and done.decided_by == w.approver.id
    assert await _risk(w) == 4  # 편집 레이어에 썼다 — 보이는 값이 바뀐다

    ds = await _deliveries(run.id)
    assert {(d.channel, d.status) for d in ds} == {("email", "pending"), ("sms", "pending")}
    async with session_scope() as db:
        notes = list(
            await db.scalars(select(Notification).where(Notification.action_run_id == run.id))
        )
        assert [(n.user_id, n.body) for n in notes] == [(w.approver.id, f"{w.tag} 변전소 → 4")]
        assert await db.scalar(
            select(Job.id).where(Job.kind == "notify_deliver", Job.status == "queued")
        )

    sent = await delivery.deliver_due(w.cfg)
    assert sent["sent"] >= 2
    assert {d.status for d in await _deliveries(run.id)} == {"sent"}
    lines = [
        json.loads(x) for x in (tmp_path / "sms.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {"recipient": "010-5555-1234", "body": f"문자 {w.pk} 4"}.items() <= next(
        x for x in lines if x["body"].endswith(f"{w.pk} 4")
    ).items()

    async with session_scope() as db:
        events = set(
            await db.scalars(
                select(AuditLog.event).where(AuditLog.target == f"action_run:{run.id}")
            )
        )
    assert events == {"action.previewed", "action.executed"}


async def test_same_request_id_is_accepted_once(tmp_path, make_user, install) -> None:
    w = await _world(tmp_path, make_user, install)
    rid = uuid.uuid4().hex
    ids = []
    for _ in range(2):
        async with session_scope() as db:
            run = await runner.preview(
                db,
                w.requester,
                action=w.action,
                target_pk=w.pk,
                params={"grade": 4},
                request_id=rid,
                cfg=w.cfg,
            )
            await db.commit()
            ids.append(run.id)
    assert ids[0] == ids[1]
    async with session_scope() as db:
        with pytest.raises(runner.Conflict):
            await runner.preview(
                db,
                w.outsider,
                action=w.action,
                target_pk=w.pk,
                params={"grade": 4},
                request_id=rid,
                cfg=w.cfg,
            )


# ---------------------------------------------------------------- 권한·막힘


async def test_who_may_request_approve_and_reject(tmp_path, make_user, install) -> None:
    w = await _world(tmp_path, make_user, install)
    _, _, nobody = await _user(make_user)
    async with session_scope() as db:
        # 대상 타입을 못 읽으면 Action 자체가 없는 것과 같다.
        with pytest.raises(runner.NotFound):
            await runner.preview(
                db,
                runner.Actor(nobody, frozenset()),
                action=w.action,
                target_pk=w.pk,
                params={"grade": 4},
                request_id=uuid.uuid4().hex,
                cfg=w.cfg,
            )
    _, _, apr_only = await _user(make_user, w.approver_role)
    async with session_scope() as db:
        with pytest.raises(runner.Forbidden):  # 요청 역할이 아니다
            await runner.preview(
                db,
                runner.Actor(apr_only, frozenset({w.approver_role})),
                action=w.action,
                target_pk=w.pk,
                params={"grade": 4},
                request_id=uuid.uuid4().hex,
                cfg=w.cfg,
            )

    # 요청 역할이 있어도 대상 타입 편집 권한(데이터 그룹)이 없으면 요청할 수 없다(Phase 14).
    async with session_scope() as db:
        gid = await db.scalar(select(DataGroup.id).where(DataGroup.name == w.requester_role))
        await db.execute(
            update(GroupTypePermission)
            .where(GroupTypePermission.group_id == gid)
            .values(can_edit=False)
        )
        await db.commit()
    async with session_scope() as db:
        with pytest.raises(runner.Forbidden, match="편집할 권한"):
            await runner.preview(
                db,
                w.requester,
                action=w.action,
                target_pk=w.pk,
                params={"grade": 4},
                request_id=uuid.uuid4().hex,
                cfg=w.cfg,
            )
        await db.execute(
            update(GroupTypePermission)
            .where(GroupTypePermission.group_id == gid)
            .values(can_edit=True)
        )
        await db.commit()

    run = await _preview(w)
    async with session_scope() as db:
        with pytest.raises(runner.Forbidden):  # 본인 승인 금지(allow_self=false)
            await runner.approve(db, w.requester, run.id, w.cfg)
    async with session_scope() as db:
        with pytest.raises(runner.Forbidden):  # 승인 역할이 아니다
            await runner.approve(db, w.outsider, run.id, w.cfg)
    async with session_scope() as db:
        withdrawn = await runner.reject(db, w.requester, run.id, None)
        await db.commit()
    assert (withdrawn.status, withdrawn.reason) == ("rejected", "요청자가 거뒀습니다")
    async with session_scope() as db:
        with pytest.raises(runner.Conflict):
            await runner.approve(db, w.approver, run.id, w.cfg)


async def test_self_approval_when_allowed(tmp_path, make_user, install) -> None:
    w = await _world(
        tmp_path,
        make_user,
        install,
        approval={"roles": [], "allow_self": True},
        notify=[],
        compensation=None,
    )
    run = await _preview(w)
    async with session_scope() as db:
        done = await runner.approve(db, w.requester, run.id, w.cfg)
        await db.commit()
    assert done.status == "executed" and await _risk(w) == 4


async def test_failed_precondition_blocks(tmp_path, make_user, install) -> None:
    w = await _world(
        tmp_path,
        make_user,
        install,
        preconditions=[{"property": "risk_grade", "op": "gte", "value": 4}],
    )
    run = await _preview(w)
    assert run.status == "blocked"
    assert any("사전 조건" in b for b in run.preview["blocked"])
    async with session_scope() as db:
        with pytest.raises(runner.Conflict):
            await runner.approve(db, w.approver, run.id, w.cfg)


async def test_unconfigured_channel_blocks_instead_of_pretending(
    tmp_path, make_user, install
) -> None:
    w = await _world(tmp_path, make_user, install)
    w.cfg = NotifyConfig()  # 메일·SMS 채널 없음
    run = await _preview(w)
    assert run.status == "blocked"
    assert sum("채널이 설정되지 않았습니다" in b for b in run.preview["blocked"]) == 2


async def test_parameter_errors_come_together(tmp_path, make_user, install) -> None:
    w = await _world(tmp_path, make_user, install)
    async with session_scope() as db:
        with pytest.raises(runner.Invalid) as e:
            await runner.preview(
                db,
                w.requester,
                action=w.action,
                target_pk=w.pk,
                params={"grade": "x", "who": 1},
                request_id=uuid.uuid4().hex,
                cfg=w.cfg,
            )
    assert len(e.value.errors) == 2


async def test_state_changed_after_preview_fails_honestly(tmp_path, make_user, install) -> None:
    w = await _world(tmp_path, make_user, install)
    run = await _preview(w)
    async with session_scope() as db:  # 미리보기 뒤 원천이 바뀌어 사전 조건(≤3)을 벗어났다
        await upsert_objects(
            db,
            "Facility",
            [
                (
                    w.pk,
                    {
                        "facility_id": w.pk,
                        "name": f"{w.tag} 변전소",
                        "facility_type": "변전소",
                        "risk_grade": 5,
                    },
                )
            ],
        )
        await db.commit()
    async with session_scope() as db:
        done = await runner.approve(db, w.approver, run.id, w.cfg)
        await db.commit()
    assert done.status == "failed" and "막혔습니다" in (done.error or "")
    assert await _risk(w) == 5 and await _deliveries(run.id) == []


# ---------------------------------------------------------------- 롤백·발송 실패


async def test_rollback_clears_edits_cancels_pending_and_corrects_sent(
    tmp_path, make_user, install
) -> None:
    w = await _world(tmp_path, make_user, install)
    run = await _preview(w)
    async with session_scope() as db:
        await runner.approve(db, w.approver, run.id, w.cfg)
        await db.commit()
    # 메일만 먼저 나갔다고 치고(SMS 는 아직 대기) 롤백한다.
    async with session_scope() as db:
        email = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.action_run_id == run.id,
                NotificationDelivery.channel == "email",
            )
        )
        assert email is not None
        email.status = "sent"
        await db.commit()
    async with session_scope() as db:
        with pytest.raises(runner.Forbidden):
            await runner.rollback(db, w.requester, run.id, w.cfg)
    async with session_scope() as db:
        result = await runner.rollback(db, w.approver, run.id, w.cfg)
        await db.commit()
    assert (result.cleared_edits, result.cancelled_deliveries, result.compensations) == (1, 1, 1)
    assert result.run.status == "rolled_back"
    assert await _risk(w) == 2  # 원천 값으로 돌아왔다

    by = {(d.channel, d.kind): d for d in await _deliveries(run.id)}
    assert by[("sms", "notify")].status == "cancelled"
    comp = by[("email", "compensation")]
    assert comp.status == "pending" and comp.body == f"[정정] {w.tag} 변전소 등급 조정(4) 취소"
    async with session_scope() as db:
        kinds = [
            n.kind
            for n in await db.scalars(
                select(Notification)
                .where(Notification.action_run_id == run.id)
                .order_by(Notification.id)
            )
        ]
    assert kinds == ["notify", "compensation"]
    async with session_scope() as db:
        with pytest.raises(runner.Conflict):
            await runner.rollback(db, w.approver, run.id, w.cfg)


async def test_gateway_failure_retries_then_fails_visibly(
    tmp_path, make_user, install, monkeypatch
) -> None:
    w = await _world(tmp_path, make_user, install)
    install(
        w.definition(
            notify=[{"channel": "sms", "to_roles": [w.approver_role], "template": "문자 {grade}"}]
        )
    )
    w.cfg = NotifyConfig.model_validate(
        {
            "max_attempts": 2,
            "retry_seconds": 0,
            "channels": {
                "sms": {"adapter": "sms_http", "url": "http://gw.local/send", "sender": "02"}
            },
        }
    )
    monkeypatch.setattr(channels, "transport", httpx.MockTransport(lambda _: httpx.Response(503)))
    run = await _preview(w)
    async with session_scope() as db:
        done = await runner.approve(db, w.approver, run.id, w.cfg)
        await db.commit()
    assert done.status == "executed"  # 실행은 성공으로 남고 발송 실패는 따로 보인다
    await delivery.deliver_due(w.cfg)
    (d,) = await _deliveries(run.id)
    assert (d.status, d.attempts) == ("pending", 1) and "503" in (d.error or "")
    await delivery.deliver_due(w.cfg)
    (d,) = await _deliveries(run.id)
    assert (d.status, d.attempts) == ("failed", 2)


# ---------------------------------------------------------------- API


async def test_api_preview_decide_and_notifications(
    tmp_path, client, make_user, install, monkeypatch
) -> None:
    w = await World(tmp_path).create()
    req = await _user(make_user, w.requester_role)
    apr = await _user(make_user, w.approver_role, phone="010-7777-4321")
    install(
        w.definition(
            notify=[
                {"channel": "app", "to_roles": [w.approver_role], "template": "{target} → {grade}"},
                {"channel": "sms", "to_roles": [w.approver_role], "template": "문자 {grade}"},
            ]
        )
    )
    monkeypatch.setattr(actions_api, "_cfg", lambda: w.cfg)

    assert (await login(client, req[0], req[1])).status_code == 200
    types = (await client.get("/api/v1/action-types")).json()
    mine = next(a for a in types if a["api_name"] == w.action)
    assert mine["can_request"] and mine["target_display_name"] == "시설"
    body = {"target_pk": w.pk, "params": {"grade": 4}, "request_id": uuid.uuid4().hex}
    r = await client.post(f"/api/v1/action-types/{w.action}/preview", json=body)
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "previewed" and not run["can_approve"] and run["can_reject"]
    again = await client.post(f"/api/v1/action-types/{w.action}/preview", json=body)
    assert again.json()["id"] == run["id"]  # 같은 요청 ID — 한 번만
    bad = await client.post(
        f"/api/v1/action-types/{w.action}/preview",
        json={**body, "request_id": uuid.uuid4().hex, "params": {"grade": "x"}},
    )
    assert bad.status_code == 422 and bad.json()["issues"]
    assert (await client.post(f"/api/v1/action-runs/{run['id']}/approve")).status_code == 403

    assert (await login(client, apr[0], apr[1])).status_code == 200
    todo = (await client.get("/api/v1/action-runs", params={"scope": "to_decide"})).json()
    assert run["id"] in [x["id"] for x in todo]
    r = await client.post(f"/api/v1/action-runs/{run['id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "executed"
    (d,) = r.json()["deliveries"]
    assert d["recipient"] == "***-4321" and d["status"] == "pending"  # 연락처 원값은 나가지 않는다
    assert r.json()["can_rollback"]
    notes = (await client.get("/api/v1/notifications")).json()
    assert notes["unread"] >= 1 and notes["items"][0]["body"] == f"{w.tag} 변전소 → 4"
    assert (await client.post("/api/v1/notifications/read")).status_code == 204
    assert (await client.get("/api/v1/notifications")).json()["unread"] == 0

    card = (await client.get(f"/api/v1/objects/Facility/{w.pk}")).status_code
    assert card in {200, 404}  # 카드는 타입 읽기 권한(승인 역할도 Facility 를 읽는다)
    r = await client.post(f"/api/v1/action-runs/{run['id']}/rollback")
    assert r.status_code == 200 and r.json()["status"] == "rolled_back"
    assert {(x["kind"], x["status"]) for x in r.json()["deliveries"]} == {("notify", "cancelled")}


async def test_ontology_api_checks_action_types(client, make_user) -> None:
    modeler = await make_user("modeler")
    assert (await login(client, modeler[0], modeler[1])).status_code == 200
    versions = (await client.get("/api/v1/ontology/versions")).json()
    published = next(v for v in versions if v["status"] == "published")
    draft = (
        await client.post("/api/v1/ontology/versions", json={"parent_id": published["id"]})
    ).json()
    bad = {
        "api_name": "rename_it",
        "display_name": "이름 바꾸기",
        "target_type": "Facility",
        "parameters": [{"api_name": "new_name", "display_name": "새 이름", "data_type": "string"}],
        "changes": [{"property": "name", "param": "new_name"}],
        "approval": {"roles": ["modeler"]},
    }
    r = await client.post(f"/api/v1/ontology/versions/{draft['id']}/action-types", json=bad)
    assert r.status_code == 201
    detail = (await client.get(f"/api/v1/ontology/versions/{draft['id']}")).json()
    # 부모 게시본에 다른 Action 이 있을 수 있다(샘플 팩) — 이 테스트가 넣은 것만 본다.
    added = next(a for a in detail["action_types"] if a["api_name"] == "rename_it")
    assert any("제목 속성" in i["message"] for i in detail["issues"])
    assert (
        await client.post(f"/api/v1/ontology/versions/{draft['id']}/publish")
    ).status_code == 422

    fixed = {**bad, "changes": [{"property": "risk_grade", "value": 1}], "parameters": []}
    r = await client.put(
        f"/api/v1/ontology/versions/{draft['id']}/action-types/{added['id']}",
        json=fixed,
    )
    assert r.status_code == 200
    changes = (
        await client.get(
            f"/api/v1/ontology/versions/{draft['id']}/diff", params={"base": published["id"]}
        )
    ).json()
    assert {"kind": "added", "element": "action_type", "path": "rename_it"}.items() <= changes[
        0
    ].items()
    exported = (
        await client.get(
            f"/api/v1/ontology/versions/{draft['id']}/export", params={"format": "yaml"}
        )
    ).text
    assert "action_types:" in exported and "rename_it" in exported
