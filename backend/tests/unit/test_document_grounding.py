"""Phase 16d — 문서 인용 [[d:N/M]] 대조(정확 일치, 순수)."""

from __future__ import annotations

from ontobot.grounding import citations
from ontobot.grounding.document import supports
from ontobot.grounding.grader import grade
from ontobot.grounding.types import Evidence, GroundingContext

CHUNK = (
    "위험등급 1·2 시설은 연 1회, 위험등급 3 시설은 반기마다 1회 정기 점검한다. "
    "차단기는 동작 횟수 2,000회마다 접점을 점검한다."
)


def _ctx() -> GroundingContext:
    return GroundingContext(
        evidence={"d:4/0": Evidence("d:4/0", None, "점검 기준", {}, text=CHUNK)},
        readable=frozenset(),
    )


def test_citation_format() -> None:
    found = citations.find("반기마다 1회입니다. [[d:4/0]] 그리고 [[o:Facility/F-1#risk_grade]]")
    assert [(c.ref, c.field, c.key) for c in found] == [
        ("d:4/0", "", "d:4/0"),
        ("o:Facility/F-1", "risk_grade", "o:Facility/F-1#risk_grade"),
    ]
    # 문서 인용에 속성을 붙이면 형식이 깨진 것이다.
    assert citations.find("[[d:4/0#text]]") == []


def test_supports_exact_numbers_and_span() -> None:
    assert supports("위험등급 3 시설은 반기마다 1회 정기 점검합니다.", CHUNK)
    assert supports("차단기 접점은 동작 2000회마다 점검합니다.", CHUNK)  # 천 단위 정규화
    assert not supports("위험등급 3 시설은 분기마다 2회 점검합니다.", CHUNK)  # "2회"가 없다
    assert not supports("차단기는 200회마다 점검합니다.", CHUNK)  # 200 ≠ 2000
    assert not supports("점검은 꼼꼼히 한다.", CHUNK)  # 옮긴 말이 없다
    assert supports("반기마다 1회 정기 점검", CHUNK)


def test_grade_with_document_citations() -> None:
    ok = grade("위험등급 3 시설은 반기마다 1회 정기 점검합니다. [[d:4/0]]", _ctx())
    assert ok.grade == 1 and [c.key for c in ok.citations] == ["d:4/0"]
    # 지어낸 숫자 — 인용이 지워지고 남은 근거가 없으면 ③.
    bad = grade("위험등급 3 시설은 매월 3회 점검합니다. [[d:4/0]]", _ctx())
    assert bad.grade == 3 and [r.reason for r in bad.removed] == ["value_mismatch"]
    # 검색에서 나오지 않은 청크·속성을 붙인 문서 인용
    other = grade("반기마다 1회 정기 점검합니다. [[d:9/1]]", _ctx())
    assert [r.reason for r in other.removed] == ["unknown_ref"]
    # 한 문장은 맞고 한 문장은 근거 없는 숫자 → ②, 그 문장은 추정
    mixed = grade(
        "위험등급 3 시설은 반기마다 1회 정기 점검합니다. [[d:4/0]]\n점검은 5일 걸립니다.", _ctx()
    )
    assert mixed.grade == 2 and mixed.estimated_sentences == [1]


def test_number_unit_must_match_in_chunk() -> None:
    # "3" 은 청크에 있지만("위험등급 3") "3회"는 없다 — 다른 자리의 숫자로 통과하지 않는다.
    assert not supports("위험등급 3 시설은 매월 3회 점검합니다.", CHUNK)
    assert supports("위험등급 3 시설은 반기마다 1회 점검", CHUNK)
    assert supports(
        "절연유 시험 값이 30kV 미만이면 교체", "시험 값이 30kV 미만이면 절연유를 교체한다."
    )
    assert not supports("시험 값이 30V 미만이면", "시험 값이 30kV 미만이면 절연유를 교체한다.")
