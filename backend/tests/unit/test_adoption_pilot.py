"""Phase 22 파일럿 판단·자산화 내보내기 — 순수 판정(고를 수 있는 판단, 사람 정보, 골든 팩)."""

from __future__ import annotations

import yaml

from ontobot.adoption import export, pilot, progress
from ontobot.adoption.pilot import GateCheck, RunEvidence
from ontobot.db.models.evaluation import GoldenItem
from ontobot.evaluation import golden


def _run(*checks: GateCheck, passed: bool = True) -> RunEvidence:
    return RunEvidence(
        run_id=1, golden_set="sample", finished_at=None, gate_passed=passed, checks=list(checks)
    )


def test_go_needs_a_real_run_that_passed_without_invariant_violations() -> None:
    assert pilot.allowed(None)["go"] and pilot.allowed(None)["no_go"] is None
    ok = _run(GateCheck("permission_violations", "invariant", "max", 0, 0, True))
    assert pilot.allowed(ok) == {"go": None, "go_after_fix": None, "no_go": None}
    # 평균 점수가 높아도 불변식 위반이 있으면 보류만(매뉴얼 20.6).
    violated = _run(
        GateCheck("accuracy", "threshold", "min", 0.9, 0.99, True),
        GateCheck("permission_violations", "invariant", "max", 0, 1, False),
        passed=False,
    )
    why = pilot.allowed(violated)
    assert "permission_violations" in (why["go"] or "") and why["go_after_fix"] == why["go"]
    assert why["no_go"] is None
    failed_gate = _run(GateCheck("accuracy", "threshold", "min", 0.9, 0.5, False), passed=False)
    assert "게이트" in (pilot.allowed(failed_gate)["go_after_fix"] or "")


def test_business_metrics_come_from_the_form_as_written() -> None:
    payloads = {"pilot": {"metrics": [{}, {}, {}, {}, {"current": "하루", "target": "10분"}]}}
    labels = ["a", "b", "c", "d", "조회에 드는 시간", "활용도"]
    out = pilot.business_metrics(payloads, labels)
    assert [(m.label, m.current, m.target) for m in out] == [
        ("조회에 드는 시간", "하루", "10분"),
        ("활용도", None, None),
    ]


def test_decision_record_fills_the_stage5_handoff() -> None:
    before = progress.evaluate({})[4]
    after = progress.evaluate({}, decided=True)[4]
    assert {h.label: h.filled for h in before.handoffs}[progress.DECIDED_LABEL] is False
    assert {h.label: h.filled for h in after.handoffs}[progress.DECIDED_LABEL] is True


def test_export_strips_people_fields() -> None:
    payload = {
        "people": [{"name": "홍길동 과장", "username": "hong", "role": "user", "group": "team_a"}],
        "support": {"supporters": "김 부장", "opponents": "없음"},
        "comms": [{"plan": "주간 회의"}],
    }
    out = export.strip_pii("stakeholders", payload)
    assert out["people"] == [{"role": "user", "group": "team_a"}]
    assert out["support"] == {}
    assert out["comms"] == payload["comms"]
    assert "홍길동" not in str(out) and "hong" not in str(out)


def _item(**kw: object) -> GoldenItem:
    base = {
        "id": 7,
        "key": None,
        "question": "고위험 시설은 몇 곳?",
        "expected_status": "ok",
        "expected_failure": None,
        "expected_grade": None,
        "answerable": True,
        "required_values": [],
        "absent_values": [],
        "required_conditions": [],
        "forbidden_values": [],
        "note": None,
    }
    return GoldenItem(**{**base, **kw})


def test_only_confirmed_items_and_pack_parses_back() -> None:
    draft = _item()
    sure = _item(id=8, required_values=["12"], note="36.7 관찰 기록 1행")
    cannot = _item(id=9, question="내일 날씨는?", answerable=False, expected_status="not_query")
    assert [export.confirmed(i) for i in (draft, sure, cannot)] == [False, True, True]
    pack = export.golden_pack("adoption-1", [sure, cannot])
    parsed = golden.parse(yaml.safe_dump(pack, allow_unicode=True))
    assert [(i.id, i.required_values, i.answerable) for i in parsed.items] == [
        ("a8", ["12"], True),
        ("a9", [], False),
    ]
