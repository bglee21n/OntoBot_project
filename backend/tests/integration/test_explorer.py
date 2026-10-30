"""Phase 15 — 객체 탐색기: 검색·1홉 이웃(PG + Neo4j)·편집 이력·확장 한도.

권한은 근거 카드와 같은 경로다: 읽을 수 없는 타입·행 정책 밖 객체는 결과·개수 어디에도 없고,
시작 객체가 그렇다면 404. stub 끝은 나오지 않는다. Neo4j 가 없으면 503(PG link 로 대신하지 않는다).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest

from ontobot.db.models.objects import ObjectEdit
from ontobot.db.session import session_scope
from ontobot.graph import client as graph
from ontobot.graph.projector import statement
from ontobot.objects.store import upsert_objects
from ontobot.ontology import importer, registry, versioning
from ontobot.security import admin
from tests.integration.conftest import MakeUser, login
from tests.integration.test_chat import _reader, neo4j  # noqa: F401 — 픽스처
from tests.integration.test_pipeline import SAMPLE

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
async def schema() -> AsyncIterator[registry.PublishedSchema]:
    async with session_scope() as db:
        doc = importer.parse(SAMPLE.read_text(encoding="utf-8"), "yaml")
        draft = await versioning.create_draft(db, actor_id=None, parent_id=None)
        await versioning.replace_document(db, draft.id, doc, actor_id=None)
        await versioning.publish(db, draft.id, actor_id=None)
        await db.commit()
        published = await registry.reload(db)
        assert published is not None
        yield published


@pytest.fixture
async def app_client(neo4j: graph.AsyncDriver) -> AsyncIterator[httpx.AsyncClient]:  # noqa: F811
    from ontobot.main import create_app

    app = create_app()
    app.state.graph = neo4j  # ASGITransport 는 lifespan 을 돌리지 않는다
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def _world(driver: graph.AsyncDriver) -> dict[str, str]:
    """조직 하나 ← 시설 셋(철탑·변전소·철탑) + 원천에 없는 시설(stub) 하나, 시설 1 → 지역."""
    tag = uuid.uuid4().hex[:8]
    ids = {
        "tag": tag,
        "org": f"XO-{tag}",
        "f1": f"XF1-{tag}",
        "f2": f"XF2-{tag}",
        "f3": f"XF3-{tag}",
        "ghost": f"XFG-{tag}",
        "region": f"XR-{tag}",
    }
    kinds = {"f1": "철탑", "f2": "변전소", "f3": "철탑"}
    async with session_scope() as db:
        await upsert_objects(
            db, "Organization", [(ids["org"], {"org_id": ids["org"], "name": f"탐색조직-{tag}"})]
        )
        await upsert_objects(
            db,
            "Region",
            [(ids["region"], {"region_code": ids["region"], "name": f"탐색지역-{tag}"})],
        )
        await upsert_objects(
            db,
            "Facility",
            [
                (
                    ids[k],
                    {
                        "facility_id": ids[k],
                        "name": f"탐색시설{k[1]}-{tag}",
                        "facility_type": kind,
                        "risk_grade": 3,
                        "owner_org_id": ids["org"],
                    },
                )
                for k, kind in kinds.items()
            ],
        )
        await db.commit()
    stmts = [statement("node_upsert", {"type": "Organization", "pk": ids["org"], "title": "t"})]
    for k in ("f1", "f2", "f3", "ghost"):
        stmts.append(
            statement(
                "link_upsert",
                {
                    "from_type": "Facility",
                    "to_type": "Organization",
                    "link_type": "owned_by",
                    "from_pk": ids[k],
                    "to_pk": ids["org"],
                },
            )
        )
        if k != "ghost":  # ghost 는 노드 갱신이 없어 stub 으로 남는다
            stmts.append(statement("node_upsert", {"type": "Facility", "pk": ids[k], "title": "t"}))
    stmts.append(
        statement(
            "link_upsert",
            {
                "from_type": "Facility",
                "to_type": "Region",
                "link_type": "located_in",
                "from_pk": ids["f1"],
                "to_pk": ids["region"],
            },
        )
    )
    await graph.write(driver, stmts)
    return ids


async def test_neighbors_respect_types_rows_and_stubs(
    schema,
    app_client: httpx.AsyncClient,
    neo4j,  # noqa: F811 — 픽스처
    make_user: MakeUser,
) -> None:
    ids = await _world(neo4j)
    reader = await _reader(make_user, "Facility", "Organization")
    assert (await login(app_client, reader[0], reader[1])).status_code == 200

    r = await app_client.get(f"/api/v1/objects/Organization/{ids['org']}/neighbors")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["groups"] == [
        {
            "link_type": "owned_by",
            "link_display_name": "소유 조직",
            "direction": "in",
            "other_type": "Facility",
            "other_display_name": "시설",
            "total": 3,  # stub 은 세지 않는다
            "truncated": False,
        }
    ]
    assert [i["pk"] for i in body["items"]] == [ids["f1"], ids["f2"], ids["f3"]]
    assert body["items"][0]["title"] == f"탐색시설1-{ids['tag']}"  # 제목은 PG 에서
    page = await app_client.get(
        f"/api/v1/objects/Organization/{ids['org']}/neighbors", params={"limit": 1, "offset": 1}
    )
    assert [i["pk"] for i in page.json()["items"]] == [ids["f2"]]
    assert page.json()["groups"][0]["total"] == 3

    # 지역을 읽을 수 없으면 located_in 가지 자체가 없다(개수도 없다).
    r = await app_client.get(f"/api/v1/objects/Facility/{ids['f1']}/neighbors")
    assert [(g["link_type"], g["total"]) for g in r.json()["groups"]] == [("owned_by", 1)]
    r = await app_client.get(f"/api/v1/objects/Region/{ids['region']}/neighbors")
    assert (r.status_code, r.json()["code"]) == (404, "object_not_found")
    r = await app_client.get(f"/api/v1/objects/Organization/{ids['ghost']}/neighbors")
    assert r.status_code == 404

    # 행 정책: 철탑만 읽는 사용자에게 변전소는 이웃·개수 어디에도 없다.
    username, password, uid = await make_user()
    name = f"xp_{uuid.uuid4().hex[:8]}"
    async with session_scope() as db:
        g = await admin.create_group(db, actor_id=None, name=name, display_name=name)
        await admin.set_members(db, actor_id=None, group_id=g.id, user_ids=[uid], via="test")
        await admin.set_permissions(
            db,
            actor_id=None,
            group_id=g.id,
            types=[
                admin.TypeGrant("Organization", can_read=True),
                admin.TypeGrant(
                    "Facility",
                    can_read=True,
                    read_filters=({"property": "facility_type", "op": "eq", "value": "철탑"},),
                ),
            ],
            links=[],
        )
        await db.commit()
    assert (await login(app_client, username, password)).status_code == 200
    body = (await app_client.get(f"/api/v1/objects/Organization/{ids['org']}/neighbors")).json()
    assert body["groups"][0]["total"] == 2
    assert [i["pk"] for i in body["items"]] == [ids["f1"], ids["f3"]]
    r = await app_client.get(f"/api/v1/objects/Facility/{ids['f2']}/neighbors")
    assert r.status_code == 404  # 정책 밖 시작 객체


async def test_neighbors_without_graph_is_503(
    schema, client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    tag = uuid.uuid4().hex[:8]
    async with session_scope() as db:
        await upsert_objects(
            db, "Organization", [(f"XN-{tag}", {"org_id": f"XN-{tag}", "name": "n"})]
        )
        await db.commit()
    reader = await _reader(make_user, "Facility", "Organization")
    assert (await login(client, reader[0], reader[1])).status_code == 200
    r = await client.get(f"/api/v1/objects/Organization/XN-{tag}/neighbors")
    assert (r.status_code, r.json()["code"]) == (503, "graph_unavailable")
    # 이웃이 될 수 있는 타입이 없으면 그래프를 부르지 않는다 — 빈 결과.
    only_org = await _reader(make_user, "Organization")
    assert (await login(client, only_org[0], only_org[1])).status_code == 200
    r = await client.get(f"/api/v1/objects/Organization/XN-{tag}/neighbors")
    assert r.json() == {"groups": [], "items": []}


async def test_search_and_edit_history(
    schema, client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    tag = uuid.uuid4().hex[:8]
    pk = f"XS-{tag}"
    async with session_scope() as db:
        await upsert_objects(
            db,
            "Facility",
            [
                (
                    pk,
                    {
                        "facility_id": pk,
                        "name": f"검색_시설%{tag}",
                        "facility_type": "철탑",
                        "risk_grade": 2,
                    },
                )
            ],
        )
        await upsert_objects(
            db,
            "Organization",
            [(f"XSO-{tag}", {"org_id": f"XSO-{tag}", "name": f"검색조직-{tag}"})],
        )
        now = datetime.now(UTC)
        db.add_all(
            [
                ObjectEdit(
                    type="Facility",
                    pk=pk,
                    property="risk_grade",
                    value=5,
                    base_source_value=2,
                    edited_at=now,
                    cleared_at=now,
                ),
                ObjectEdit(
                    type="Facility",
                    pk=pk,
                    property="capacity_kw",
                    value=10,
                    base_source_value=None,
                    edited_at=now,
                ),
            ]
        )
        await db.commit()

    reader = await _reader(make_user, "Facility")
    assert (await login(client, reader[0], reader[1])).status_code == 200
    hits = (await client.get("/api/v1/objects/search", params={"q": tag})).json()
    assert [(h["type"], h["pk"]) for h in hits] == [("Facility", pk)]  # 조직은 읽을 수 없다
    assert hits[0]["title"] == f"검색_시설%{tag}"
    # % · _ 는 글자 그대로 찾는다.
    assert (await client.get("/api/v1/objects/search", params={"q": f"%{tag}"})).json()
    assert not (await client.get("/api/v1/objects/search", params={"q": f"_{tag}"})).json()
    by_pk = (await client.get("/api/v1/objects/search", params={"q": pk.lower()})).json()
    assert [h["pk"] for h in by_pk] == [pk]

    history = (await client.get(f"/api/v1/objects/Facility/{pk}/edits")).json()
    assert {(h["property"], h["value"], h["cleared_at"] is None) for h in history} == {
        ("risk_grade", "5", False),
        ("capacity_kw", "10", True),
    }
    assert (await client.get(f"/api/v1/objects/Facility/NOPE-{tag}/edits")).status_code == 404

    # 숨긴 속성의 편집은 이력에도 없다.
    username, password, uid = await make_user()
    name = f"xh_{uuid.uuid4().hex[:8]}"
    async with session_scope() as db:
        g = await admin.create_group(db, actor_id=None, name=name, display_name=name)
        await admin.set_members(db, actor_id=None, group_id=g.id, user_ids=[uid], via="test")
        await admin.set_permissions(
            db,
            actor_id=None,
            group_id=g.id,
            types=[admin.TypeGrant("Facility", can_read=True, hidden=("capacity_kw",))],
            links=[],
        )
        await db.commit()
    assert (await login(client, username, password)).status_code == 200
    history = (await client.get(f"/api/v1/objects/Facility/{pk}/edits")).json()
    assert [h["property"] for h in history] == ["risk_grade"]


async def test_explorer_limits_are_admin_settings(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    username, password, _ = await make_user("admin")
    assert (await login(client, username, password)).status_code == 200
    r = await client.put("/api/v1/admin/explorer-limits", json={"max_depth": 2, "max_nodes": 50})
    assert r.status_code == 200, r.text
    assert (await client.get("/api/v1/objects/explorer-limits")).json() == {
        "max_depth": 2,
        "max_nodes": 50,
    }
    r = await client.put("/api/v1/admin/explorer-limits", json={"max_depth": 9, "max_nodes": 50})
    assert r.status_code == 422
    await client.put("/api/v1/admin/explorer-limits", json={"max_depth": 3, "max_nodes": 200})
    username, password, _ = await make_user("user")
    assert (await login(client, username, password)).status_code == 200
    r = await client.put("/api/v1/admin/explorer-limits", json={"max_depth": 2, "max_nodes": 50})
    assert r.status_code == 403
