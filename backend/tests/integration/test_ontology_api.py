"""온톨로지 draft·임포트·게시·버전 비교 흐름(Phase 1 완료 기준, TRD 20절 W1–2)."""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from ontobot.db.models.ontology import OntologyVersion
from ontobot.db.models.security import AuditLog
from ontobot.db.session import session_scope
from ontobot.ontology import importer, registry
from tests.integration.conftest import MakeUser, login

pytestmark = pytest.mark.integration

SAMPLE = Path(__file__).resolve().parents[3] / "domain-packs" / "sample" / "ontology.yaml"
V = "/api/v1/ontology/versions"


@pytest.fixture
async def modeler(client, make_user: MakeUser) -> int:
    username, password, uid = await make_user("modeler")
    assert (await login(client, username, password)).status_code == 200
    return uid


async def _import_sample(client) -> dict:
    draft = (await client.post(V, json={})).json()
    res = await client.post(
        f"{V}/{draft['id']}/import",
        json={"format": "yaml", "content": SAMPLE.read_text(encoding="utf-8")},
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_requires_modeler_role(client, make_user: MakeUser) -> None:
    username, password, _ = await make_user("user")
    await login(client, username, password)
    res = await client.post(V, json={})
    assert res.status_code == 403
    assert res.json()["code"] == "forbidden"


async def test_import_and_publish_sample_pack(client, modeler: int) -> None:
    detail = await _import_sample(client)
    assert detail["status"] == "draft"
    assert detail["issues"] == []
    assert {t["api_name"] for t in detail["object_types"]} == {"Region", "Organization", "Facility"}

    res = await client.post(f"{V}/{detail['id']}/publish")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "published"

    current = registry.current()
    assert current is not None and current.version_id == detail["id"]
    expected = importer.parse(SAMPLE.read_text(encoding="utf-8"), "yaml").content_hash()
    assert current.content_hash == expected

    async with session_scope() as db:
        published = await db.scalar(
            select(func.count())
            .select_from(OntologyVersion)
            .where(OntologyVersion.status == "published")
        )
        events = set(
            await db.scalars(
                select(AuditLog.event).where(AuditLog.target == f"ontology_version:{detail['id']}")
            )
        )
    assert published == 1
    assert {
        "ontology.draft_created",
        "ontology.draft_imported",
        "ontology.version_published",
    } <= events


async def test_published_version_is_immutable_and_parent_copy_is_editable(
    client, modeler: int
) -> None:
    detail = await _import_sample(client)
    await client.post(f"{V}/{detail['id']}/publish")
    facility = next(t for t in detail["object_types"] if t["api_name"] == "Facility")

    blocked = await client.patch(
        f"{V}/{detail['id']}/object-types/{facility['id']}", json={"display_name": "설비"}
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "version_not_editable"

    copy = (await client.post(V, json={"parent_id": detail["id"]})).json()
    assert copy["status"] == "draft" and copy["parent_id"] == detail["id"]
    copy_facility = next(t for t in copy["object_types"] if t["api_name"] == "Facility")
    added = await client.post(
        f"{V}/{copy['id']}/object-types/{copy_facility['id']}/properties",
        json={"api_name": "inspected_on", "display_name": "점검일", "data_type": "date"},
    )
    assert added.status_code == 201, added.text

    changes = (await client.get(f"{V}/{copy['id']}/diff", params={"base": detail["id"]})).json()
    assert changes == [
        {
            "kind": "added",
            "element": "property",
            "path": "Facility.inspected_on",
            "before": None,
            "after": {
                "api_name": "inspected_on",
                "display_name": "점검일",
                "description": None,
                "data_type": "date",
                "enum_values": None,
                "nullable": True,
                "indexed": False,
                "pii_kind": None,
            },
        }
    ]


async def test_publish_is_blocked_until_cross_references_are_complete(client, modeler: int) -> None:
    draft = (await client.post(V, json={})).json()
    created = await client.post(
        f"{V}/{draft['id']}/object-types",
        json={
            "api_name": "Site",
            "display_name": "현장",
            "pk_property": "site_id",
            "title_property": "name",
            "properties": [],
        },
    )
    assert created.status_code == 201

    detail = (await client.get(f"{V}/{draft['id']}")).json()
    assert {i["message"] for i in detail["issues"]} == {
        "pk 속성 'site_id' 이 없습니다",
        "제목 속성 'name' 이 없습니다",
    }
    res = await client.post(f"{V}/{draft['id']}/publish")
    assert res.status_code == 422
    assert res.json()["code"] == "publish_blocked"
    assert len(res.json()["issues"]) == 2


@pytest.mark.parametrize("bad", ["site", "Site-1", "S" * 65, "Site`) RETURN 1 //"])
async def test_type_names_that_cannot_be_neo4j_labels_are_rejected(
    client, modeler: int, bad: str
) -> None:
    draft = (await client.post(V, json={})).json()
    res = await client.post(
        f"{V}/{draft['id']}/object-types",
        json={
            "api_name": bad,
            "display_name": "x",
            "pk_property": "id",
            "title_property": "id",
        },
    )
    assert res.status_code == 422
    assert res.json()["code"] == "validation_error"


async def test_import_rejects_broken_document_with_paths(client, modeler: int) -> None:
    draft = (await client.post(V, json={})).json()
    res = await client.post(
        f"{V}/{draft['id']}/import",
        json={
            "format": "yaml",
            "content": """
object_types:
  - api_name: Facility
    display_name: 시설
    pk_property: facility_id
    title_property: name
    properties:
      - {api_name: facility_id, display_name: ID, data_type: string, nullable: false}
      - {api_name: name, display_name: 이름, data_type: string}
link_types:
  - api_name: owned_by
    display_name: 소유
    from_type: Facility
    to_type: Organization
    cardinality: one
    source: {kind: fk_property, property: owner_org_id}
""",
        },
    )
    assert res.status_code == 422
    messages = {i["message"] for i in res.json()["issues"]}
    assert messages == {
        "도착 타입 'Organization' 이 없습니다",
        "FK 속성 'owner_org_id' 이 'Facility' 에 없습니다",
    }
    # 실패한 임포트는 draft 를 반쯤 바꾸지 않는다.
    assert (await client.get(f"{V}/{draft['id']}")).json()["object_types"] == []


async def test_export_round_trips_to_the_same_definition(client, modeler: int) -> None:
    detail = await _import_sample(client)
    exported = await client.get(f"{V}/{detail['id']}/export", params={"format": "json"})
    assert exported.status_code == 200
    original = importer.parse(SAMPLE.read_text(encoding="utf-8"), "yaml")
    assert importer.parse(exported.text, "json").content_hash() == original.content_hash()
