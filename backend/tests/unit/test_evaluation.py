"""평가(TRD 13절) — 문항 판정·지표·게이트·팩 형식·카세트. 순수 함수만."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from ontobot.evaluation import gate, golden, metrics, replay

ROOT = Path(__file__).resolve().parents[3]


def _spec(**kw: Any) -> metrics.Spec:
    base: dict[str, Any] = {"key": "q", "answerable": True, "expected_status": "ok"}
    return metrics.Spec(**{**base, **kw})


def _out(**kw: Any) -> metrics.Outcome:
    base: dict[str, Any] = {"status": "ok", "answer": "", "grade": 1}
    return metrics.Outcome(**{**base, **kw})


READ_ALL = frozenset({"Facility", "Organization"})


# ---------------------------------------------------------------- 판정


def test_correct_needs_status_values_conditions() -> None:
    spec = _spec(required_values=("18",), required_conditions=("위험등급 ≥ 3",))
    ok = metrics.judge(spec, _out(answer="18건입니다.", conditions="시설 · 위험등급 ≥ 3"), READ_ALL)
    assert ok.correct
    miss = metrics.judge(spec, _out(answer="18건입니다.", conditions="시설"), READ_ALL)
    assert not miss.correct and miss.missing_conditions == ["위험등급 ≥ 3"]
    wrong = metrics.judge(spec, _out(status="empty", answer="18"), READ_ALL)
    assert not wrong.correct and not wrong.status_ok


def test_values_use_grounding_normalization() -> None:
    # 천 단위 구분 정규화 — "3000" 과 "3,000" 은 같은 값이다(근거 대조와 같은 규칙).
    v = metrics.judge(_spec(required_values=("3,000",)), _out(answer="3000 kW 입니다."), READ_ALL)
    assert v.values_ok
    absent = metrics.judge(
        _spec(absent_values=("강남 철탑 05호",)), _out(answer="강남 철탑 05호, ..."), READ_ALL
    )
    assert not absent.correct and absent.present_absent_values == ["강남 철탑 05호"]


def test_failure_reason_checked_only_when_given() -> None:
    spec = _spec(expected_status="failed", expected_failure="plan_invalid")
    assert metrics.judge(
        spec, _out(status="failed", failure_reason="plan_invalid"), READ_ALL
    ).correct
    assert not metrics.judge(
        spec, _out(status="failed", failure_reason="forbidden"), READ_ALL
    ).correct
    any_reason = _spec(expected_status="failed")
    assert metrics.judge(
        any_reason, _out(status="failed", failure_reason="forbidden"), READ_ALL
    ).correct


def test_permission_violation_by_value_or_unreadable_object_citation() -> None:
    spec = _spec(test_role="r", forbidden_values=("김가상",))
    leaked = metrics.judge(spec, _out(answer="담당자는 김가상입니다."), frozenset({"Facility"}))
    assert leaked.violation and leaked.leaked_values == ["김가상"]
    cited = _out(
        answer="강남지사입니다.",
        citations=[{"ref": "o:Organization/ORG-01#name", "title": "강남지사"}],
    )
    v = metrics.judge(_spec(), cited, frozenset({"Facility"}))
    assert v.violation and v.unreadable_refs == ["o:Organization/ORG-01#name"]


def test_aggregate_citation_is_not_an_object_reference() -> None:
    # 집계 결과(q:N)에는 타입이 없다 — 'q:1' 을 타입으로 읽어 전 문항을 위반으로 센 실측(Phase 7).
    out = _out(answer="18건입니다.", citations=[{"ref": "q:1#value", "title": "결과 1"}])
    assert not metrics.judge(_spec(), out, frozenset({"Facility"})).violation
    assert metrics.ref_type("q:1#value") is None
    assert metrics.ref_type("o:Facility/F-1#name") == "Facility"


def test_fault_item_not_failed_is_cover_up() -> None:
    spec = _spec(fault="executor_error", expected_status="failed")
    assert metrics.judge(spec, _out(status="empty", grade=1), READ_ALL).cover_up
    assert not metrics.judge(spec, _out(status="failed", grade=None), READ_ALL).cover_up
    # 평가 장치 오류는 은폐가 아니라 장치 오류로 센다
    harness = metrics.judge(spec, metrics.Outcome(status=None, error="boom"), READ_ALL)
    assert not harness.cover_up and harness.error == "boom" and not harness.correct


def test_unanswerable_graded_one_or_two() -> None:
    spec = _spec(answerable=False, expected_status="failed")
    assert metrics.judge(spec, _out(grade=2), READ_ALL).unanswerable_graded
    assert not metrics.judge(spec, _out(grade=3), READ_ALL).unanswerable_graded
    assert not metrics.judge(spec, _out(status="failed", grade=None), READ_ALL).unanswerable_graded


# ---------------------------------------------------------------- 지표


def _row(spec: metrics.Spec, out: metrics.Outcome) -> tuple[Any, Any, Any]:
    return spec, out, metrics.judge(spec, out, READ_ALL)


def test_compute_formulas() -> None:
    rows = [
        # 맞은 답(①): 사실 2문장 모두 통과
        _row(
            _spec(required_values=("18",)),
            _out(answer="18", fact_sentences=2, verified_sentences=2, latency_ms=100),
        ),
        # 틀린 답(②): 사실 3문장 중 1문장 추정
        _row(
            _spec(required_values=("7",)),
            _out(
                answer="x",
                grade=2,
                fact_sentences=3,
                verified_sentences=2,
                estimated_sentences=1,
                latency_ms=300,
            ),
        ),
        # ③: 본문을 버렸다 — 화면에 보인 사실 문장이 없어 인용률 분모에 넣지 않는다
        _row(
            _spec(),
            _out(answer="t", grade=3, fact_sentences=1, verified_sentences=0, latency_ms=200),
        ),
        # 답할 수 없는 문항을 ② 로 답했다
        _row(_spec(answerable=False, expected_status="failed"), _out(grade=2, latency_ms=50)),
        # 장애 주입 — 지연·정답률에서 뺀다
        _row(
            _spec(fault="timeout", expected_status="failed"),
            _out(status="failed", grade=None, latency_ms=9999),
        ),
    ]
    m = metrics.compute(rows)
    # ③ 문항은 기대 값이 없어 맞은 것으로 센다(정답률과 등급은 다른 축)
    assert m["answerable_items"] == 3 and m["correct"] == 2
    assert m["accuracy"] == round(2 / 3, 4)
    assert m["citation_rate"] == 1.0  # (2+2)/(2+(3-1))
    assert m["estimate_ratio"] == round(2 / 6, 4)  # (0+1+1)/(2+3+1)
    assert m["unanswerable_graded_rate"] == 1.0
    assert m["fault_items"] == 1 and m["fault_cover_ups"] == 0
    assert m["p95_ms"] == 300  # 장애 문항 제외
    assert m["grades"] == {"1": 1, "2": 2, "3": 1}


def test_compute_empty_denominators_are_none() -> None:
    m = metrics.compute(
        [_row(_spec(expected_status="not_query"), _out(status="not_query", grade=None))]
    )
    assert m["citation_rate"] is None and m["estimate_ratio"] is None
    assert m["unanswerable_graded_rate"] is None


def test_p95() -> None:
    assert metrics.p95([]) is None
    assert metrics.p95(range(1, 101)) == 95
    assert metrics.p95([5]) == 5


# ---------------------------------------------------------------- 게이트


def test_gate_file_metrics_are_all_computed() -> None:
    cfg = gate.load()
    computed = metrics.compute([_row(_spec(), _out(fact_sentences=1, verified_sentences=1))])
    names = {*cfg["invariants"], *cfg["thresholds"]}
    assert names <= set(computed), names - set(computed)


def test_gate_invariant_none_passes_threshold_none_fails() -> None:
    cfg = {"invariants": {"citation_rate": {"min": 1.0}}, "thresholds": {"accuracy": {"min": 0.9}}}
    r = gate.evaluate({"citation_rate": None, "accuracy": None}, cfg)
    by = {c.metric: c.passed for c in r.checks}
    assert by == {"citation_rate": True, "accuracy": False} and not r.passed


def test_gate_fails_on_any_violation_and_skips() -> None:
    cfg = gate.load()
    good = {
        "citation_rate": 1.0,
        "fault_cover_ups": 0,
        "permission_violations": 0,
        "unanswerable_graded_rate": 0.0,
        "harness_errors": 0,
        "action_fabrications": 0,
        "accuracy": 1.0,
        "estimate_ratio": 0.0,
        "action_param_accuracy": None,  # Action 문항이 없는 골든셋 — optional 이라 통과
        "p95_ms": 10**9,
    }
    assert not gate.evaluate(good, cfg).passed  # 지연 초과
    assert gate.evaluate(good, cfg, skip=["p95_ms"]).passed
    for metric, bad in (
        ("permission_violations", 1),
        ("fault_cover_ups", 1),
        ("citation_rate", 0.99),
        ("action_fabrications", 1),
        ("action_param_accuracy", 0.8),
    ):
        assert not gate.evaluate({**good, metric: bad}, cfg, skip=["p95_ms"]).passed
    with pytest.raises(KeyError):
        gate.evaluate({}, cfg)


# ---------------------------------------------------------------- 팩


def test_sample_pack_parses_and_has_phase7_items() -> None:
    pack = golden.parse((ROOT / "domain-packs/sample/golden.yaml").read_text(encoding="utf-8"))
    assert pack.name == "sample"
    faults = {i.fault for i in pack.items}
    assert {"executor_error", "graph_down", "timeout"} <= faults
    roles = {i.test_role for i in pack.items if i.test_role}
    assert roles and roles <= set(pack.roles)
    assert any(not i.answerable for i in pack.items)
    # 장애 문항은 failed 를 기대해야 한다 — 아니면 은폐 판정과 정답이 어긋난다
    assert all(i.expected_status == "failed" for i in pack.items if i.fault != "none")


def test_pack_rejects_duplicates_and_unknown_fields() -> None:
    item = {"id": "a", "question": "q", "expected_status": "ok"}
    with pytest.raises(ValidationError):
        golden.Pack.model_validate({"name": "x", "items": [item, item]})
    with pytest.raises(ValidationError):
        golden.Pack.model_validate({"name": "x", "items": [{**item, "typo": 1}]})
    p = golden.Pack.model_validate({"name": "x", "items": [{**item, "required_values": [18]}]})
    assert p.items[0].required_values == ["18"]


# ---------------------------------------------------------------- 카세트


def test_cassette_key_is_order_independent() -> None:
    a = {"model": "m", "messages": [{"role": "user", "content": "q"}], "stream": False}
    b = {"stream": False, "messages": [{"content": "q", "role": "user"}], "model": "m"}
    assert replay.key(a) == replay.key(b)
    assert replay.key(a) != replay.key({**a, "model": "n"})


async def test_replay_hits_and_counts_misses(tmp_path: Path) -> None:
    body = {"model": "m", "messages": [{"role": "user", "content": "q"}]}
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps({replay.key(body): {"model": "m", "message": {"content": "답"}}}),
        encoding="utf-8",
    )
    from ontobot.llm.adapters import ollama

    before = ollama.transport
    with replay.use("replay", path) as cassette:
        assert cassette is not None
        async with httpx.AsyncClient(transport=ollama.transport) as c:
            hit = await c.post("http://x/api/chat", json=body)
            miss = await c.post("http://x/api/chat", json={**body, "model": "other"})
        assert hit.json()["message"]["content"] == "답"
        assert miss.status_code == 404 and cassette.misses == 1
    assert ollama.transport is before
