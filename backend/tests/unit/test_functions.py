"""Function(PRD 5.10, TRD 17.1-7) — 정의 검사·바인드·식 계산·근거 레코드·플래너 규칙."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from ontobot.functions import binding, evaluator
from ontobot.llm.types import Message
from ontobot.ontology import importer
from ontobot.ontology.models import Expr, FunctionDef, check_document
from ontobot.ontology.summary import schema_summary
from ontobot.query import planner
from ontobot.query.coverage import uncovered_in
from ontobot.query.plan import FunctionCall, FunctionPlannerOutput, PlannerOutput
from ontobot.query.records import render_for_llm
from ontobot.query.validator import PlanInvalid

SAMPLE = Path(__file__).resolve().parents[3] / "domain-packs" / "sample" / "ontology.yaml"
DOC = importer.parse(SAMPLE.read_text(encoding="utf-8"), "yaml")
ALL = frozenset(t.api_name for t in DOC.object_types)


def call(name: str, **args: Any) -> FunctionCall:
    return FunctionCall(name=name, args=[{"name": k, "value": v} for k, v in args.items()])


def fn(**kw: Any) -> FunctionDef:
    base: dict[str, Any] = {
        "api_name": "f",
        "display_name": "f",
        "queries": [
            {
                "api_name": "total",
                "display_name": "전체",
                "target": "Facility",
                "aggregate": {"fn": "count"},
            }
        ],
        "body": {"op": "query", "name": "total"},
        "result_label": "값",
    }
    return FunctionDef.model_validate({**base, **kw})


def issues(f: FunctionDef) -> list[str]:
    doc = DOC.model_copy(update={"functions": [f]})
    return [i.message for i in check_document(doc) if i.path == "functions.f"]


# ---------------------------------------------------------------- 정의


def test_sample_functions_pass_the_publish_check() -> None:
    assert [f.api_name for f in DOC.functions] == ["high_risk_ratio", "region_high_risk_ratio"]
    assert not [i for i in check_document(DOC) if i.path.startswith("functions.")]


def test_document_without_functions_keeps_its_hash() -> None:
    # Function 이 없는 문서는 Phase 12 이전과 같은 정규 표현(게시본 content_hash 가 맞는다).
    plain = DOC.model_copy(update={"functions": []})
    assert '"functions"' not in plain.canonical_json()
    assert '"functions"' in DOC.canonical_json()
    shuffled = DOC.model_copy(update={"functions": list(reversed(DOC.functions))})
    assert shuffled.content_hash() == DOC.content_hash()


@pytest.mark.parametrize(
    ("node", "fragment"),
    [
        ({"op": "add", "args": [{"op": "const", "value": 1}]}, "인자가 2개"),
        ({"op": "query"}, "name"),
        ({"op": "const", "value": "abc"}, "숫자나 참/거짓"),
        ({"op": "round", "args": [{"op": "const", "value": 1}]}, "digits"),
        ({"op": "param", "name": "x", "value": 3}, "value 는 const 에만"),
    ],
)
def test_expression_nodes_have_fixed_shapes(node: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        Expr.model_validate(node)


def test_expression_size_is_capped() -> None:
    deep: dict[str, Any] = {"op": "const", "value": 1}
    for _ in range(9):
        deep = {"op": "round", "digits": 0, "args": [deep]}
    with pytest.raises(ValidationError, match="단계까지"):
        fn(body=deep)


def test_required_param_cannot_have_a_default() -> None:
    with pytest.raises(ValidationError, match="기본값"):
        fn(params=[{"api_name": "x", "display_name": "x", "data_type": "int", "default": 1}])


def test_cross_checks_catch_definition_mistakes() -> None:
    q = {"api_name": "high", "display_name": "높음", "target": "Facility"}
    # 파라미터 자료형이 속성과 다르다 / 목록 연산자에 파라미터 / 없는 속성·조회·파라미터
    got = issues(
        fn(
            params=[
                {"api_name": "kind", "display_name": "유형", "data_type": "int"},
                {"api_name": "n", "display_name": "n", "data_type": "int", "required": False},
            ],
            queries=[
                {
                    **q,
                    "filters": [
                        {"property": "name", "op": "eq", "value": {"param": "kind"}},
                        {"property": "risk_grade", "op": "in", "value": {"param": "n"}},
                        {"property": "nope", "op": "eq", "value": 1},
                        {"property": "risk_grade", "op": "contains", "value": 1},
                    ],
                    "aggregate": {"fn": "count"},
                }
            ],
            body={
                "op": "div",
                "args": [{"op": "query", "name": "x"}, {"op": "param", "name": "y"}],
            },
        )
    )
    joined = " | ".join(got)
    assert "자료형 int 을 속성 자료형 string" in joined
    assert "'in' 의 값에는 파라미터를 쓸 수 없습니다" in joined
    assert "속성 'nope'" in joined
    assert "연산자 'contains'" in joined
    assert "조회 'x' 이 없습니다" in joined
    assert all(m.startswith("조회 'high'") for m in got[:4])


def test_grouped_queries_must_share_group_by_and_names_must_not_clash() -> None:
    def q(name: str, groups: list[str]) -> dict[str, Any]:
        return {
            "api_name": name,
            "display_name": name,
            "target": "Facility",
            "aggregate": {"fn": "count", "group_by": groups},
        }

    got = issues(fn(queries=[q("a", ["facility_type"]), q("b", ["region_code"])]))
    assert "같은 group_by" in " ".join(got)
    got = issues(fn(queries=[q("value", [])], body={"op": "query", "name": "value"}))
    assert "예약 이름" in " ".join(got)


def test_body_types_are_checked() -> None:
    cmp = {"op": "gt", "args": [{"op": "query", "name": "total"}, {"op": "const", "value": 3}]}
    assert "결과가 decimal 이 아닙니다" in " ".join(issues(fn(body=cmp)))
    assert not issues(fn(body=cmp, result_type="bool"))
    bad_if = {"op": "if", "args": [{"op": "query", "name": "total"}, cmp, cmp]}
    assert "'if'" in " ".join(issues(fn(body=bad_if, result_type="bool")))
    sum_on_text = fn(
        queries=[
            {
                "api_name": "total",
                "display_name": "t",
                "target": "Facility",
                "aggregate": {"fn": "sum", "property": "name"},
            }
        ]
    )
    assert "숫자 속성" in " ".join(issues(sum_on_text))


def test_deprecated_types_drop_their_functions_from_the_active_document() -> None:
    dep = DOC.model_copy(
        update={
            "object_types": [
                t.model_copy(update={"deprecated": t.api_name == "Region"})
                for t in DOC.object_types
            ]
        }
    )
    assert [f.api_name for f in dep.active().functions] == ["high_risk_ratio"]
    assert "functions.region_high_risk_ratio" in {i.path for i in check_document(dep)}


def test_summary_lists_functions_only_when_every_type_is_readable() -> None:
    names = [f["name"] for f in schema_summary(DOC, ALL)["functions"]]
    assert names == ["high_risk_ratio", "region_high_risk_ratio"]
    # Region 을 못 읽으면 Region 을 조회하는 Function 은 이름조차 없다.
    assert [f["name"] for f in schema_summary(DOC, {"Facility"})["functions"]] == [
        "high_risk_ratio"
    ]
    assert "functions" not in schema_summary(DOC, {"Region"})
    first = schema_summary(DOC, ALL)["functions"][0]
    assert first["group_by"] == ["facility_type"]
    assert first["params"] == [
        {
            "name": "min_grade",
            "display_name": "기준 위험등급",
            "type": "int",
            "required": False,
            "default": 4,
        }
    ]


# ---------------------------------------------------------------- 바인드


def test_bind_uses_defaults_and_coerces_args() -> None:
    vf = binding.bind(DOC, ALL, call("high_risk_ratio"))
    assert vf.args == {"min_grade": 4} and vf.given == frozenset()
    high = vf.queries[0].plan
    assert [(f.prop.api_name, f.op, f.value) for f in high.filters] == [("risk_grade", "gte", 4)]
    assert [g.api_name for g in vf.group_by] == ["facility_type"]
    vf = binding.bind(DOC, ALL, call("high_risk_ratio", min_grade="3"))
    assert vf.args == {"min_grade": 3} and vf.given == {"min_grade"}
    assert binding.input_values(vf) == [3, 3]


def test_bind_errors_are_collected_for_one_replan() -> None:
    with pytest.raises(PlanInvalid) as e:
        binding.bind(DOC, ALL, call("high_risk_ratio", min_grade="높음", extra=1))
    assert len(e.value.errors) == 2 and not e.value.forbidden
    with pytest.raises(PlanInvalid, match="필수 인자 'region'"):
        binding.bind(DOC, ALL, call("region_high_risk_ratio"))
    with pytest.raises(PlanInvalid, match="있는 Function: high_risk_ratio"):
        binding.bind(DOC, {"Facility"}, call("nope"))


def test_function_over_unreadable_type_is_forbidden() -> None:
    # 요약에 없더라도 이름을 지어 부르면 권한 없음이다 — 재계획시키지 않는다(TRD 7.3).
    with pytest.raises(PlanInvalid) as e:
        binding.bind(DOC, {"Facility"}, call("region_high_risk_ratio", region="대구"))
    assert e.value.forbidden


def test_optional_param_without_default_drops_its_condition() -> None:
    f = fn(
        params=[
            {
                "api_name": "kind",
                "display_name": "유형",
                "data_type": "enum",
                "enum_values": ["변전소", "철탑", "전주"],
                "required": False,
            },
        ],
        queries=[
            {
                "api_name": "total",
                "display_name": "전체",
                "target": "Facility",
                "filters": [{"property": "facility_type", "op": "eq", "value": {"param": "kind"}}],
                "aggregate": {"fn": "count"},
            }
        ],
    )
    doc = DOC.model_copy(update={"functions": [f]})
    assert binding.bind(doc, ALL, call("f")).queries[0].plan.filters == ()
    vf = binding.bind(doc, ALL, call("f", kind="철탑"))
    assert vf.queries[0].plan.filters[0].value == "철탑"
    assert evaluator.inputs(binding.bind(doc, ALL, call("f"))) == "유형 = 지정 안 함"


# ---------------------------------------------------------------- 계산


def _vf() -> binding.ValidatedFunction:
    return binding.bind(DOC, ALL, call("high_risk_ratio"))


def test_grouped_evaluation_unions_keys_and_counts_missing_as_zero() -> None:
    rows = evaluator.evaluate(
        _vf(),
        {
            "high_risk": [{"g0": "철탑", "value": 7}, {"g0": "변전소", "value": 3}],
            "total": [
                {"g0": "변전소", "value": 10},
                {"g0": "전주", "value": 10},
                {"g0": "철탑", "value": 10},
            ],
        },
    )
    assert [(r.key, r.values, r.value) for r in rows] == [
        (("변전소",), {"high_risk": 3, "total": 10}, Decimal(30)),
        (("전주",), {"high_risk": 0, "total": 10}, Decimal(0)),
        (("철탑",), {"high_risk": 7, "total": 10}, Decimal(70)),
    ]


def test_division_by_zero_and_missing_values_stay_missing() -> None:
    rows = evaluator.evaluate(_vf(), {"high_risk": [], "total": [{"g0": "변전소", "value": 0}]})
    assert rows[0].value is None
    rs = evaluator.build(_vf(), rows)
    assert rs.records[0].field("value").display == "—"  # type: ignore[union-attr]


def test_rounding_comparison_and_if() -> None:
    body = {
        "op": "if",
        "args": [
            {"op": "gte", "args": [{"op": "query", "name": "total"}, {"op": "const", "value": 3}]},
            {"op": "div", "args": [{"op": "query", "name": "total"}, {"op": "const", "value": 3}]},
            {"op": "const", "value": -1},
        ],
    }
    doc = DOC.model_copy(update={"functions": [fn(body=body, decimals=2)]})
    vf = binding.bind(doc, ALL, call("f"))
    assert evaluator.evaluate(vf, {"total": [{"value": 10}]})[0].value == Decimal("3.33")
    assert evaluator.evaluate(vf, {"total": [{"value": 2}]})[0].value == Decimal(-1)
    # 그룹 없는 count 가 비면(행 없음) 0 이다.
    assert evaluator.evaluate(vf, {"total": []})[0].value == Decimal(-1)


def test_records_carry_inner_values_and_inputs() -> None:
    vf = binding.bind(DOC, ALL, call("high_risk_ratio", min_grade=3))
    rows = evaluator.evaluate(
        vf,
        {"high_risk": [{"g0": "철탑", "value": 8}], "total": [{"g0": "철탑", "value": 10}]},
    )
    block = render_for_llm(evaluator.build(vf, rows))
    assert block.splitlines() == [
        "<data>",
        "## q:1 — 결과 1",
        "q:1#facility_type | 시설 유형 | 철탑",
        "q:1#high_risk | 고위험 시설 수 | 8",
        "q:1#total | 전체 시설 수 | 10",
        "q:1#value | 고위험 비율(%) | 80",
        "q:1#inputs | 입력 조건 | 기준 위험등급 = 3",
        "</data>",
    ]


def test_conditions_sentence_names_the_function_and_inner_queries() -> None:
    assert evaluator.describe(_vf()) == (
        "고위험 시설 비율(기준 위험등급 = 4(기본값)) · 고위험 시설 수: 시설 · 위험등급 ≥ 4 · "
        "시설 유형별 건수 / 전체 시설 수: 시설 · 시설 유형별 건수 · 최대 100개 그룹"
    )
    vf = binding.bind(DOC, ALL, call("region_high_risk_ratio", region="대구"))
    assert "소재 지역[지역: 지역명 = 대구]" in evaluator.describe(vf)


def test_coverage_counts_function_inputs() -> None:
    vf = binding.bind(DOC, ALL, call("high_risk_ratio"))
    # "4 이상" 은 기본값으로 덮인다 — 질문의 숫자가 인자에 있으면 빠진 조건이 아니다.
    assert uncovered_in("위험등급 4 이상 비율", binding.input_values(vf)) == []
    assert uncovered_in("위험등급 3 이상 비율", binding.input_values(vf)) == ["3"]


# ---------------------------------------------------------------- 플래너


def _examples(text: str) -> list[dict[str, Any]]:
    out = []
    i = text.find('{"kind"')
    while i >= 0:
        out.append(json.JSONDecoder().raw_decode(text[i:].replace("\n", ""))[0])
        i = text.find('{"kind"', i + 1)
    return out


def test_function_examples_follow_the_schema_key_order() -> None:
    examples = _examples(planner.FUNCTION_RULES)
    calls = [e for e in examples if e["kind"] == "function"]
    assert len(calls) >= 3
    for e in examples:
        out = FunctionPlannerOutput.model_validate(e)
        assert (out.kind == "function") == (out.function is not None)
        pairs = [(e, FunctionPlannerOutput)] + (
            [(e["function"], FunctionCall)] if out.function else []
        )
        for obj, model in pairs:
            order = list(model.model_fields)
            assert list(obj) == sorted(obj, key=order.index), obj
    # 인자 없이 부르는 예시가 있어야 한다 — 없으면 모델이 기본값 인자를 지어 넣는다.
    assert any(not e["function"]["args"] for e in calls)
    # 인자로 넣을 수 없는 조건이 있으면 부르지 않는 예시(조건을 빼고 부르면 다른 답이다 — 측정).
    assert any(e["kind"] == "cannot_answer" for e in examples)


class _Capture:
    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = reply
        self.schemas: list[type[BaseModel]] = []

    async def structured[M: BaseModel](
        self, role: str, messages: list[Message], schema: type[M]
    ) -> M:
        self.schemas.append(schema)
        return schema.model_validate(self.reply)


async def test_planner_schema_changes_only_when_summary_has_functions() -> None:
    plain = schema_summary(DOC.model_copy(update={"functions": []}), ALL)
    llm = _Capture({"kind": "not_query"})
    out = await planner.plan(llm, plain, "안녕")
    # Function 이 없는 배포는 측정한 스키마·프롬프트 그대로다.
    assert llm.schemas == [PlannerOutput] and out.kind == "not_query"
    assert planner.FUNCTION_RULES not in planner.messages(plain, "q")[0].content
    with_fns = schema_summary(DOC, ALL)
    llm = _Capture({"kind": "function", "function": {"name": "high_risk_ratio", "args": []}})
    out = await planner.plan(llm, with_fns, "비율")
    assert llm.schemas == [FunctionPlannerOutput] and out.function is not None
    assert planner.FUNCTION_RULES in planner.messages(with_fns, "q")[0].content


def test_planner_output_without_functions_is_unchanged() -> None:
    # 녹화 키(구조화 출력 스키마)가 그대로여야 Function 없는 배포의 측정이 유효하다.
    assert list(PlannerOutput.model_fields) == ["kind", "plan", "action", "reason"]
    assert "function" not in json.dumps(PlannerOutput.model_json_schema())
