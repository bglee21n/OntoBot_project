"""질문의 숫자가 계획에 반영됐는가(TRD 7.1 3단계 보강) — 조건 누락의 한 갈래를 결정론적으로 잡는다.

조건 누락은 일반적으로 결정론적으로 막을 수 없다(TRD 21절). 하지만 질문에 **숫자**가 있는데
계획의 어느 값(조건 값·limit·맥락 식별자)에도 그 숫자가 없으면, 그 숫자를 쓰는 조건이 빠졌을
가능성이 높다. 측정에서 "그 중 위험등급이 4 이상인 것만"이 맥락 참조만 남고 위험등급 조건이 빠졌고,
답변 모델이 스스로 걸러 겉보기엔 맞는 답이 나왔다 — 조회가 하지 않은 일을 모델이 한 것이다.

숫자만 본다. enum 낱말("변전소")은 "변전소별 개수"처럼 조건이 아닌 쓰임이 흔해 오탐이 난다.
비교는 문자열 포함과 수 값 같음뿐이다(유사도 없음).
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from ontobot.query.plan import QueryPlan

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


def _numbers(text: str) -> list[str]:
    return _NUMBER.findall(_THOUSANDS.sub("", text))


def _plan_values(plan: QueryPlan) -> list[Any]:
    out: list[Any] = []
    for f in (*plan.filters, *(f for t in plan.traverse for f in t.filters)):
        out.extend(f.value if isinstance(f.value, list) else [f.value])
    out.extend(r.pk for r in plan.context_refs)
    if plan.limit is not None:
        out.append(plan.limit)
    return [v for v in out if v is not None and not isinstance(v, bool)]


def uncovered_numbers(question: str, plan: QueryPlan) -> list[str]:
    return uncovered_in(question, _plan_values(plan))


def uncovered_in(question: str, values: list[Any]) -> list[str]:
    """질문의 숫자 중 ``values``(계획·Function 인자의 값) 어디에도 없는 것."""
    values = [v for v in values if v is not None and not isinstance(v, bool)]
    text = json.dumps([str(v) for v in values], ensure_ascii=False)
    nums: set[Decimal] = set()
    for v in values:
        for n in _numbers(str(v)):
            try:
                nums.add(Decimal(n))
            except InvalidOperation:  # pragma: no cover — 정규식이 숫자만 고른다
                continue
    missing = []
    for n in dict.fromkeys(_numbers(question)):
        if n in text or Decimal(n) in nums:
            continue
        missing.append(n)
    return missing


def coverage_errors(question: str, plan: QueryPlan) -> list[str]:
    missing = uncovered_numbers(question, plan)
    if not missing:
        return []
    return [
        f"질문의 값 {missing} 이 계획 어디에도 없습니다. 그 값을 쓰는 조건을 빼지 말고 "
        "filters(또는 traverse 의 filters, limit)에 넣으세요. "
        "스키마로 표현할 수 없으면 cannot_answer 를 내세요"
    ]
