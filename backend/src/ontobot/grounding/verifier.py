"""인용 하나의 대조(TRD 8.2-3). 등급은 ``grader`` 가 이 판정만 모아 정한다.

통과 조건 셋을 **모두** 만족해야 한다.

1. 대상(객체 또는 질의 결과 행)이 이번 조회 결과에 있다 — 지어낸 pk·다른 질의의 행은 ``unknown_ref``
2. 사용자가 그 타입을 읽을 수 있다 — 권한은 호출부가 ``readable`` 로 넘긴다(이 패키지는 security 를
   import 하지 않는다, 16.3-2)
3. 그 속성의 원값 또는 표시 문자열이 **인용이 붙은 문장 안에** 그대로 있다 — 다른 문장·다른
   객체의 값을 증명할 수 없다. 조회 조건 값은 하나에 한 번씩 걷어 낸 뒤 본다 — "2 이하인
   철탑은 1개" 의 "2"는 조건의 되풀이라 건수 2를 증명하지 못한다(8.2-2 와 같은 이유). 인용한
   속성이 대상 필터의 속성이면 그 조건 값은 남긴다(값이 조건과 같을 수밖에 없다)

문서 청크(``d:N/M``)는 3 대신 ``grounding.document.supports`` 로 본다(문장의 숫자가 모두
청크에 있고, 청크의 말을 그대로 옮긴 데가 있다). 문서 권한은 검색 단계가 이미 걸렀다(보는 사람의
그룹 안에서만 찾는다).
"""

from __future__ import annotations

import re
from collections.abc import Set
from dataclasses import dataclass
from typing import Literal

from ontobot.grounding.citations import ANY_BRACKET, CITATION, Citation, find
from ontobot.grounding.document import supports
from ontobot.grounding.normalize import contains, normalize
from ontobot.grounding.types import GroundingContext

RemovalReason = Literal[
    "malformed", "unknown_ref", "unknown_field", "forbidden_type", "value_mismatch"
]


@dataclass(frozen=True)
class Removed:
    sentence: int
    citation: str
    reason: RemovalReason


def check(c: Citation, sentence_plain: str, ctx: GroundingContext) -> RemovalReason | None:
    """통과면 None, 실패면 사유."""
    ev = ctx.evidence.get(c.ref)
    if ev is None:
        return "unknown_ref"
    if ev.object_type is not None and ev.object_type not in ctx.readable:
        return "forbidden_type"
    if ev.text is not None:  # 문서 청크 — 속성이 없다
        if c.field:
            return "unknown_field"
        return None if supports(sentence_plain, ev.text) else "value_mismatch"
    if not c.field:  # 객체·집계 근거에 속성 없는 인용
        return "unknown_field"
    pair = ev.values.get(c.field)
    if pair is None:
        return "unknown_field"
    raw, shown = pair
    keep = ctx.field_conditions.get(c.field, frozenset())
    sentence = _without_condition_echo(sentence_plain, ctx.condition_terms - keep)
    if contains(sentence, raw) or contains(sentence, shown):
        return None
    return "value_mismatch"


def _without_condition_echo(sentence: str, terms: Set[str]) -> str:
    """조건 값마다 **한 번씩** 걷어 낸다(긴 것부터, 정확 일치). 숫자는 다른 숫자·날짜의 일부를
    지우지 않는다 — "12" 안의 "2", "2015-08-02" 안의 "2015" 는 조건 값이 아니다."""
    text = normalize(sentence)
    for term in sorted({normalize(t) for t in terms} - {""}, key=len, reverse=True):
        pattern = re.escape(term)
        if term[0].isdigit():
            pattern = r"(?<!\d)(?<!\d[.,\-/])" + pattern
        if term[-1].isdigit():
            pattern += r"(?!\d|[.,\-/]\d)"
        text = re.sub(pattern, " ", text, count=1)
    return text


@dataclass(frozen=True)
class SentenceVerdict:
    passed: list[Citation]
    removed: list[Removed]


def verify_sentence(index: int, raw: str, plain: str, ctx: GroundingContext) -> SentenceVerdict:
    """문장 하나의 인용을 모두 대조한다. 형식이 깨진 인용도 ``malformed`` 로 남긴다 — 조용히
    사라지면 모델이 무엇을 틀리는지 알 수 없다(ADR-0004)."""
    removed = [
        Removed(index, m.group(0), "malformed")
        for m in ANY_BRACKET.finditer(raw)
        if not CITATION.fullmatch(m.group(0))
    ]
    passed: list[Citation] = []
    for c in find(raw):
        reason = check(c, plain, ctx)
        if reason is None:
            passed.append(c)
        else:
            removed.append(Removed(index, c.key, reason))
    return SentenceVerdict(passed, removed)
