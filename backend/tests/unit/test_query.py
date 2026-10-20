"""QueryPlan 검증·컴파일·조회 조건 문장(TRD 7절)."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from ontobot.objects.reader import render_sql
from ontobot.ontology import importer
from ontobot.ontology.summary import schema_summary
from ontobot.query.compiler import compile_hop, compile_plan, compile_traverse
from ontobot.query.conditions import describe
from ontobot.query.plan import ActionParamValue, ActionProposal, PlannerOutput, QueryPlan
from ontobot.query.planner import ACTION_RULES, SYSTEM
from ontobot.query.planner import messages as planner_messages
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


OWNED_BY_GANGNAM = {
    "link": "owned_by",
    "direction": "out",
    "to": "Organization",
    "filters": [{"property": "name", "op": "eq", "value": "강남지사"}],
}


def test_traverse_is_validated_against_the_far_side_type() -> None:
    vp = validate(DOC, ALL, plan(traverse=[OWNED_BY_GANGNAM]))
    (hop,) = vp.hops
    assert hop.outgoing and hop.to.api_name == "Organization"
    assert [(f.prop.api_name, f.value) for f in hop.filters] == [("name", "강남지사")]
    # 역방향: 조직이 대상이고 시설 쪽 조건으로 좁힌다.
    vp = validate(
        DOC,
        ALL,
        plan(
            target="Organization",
            traverse=[
                {
                    "link": "owned_by",
                    "direction": "in",
                    "to": "Facility",
                    "filters": [{"property": "risk_grade", "op": "gte", "value": "4"}],
                }
            ],
        ),
    )
    assert not vp.hops[0].outgoing and vp.hops[0].filters[0].value == 4


@pytest.mark.parametrize(
    ("traverse", "fragment"),
    [
        ([{"link": "funded_by", "to": "Organization"}], "링크 'funded_by' 이 없습니다"),
        ([{"link": "owned_by", "direction": "in", "to": "Organization"}], "direction 을 'out'"),
        ([{"link": "owned_by", "to": "Region"}], "'Organization' 입니다"),
        (
            [
                {
                    "link": "owned_by",
                    "to": "Organization",
                    "filters": [{"property": "risk_grade", "op": "eq", "value": 1}],
                }
            ],
            "'Organization' 에 속성 'risk_grade' 이 없습니다",
        ),
        ([OWNED_BY_GANGNAM] * 4, "3개까지"),
    ],
)
def test_traverse_errors_explain_themselves(traverse: list[object], fragment: str) -> None:
    with pytest.raises(PlanInvalid) as e:
        validate(DOC, ALL, plan(traverse=traverse))
    assert fragment in " ".join(e.value.errors)
    assert not e.value.forbidden


def test_traverse_into_unreadable_type_is_forbidden_not_replanned() -> None:
    # 링크 반대쪽 타입을 읽을 수 없으면 계획 오류가 아니라 권한 없음이다(재계획하지 않는다).
    with pytest.raises(PlanInvalid) as e:
        validate(DOC, ALL - {"Organization"}, plan(traverse=[OWNED_BY_GANGNAM]))
    assert e.value.forbidden


def test_context_refs_must_come_from_the_conversation() -> None:
    ctx = frozenset({("Facility", "F-0001"), ("Organization", "ORG-01")})
    vp = validate(
        DOC,
        ALL,
        plan(
            context_refs=[
                {"type": "Facility", "pk": "F-0001"},
                {"type": "Organization", "pk": "ORG-01"},
            ],
            traverse=[{"link": "owned_by", "to": "Organization"}],
        ),
        ctx,
    )
    assert vp.target_pks == ("F-0001",) and vp.hops[0].pks == ("ORG-01",)
    with pytest.raises(PlanInvalid, match="대화 맥락에 없는"):
        validate(DOC, ALL, plan(context_refs=[{"type": "Facility", "pk": "F-9999"}]), ctx)
    with pytest.raises(PlanInvalid, match="traverse 의 to 도 아닙니다"):
        validate(DOC, ALL, plan(context_refs=[{"type": "Organization", "pk": "ORG-01"}]), ctx)


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


# ---------------------------------------------------------------- 탐색 컴파일


def test_traverse_compiles_to_bound_sql_and_cypher() -> None:
    hostile = "강남' }) DETACH DELETE n //"
    vp = validate(
        DOC,
        ALL,
        plan(
            traverse=[
                {
                    **OWNED_BY_GANGNAM,
                    "filters": [{"property": "name", "op": "eq", "value": hostile}],
                }
            ]
        ),
    )
    hop_q = compile_hop(vp.hops[0])
    assert hop_q is not None and hop_q.object_type == "Organization"
    assert hostile not in render_sql(hop_q) and _values(hop_q.params) == [hostile]

    cypher, params = compile_traverse(vp, [["ORG-01", "ORG-02"]])
    assert "ORG-01" not in cypher and params["p0"] == ["ORG-01", "ORG-02"]
    assert "MATCH (t:`Facility`)" in cypher
    assert "(t)-[:`owned_by`]->(n:`Organization`)" in cypher
    # stub(원천에 없는 끝점)과의 관계로는 대상을 통과시키지 않는다.
    assert "NOT coalesce(n.stub, false)" in cypher and "NOT coalesce(t.stub, false)" in cypher

    q = compile_plan(vp, ["F-0001"])
    assert "o.pk = ANY(CAST(:v0 AS text[]))" in render_sql(q)
    assert q.params["v0"] == ["F-0001"]


def test_hop_without_conditions_needs_no_sql_and_reverse_arrow_points_in() -> None:
    vp = validate(
        DOC,
        ALL,
        plan(
            target="Organization",
            traverse=[{"link": "owned_by", "direction": "in", "to": "Facility"}],
        ),
    )
    assert compile_hop(vp.hops[0]) is None
    cypher, params = compile_traverse(vp, [None])
    assert "(t)<-[:`owned_by`]-(n:`Facility`)" in cypher and "p0" not in params


def test_traverse_conditions_sentence() -> None:
    vp = validate(DOC, ALL, plan(traverse=[OWNED_BY_GANGNAM]))
    assert describe(vp) == "시설 · 소유 조직[조직: 조직명 = 강남지사] · 최대 20건"
    vp = validate(
        DOC,
        ALL,
        plan(
            target="Organization",
            traverse=[{"link": "owned_by", "direction": "in", "to": "Facility"}],
        ),
    )
    assert describe(vp) == "조직 · 소유 조직(역방향)[시설 있음] · 최대 20건"
    vp = validate(
        DOC, ALL, plan(context_refs=[{"type": "Facility", "pk": "F-1"}]), {("Facility", "F-1")}
    )
    assert describe(vp) == "시설 · 직전 결과 1건 중 · 최대 20건"


# ---------------------------------------------------------------- 플래너 프롬프트


def test_prompt_examples_follow_the_schema_key_order() -> None:
    # 구조화 출력은 스키마의 속성 순서대로 생성된다. 예시가 context_refs 를 filters 보다 먼저
    # 적자 모델이 filters 를 빈 채로 지나쳐 "그 중 위험등급 4 이상"의 조건이 빠졌다
    # (Phase 4 측정: 예시 순서를 고치기 전 0/6, 고친 뒤 6/6).
    order = list(QueryPlan.model_fields)
    examples = []
    i = SYSTEM.find('{"kind"')
    while i >= 0:
        examples.append(_decode(i)[0])
        i = SYSTEM.find('{"kind"', i + 1)
    plans = [e["plan"] for e in examples if e.get("plan")]
    assert len(plans) >= 6
    for p in plans:
        keys = [k for k in p if k in order]
        assert keys == sorted(keys, key=order.index), p
        QueryPlan.model_validate(p)


def _decode(start: int) -> tuple[dict, int]:
    # 예시는 보기 좋게 줄을 나눴다 — 줄바꿈을 지운 뒤 JSON 하나를 읽는다.
    text = SYSTEM[start:].replace("\n", "")
    return json.JSONDecoder().raw_decode(text)


def test_action_examples_follow_the_schema_key_order() -> None:
    # 플래너 예시와 같은 규칙(구조화 출력은 스키마 순서대로 생성된다) — Action 예시도
    # PlannerOutput → ActionProposal → ActionParamValue 필드 순서를 지킨다.
    examples = []
    i = ACTION_RULES.find('{"kind"')
    while i >= 0:
        text = ACTION_RULES[i:].replace("\n", "")
        examples.append(json.JSONDecoder().raw_decode(text)[0])
        i = ACTION_RULES.find('{"kind"', i + 1)
    assert len(examples) >= 3
    for e in examples:
        out = PlannerOutput.model_validate(e)
        assert out.kind == "action" and out.action is not None
        for obj, model in [(e, PlannerOutput), (e["action"], ActionProposal)] + [
            (p, ActionParamValue) for p in e["action"]["params"]
        ]:
            order = list(model.model_fields)
            assert list(obj) == sorted(obj, key=order.index), obj
    # 값이 없는 요청의 예시가 있어야 한다 — 없으면 모델이 값을 지어낸다.
    assert any(not e["action"]["params"] for e in examples)


def test_action_rules_only_when_summary_has_actions() -> None:
    # Action 이 없는 배포의 프롬프트는 측정한 그대로다(ADR-0004).
    plain = schema_summary(DOC, ALL)
    assert "action_types" not in plain
    assert ACTION_RULES not in planner_messages(plain, "질문")[0].content
    with_actions = schema_summary(DOC, ALL, {"adjust_risk_grade"})
    assert [a["name"] for a in with_actions["action_types"]] == ["adjust_risk_grade"]
    assert ACTION_RULES in planner_messages(with_actions, "질문")[0].content
    # 대상 타입을 읽을 수 없으면 요청할 수 있어도 싣지 않는다.
    assert "action_types" not in schema_summary(DOC, {"Region"}, {"adjust_risk_grade"})
