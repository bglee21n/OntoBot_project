"""편집 레이어·값 계보(TRD 6.3·11절·17.1-1, v1 계획 Phase 9 완료 기준).

편집은 근거 카드만이 아니라 조회 조건·집계·참조 값 대조에도 보여야 한다 — 카드는 편집 값인데
필터가 원천 값이면 결과가 조용히 틀린다. 동기화는 편집을 건드리지 않고 충돌만 남긴다.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from ontobot.db.models.objects import ObjectEdit, SyncConflict
from ontobot.db.models.security import AuditLog, Role, TypePermission
from ontobot.db.session import session_scope
from ontobot.objects import edits
from ontobot.objects.reader import CompiledQuery, existing_values, resolve, run_query
from ontobot.sync.runner import run_mapping
from tests.integration.conftest import MakeUser, login
from tests.integration.test_sync_graph import Source

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("source_env")]

DECLARED = ["facility_id", "name", "risk_grade", "capacity_kw"]


async def _synced() -> Source:
    """원천 시설 5개: 위험등급 F1=2 F2=3 F3=4 F4=5 F5=1 (i % 5 + 1)."""
    src = await Source().create()
    assert (await run_mapping(src.org_mapping)).status == "succeeded"
    assert (await run_mapping(src.fac_mapping)).status == "succeeded"
    return src


async def _edit(src: Source, n: int, prop: str, value: Any, user_id: int | None = None) -> int:
    async with session_scope() as db:
        edit_id = await edits.apply_edit(
            db, object_type="Facility", pk=src.fid(n), prop=prop, value=value, edited_by=user_id
        )
        await db.commit()
        return edit_id
    raise AssertionError("unreachable")


def _risk_at_least(src: Source, grade: int, *, count: bool = False) -> CompiledQuery:
    return CompiledQuery(
        object_type="Facility",
        declared_properties=DECLARED,
        where=["o.pk LIKE :pre", "(o.properties ->> CAST(:k AS text))::int >= :v"],
        aggregate_select=[("value", "count(*)")] if count else (),
        order_by=() if count else ["o.pk"],
        params={"pre": f"{src.pk_prefix}%", "k": "risk_grade", "v": grade},
    )


async def _visible_risk_4_up(src: Source) -> tuple[list[str], int]:
    async with session_scope() as db:
        rows = await run_query(db, _risk_at_least(src, 4))
        agg = await run_query(db, _risk_at_least(src, 4, count=True))
        return [r.pk for r in rows], agg[0]["value"]  # type: ignore[union-attr,index]
    raise AssertionError("unreachable")


async def _conflict(edit_id: int) -> SyncConflict | None:
    async with session_scope() as db:
        return await db.scalar(
            select(SyncConflict)
            .where(SyncConflict.edit_id == edit_id)
            .order_by(SyncConflict.id.desc())
        )
    return None


async def _set_source_risk(src: Source, n: int, risk: int) -> None:
    await src.exec("UPDATE {fac} SET risk = $1 WHERE id = $2", risk, src.fid(n))
    assert (await run_mapping(src.fac_mapping)).status == "succeeded"


# ---------------------------------------------------------------- 읽기 경로


async def test_edit_is_visible_to_filters_aggregates_and_cards() -> None:
    src = await _synced()
    assert await _visible_risk_4_up(src) == ([src.fid(3), src.fid(4)], 2)

    await _edit(src, 5, "risk_grade", 5)
    assert await _visible_risk_4_up(src) == ([src.fid(3), src.fid(4), src.fid(5)], 3)

    async with session_scope() as db:
        row = (await resolve(db, "Facility", [src.fid(5)], DECLARED))[src.fid(5)]
        assert row.properties["risk_grade"] == 5 and row.edited == frozenset({"risk_grade"})
        plain = (await resolve(db, "Facility", [src.fid(1)], DECLARED))[src.fid(1)]
        assert plain.edited == frozenset()
        # 선언되지 않은 속성의 편집은 값도 표시도 보이지 않는다.
        hidden = (await resolve(db, "Facility", [src.fid(5)], ["facility_id"]))[src.fid(5)]
        assert hidden.properties == {"facility_id": src.fid(5)} and hidden.edited == frozenset()


async def test_reference_check_sees_edited_values() -> None:
    src = await _synced()
    unique = str(10**9 + uuid.uuid4().int % 10**8)  # 다른 테스트의 객체와 겹치지 않는 값
    edit_id = await _edit(src, 1, "capacity_kw", int(unique))
    async with session_scope() as db:
        assert await existing_values(db, "Facility", "capacity_kw", [unique]) == {unique}
        await edits.clear_edit(db, edit_id, None)
        await db.commit()
        assert await existing_values(db, "Facility", "capacity_kw", [unique]) == set()


async def test_clearing_an_edit_restores_the_source_value() -> None:
    src = await _synced()
    edit_id = await _edit(src, 5, "risk_grade", 5)
    async with session_scope() as db:
        await edits.clear_edit(db, edit_id, None)
        await db.commit()
    assert await _visible_risk_4_up(src) == ([src.fid(3), src.fid(4)], 2)
    async with session_scope() as db:
        with pytest.raises(edits.EditError):
            await edits.clear_edit(db, edit_id, None)


async def test_re_editing_replaces_the_active_edit() -> None:
    src = await _synced()
    first = await _edit(src, 2, "risk_grade", 4)
    second = await _edit(src, 2, "risk_grade", 1)
    async with session_scope() as db:
        old = await db.get(ObjectEdit, first)
        new = await db.get(ObjectEdit, second)
        assert old is not None and old.cleared_at is not None
        assert new is not None and new.cleared_at is None and new.base_source_value == 3
        row = (await resolve(db, "Facility", [src.fid(2)], DECLARED))[src.fid(2)]
        assert row.properties["risk_grade"] == 1


async def test_cannot_edit_a_missing_object() -> None:
    async with session_scope() as db:
        with pytest.raises(edits.EditError):
            await edits.apply_edit(
                db,
                object_type="Facility",
                pk="NOPE-" + uuid.uuid4().hex,
                prop="name",
                value="x",
                edited_by=None,
            )


# ---------------------------------------------------------------- 동기화 충돌


async def test_source_change_records_a_conflict_and_the_edit_still_wins() -> None:
    src = await _synced()
    edit_id = await _edit(src, 2, "risk_grade", 5)  # 편집 당시 원천 3

    await _set_source_risk(src, 2, 4)
    c = await _conflict(edit_id)
    assert c is not None and (c.status, c.new_source_value) == ("open", 4)
    assert c.source_synced_at is not None
    async with session_scope() as db:
        row = (await resolve(db, "Facility", [src.fid(2)], DECLARED))[src.fid(2)]
        assert row.properties["risk_grade"] == 5  # 편집 레이어 우선

    # 원천이 또 바뀌면 같은 충돌의 값을 고친다(새 행을 만들지 않는다).
    await _set_source_risk(src, 2, 2)
    again = await _conflict(edit_id)
    assert again is not None
    assert (again.id, again.status, again.new_source_value) == (c.id, "open", 2)

    # 확인한 뒤 원천이 다시 바뀌면 다시 open.
    _, _, user_id = await _anyone()
    async with session_scope() as db:
        await edits.acknowledge_conflict(db, c.id, user_id)
        await db.commit()
    acked = await _conflict(edit_id)
    assert acked is not None and acked.status == "acknowledged"
    await _set_source_risk(src, 2, 1)
    reopened = await _conflict(edit_id)
    assert reopened is not None and reopened.status == "open"

    # 원천이 편집 당시 값으로 돌아오면 충돌이 닫힌다.
    await _set_source_risk(src, 2, 3)
    closed = await _conflict(edit_id)
    assert closed is not None and closed.status == "cleared"


async def test_changes_to_other_properties_are_not_conflicts() -> None:
    src = await _synced()
    edit_id = await _edit(src, 3, "risk_grade", 1)
    await src.exec("UPDATE {fac} SET name = 'renamed' WHERE id = $1", src.fid(3))
    assert (await run_mapping(src.fac_mapping)).status == "succeeded"
    assert await _conflict(edit_id) is None


async def test_clearing_an_edit_closes_its_conflict() -> None:
    src = await _synced()
    edit_id = await _edit(src, 4, "risk_grade", 1)
    await _set_source_risk(src, 4, 3)
    async with session_scope() as db:
        await edits.clear_edit(db, edit_id, None)
        await db.commit()
    c = await _conflict(edit_id)
    assert c is not None and c.status == "cleared"
    async with session_scope() as db:
        row = (await resolve(db, "Facility", [src.fid(4)], DECLARED))[src.fid(4)]
        assert row.properties["risk_grade"] == 3


# ---------------------------------------------------------------- API


async def _anyone() -> tuple[str, str, int]:
    """FK 를 채울 사용자 하나(역할 없음)."""
    from ontobot.db.models.security import AppUser

    async with session_scope() as db:
        name = f"u_{uuid.uuid4().hex[:10]}"
        user = AppUser(username=name, display_name=name, status="active")
        db.add(user)
        await db.commit()
        return name, "", user.id
    raise AssertionError("unreachable")


async def _user(
    make_user: MakeUser, *roles: str, types: tuple[str, ...] = ("Facility",)
) -> tuple[str, str, int]:
    # 이름의 test_ 접두어: 시드 역할 검사(test_schema)가 테스트가 만든 역할을 구분한다.
    role = f"test_reader_{uuid.uuid4().hex[:8]}"
    async with session_scope() as db:
        r = Role(name=role, description="테스트")
        db.add(r)
        await db.flush()
        for t in types:
            db.add(TypePermission(role_id=r.id, object_type=t, can_read=True))
        await db.commit()
    return await make_user(role, *roles)


async def test_card_shows_edit_conflict_and_source_lineage(client, make_user) -> None:
    src = await _synced()
    _, _, editor_id = await _anyone()
    await _edit(src, 2, "risk_grade", 5, user_id=editor_id)
    await _set_source_risk(src, 2, 4)

    u = await _user(make_user)
    assert (await login(client, u[0], u[1])).status_code == 200
    card = (await client.get(f"/api/v1/objects/Facility/{src.fid(2)}")).json()
    risk = next(f for f in card["fields"] if f["name"] == "risk_grade")
    assert risk["value"] == "5"
    edit = risk["edit"]
    assert edit["source_value"] == "3" and edit["edited_by"] is not None
    assert edit["conflict"]["status"] == "open" and edit["conflict"]["new_source_value"] == "4"
    assert next(f for f in card["fields"] if f["name"] == "name")["edit"] is None
    source = card["source"]
    assert source["data_source"] == f"src-{src.tag}" and source["origin"] == src.fac
    assert source["sync_run_id"] is not None and source["run_finished_at"] is not None


async def test_conflict_list_acknowledge_and_clear_edit_api(client, make_user) -> None:
    src = await _synced()
    edit_id = await _edit(src, 1, "risk_grade", 5)
    await _set_source_risk(src, 1, 4)

    plain = await _user(make_user)
    assert (await login(client, plain[0], plain[1])).status_code == 200
    assert (await client.get("/api/v1/sync-conflicts")).status_code == 403

    blind = await _user(make_user, "modeler", types=("Region",))
    assert (await login(client, blind[0], blind[1])).status_code == 200
    listed = (await client.get("/api/v1/sync-conflicts")).json()
    assert edit_id not in [c["edit_id"] for c in listed]
    assert (await client.delete(f"/api/v1/object-edits/{edit_id}")).status_code == 404

    modeler = await _user(make_user, "modeler")
    assert (await login(client, modeler[0], modeler[1])).status_code == 200
    listed = (await client.get("/api/v1/sync-conflicts")).json()
    mine = [c for c in listed if c["edit_id"] == edit_id]
    assert len(mine) == 1
    c = mine[0]
    values = (c["status"], c["edit_value"], c["base_source_value"], c["new_source_value"])
    assert values == ("open", "5", "2", "4")
    assert c["property_display_name"] != "risk_grade"

    r = await client.post(f"/api/v1/sync-conflicts/{c['id']}/acknowledge")
    assert r.status_code == 204
    r = await client.post(f"/api/v1/sync-conflicts/{c['id']}/acknowledge")
    assert r.status_code == 409
    assert (await client.post("/api/v1/sync-conflicts/0/acknowledge")).status_code == 404
    acked = (await client.get("/api/v1/sync-conflicts", params={"status": "acknowledged"})).json()
    assert c["id"] in [x["id"] for x in acked]

    assert (await client.delete(f"/api/v1/object-edits/{edit_id}")).status_code == 204
    assert (await client.delete(f"/api/v1/object-edits/{edit_id}")).status_code == 409
    card = (await client.get(f"/api/v1/objects/Facility/{src.fid(1)}")).json()
    risk = next(f for f in card["fields"] if f["name"] == "risk_grade")
    assert (risk["value"], risk["edit"]) == ("4", None)

    async with session_scope() as db:
        events = set(
            await db.scalars(
                select(AuditLog.event).where(
                    AuditLog.target.in_([f"object_edit:{edit_id}", f"sync_conflict:{c['id']}"])
                )
            )
        )
    assert events == {"objects.edit_cleared", "objects.conflict_acknowledged"}
