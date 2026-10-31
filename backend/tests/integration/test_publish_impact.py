"""Phase 17a — 게시 영향: draft 의 지우기·바꾸기마다 참조처.

행 정책이 지우는 속성을 쓰면 게시를 막는다.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from ontobot.db.models.evaluation import GoldenItem, GoldenSet
from ontobot.db.models.security import DataGroup, GroupPropertyHide, GroupRowPolicy
from ontobot.db.session import session_scope
from ontobot.ontology import impact, registry, versioning
from tests.integration.conftest import MakeUser, login

pytestmark = pytest.mark.integration


async def _draft_without(prop: str, *, rename: dict[str, str] | None = None) -> int:
    """게시본을 복사한 draft 에서 Facility 속성 ``prop`` 을 지운다(표시 이름 바꾸기도)."""
    async with session_scope() as db:
        published = await versioning.published_version_id(db)
        assert published is not None
        doc = versioning.to_document(await versioning.get_version(db, published))
        facility = doc.object_type("Facility")
        assert facility is not None
        props = [
            p.model_copy(update={"display_name": (rename or {}).get(p.api_name, p.display_name)})
            for p in facility.properties
            if p.api_name != prop
        ]
        types = [
            t.model_copy(update={"properties": props}) if t.api_name == "Facility" else t
            for t in doc.object_types
        ]
        draft = await versioning.create_draft(db, actor_id=None, parent_id=published)
        await versioning.replace_document(
            db, draft.id, doc.model_copy(update={"object_types": types}), actor_id=None
        )
        await db.commit()
        return draft.id


async def test_removed_property_lists_references_and_row_policy_blocks(
    sample_published: None, client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    tag = uuid.uuid4().hex[:8]
    async with session_scope() as db:
        g = DataGroup(name=f"imp_{tag}", display_name=f"영향 {tag}")
        db.add(g)
        await db.flush()
        db.add(GroupPropertyHide(group_id=g.id, object_type="Facility", property="capacity_kw"))
        policy = GroupRowPolicy(
            group_id=g.id,
            object_type="Facility",
            applies_to="read",
            filters=[{"property": "capacity_kw", "op": "gt", "value": 0}],
        )
        db.add(policy)
        await db.commit()
        policy_id = policy.id
    draft_id = await _draft_without("capacity_kw", rename={"risk_grade": "위험 등급"})

    username, password, _ = await make_user("modeler")
    assert (await login(client, username, password)).status_code == 200
    r = await client.get(f"/api/v1/ontology/versions/{draft_id}/impact")
    assert r.status_code == 200, r.text
    by_path = {i["path"]: i for i in r.json()}
    removed = by_path["Facility.capacity_kw"]
    assert removed["kind"] == "removed" and removed["element"] == "property"
    kinds = {(x["kind"], x["blocking"]) for x in removed["refs"]}
    assert ("row_policy", True) in kinds and ("property_hide", False) in kinds
    assert any(f"imp_{tag}" in x["label"] for x in removed["refs"])
    # 표시 이름만 바꾼 속성 — 막지 않는다(참조처가 있으면 알리기만).
    renamed = by_path["Facility.risk_grade"]
    assert renamed["kind"] == "changed" and not any(x["blocking"] for x in renamed["refs"])

    # 게시는 막힌다(문서 안은 깨끗해도 행 정책이 지우는 속성을 쓴다).
    r = await client.post(f"/api/v1/ontology/versions/{draft_id}/publish")
    assert (r.status_code, r.json()["code"]) == (422, "publish_blocked"), r.text
    assert any("행 정책" in i["message"] for i in r.json()["issues"])

    # 정책을 고치면(그 속성을 안 쓰면) 막는 것이 없다.
    # 게시는 하지 않는다(다른 테스트가 게시본을 쓴다).
    async with session_scope() as db:
        p = await db.get(GroupRowPolicy, policy_id)
        assert p is not None
        p.filters = [{"property": "facility_type", "op": "eq", "value": "철탑"}]
        await db.commit()
        schema = registry.current()
        assert schema is not None
        draft = versioning.to_document(await versioning.get_version(db, draft_id))
        impacts = await impact.change_impact(db, schema.document, draft)
        assert impact.blocking_issues(impacts) == []
        await db.delete(await db.get(DataGroup, p.group_id))
        await db.commit()


async def test_removed_property_shows_actions_functions_and_golden_items(
    sample_published: None,
) -> None:
    """완료 기준(Phase 17) — 지운 속성을 쓰는 Action·Function·골든 문항이 영향에 모두 나온다."""
    tag = uuid.uuid4().hex[:8]
    async with session_scope() as db:
        gs = GoldenSet(name=f"imp_{tag}")
        db.add(gs)
        await db.flush()
        item = GoldenItem(
            golden_set_id=gs.id,
            question="위험등급 4 이상 시설 수",
            expected_status="ok",
            required_conditions=["위험등급 ≥ 4"],
        )
        db.add(item)
        await db.commit()
        item_id = item.id
    draft_id = await _draft_without("risk_grade")
    async with session_scope() as db:
        schema = registry.current()
        assert schema is not None
        draft = versioning.to_document(await versioning.get_version(db, draft_id))
        impacts = await impact.change_impact(db, schema.document, draft)
    (removed,) = [i for i in impacts if i.change.path == "Facility.risk_grade"]
    labels = [r.label for r in removed.refs]
    assert any("Action 'adjust_risk_grade'" in x for x in labels), labels
    assert any("Function 'high_risk_ratio'" in x for x in labels), labels
    assert f"골든 문항 #{item_id} 의 조건 조각에 '위험등급'" in labels
    # 문서 안의 깨진 참조는 게시 전 검사가 막는다(blocking) — 막는 문서 밖 참조는 없다.
    assert all(r.blocking for r in removed.refs if r.kind == "document")
