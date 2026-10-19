"""인용 대조와 등급(TRD 8.2-3~6). LLM 이 매긴 등급·확신도는 쓰지 않는다.

- ① 사실 문장이 하나 이상이고 모두 대조를 통과한 인용을 가짐
- ② 통과한 인용이 하나 이상이지만, 통과한 인용이 없는 사실 문장이 섞임 → 그 문장에 "추정" 표시
- ③ 통과한 인용이 없음(사실 문장이 없는 ok 답변 포함) → 본문을 버리고 거부 템플릿
- 등급 ②를 끄면 ②를 ③으로 처리한다.

불변식: "추정"이 아닌 사실 문장은 모두 통과한 인용을 가진다(근거 인용률 100%, TRD 13.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ontobot.grounding.citations import Citation, strip
from ontobot.grounding.facts import Sentence, is_fact, split
from ontobot.grounding.types import GroundingContext
from ontobot.grounding.verifier import RemovalReason, Removed, verify_sentence

ESTIMATE_MARK = "(추정) "


@dataclass
class GradeResult:
    grade: Literal[1, 2, 3]
    text: str | None  # ③이면 None — 호출부가 거부 템플릿을 쓴다
    citations: list[Citation] = field(default_factory=list)  # 통과한 인용(중복 제거)
    removed: list[Removed] = field(default_factory=list)
    estimated_sentences: list[int] = field(default_factory=list)
    fact_sentences: int = 0
    verified_sentences: int = 0


def grade(answer: str, ctx: GroundingContext, *, grade2_enabled: bool = True) -> GradeResult:
    sentences = split(answer)
    removed: list[Removed] = []
    passed: dict[str, Citation] = {}
    facts = verified = 0
    estimated: list[int] = []
    rendered: list[str] = []

    for s in sentences:
        plain = s.plain
        verdict = verify_sentence(s.index, s.raw, plain, ctx)
        removed.extend(verdict.removed)
        ok_here = bool(verdict.passed)
        for c in verdict.passed:
            passed.setdefault(c.key, c)
        fact = is_fact(s, ctx)
        if fact:
            facts += 1
            if ok_here:
                verified += 1
            else:
                estimated.append(s.index)
        prefix = ESTIMATE_MARK if fact and not ok_here else ""
        rendered.append(prefix + plain + s.sep)

    if not passed:
        level: Literal[1, 2, 3] = 3
    elif estimated:
        level = 2 if grade2_enabled else 3
    else:
        level = 1
    return GradeResult(
        grade=level,
        text=None if level == 3 else "".join(rendered).strip(),
        citations=list(passed.values()),
        removed=removed,
        estimated_sentences=estimated if level == 2 else [],
        fact_sentences=facts,
        verified_sentences=verified,
    )


__all__ = [
    "ESTIMATE_MARK",
    "GradeResult",
    "RemovalReason",
    "Removed",
    "Sentence",
    "grade",
    "strip",
]
