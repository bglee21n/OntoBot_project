"""QueryPlan 검증·컴파일·조회 조건 문장(TRD 7절)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from ontobot.objects.reader import render_sql
from ontobot.ontology import importer
from ontobot.query.compiler import compile_plan
from ontobot.query.conditions import describe
from ontobot.query.plan import QueryPlan
from ontobot.query.validator import PlanInvalid, validate

SAMPLE = Path(__file__).resolve().parents[3] / "domain-packs" / "sample" / "ontology.yaml"
DOC = importer.parse(SAMPLE.read_text(encoding="utf-8"), "yaml")
ALL = frozenset(t.api_name for t in DOC.object_types)


def plan(**kw: object) -> QueryPlan:
    return QueryPlan.model_validate({"target": "Facility", **kw})


# ---------------------------------------------------------------- 검증


def test_values_are_coerced_to_property_types() -> None:
    vp = validate(
        DOC,
        ALL,
        plan(
            filters=[
                {"property": "risk_grade", "op": "gte", "value": "3"},
                {"property": "capacity_kw", "op": "gt", "value": 1000},
                {"property": "installed_on", "op": "lt", "value": "2015-01-01"},
                {"property": "facility_type", "op": "in", "value": ["변전소", "철탑"]},
            ]
        ),
    )
    assert [f.value for f in vp.filters] == [
        3,
        Decimal("1000"),
        date(2015, 1, 1),
        ("변전소", "철탑"),
    ]


@pytest.mark.parametrize(
    ("filters", "fragment"),
    [
        ([{"property": "budget", "op": "eq", "value": 1}], "속성 'budget' 이 없습니다"),
        ([{"property": "facility_type", "op": "eq", "value": "발전소"}], "중 하나여야"),
        ([{"property": "facility_type", "op": "gt", "value": "철탑"}], "연산자 'gt'"),
        ([{"property": "risk_grade", "op": "eq", "value": "높음"}], "정수"),
        ([{"property": "risk_grade", "op": "eq", "value": 2.5}], "정수"),
        ([{"property": "installed_on", "op": "lt", "value": "2015년"}], "YYYY-MM-DD"),
        ([{"property": "risk_grade", "op": "between", "value": [1]}], "두 값"),
        ([{"property": "risk_grade", "op": "eq", "value": True}], "참/거짓"),
    ],
)
def test_invalid_filters_explain_themselves(
    filters: list[dict[str, object]], fragment: str
) -> None:
    with pytest.raises(PlanInvalid) as e:
        validate(DOC, ALL, plan(filters=filters))
    assert fragment in " ".join(e.value.errors)
    assert not e.value.forbidden


def test_unknown_and_unreadable_types() -> None:
    with pytest.raises(PlanInvalid) as missing:
        validate(DOC, ALL, plan(target="Budget"))
    assert not missing.value.forbidden
    with pytest.raises(PlanInvalid) as forbidden:
        validate(DOC, ALL - {"Facility"}, plan())
    assert forbidden.value.forbidden
    # 읽을 수 없는 타입 이름은 '있는 타입' 목록에도 나오지 않는다.
    with pytest.raises(PlanInvalid) as hidden:
        validate(DOC, {"Region"}, plan(target="Budget"))
    assert "Facility" not in hidden.value.errors[0]


def test_traverse_is_refused_until_phase4() -> None:
    with pytest.raises(PlanInvalid, match="traverse"):
        validate(DOC, ALL, plan(traverse=[{"link": "owned_by", "to": "Organization"}]))


def test_aggregate_rules() -> None:
    with pytest.raises(PlanInvalid, match="숫자 속성"):
        validate(DOC, ALL, plan(aggregate={"fn": "avg", "property": "name"}))
    with pytest.raises(PlanInvalid, match="정렬"):
        validate(
            DOC,
            ALL,
            plan(
                aggregate={"fn": "count", "group_by": ["facility_type"]},
                sort=[{"property": "risk_grade"}],
            ),
        )


# ---------------------------------------------------------------- 컴파일


def _values(params: dict[str, object]) -> list[object]:
    return [v for k, v in params.items() if k.startswith("v")]


def test_values_and_keys_are_always_bound() -> None:
    hostile = "x' OR 1=1 --"
    vp = validate(
        DOC,
        ALL,
        plan(
            filters=[{"property": "name", "op": "contains", "value": hostile}],
            sort=[{"property": "risk_grade", "dir": "desc"}],
            limit=5,
        ),
    )
    q = compile_plan(vp)
    sql = render_sql(q)
    assert hostile not in sql and "name" not in sql and "risk_grade" not in sql
    assert _values(q.params) == [hostile]
    assert set(q.params) >= {"k0", "k1"}  # 속성 키도 바인드
    assert "o.deleted_at IS NULL" in sql and "o.type = :__type" in sql
    assert sql.endswith("LIMIT :__limit")


def test_group_by_uses_ordinals_and_avg_is_rounded() -> None:
    q = compile_plan(
        validate(
            DOC,
            ALL,
            plan(
                aggregate={"fn": "avg", "property": "risk_grade", "group_by": ["facility_type"]},
                sort=[{"property": "value", "dir": "desc"}],
            ),
        )
    )
    sql = render_sql(q)
    assert "GROUP BY 1" in sql
    assert "round(avg(" in sql
    assert "ORDER BY value DESC" in sql


# ---------------------------------------------------------------- 조회 조건 문장


def test_conditions_sentence_is_deterministic_and_human_readable() -> None:
    vp = validate(
        DOC,
        ALL,
        plan(
            filters=[
                {"property": "risk_grade", "op": "gte", "value": 3},
                {"property": "capacity_kw", "op": "gt", "value": 1000},
            ],
            aggregate={"fn": "count", "group_by": ["facility_type"]},
        ),
    )
    assert (
        describe(vp)
        == "시설 · 위험등급 ≥ 3 · 설비 용량(kW) > 1,000 · 시설 유형별 건수 · 최대 100건"
    )
    assert describe(vp) == describe(vp)
