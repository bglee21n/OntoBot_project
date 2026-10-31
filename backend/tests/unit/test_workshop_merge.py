"""Phase 18b — 세션 병합 후보(이름 정확 일치, 순수)."""

from __future__ import annotations

from ontobot.workshop.merge import candidates


def test_groups_by_exact_name_and_flags_conflicts() -> None:
    a = [
        (1, "object_type", {"api_name": "Facility", "display_name": "시설"}, "confirmed"),
        (
            2,
            "property",
            {
                "object_type": "Facility",
                "api_name": "grade",
                "data_type": "int",
                "display_name": "등급",
            },
            "edited",
        ),
        (
            3,
            "property",
            {
                "object_type": "Facility",
                "api_name": "kind",
                "data_type": "enum",
                "enum_values": ["a"],
                "display_name": "유형",
            },
            "confirmed",
        ),
        (4, "golden_query", {"question": "시설 몇 개?"}, "proposed"),  # 확정 전은 보지 않는다
    ]
    b = [
        (11, "object_type", {"api_name": "Facility", "display_name": "설비"}, "confirmed"),
        (
            12,
            "property",
            {
                "object_type": "Facility",
                "api_name": "grade",
                "data_type": "int",
                "display_name": "등급",
            },
            "confirmed",
        ),
        (
            13,
            "property",
            {
                "object_type": "Facility",
                "api_name": "kind",
                "data_type": "enum",
                "enum_values": ["a", "b"],
                "display_name": "유형",
            },
            "confirmed",
        ),
        (
            14,
            "property",
            {
                "object_type": "Facility",
                "api_name": "risk",
                "data_type": "int",
                "display_name": "등급",
            },
            "confirmed",
        ),
        (15, "object_type", {"api_name": "Site", "display_name": "시설"}, "rejected"),
    ]
    groups, same = candidates([(100, a), (200, b)])
    by = {(g.kind, g.key): g for g in groups}
    assert by[("object_type", "Facility")].state == "agree"  # 표시 이름만 다르다
    assert by[("property", "Facility.grade")].state == "agree"
    kind = by[("property", "Facility.kind")]
    assert (kind.state, kind.differs) == ("conflict", ["enum_values"])
    assert by[("property", "Facility.risk")].state == "single"
    assert ("golden_query", "시설 몇 개?") not in by
    # 이름이 다르고 표시 이름이 같은 속성 — 알리기만 한다(유사도로 묶지 않는다).
    assert [(s.kind, s.display_name, s.keys) for s in same] == [
        ("property", "등급", ["Facility.grade", "Facility.risk"])
    ]
