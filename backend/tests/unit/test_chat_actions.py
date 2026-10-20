"""채팅 Action 제안 검증·평가 판정(TRD 17.1-5, Phase 11) — DB 없이."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ontobot.actions.params import ground_params, in_question, requestable
from ontobot.evaluation import metrics
from ontobot.ontology import importer
from ontobot.ontology.models import ActionTypeDef
from ontobot.query.plan import ActionParamValue, ActionProposal

SAMPLE = Path(__file__).resolve().parents[3] / "domain-packs" / "sample" / "ontology.yaml"
DOC = importer.parse(SAMPLE.read_text(encoding="utf-8"), "yaml")


def _def(name: str) -> ActionTypeDef:
    a = DOC.action_type(name)
    assert a is not None
    return a


ADJUST = _def("adjust_risk_grade")
INSPECT = _def("request_inspection")


def _proposal(action: str = "adjust_risk_grade", **params: Any) -> ActionProposal:
    return ActionProposal(
        action_type=action,
        target="F-0004",
        params=[ActionParamValue(name=k, value=v) for k, v in params.items()],
    )


@pytest.mark.parametrize(
    ("question", "value", "found"),
    [
        ("위험등급을 4로 바꿔줘", 4, True),
        ("위험등급을 4로 바꿔줘", 4.0, True),  # 표기가 달라도 수 값이 같다
        ("용량을 1,200 으로", 1200, True),
        ("위험등급을 4로 바꿔줘", 5, False),
        ("사유: 정기점검", "정기점검", True),
        ("사유: 정기 점검", "정기점검", False),  # 정규화는 공백을 지우지 않는다(유사도 없음)
        ("2026년 3월 5일로", "2026-03-05", True),  # 날짜 ISO 정규화(근거 대조와 같다)
    ],
)
def test_in_question(question: str, value: Any, found: bool) -> None:
    assert in_question(question, value) is found


def test_values_must_come_from_the_question() -> None:
    params, errors = ground_params(ADJUST, _proposal(risk_grade=4), "F-0004 위험등급을 4로")
    assert params == {"risk_grade": 4} and errors == []
    # 질문에 없는 값은 버리고, 빠진 필수 값은 "비워 두라"는 안내와 함께 알린다(지어내지 않게).
    params, errors = ground_params(ADJUST, _proposal(risk_grade=3), "F-0004 위험등급 바꿔줘")
    assert params == {}
    assert any("질문에 없습니다" in e for e in errors)
    assert any("필요합니다" in e and "비워 두세요" in e for e in errors)


def test_closed_values_need_only_the_type_check() -> None:
    # enum 은 목록 밖 값이 될 수 없다 — 질문 표기("급한")와 달라도 변환을 통과하면 된다.
    params, errors = ground_params(
        INSPECT, _proposal("request_inspection", urgency="긴급"), "급한 점검"
    )
    assert params == {"urgency": "긴급"} and errors == []
    _, errors = ground_params(INSPECT, _proposal("request_inspection", urgency="보통"), "보통")
    assert errors and "enum" in errors[0]


def test_unknown_and_duplicate_params_are_reported_together() -> None:
    proposal = ActionProposal(
        action_type="adjust_risk_grade",
        target="F-0004",
        params=[
            ActionParamValue(name="risk_grade", value=4),
            ActionParamValue(name="risk_grade", value=4),
            ActionParamValue(name="grade", value=4),
        ],
    )
    _, errors = ground_params(ADJUST, proposal, "F-0004 위험등급 4")
    assert any("두 번" in e for e in errors)
    assert any("알 수 없는 파라미터: grade" in e for e in errors)


def test_requestable_follows_read_permission_and_requester_roles() -> None:
    assert requestable(DOC, {"Facility"}, None) == {"adjust_risk_grade", "request_inspection"}
    assert requestable(DOC, {"Region"}, None) == frozenset()
    gated = DOC.model_copy(
        update={"action_types": [ADJUST.model_copy(update={"requester_roles": ["domain_expert"]})]}
    )
    assert requestable(gated, {"Facility"}, {"user"}) == frozenset()
    assert requestable(gated, {"Facility"}, {"domain_expert"}) == {"adjust_risk_grade"}


# ---------------------------------------------------------------- 평가 판정


def _spec(**expected: Any) -> metrics.Spec:
    return metrics.Spec(
        key="a", answerable=True, expected_status="action_preview", expected_action=dict(expected)
    )


def _out(**action: Any) -> metrics.Outcome:
    base = {"action_type": "adjust_risk_grade", "target_pk": "F-0004", "params": {}, "ready": True}
    return metrics.Outcome(status="action_preview", action={**base, **action})


def test_action_judged_by_type_target_and_params() -> None:
    spec = _spec(action_type="adjust_risk_grade", target_pk="F-0004", params={"risk_grade": 4})
    ok = metrics.judge(spec, _out(params={"risk_grade": 4}), set())
    assert ok.correct and ok.action_ok and not ok.action_fabricated
    wrong_value = metrics.judge(spec, _out(params={"risk_grade": 5}), set())
    assert not wrong_value.correct and wrong_value.action_mismatch
    extra = metrics.judge(spec, _out(params={"risk_grade": 4, "reason": "x"}), set())
    assert not extra.action_ok
    other = metrics.judge(spec, _out(target_pk="F-0001", params={"risk_grade": 4}), set())
    assert not other.action_ok and other.action_fabricated  # 다른 실재 객체로 바꿔 끼움


def test_blocked_item_counts_a_preview_as_fabrication() -> None:
    spec = _spec(action_type="adjust_risk_grade", ready=False)
    blocked = metrics.judge(spec, _out(ready=False, target_pk=None), set())
    assert blocked.correct and not blocked.action_fabricated
    made = metrics.judge(spec, _out(), set())
    assert not made.correct and made.action_fabricated
    # Action 이 아닌 문항에서 미리보기를 만들어도 지어내기다.
    query = metrics.Spec(key="q", answerable=True, expected_status="ok")
    assert metrics.judge(query, _out(), set()).action_fabricated


def test_action_metrics() -> None:
    spec_ok = _spec(action_type="adjust_risk_grade", target_pk="F-0004", params={})
    spec_block = _spec(action_type="adjust_risk_grade", ready=False)
    pairs = [(spec_ok, _out()), (spec_ok, _out(target_pk="F-0001")), (spec_block, _out())]
    m = metrics.compute([(s, o, metrics.judge(s, o, set())) for s, o in pairs])
    assert m["action_items"] == 3
    assert m["action_param_accuracy"] == 0.5  # 막아야 할 문항은 분모에 넣지 않는다
    assert m["action_fabrications"] == 2
    assert metrics.compute([])["action_param_accuracy"] is None
