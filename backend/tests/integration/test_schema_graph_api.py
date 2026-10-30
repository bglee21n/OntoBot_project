"""스키마 그래프 편집기 API — 폐기·영향 분석·배치·draft 소유자(Phase G1)."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from ontobot.db.models.evaluation import GoldenItem, GoldenSet
from ontobot.db.models.objects import ObjectRecord
from ontobot.db.models.ontology import OntologyVersion
from ontobot.db.models.sync import DataSource, Mapping
from ontobot.db.session import session_scope
from tests.integration.conftest import MakeUser, login

pytestmark = pytest.mark.integration

V = "/api/v1/ontology/versions"
SAMPLE = Path(__file__).resolve().parents[3] / "domain-packs" / "sample" / "ontology.yaml"


@pytest.fixture
async def modeler(client, make_user: MakeUser) -> int:
    username, password, uid = await make_user("modeler")
    assert (await login(client, username, password)).status_code == 200
    return uid


def _type(name: str, *extra: dict) -> dict:
    return {
        "api_name": name,
        "display_name": name,
        "pk_property": "id",
        "title_property": "name",
        "properties": [
            {"api_name": "id", "display_name": "ID", "data_type": "string", "nullable": False},
            {"api_name": "name", "display_name": "이름", "data_type": "string"},
            *extra,
        ],
    }


async def _draft_with_link(client) -> tuple[dict, str, str]:
    """샘플 팩 + 고유 이름의 두 타입 A → B 와 FK 링크 하나를 가진 draft.

    샘플 팩을 함께 둔다 — 이 draft 를 게시하면 다음 테스트들(동기화 매핑 등)이 그 게시본을 본다.
    """
    tag = uuid.uuid4().hex[:8]
    a, b = f"Ga{tag}", f"Gb{tag}"
    draft = (await client.post(V, json={})).json()
    sample = {"format": "yaml", "content": SAMPLE.read_text(encoding="utf-8")}
    assert (await client.post(f"{V}/{draft['id']}/import", json=sample)).status_code == 200
    fk = {"api_name": "b_id", "display_name": "B", "data_type": "string"}
    for t in (_type(a, fk), _type(b)):
        assert (await client.post(f"{V}/{draft['id']}/object-types", json=t)).status_code == 201
    link = {
        "api_name": f"to_b_{tag}",
        "display_name": "B 로",
        "from_type": a,
        "to_type": b,
        "cardinality": "one",
        "source": {"kind": "fk_property", "property": "b_id"},
    }
    assert (await client.post(f"{V}/{draft['id']}/link-types", json=link)).status_code == 201
    detail = (await client.get(f"{V}/{draft['id']}")).json()
    return detail, a, b


async def _refs(client, version_id: int, **params: str):
    return await client.get(f"{V}/{version_id}/references", params=params)


async def test_deprecate_type_and_link_round_trips(client, modeler: int) -> None:
    detail, a, b = await _draft_with_link(client)
    assert detail["issues"] == []
    assert all(t["deprecated"] is False for t in detail["object_types"])
    tb = next(t for t in detail["object_types"] if t["api_name"] == b)
    res = await client.patch(
        f"{V}/{detail['id']}/object-types/{tb['id']}", json={"deprecated": True}
    )
    assert res.status_code == 200 and res.json()["deprecated"] is True
    link = next(lt for lt in detail["link_types"] if lt["from_type"] == a)
    body = {k: v for k, v in link.items() if k != "id"}
    res = await client.patch(
        f"{V}/{detail['id']}/link-types/{link['id']}", json={**body, "deprecated": True}
    )
    assert res.status_code == 200 and res.json()["deprecated"] is True

    exported = (await client.get(f"{V}/{detail['id']}/export?format=json")).json()
    flags = {t["api_name"]: t.get("deprecated") for t in exported["object_types"]}
    assert flags == {"Region": None, "Organization": None, "Facility": None, a: None, b: True}
    # 거짓은 내보내지 않는다 — 폐기 표시 전의 문서와 같은 모양이다.

    published = await client.post(f"{V}/{detail['id']}/publish")
    assert published.status_code == 200, published.text
    child = (await client.post(V, json={"parent_id": detail["id"]})).json()
    flags = {t["api_name"]: t["deprecated"] for t in child["object_types"]}
    assert (flags[a], flags[b], flags["Facility"]) == (False, True, False)
    assert next(lt for lt in child["link_types"] if lt["from_type"] == a)["deprecated"] is True
    diff = (await client.get(f"{V}/{child['id']}/diff?base={detail['id']}")).json()
    assert diff == []


async def test_references_report_names_and_existence_only(client, modeler: int) -> None:
    detail, a, b = await _draft_with_link(client)
    link_name = next(lt["api_name"] for lt in detail["link_types"] if lt["from_type"] == a)
    tag = uuid.uuid4().hex[:8]
    async with session_scope() as db:
        src = DataSource(kind="postgres", name=f"원천 {tag}", secret_ref="env:SECRET_SRC")
        gset = GoldenSet(name=f"골든 {tag}")
        db.add_all([src, gset])
        await db.flush()
        mapping = Mapping(
            source_id=src.id,
            object_type=b,
            source_query="SELECT secret_column FROM hidden_table",
            column_map={"bid": "id", "bname": "name"},
            pk_column="bid",
            mode="full",
            link_rules=[],
        )
        item = GoldenItem(
            golden_set_id=gset.id,
            question="비밀 질문 본문",
            expected_status="ok",
            context=[{"type": b, "pk": "B-1"}],
        )
        obj = ObjectRecord(type=b, pk="B-1", properties={"id": "B-1", "name": "값"})
        db.add_all([mapping, item, obj])
        await db.commit()
        mapping_id, item_id = mapping.id, item.id
    ref = {"id": mapping_id, "data_source": f"원천 {tag}", "object_type": b}

    res = await _refs(client, detail["id"], kind="object_type", name=b)
    assert res.status_code == 200, res.text
    body = res.json()
    assert [r["path"] for r in body["in_document"]] == [f"link_types.{link_name}"]
    assert body["mappings"] == [ref]
    assert body["golden_items"] == [item_id]
    assert body["has_data"] is True
    # 매핑 설정·골든 본문·객체 값은 응답 어디에도 없다.
    for secret in ("hidden_table", "secret_column", "비밀 질문", "B-1", "SECRET_SRC"):
        assert secret not in res.text

    prop = (await _refs(client, detail["id"], kind="property", name="name", type=b)).json()
    assert [r["path"] for r in prop["in_document"]] == [f"object_types.{b}"]
    assert prop["mappings"] == [ref]
    empty = (await _refs(client, detail["id"], kind="object_type", name=a)).json()
    assert empty["mappings"] == [] and empty["golden_items"] == []
    assert empty["has_data"] is False
    link = (await _refs(client, detail["id"], kind="link_type", name=link_name)).json()
    assert link == {"in_document": [], "mappings": [], "golden_items": [], "has_data": False}
    missing = await _refs(client, detail["id"], kind="property", name="id")
    assert missing.status_code == 422


async def _hash(version_id: int) -> str | None:
    async with session_scope() as db:
        return await db.scalar(
            select(OntologyVersion.content_hash).where(OntologyVersion.id == version_id)
        )


async def test_layout_is_outside_the_definition_and_copied_to_child_draft(
    client, modeler: int
) -> None:
    detail, a, b = await _draft_with_link(client)
    nodes = [{"kind": "object_type", "name": a, "x": 10.5, "y": -3}, {"name": b, "x": 200, "y": 40}]
    res = await client.put(f"{V}/{detail['id']}/layout", json={"nodes": nodes})
    assert res.status_code == 200, res.text
    assert [n["name"] for n in res.json()["nodes"]] == sorted([a, b])  # 저장한 것만
    dup = await client.put(f"{V}/{detail['id']}/layout", json={"nodes": [nodes[0], nodes[0]]})
    assert dup.status_code == 422

    assert (await client.post(f"{V}/{detail['id']}/publish")).status_code == 200
    before = await _hash(detail["id"])
    # 게시본의 배치도 바꿀 수 있고, 정의 해시는 그대로다.
    moved = [{**nodes[0], "x": 999}, nodes[1]]
    assert (
        await client.put(f"{V}/{detail['id']}/layout", json={"nodes": moved})
    ).status_code == 200
    assert await _hash(detail["id"]) == before

    child = (await client.post(V, json={"parent_id": detail["id"]})).json()
    copied = (await client.get(f"{V}/{child['id']}/layout")).json()["nodes"]
    assert {n["name"]: n["x"] for n in copied} == {a: 999, b: 200}
    empty = (await client.post(V, json={})).json()
    assert (await client.get(f"{V}/{empty['id']}/layout")).json() == {"nodes": []}


async def test_draft_shows_creator_and_layout_write_needs_modeler(
    client, make_user: MakeUser
) -> None:
    username, password, uid = await make_user("modeler")
    await login(client, username, password)
    draft = (await client.post(V, json={})).json()
    assert draft["created_by"] == uid and draft["created_by_name"]
    listed = next(v for v in (await client.get(V)).json() if v["id"] == draft["id"])
    assert listed["created_by_name"] == draft["created_by_name"]

    admin_name, admin_pw, _ = await make_user("admin")
    await login(client, admin_name, admin_pw)
    assert (await client.get(f"{V}/{draft['id']}/layout")).status_code == 200
    refs = await _refs(client, draft["id"], kind="object_type", name="X")
    assert refs.status_code == 200
    put = await client.put(f"{V}/{draft['id']}/layout", json={"nodes": []})
    assert put.status_code == 403

    user_name, user_pw, _ = await make_user("user")
    await login(client, user_name, user_pw)
    assert (await client.get(f"{V}/{draft['id']}/layout")).status_code == 403
