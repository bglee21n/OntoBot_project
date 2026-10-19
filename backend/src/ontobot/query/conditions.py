"""조회 조건 문장(TRD 7.1 5단계) — 검증된 계획에서 **결정론적으로** 만든다. LLM 이 만들지 않는다.

    시설 · 위험등급 ≥ 3 · 시설 유형 = 변전소 · 시설 유형별 건수

사용자가 "무엇을 조회했는가"를 읽고 틀린 해석을 바로 알아보게 하는 것이 목적이다. 그래서
api_name 이 아니라 표시 이름을 쓰고, 값은 사람이 읽는 표기로 쓴다.
"""

from __future__ import annotations

from ontobot.query.records import display
from ontobot.query.validator import CheckedFilter, ValidatedPlan

_SYM = {"eq": "=", "ne": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤"}
_AGG = {"count": "건수", "sum": "합계", "avg": "평균", "min": "최솟값", "max": "최댓값"}


def _filter(f: CheckedFilter) -> str:
    name = f.prop.display_name
    if f.op in _SYM:
        return f"{name} {_SYM[f.op]} {display(f.prop, f.value)}"
    if f.op == "in":
        return f"{name} ∈ {{{', '.join(display(f.prop, v) for v in f.value)}}}"
    if f.op == "between":
        lo, hi = f.value
        return f"{name} {display(f.prop, lo)} ~ {display(f.prop, hi)}"
    if f.op == "contains":
        return f"{name}에 '{f.value}' 포함"
    return f"{name} 값 없음" if f.value else f"{name} 값 있음"


def describe(vp: ValidatedPlan) -> str:
    parts = [vp.target.display_name, *(_filter(f) for f in vp.filters)]
    a = vp.aggregate
    if a is not None:
        subject = f"{a.prop.display_name} " if a.prop is not None else ""
        groups = "·".join(g.display_name for g in a.group_by)
        parts.append(f"{groups + '별 ' if groups else ''}{subject}{_AGG[a.fn]}")
    for s in vp.sort:
        if isinstance(s.key, str):
            label = _AGG[a.fn] if a is not None else s.key
        else:
            label = s.key.display_name
        parts.append(f"{label} {'내림차순' if s.desc else '오름차순'}")
    if a is None or a.group_by:
        parts.append(f"최대 {vp.limit}건")
    return " · ".join(parts)
