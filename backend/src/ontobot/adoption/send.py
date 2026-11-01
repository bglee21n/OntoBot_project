"""양식 → 제품 보내기의 미리보기·적용(TRD 17.17 결정 A5~A9, Phase 21).

미리보기는 ``bridges`` 의 순수 함수로 바뀔 것의 목록과 지문을 만든다. 적용은 같은 목록을 다시
계산하고, 지문이 미리보기 때와 다르면(그 사이 양식이나 받는 곳이 바뀌었으면) 멈춘다 — 본 것과 다른
것을 적용하지 않는다. 적용은 받는 곳의 **기존 서비스 함수**만 부르고(우회 쓰기 경로를 만들지
않는다), 모두 호출자 트랜잭션 하나에서 일어난다. 받는 곳의 감사는 그 함수가 남기고, 여기서는
``adoption.sent`` 를 더한다.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.adoption import bridges, store
from ontobot.adoption.bridges import Bridge, Change, UserState
from ontobot.db.models.evaluation import GoldenItem, GoldenSet
from ontobot.evaluation import golden, targets
from ontobot.ontology import versioning
from ontobot.ontology.models import OntologyDocument
from ontobot.security import admin, audit, rbac


class SendError(Exception):
    def __init__(self, message: str, *, code: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Preview:
    bridge: Bridge
    changes: list[Change]
    fingerprint: str
    # 받는 곳(설계도 버전, 골든셋)
    version_id: int | None = None
    golden_set_id: int | None = None
    golden_set_name: str | None = None


@dataclass
class Applied:
    applied: int
    version_id: int | None = None
    golden_set_id: int | None = None
    # 새로 만든 계정의 임시 비밀번호 — 응답에 한 번만 낸다(저장·감사·로그에 남기지 않는다).
    passwords: dict[str, str] = field(default_factory=dict)


def golden_set_name(engagement_id: int) -> str:
    return f"adoption-engagement-{engagement_id}"


async def _payloads(db: AsyncSession, engagement_id: int) -> dict[str, dict[str, Any]]:
    return {s.kind: s.payload for s in await store.sheet_states(db, engagement_id)}


# ---------------------------------------------------------------- 받는 곳 읽기


async def _draft_base(db: AsyncSession, version_id: int | None) -> tuple[int, OntologyDocument]:
    """보낼 버전 — 고른 draft, 또는 게시본(적용할 때 그 게시본을 부모로 새 draft 를 만든다)."""
    if version_id is not None:
        try:
            v = await versioning.get_version(db, version_id)
        except versioning.VersionNotFound as e:
            raise SendError("설계도 버전이 없습니다", code="version_not_found", status=404) from e
        if v.status != "draft":
            raise SendError("draft 에만 보낼 수 있습니다", code="not_draft")
        return v.id, versioning.to_document(v)
    published = await versioning.published_version_id(db)
    if published is None:
        raise SendError(
            "게시된 설계도가 없습니다 — draft 를 고르세요", code="no_published_ontology"
        )
    return published, versioning.to_document(await versioning.get_version(db, published))


async def _golden_set(db: AsyncSession, engagement_id: int) -> GoldenSet | None:
    gs: GoldenSet | None = await db.scalar(
        select(GoldenSet).where(GoldenSet.name == golden_set_name(engagement_id))
    )
    return gs


async def _account_state(
    db: AsyncSession,
) -> tuple[dict[str, UserState], dict[str, str], frozenset[str]]:
    people: dict[str, UserState] = {}
    services: set[str] = set()
    for u in await admin.list_users(db):
        if u.username is None:
            continue
        if u.auth == "service":
            services.add(u.username)
        else:
            people[u.username] = UserState(frozenset(u.roles), frozenset(u.groups))
    groups = {g.name: g.display_name for g in await admin.list_groups(db)}
    return people, groups, frozenset(services)


async def preview(
    db: AsyncSession, engagement_id: int, bridge: Bridge, *, version_id: int | None = None
) -> Preview:
    payloads = await _payloads(db, engagement_id)
    if bridge == "draft":
        vid, doc = await _draft_base(db, version_id)
        changes = bridges.glossary_to_draft(payloads, doc)
        return Preview(bridge, changes, bridges.fingerprint(changes), version_id=vid)
    if bridge == "golden":
        gs = await _golden_set(db, engagement_id)
        existing = (
            list(
                await db.scalars(
                    select(GoldenItem.question).where(GoldenItem.golden_set_id == gs.id)
                )
            )
            if gs
            else []
        )
        changes = bridges.questions_to_golden(payloads, existing)
        return Preview(
            bridge,
            changes,
            bridges.fingerprint(changes),
            golden_set_id=gs.id if gs else None,
            golden_set_name=golden_set_name(engagement_id),
        )
    if bridge == "targets":
        current = {t.spec.key: t.value for t in await targets.load(db)}
        changes = bridges.kpis_to_targets(payloads, current)
        return Preview(bridge, changes, bridges.fingerprint(changes))
    users, groups, services = await _account_state(db)
    changes = bridges.stakeholders_to_accounts(
        payloads,
        users=users,
        groups=groups,
        known_roles=rbac.KNOWN_ROLES,
        service_usernames=services,
    )
    return Preview(bridge, changes, bridges.fingerprint(changes))


# ---------------------------------------------------------------- 적용


async def apply(
    db: AsyncSession,
    engagement_id: int,
    bridge: Bridge,
    *,
    actor_id: int,
    fingerprint: str,
    version_id: int | None = None,
) -> Applied:
    p = await preview(db, engagement_id, bridge, version_id=version_id)
    if p.fingerprint != fingerprint:
        raise SendError(
            "미리보기 뒤에 양식이나 받는 곳이 바뀌었습니다 — 미리보기를 다시 보세요",
            code="preview_stale",
        )
    todo = bridges.applicable(p.changes)
    if bridge == "draft":
        result = await _apply_draft(
            db, todo, actor_id=actor_id, version_id=version_id, base=p.version_id
        )
    elif bridge == "golden":
        result = await _apply_golden(db, engagement_id, todo, actor_id=actor_id)
    elif bridge == "targets":
        if todo:
            await targets.update(
                db, {c.target: Decimal(c.after or "0") for c in todo}, actor_id=actor_id
            )
        result = Applied(applied=len(todo))
    else:
        result = await _apply_accounts(db, todo, actor_id=actor_id)
    audit.record(
        db,
        audit.ENGAGEMENT_SENT,
        actor_id=actor_id,
        target=f"engagement:{engagement_id}",
        payload={
            "bridge": bridge,
            "applied": result.applied,
            "blocked": sum(1 for c in p.changes if c.blocked),
            "version_id": result.version_id,
            "golden_set_id": result.golden_set_id,
        },
    )
    await db.flush()
    return result


async def _apply_draft(
    db: AsyncSession, todo: list[Change], *, actor_id: int, version_id: int | None, base: int | None
) -> Applied:
    if not todo:
        return Applied(applied=0, version_id=version_id)
    if version_id is None:
        version = await versioning.create_draft(db, actor_id=actor_id, parent_id=base)
    else:
        version = await versioning.get_version(db, version_id)
    targets_ = list(dict.fromkeys(c.target for c in todo))
    for target in targets_:
        patch = bridges.element_patch(todo, target)
        type_name, _, prop_name = target.partition(".")
        t = next(x for x in version.object_types if x.api_name == type_name)
        if not prop_name:
            await versioning.update_object_type(
                db,
                version.id,
                t.id,
                actor_id=actor_id,
                display_name=patch.get("display_name"),
                description=patch.get("description"),
            )
            continue
        row = next(x for x in t.properties if x.api_name == prop_name)
        new = versioning.prop_def(row).model_copy(update=patch)
        await versioning.replace_property(db, version.id, t.id, row.id, new, actor_id=actor_id)
    return Applied(applied=len(todo), version_id=version.id)


async def _apply_golden(
    db: AsyncSession, engagement_id: int, todo: list[Change], *, actor_id: int
) -> Applied:
    gs = await _golden_set(db, engagement_id)
    if not todo:
        return Applied(applied=0, golden_set_id=gs.id if gs else None)
    if gs is None:
        gs = await golden.create_set(db, golden_set_name(engagement_id), {}, actor_id=actor_id)
        gs.ontology_version_id = await versioning.published_version_id(db)
    for c in todo:
        # 기대 답이 없는 후보 — 평가 화면에서 기대 값을 확정한다. 도입 전용 골든셋이라
        # 운영 게이트에 섞이지 않는다.
        await golden.save_item(
            db,
            gs,
            {
                "question": c.after,
                "answerable": True,
                "expected_status": "ok",
                "required_values": [],
                "forbidden_values": [],
                "note": f"도입 {engagement_id} · {c.source}",
            },
            actor_id=actor_id,
            source="adoption",
        )
    return Applied(applied=len(todo), golden_set_id=gs.id)


async def _apply_accounts(db: AsyncSession, todo: list[Change], *, actor_id: int) -> Applied:
    """그룹 만들기 → 계정 만들기 → 역할 더하기 → 구성원 넣기 순서. 실패하면 트랜잭션째 되돌린다."""
    passwords: dict[str, str] = {}
    try:
        for c in todo:
            if c.op == "create" and c.field == "group":
                await admin.create_group(
                    db, actor_id=actor_id, name=c.after or "", display_name=c.after or ""
                )
        roles_for: dict[str, list[str]] = {}
        for c in todo:
            if c.op == "add_role":
                roles_for.setdefault(c.target.removeprefix("user:"), []).extend(
                    bridges.split_list(c.after or "")
                )
        users = {u.username: u for u in await admin.list_users(db) if u.username}
        for c in todo:
            if c.op == "create" and c.field == "user":
                username = c.target.removeprefix("user:")
                password = secrets.token_urlsafe(12)
                await admin.create_local_user(
                    db,
                    actor_id=actor_id,
                    username=username,
                    display_name=c.after or username,
                    password=password,
                    roles=roles_for.pop(username, []),
                )
                passwords[username] = password
        users = {u.username: u for u in await admin.list_users(db) if u.username}
        for username, extra in roles_for.items():
            u = users[username]
            await admin.update_user(
                db, actor_id=actor_id, user_id=u.id, roles=sorted({*u.roles, *extra})
            )
        groups = {g.name: g for g in await admin.list_groups(db)}
        adds: dict[str, set[int]] = {}
        for c in todo:
            if c.op == "add_member":
                adds.setdefault(c.target.removeprefix("group:"), set()).add(users[c.after or ""].id)
        for name, ids in adds.items():
            g = groups[name]
            await admin.set_members(
                db,
                actor_id=actor_id,
                group_id=g.id,
                user_ids=sorted({*g.member_ids, *ids}),
                via="adoption",
            )
    except admin.AdminError as e:
        raise SendError(str(e), code=e.code, status=e.status) from e
    return Applied(applied=len(todo), passwords=passwords)
