from pathlib import Path

from ontobot.ontology import importer
from ontobot.ontology.impact import references
from ontobot.ontology.models import OntologyDocument, check_document

SAMPLE = Path(__file__).resolve().parents[3] / "domain-packs" / "sample" / "ontology.yaml"


def sample() -> OntologyDocument:
    return importer.parse(SAMPLE.read_text(encoding="utf-8"), "yaml")


def paths(doc: OntologyDocument, *args: str) -> list[str]:
    return [i.path for i in references(doc, *args)]  # type: ignore[arg-type]


def test_type_references_are_links_and_actions() -> None:
    doc = sample()
    assert paths(doc, "object_type", "Facility") == [
        "link_types.owned_by",
        "link_types.located_in",
        "action_types.adjust_risk_grade",
        "action_types.request_inspection",
    ]
    assert paths(doc, "object_type", "Region") == ["link_types.located_in"]
    msgs = [i.message for i in references(doc, "object_type", "Region")]
    assert msgs == ["링크 'located_in' 의 도착 타입"]


def test_property_references_cover_pk_fk_and_action_uses() -> None:
    doc = sample()
    assert paths(doc, "property", "facility_id", "Facility") == ["object_types.Facility"]
    assert paths(doc, "property", "owner_org_id", "Facility") == ["link_types.owned_by"]
    risk = references(doc, "property", "risk_grade", "Facility")
    assert [i.path for i in risk] == [
        "action_types.adjust_risk_grade",
        "action_types.request_inspection",
    ]
    assert "바꿀 속성" in risk[0].message and "사전 조건" in risk[1].message
    # 같은 이름이라도 다른 타입의 속성은 아니다(정확 일치).
    assert paths(doc, "property", "region_code", "Region") == ["object_types.Region"]
    assert paths(doc, "property", "name", "Nope") == []


def test_reference_paths_match_issue_paths_after_deleting_the_type() -> None:
    # 화면이 영향 경로와 게시 전 검사 경로를 같은 노드·엣지에 붙인다.
    doc = sample()
    gone = doc.model_copy(
        update={"object_types": [t for t in doc.object_types if t.api_name != "Region"]}
    )
    issue_paths = {i.path for i in check_document(gone)}
    assert set(paths(doc, "object_type", "Region")) <= issue_paths


def test_deprecated_action_target_is_an_issue() -> None:
    doc = sample()
    dep = doc.model_copy(
        update={
            "object_types": [
                t.model_copy(update={"deprecated": t.api_name == "Facility"})
                for t in doc.object_types
            ]
        }
    )
    assert {i.path for i in check_document(dep)} == {
        "action_types.adjust_risk_grade",
        "action_types.request_inspection",
    }
