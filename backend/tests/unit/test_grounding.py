"""근거 대조·등급(TRD 8.2) — 순수 함수라 픽스처만으로 규칙을 고정한다."""

import pytest

from ontobot.grounding.facts import split
from ontobot.grounding.grader import ESTIMATE_MARK, grade
from ontobot.grounding.normalize import contains, normalize
from ontobot.grounding.types import Evidence, GroundingContext

CTX = GroundingContext(
    evidence={
        "o:Facility/F-0001": Evidence(
            "o:Facility/F-0001",
            "Facility",
            "강남 변전소 01호",
            {
                "name": ("강남 변전소 01호", "강남 변전소 01호"),
                "risk_grade": ("2", "2"),
                "capacity_kw": ("2400", "2,400"),
                "installed_on": ("2015-08-02", "2015-08-02"),
            },
        ),
        "o:Organization/ORG-01": Evidence(
            "o:Organization/ORG-01", "Organization", "강남지사", {"name": ("강남지사", "강남지사")}
        ),
        "q:1": Evidence("q:1", None, "결과 1", {"value": ("18", "18")}),
    },
    readable=frozenset({"Facility"}),
    titles=frozenset({"강남 변전소 01호", "강남지사"}),
    enum_values=frozenset({"변전소", "철탑"}),
)


# ---------------------------------------------------------------- 정규화


@pytest.mark.parametrize(
    ("sentence", "value"),
    [
        ("용량은 2,400kW 입니다", "2400"),
        ("용량은 2400kW 입니다", "2,400"),
        ("2015년 8월 2일에 설치", "2015-08-02"),
        ("2015.08.02 설치", "2015-08-02"),
        ("강남   변전소\n01호", "강남 변전소 01호"),
    ],
)
def test_normalization_bridges_only_formatting(sentence: str, value: str) -> None:
    assert contains(sentence, value)


@pytest.mark.parametrize(
    ("sentence", "value"),
    [
        ("약 2,000kW 입니다", "2400"),  # 반올림한 값은 근거가 아니다
        ("위험등급은 높음입니다", "2"),
        ("등급 —", "—"),  # 값 없음 표기는 근거가 될 수 없다
    ],
)
def test_no_fuzzy_matching(sentence: str, value: str) -> None:
    assert not contains(sentence, value)


def test_thousands_are_not_confused_with_decimals() -> None:
    assert normalize("1,234.5") == "1234.5"
    assert normalize("2.5,3") == "2.5,3"


# ---------------------------------------------------------------- 문장 나누기


def test_trailing_citation_belongs_to_previous_sentence() -> None:
    parts = split("용량은 2,400kW입니다. [[o:Facility/F-0001#capacity_kw]] 다음 문장.")
    assert [p.raw for p in parts] == [
        "용량은 2,400kW입니다. [[o:Facility/F-0001#capacity_kw]]",
        "다음 문장.",
    ]


def test_split_round_trips_text() -> None:
    text = "첫째입니다.  둘째입니다.\n\n- 셋째\n- 넷째"
    assert "".join(s.raw + s.sep for s in split(text)) == text


# ---------------------------------------------------------------- 등급


def test_grade_one_when_every_fact_is_cited() -> None:
    g = grade("강남 변전소 01호의 용량은 2,400kW입니다. [[o:Facility/F-0001#capacity_kw]]", CTX)
    assert g.grade == 1
    assert g.text == "강남 변전소 01호의 용량은 2,400kW입니다."
    assert [c.key for c in g.citations] == ["o:Facility/F-0001#capacity_kw"]
    assert g.removed == []


def test_grade_two_marks_uncited_fact_as_estimate() -> None:
    g = grade("위험등급은 2입니다. [[o:Facility/F-0001#risk_grade]]\n2016년에 점검했습니다.", CTX)
    assert g.grade == 2
    assert g.estimated_sentences == [1]
    assert g.text is not None and f"{ESTIMATE_MARK}2016년에 점검했습니다." in g.text
    assert g.fact_sentences == 2 and g.verified_sentences == 1


def test_grade_three_drops_the_body() -> None:
    g = grade("시설은 모두 30개입니다. [[q:1#value]]", CTX)  # 근거 값은 18
    assert g.grade == 3 and g.text is None
    assert [(r.citation, r.reason) for r in g.removed] == [("q:1#value", "value_mismatch")]


def test_answer_without_facts_is_grade_three() -> None:
    # 사실 문장이 없는 ok 답변도 ③ — 통과한 인용이 하나도 없다.
    assert grade("다음과 같습니다.", CTX).grade == 3


def test_grade_two_can_be_disabled() -> None:
    answer = "위험등급은 2입니다. [[o:Facility/F-0001#risk_grade]]\n2016년에 점검했습니다."
    assert grade(answer, CTX, grade2_enabled=False).grade == 3


@pytest.mark.parametrize(
    ("citation", "reason"),
    [
        ("[[o:Facility/F-9999#risk_grade]]", "unknown_ref"),
        ("[[o:Facility/F-0001#budget]]", "unknown_field"),
        ("[[o:Organization/ORG-01#name]]", "forbidden_type"),
    ],
)
def test_invented_or_forbidden_citations_are_removed(citation: str, reason: str) -> None:
    g = grade(f"강남지사의 위험등급은 2입니다. {citation}", CTX)
    assert g.grade == 3
    assert [r.reason for r in g.removed] == [reason]


def test_citation_on_wrong_sentence_does_not_vouch_for_it() -> None:
    # 인용은 '그 문장'의 값을 증명해야 한다 — 다른 값의 인용을 갖다 붙여도 통과하지 않는다.
    g = grade("용량은 9,999kW입니다. [[o:Facility/F-0001#risk_grade]]", CTX)
    assert g.grade == 3


def test_invariant_every_non_estimated_fact_has_a_passing_citation() -> None:
    answer = (
        "- 강남 변전소 01호: 위험등급 2 [[o:Facility/F-0001#risk_grade]]\n"
        "- 용량 2,400kW [[o:Facility/F-0001#capacity_kw]]\n"
        "- 설치 2010년"
    )
    g = grade(answer, CTX)
    assert g.fact_sentences - len(g.estimated_sentences) == g.verified_sentences


def test_malformed_citation_is_recorded_not_silently_dropped() -> None:
    # 스파이크 실측: 모델이 라벨을 인용에 베꼈다 — [[q:1#value (평균(위험등급))]]
    g = grade("평균 위험등급은 18입니다. [[q:1#value (건수(건))]]", CTX)
    assert g.grade == 3
    assert [(r.citation, r.reason) for r in g.removed] == [
        ("[[q:1#value (건수(건))]]", "malformed")
    ]


def test_restating_the_query_condition_is_not_a_fact() -> None:
    ctx = GroundingContext(
        evidence=CTX.evidence,
        readable=CTX.readable,
        titles=CTX.titles,
        enum_values=CTX.enum_values,
        condition_terms=frozenset({"2015-01-01", "2015", "철탑"}),
    )
    answer = (
        "2015년 이전에 설치된 철탑은 다음과 같습니다:\n"
        "* 강남 변전소 01호: 설치일 2015-08-02 [[o:Facility/F-0001#installed_on]]"
    )
    g = grade(answer, ctx)
    assert g.grade == 1 and g.fact_sentences == 1
    # 조건 값을 걷어 내도 결과 값이 남으면 여전히 사실 문장이다.
    assert grade("2015년 이전 철탑은 7개입니다.", ctx).fact_sentences == 1
