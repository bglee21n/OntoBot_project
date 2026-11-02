"""도입 작업 공간(Phase 20, TRD 17.17) — 양식 선언·검증·진행·권한 판정(순수)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ontobot.adoption import progress, sheets
from ontobot.security import rbac

MANUAL = Path(__file__).resolve().parents[3] / "docs" / "manual" / "user" / "36-adoption-forms.md"


def _cell(text: str) -> str:
    return text.strip().replace("**", "").replace("`", "").replace("☐", "").strip()


def _manual_tables() -> dict[str, list[tuple[tuple[str, ...], list[tuple[str, ...]]]]]:
    """절 번호 → 그 절의 표들(머리글, 본문 행)."""
    out: dict[str, list[tuple[tuple[str, ...], list[tuple[str, ...]]]]] = {}
    number = None
    block: list[str] = []

    def flush() -> None:
        if number and block:
            rows = [tuple(_cell(c) for c in line.strip().strip("|").split("|")) for line in block]
            out.setdefault(number, []).append((rows[0], rows[2:]))
        block.clear()

    for line in MANUAL.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            flush()
            m = re.match(r"## (36\.\d+) ", line)
            number = m.group(1) if m else None
        elif line.startswith("|"):
            block.append(line)
        else:
            flush()
    flush()
    return out


def test_sheets_cover_manual_sections() -> None:
    assert [s.number for s in sheets.SHEETS] == [f"36.{i}" for i in range(1, 17)]
    assert len(sheets.BY_KIND) == 16


@pytest.mark.parametrize("sheet", sheets.SHEETS, ids=lambda s: s.kind)
def test_tables_match_manual(sheet: sheets.Sheet) -> None:
    """표 머리글·고정 행이 매뉴얼 36장과 같다. 매뉴얼의 표는 모두 선언에 있다."""
    manual = _manual_tables()[sheet.number]
    declared = [s for s in sheet.sections if s.layout == "table"]
    for sec in declared:
        header = tuple(c.label for c in sec.columns)
        bodies = [body for h, body in manual if h == header]
        assert bodies, f"{sheet.number} {sec.key}: 매뉴얼에 머리글 {header} 인 표가 없다"
        if sec.rows:
            assert list(sec.rows) in bodies, f"{sheet.number} {sec.key}: 행이 매뉴얼과 다르다"
    assert len(declared) == len(manual), (
        f"{sheet.number}: 매뉴얼 표 {len(manual)}개, 선언 {len(declared)}개"
    )


def test_declaration_is_well_formed() -> None:
    for s in sheets.SHEETS:
        assert s.stage in (0, *sheets.STAGES)
        assert len({sec.key for sec in s.sections}) == len(s.sections)
        for sec in s.sections:
            keys = [c.key for c in sec.inputs]
            assert len(set(keys)) == len(keys), (s.kind, sec.key)
            assert all(re.fullmatch(r"[a-z][a-z_]*", k) for k in keys)
            for c in sec.columns:
                assert (c.kind == "choice") == bool(c.options), (s.kind, sec.key, c.label)
            for r in sec.rows:
                assert len(r) == len(sec.columns), (s.kind, sec.key, r)
            if sec.layout == "fields":
                assert not sec.rows and all(not c.fixed for c in sec.columns)


def test_people_names_are_pii() -> None:
    people = sheets.STAKEHOLDERS.section("people")
    assert people is not None
    assert [c.key for c in people.inputs if c.pii] == ["name", "username"]


# ---------------------------------------------------------------- 검증


def test_validate_cleans_blank_and_drops_empty_free_rows() -> None:
    out = sheets.validate(
        "kickoff",
        {
            "questions": [{"memo": "  "}, {"memo": "예산 승인 필요"}],
            "todos": [{"task": "DB 계정 요청"}, {"task": ""}, {}],
        },
    )
    # 행이 정해진 표는 순서가 뜻이라 빈 행도 그 자리에 남는다.
    assert out["questions"] == [{}, {"memo": "예산 승인 필요"}]
    assert out["todos"] == [{"task": "DB 계정 요청"}]


def test_validate_reports_every_problem_at_once() -> None:
    with pytest.raises(sheets.SheetInvalid) as e:
        sheets.validate(
            "fit",
            {
                "criteria": [{"score": "6"}],
                "decision": {"decision": "아마도"},
                "unknown": {},
                "overview": {"task": "x" * (sheets.TEXT_MAX + 1)},
            },
        )
    paths = {i["path"] for i in e.value.issues}
    assert paths == {"criteria.0.score", "decision.decision", "unknown", "overview.task"}


def test_fixed_table_cannot_grow_and_reference_tables_take_no_payload() -> None:
    with pytest.raises(sheets.SheetInvalid):
        sheets.validate("precheck", {"approval": [{}, {}, {}, {}]})
    # 36.6 분류표는 입력 열이 없는 참고 표라 payload 에 그 키가 없다.
    with pytest.raises(sheets.SheetInvalid):
        sheets.validate("stakeholders", {"roles": []})
    assert sheets.validate("fit", {"forms": [{"picked": True}, {"picked": False}]})["forms"] == [
        {"picked": True},
        {"picked": False},
    ]


# ---------------------------------------------------------------- 진행


def test_handoff_slots_point_at_input_columns() -> None:
    for h in progress.HANDOFFS:
        assert h.stage in sheets.STAGES
        for slot in h.slots:
            sheet = sheets.BY_KIND[slot.kind]
            sec = sheet.section(slot.section)
            assert sec is not None, slot
            assert slot.field in {c.key for c in sec.inputs}, slot
            if slot.row is not None:
                assert sec.rows and slot.row < len(sec.rows), slot
    assert {h.stage for h in progress.HANDOFFS} == set(sheets.STAGES)


def test_progress_counts_only_non_blank_values() -> None:
    empty = progress.evaluate({})
    assert [s.stage for s in empty] == [1, 2, 3, 4, 5]
    assert not any(s.done for s in empty)

    stages = progress.evaluate(
        {
            "fit": {"overview": {"task": "시설 점검"}, "decision": {"decision": "바로 진행"}},
            "systems": {"areas": [{"now": "PostgreSQL 읽기 전용 계정"}]},
            "request": {"problem": {"statement": "   "}},
            "stakeholders": {"people": [{"name": "홍길동"}, {"group": "점검팀"}]},
        }
    )
    assert stages[0].done
    two = {h.label: h.filled for h in stages[1].handoffs}
    assert two == {"풀 문제 한 문장": False, "계정·데이터 그룹 초안": True}


def test_progress_row_slot_looks_at_that_row_only() -> None:
    slot = progress.Slot("systems", "areas", "now", row=0)
    assert not progress.slot_filled({"systems": {"areas": [{}, {"now": "엑셀"}]}}, slot)
    assert progress.slot_filled({"systems": {"areas": [{"now": "엑셀"}]}}, slot)


# ---------------------------------------------------------------- 권한


def test_engagement_access() -> None:
    a = rbac.engagement_access
    assert a(None) == rbac.EngagementAccess(view=False, edit=False, manage=False)
    assert a("viewer") == rbac.EngagementAccess(view=True, edit=False, manage=False)
    assert a("editor") == rbac.EngagementAccess(view=True, edit=True, manage=False)
    assert a("owner") == rbac.EngagementAccess(view=True, edit=True, manage=True)
    assert a("admin") == rbac.EngagementAccess(view=False, edit=False, manage=False)
    assert rbac.can_create_engagement({"modeler"})
    assert rbac.can_create_engagement({"admin"})
    assert not rbac.can_create_engagement({"user", "bot_builder", "domain_expert"})
