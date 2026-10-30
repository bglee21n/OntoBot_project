from pathlib import Path

import pytest
from pydantic import ValidationError

from ontobot.ontology import importer
from ontobot.ontology.diff import diff
from ontobot.ontology.models import (
    LinkSource,
    LinkTypeDef,
    ObjectTypeDef,
    OntologyDocument,
    PropertyDef,
    check_document,
)
from ontobot.ontology.summary import schema_summary

SAMPLE = Path(__file__).resolve().parents[3] / "domain-packs" / "sample" / "ontology.yaml"


def sample() -> OntologyDocument:
    """객체·링크 타입만.

    샘플 팩의 Action·Function 정의는 test_actions.py·test_functions.py 가 본다.
    """
    doc = importer.parse(SAMPLE.read_text(encoding="utf-8"), "yaml")
    return doc.model_copy(update={"action_types": [], "functions": []})


def prop(name: str, **kw: object) -> PropertyDef:
    return PropertyDef(
        api_name=name, display_name=name, data_type=kw.pop("data_type", "string"), **kw
    )


# ---------------------------------------------------------------- 요소 규칙


@pytest.mark.parametrize(
    "bad",
    ["facility", "Fac-ility", "1Facility", "F" * 65, "F`) RETURN 1 //", "Facility Name", "시설"],
)
def test_type_name_must_be_safe_neo4j_label(bad: str) -> None:
    with pytest.raises(ValidationError):
        ObjectTypeDef(api_name=bad, display_name="x", pk_property="id", title_property="id")


@pytest.mark.parametrize("bad", ["RiskGrade", "risk-grade", "_x", "a" * 65, "위험"])
def test_member_name_format(bad: str) -> None:
    with pytest.raises(ValidationError):
        prop(bad)


def test_enum_requires_unique_values_and_non_enum_forbids_them() -> None:
    with pytest.raises(ValidationError, match="enum_values"):
        prop("kind", data_type="enum")
    with pytest.raises(ValidationError, match="중복"):
        prop("kind", data_type="enum", enum_values=["a", "a"])
    with pytest.raises(ValidationError, match="enum 이 아닌"):
        prop("name", enum_values=["a"])


def test_duplicate_property_names_are_rejected() -> None:
    with pytest.raises(ValidationError, match="중복"):
        ObjectTypeDef(
            api_name="A",
            display_name="a",
            pk_property="id",
            title_property="id",
            properties=[prop("id"), prop("id")],
        )


def test_fk_link_needs_property() -> None:
    with pytest.raises(ValidationError):
        LinkSource(kind="fk_property")


def test_unknown_fields_are_rejected() -> None:
    # 오타 난 필드가 조용히 무시되면 '인덱스를 걸었다'고 믿는 속성에 인덱스가 없다.
    with pytest.raises(ValidationError):
        PropertyDef.model_validate(
            {"api_name": "x", "display_name": "x", "data_type": "int", "indexd": True}
        )


# ---------------------------------------------------------------- 교차 참조


def test_sample_pack_is_consistent() -> None:
    assert check_document(sample()) == []


def test_cross_reference_issues() -> None:
    doc = OntologyDocument(
        object_types=[
            ObjectTypeDef(
                api_name="A",
                display_name="a",
                pk_property="id",
                title_property="name",
                properties=[prop("id")],
            )
        ],
        link_types=[
            LinkTypeDef(
                api_name="to_b",
                display_name="b",
                from_type="A",
                to_type="B",
                cardinality="one",
                source=LinkSource(kind="fk_property", property="b_id"),
            )
        ],
    )
    messages = {i.message for i in check_document(doc)}
    assert messages == {
        "pk 속성은 nullable 일 수 없습니다",
        "제목 속성 'name' 이 없습니다",
        "도착 타입 'B' 이 없습니다",
        "FK 속성 'b_id' 이 'A' 에 없습니다",
    }


def test_empty_document_cannot_be_published() -> None:
    assert [i.message for i in check_document(OntologyDocument())] == [
        "객체 타입이 하나도 없습니다"
    ]


def test_content_hash_ignores_order() -> None:
    doc = sample()
    shuffled = OntologyDocument(
        object_types=[
            t.model_copy(update={"properties": list(reversed(t.properties))})
            for t in reversed(doc.object_types)
        ],
        link_types=list(reversed(doc.link_types)),
    )
    assert shuffled.content_hash() == doc.content_hash()


# ---------------------------------------------------------------- 임포트


def test_yaml_and_json_round_trip() -> None:
    doc = sample()
    for fmt in ("yaml", "json"):
        assert importer.parse(importer.dump(doc, fmt), fmt).content_hash() == doc.content_hash()


@pytest.mark.parametrize(
    ("text", "fmt", "fragment"),
    [
        ("object_types: [", "yaml", "YAML 형식이 아닙니다"),
        ("[]", "json", "최상위는"),
        ("object_types:\n  - api_name: bad name\n", "yaml", "object_types.0.api_name"),
    ],
)
def test_import_failures_say_where(text: str, fmt: str, fragment: str) -> None:
    with pytest.raises(importer.ImportFailed) as e:
        importer.parse(text, fmt)  # type: ignore[arg-type]
    assert fragment in str(e.value)


# ---------------------------------------------------------------- 버전 비교


def test_diff_reports_each_kind() -> None:
    base = sample()
    facility = base.object_type("Facility")
    assert facility is not None
    changed = facility.model_copy(
        update={
            "display_name": "설비",
            "properties": [p for p in facility.properties if p.api_name != "capacity_kw"]
            + [prop("inspected_on", data_type="date")],
        }
    )
    target = OntologyDocument(
        object_types=[t for t in base.object_types if t.api_name not in {"Facility", "Region"}]
        + [changed],
        link_types=[lt for lt in base.link_types if lt.api_name != "located_in"],
    )
    got = {(c.kind, c.element, c.path) for c in diff(base, target)}
    assert got == {
        ("removed", "object_type", "Region"),
        ("changed", "object_type", "Facility"),
        ("removed", "property", "Facility.capacity_kw"),
        ("added", "property", "Facility.inspected_on"),
        ("removed", "link_type", "located_in"),
    }
    assert diff(base, base) == []


# ---------------------------------------------------------------- 플래너 요약 (권한 필터)


def test_summary_hides_unreadable_types_and_links_touching_them() -> None:
    s = schema_summary(sample(), {"Facility", "Region"})
    assert [t["name"] for t in s["object_types"]] == ["Region", "Facility"]
    # owned_by 는 Organization 을 거치므로 이름조차 싣지 않는다(연결 여부도 정보다 — TRD 5.4).
    assert [link["name"] for link in s["link_types"]] == ["located_in"]
    assert "Organization" not in repr(s)


def test_summary_marks_fk_properties_only_through_visible_links() -> None:
    def props(s: dict, type_name: str) -> dict:
        t = next(t for t in s["object_types"] if t["name"] == type_name)
        return {p["name"]: p for p in t["properties"]}

    full = props(schema_summary(sample(), {"Facility", "Region", "Organization"}), "Facility")
    assert "owned_by" in full["owner_org_id"]["refers_to"]
    assert "located_in" in full["region_code"]["refers_to"]
    # 조직을 읽을 수 없으면 owner_org_id 가 무엇을 가리키는지도 싣지 않는다.
    partial = props(schema_summary(sample(), {"Facility", "Region"}), "Facility")
    assert "refers_to" not in partial["owner_org_id"]
    assert "refers_to" in partial["region_code"]


def test_summary_with_no_permission_is_empty() -> None:
    assert schema_summary(sample(), set()) == {"object_types": [], "link_types": []}


# ---------------------------------------------------------------- 폐기(Phase G1)


def _deprecate(
    doc: OntologyDocument,
    *,
    types: frozenset[str] = frozenset(),
    links: frozenset[str] = frozenset(),
) -> OntologyDocument:
    return doc.model_copy(
        update={
            "object_types": [
                t.model_copy(update={"deprecated": t.api_name in types}) for t in doc.object_types
            ],
            "link_types": [
                lt.model_copy(update={"deprecated": lt.api_name in links}) for lt in doc.link_types
            ],
        }
    )


def test_no_deprecation_keeps_canonical_form_and_export() -> None:
    # 폐기 표시가 생기기 전과 같은 정규 표현이어야 게시본 content_hash·카세트 키가 그대로다.
    doc = sample()
    assert "deprecated" not in doc.canonical_json()
    assert "deprecated" not in importer.dump(doc, "yaml")
    assert doc.active() is doc


def test_deprecated_flag_round_trips_and_changes_hash() -> None:
    doc = _deprecate(sample(), types={"Region"}, links={"owned_by"})
    assert doc.content_hash() != sample().content_hash()
    for fmt in ("yaml", "json"):
        back = importer.parse(importer.dump(doc, fmt), fmt)
        assert back.content_hash() == doc.content_hash()
    changed = {(c.kind, c.element, c.path) for c in diff(sample(), doc)}
    assert changed == {("changed", "object_type", "Region"), ("changed", "link_type", "owned_by")}


def test_active_document_drops_deprecated_types_and_their_links() -> None:
    active = _deprecate(sample(), types={"Region"}).active()
    assert [t.api_name for t in active.object_types] == ["Organization", "Facility"]
    assert [lt.api_name for lt in active.link_types] == ["owned_by"]
    readable = {"Facility", "Region", "Organization"}
    s = schema_summary(active, readable)
    assert "Region" not in repr(s) and "located_in" not in repr(s)
    # 폐기가 없으면 요약이 바이트 단위로 같다(측정한 프롬프트 유지).
    assert schema_summary(sample().active(), readable) == schema_summary(sample(), readable)


def test_deprecated_link_alone_is_dropped() -> None:
    active = _deprecate(sample(), links={"located_in"}).active()
    assert [t.api_name for t in active.object_types] == ["Region", "Organization", "Facility"]
    assert [lt.api_name for lt in active.link_types] == ["owned_by"]
