"""속성·행 단위 권한(Phase 14b) — 합집합 규칙, 보이는 문서, 행 정책 컴파일."""

from pathlib import Path

from ontobot.objects.reader import render_sql
from ontobot.ontology import importer
from ontobot.query.compiler import compile_plan
from ontobot.query.plan import QueryPlan
from ontobot.query.validator import check_scopes, validate
from ontobot.security import rbac

SAMPLE = Path(__file__).resolve().parents[3] / "domain-packs" / "sample" / "ontology.yaml"
DOC = importer.parse(SAMPLE.read_text(encoding="utf-8"), "yaml")
TOWERS = ({"property": "facility_type", "op": "eq", "value": "철탑"},)


def test_hidden_only_when_every_reading_group_hides() -> None:
    one = rbac.merge({"Facility": [rbac.grant(hidden=["capacity_kw", "installed_on"])]})
    assert one.of("Facility").hidden == {"capacity_kw", "installed_on"}
    two = rbac.merge(
        {
            "Facility": [
                rbac.grant(hidden=["capacity_kw", "installed_on"]),
                rbac.grant(hidden=["capacity_kw"]),
            ]
        }
    )
    assert two.of("Facility").hidden == {"capacity_kw"}


def test_row_policies_union_and_unrestricted_group_wins() -> None:
    a = rbac.merge({"Facility": [rbac.grant(read_rows=TOWERS)]})
    assert a.of("Facility").read_rows == (TOWERS,)
    b = rbac.merge({"Facility": [rbac.grant(read_rows=TOWERS), rbac.grant()]})
    assert b.of("Facility").read_rows is None  # 정책 없는 그룹이 하나라도 있으면 제한 없음
    c = rbac.merge({"Facility": [rbac.grant(read_rows=TOWERS), rbac.grant(read_rows=TOWERS)]})
    assert c.of("Facility").read_rows == (TOWERS,)  # 같은 정책은 한 번
    # 쓰기 정책은 편집을 주는 그룹만 본다.
    d = rbac.merge({"Facility": [rbac.grant(edit=True, write_rows=TOWERS), rbac.grant(edit=False)]})
    assert d.editable == {"Facility"} and d.of("Facility").write_rows == (TOWERS,)


def test_visible_document_drops_hidden_properties_and_dependents() -> None:
    view = DOC.without_properties({"Facility": {"risk_grade", "facility_id"}})
    facility = view.object_type("Facility")
    assert facility is not None
    names = {p.api_name for p in facility.properties}
    assert "risk_grade" not in names and "facility_id" in names  # pk 는 숨기지 않는다
    # 위험등급을 바꾸는·조건으로 쓰는 Action 과 Function 은 통째로 빠진다.
    assert view.action_types == []
    assert view.functions == []
    assert DOC.without_properties({}) is DOC


def test_scope_is_compiled_as_bound_or_of_ands() -> None:
    scopes = check_scopes(
        DOC, {"Facility": [list(TOWERS), [{"property": "risk_grade", "op": "gte", "value": 4}]]}
    )
    vp = validate(DOC, {"Facility"}, QueryPlan(target="Facility"), scopes=scopes)
    q = compile_plan(vp)
    sql = render_sql(q)
    assert " OR " in sql and "철탑" not in sql  # 값은 바인드
    assert "철탑" in q.params.values()


def test_invalid_policy_closes() -> None:
    scopes = check_scopes(DOC, {"Facility": [[{"property": "nope", "op": "eq", "value": 1}]]})
    assert scopes == {"Facility": ()}
    vp = validate(DOC, {"Facility"}, QueryPlan(target="Facility"), scopes=scopes)
    assert "false" in render_sql(compile_plan(vp))
