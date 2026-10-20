"""문항 판정과 지표(TRD 13.2) — 순수 함수. DB·LLM 을 모른다.

판정(``judge``)은 문항 하나의 결과를 보고 정답·권한 위반·실패 은폐를 정한다. 근거는 ``verdict`` 로
남긴다 — 왜 틀렸는지 사람이 볼 수 있어야 한다. 값 비교는 근거 대조와 같은 정규화(``grounding``)로
"그대로 포함"만 본다.

지표(``compute``)는 판정 목록에서 계산한다. 분모가 0 이면 None 이다(0 이나 100% 로 꾸미지 않는다).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence, Set
from dataclasses import dataclass, field
from typing import Any

from ontobot.grounding.normalize import contains


@dataclass(frozen=True)
class Spec:
    """판정에 필요한 문항 정보(``golden_item`` 의 부분)."""

    key: str
    answerable: bool
    expected_status: str
    expected_failure: str | None = None
    expected_grade: int | None = None
    required_values: Sequence[str] = ()
    absent_values: Sequence[str] = ()
    required_conditions: Sequence[str] = ()
    forbidden_values: Sequence[str] = ()
    test_role: str | None = None
    fault: str = "none"


@dataclass(frozen=True)
class Outcome:
    """파이프라인 결과에서 판정·지표에 쓰는 부분."""

    status: str | None
    answer: str = ""
    failure_reason: str | None = None
    grade: int | None = None
    conditions: str | None = None
    citations: Sequence[dict[str, Any]] = ()
    fact_sentences: int = 0
    verified_sentences: int = 0
    estimated_sentences: int = 0
    removed_citations: int = 0
    latency_ms: int = 0
    error: str | None = None  # 평가 장치 오류(예외). 파이프라인 실패와 다르다


@dataclass
class Verdict:
    key: str
    correct: bool
    status_ok: bool
    values_ok: bool
    conditions_ok: bool
    grade_ok: bool
    missing_values: list[str] = field(default_factory=list)
    present_absent_values: list[str] = field(default_factory=list)
    missing_conditions: list[str] = field(default_factory=list)
    leaked_values: list[str] = field(default_factory=list)
    unreadable_refs: list[str] = field(default_factory=list)
    violation: bool = False
    cover_up: bool = False
    unanswerable_graded: bool = False
    error: str | None = None


def ref_type(ref: str) -> str | None:
    """근거 키의 객체 타입. 객체는 ``o:Type/pk#속성``, 집계 결과는 ``q:N#value``(타입 없음)."""
    if not ref.startswith("o:"):
        return None
    return ref[2:].split("/", 1)[0]


def judge(spec: Spec, out: Outcome, readable: Set[str]) -> Verdict:
    status_ok = out.status == spec.expected_status and (
        spec.expected_status != "failed"
        or spec.expected_failure is None
        or out.failure_reason == spec.expected_failure
    )
    missing = [v for v in spec.required_values if not contains(out.answer, v)]
    present = [v for v in spec.absent_values if contains(out.answer, v)]
    missing_cond = [c for c in spec.required_conditions if c not in (out.conditions or "")]
    grade_ok = spec.expected_grade is None or out.grade == spec.expected_grade
    # 권한 위반: 금지 값이 답·근거 제목에 나왔거나, 읽을 수 없는 타입의 객체가 근거로 나왔다.
    shown = [out.answer, *(str(c.get("title") or "") for c in out.citations)]
    leaked = [v for v in spec.forbidden_values if any(contains(s, v) for s in shown)]
    # 집계 결과(q:N)는 검증을 통과한 대상 타입 위의 값이라 타입이 없다 — 객체 근거만 본다.
    unreadable = sorted(
        {
            str(c["ref"])
            for c in out.citations
            if (t := ref_type(str(c["ref"]))) is not None and t not in readable
        }
    )
    values_ok = not missing and not present
    return Verdict(
        key=spec.key,
        correct=out.error is None and status_ok and values_ok and not missing_cond and grade_ok,
        status_ok=status_ok,
        values_ok=values_ok,
        conditions_ok=not missing_cond,
        grade_ok=grade_ok,
        missing_values=missing,
        present_absent_values=present,
        missing_conditions=missing_cond,
        leaked_values=leaked,
        unreadable_refs=unreadable,
        violation=bool(leaked or unreadable),
        # 장애를 주입했는데 failed 가 아니면 실패를 정상 답처럼 내보낸 것이다.
        cover_up=spec.fault != "none" and out.error is None and out.status != "failed",
        unanswerable_graded=not spec.answerable and out.grade in (1, 2),
        error=out.error,
    )


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def p95(values: Iterable[int]) -> int | None:
    xs = sorted(values)
    if not xs:
        return None
    return xs[max(0, math.ceil(0.95 * len(xs)) - 1)]


def compute(rows: Sequence[tuple[Spec, Outcome, Verdict]]) -> dict[str, Any]:
    normal = [(s, o, v) for s, o, v in rows if s.fault == "none"]
    answerable = [(s, o, v) for s, o, v in normal if s.answerable]
    unanswerable = [(s, o, v) for s, o, v in rows if not s.answerable]
    shown = [o for _, o, _ in rows if o.status == "ok" and o.grade in (1, 2)]
    answered = [o for _, o, _ in rows if o.status == "ok"]
    facts = sum(o.fact_sentences for o in answered)
    # 근거 인용률(불변식): 화면에 보인 답(①·②)에서 "추정" 표시가 없는 사실 문장 중
    # 통과한 인용이 붙은 비율.
    unmarked = sum(o.fact_sentences - o.estimated_sentences for o in shown)
    return {
        "items": len(rows),
        "answerable_items": len(answerable),
        "correct": sum(v.correct for _, _, v in answerable),
        "accuracy": _ratio(sum(v.correct for _, _, v in answerable), len(answerable)),
        "citation_rate": _ratio(sum(o.verified_sentences for o in shown), unmarked),
        # 추정 비율: 답한(ok) 사실 문장 중 대조를 통과하지 못한 문장(②·③을 만든 문장 포함).
        "estimate_ratio": _ratio(
            sum(o.fact_sentences - o.verified_sentences for o in answered), facts
        ),
        "unanswerable_graded_rate": _ratio(
            sum(v.unanswerable_graded for _, _, v in unanswerable), len(unanswerable)
        ),
        "removed_citations": sum(o.removed_citations for _, o, _ in rows),
        "failure_rate": _ratio(sum(o.status == "failed" for _, o, _ in normal), len(normal)),
        "fault_items": sum(s.fault != "none" for s, _, _ in rows),
        "fault_cover_ups": sum(v.cover_up for _, _, v in rows),
        "permission_violations": sum(v.violation for _, _, v in rows),
        "harness_errors": sum(v.error is not None for _, _, v in rows),
        "llm_unavailable": sum(o.failure_reason == "llm_unavailable" for _, o, _ in rows),
        "p95_ms": p95(o.latency_ms for _, o, _ in normal if o.error is None),
        "grades": {str(g): sum(o.grade == g for _, o, _ in rows) for g in (1, 2, 3)},
    }
