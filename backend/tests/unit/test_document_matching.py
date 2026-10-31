"""Phase 16c — 문서 본문의 객체 이름 찾기(순수, 정확 일치)."""

from __future__ import annotations

from ontobot.documents import matching
from ontobot.documents.matching import Name


def _found(text: str, names: list[Name]) -> dict[str, tuple[str, str, int]]:
    return {m.pk: (m.kind, m.text, m.occurrences) for m in matching.find(text, names)}


def test_exact_title_and_pk_only() -> None:
    names = [
        Name("Facility", "F-0001", "강남 변전소 01호"),
        Name("Facility", "F-0002", "부산 철탑 02호"),
        Name("Facility", "F-0003", "대구 전주 03호"),
    ]
    text = (
        "강남 변전소 01호는 점검 대상이다. F-0002 도 같다. 강남 변전소 01호 재점검. 대구 전주 3호"
    )
    assert _found(text, names) == {
        "F-0001": ("title", "강남 변전소 01호", 2),
        "F-0002": ("pk", "F-0002", 1),
    }  # "대구 전주 3호"는 제목과 한 글자 달라 후보가 아니다(유사도 없음)


def test_short_and_numeric_names_are_skipped() -> None:
    names = [Name("Region", "11", "서"), Name("Region", "26", "부산"), Name("X", "AB", None)]
    assert _found("서울 11호, 부산 26, AB 등급", names) == {"26": ("title", "부산", 1)}


def test_match_inside_longer_name_is_not_counted() -> None:
    names = [Name("Region", "11", "강남"), Name("Organization", "ORG-01", "강남지사")]
    assert _found("강남지사가 맡는다.", names) == {"ORG-01": ("title", "강남지사", 1)}
    # 따로 나오면 둘 다 후보다.
    assert set(_found("강남지사는 강남 일대를 맡는다.", names)) == {"11", "ORG-01"}


def test_title_wins_over_pk_and_nfc() -> None:
    import unicodedata

    names = [Name("Facility", "F-0001", "강남 변전소 01호")]
    nfd = unicodedata.normalize("NFD", "F-0001 강남 변전소 01호")
    (m,) = matching.find(nfd, names)
    assert (m.kind, m.occurrences, m.first_start) == ("title", 1, 7)
    # PII 제목은 None — pk 로만 찾는다.
    (m,) = matching.find("F-0001 강남 변전소 01호", [Name("Facility", "F-0001", None)])
    assert (m.kind, m.first_start) == ("pk", 0)


def test_chunk_of() -> None:
    assert [matching.chunk_of([0, 100, 250], p) for p in (0, 99, 100, 300)] == [0, 0, 1, 2]
