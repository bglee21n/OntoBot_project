"""Function 부르기 → 검증된 내부 계획(TRD 7.1 3단계와 같은 자리). 순수 함수(DB 를 모른다).

인자는 파라미터 자료형으로 **변환**한다(``query.validator.coerce`` — 조회 조건 값과 같은 규칙). 값이
없는 선택 인자는 기본값을 쓰고, 기본값도 없으면 그 인자를 쓰는 조건을 **빼고** 조회한다(조건이 없는
것과 같다 — 조회 조건 문장에 그대로 드러난다).

내부 조회는 ``QueryPlan`` 으로 바꿔 ``validate`` 를 그대로 지난다. 검증기가 권한
(``readable``)을 보므로 Function 이 읽을 수 없는 타입을 조회하면 ``PlanInvalid(forbidden)``
이다 — 정의가 있다는 이유로 권한을 건너뛰지 않는다(PRD 5.10 "호출한 사용자의 권한으로 실행").
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ontobot.ontology.models import (
    FnFilter,
    FunctionDef,
    FunctionParamDef,
    FunctionQuery,
    OntologyDocument,
    ParamRef,
    PropertyDef,
)
from ontobot.query.plan import Aggregate, Filter, FunctionCall, QueryPlan, Scalar, Traverse
from ontobot.query.validator import PlanInvalid, Scope, ValidatedPlan, coerce, validate

# Function 결과 그룹 상한 — 넘으면 앞에서부터 이만큼만 근거로 싣는다(조건 문장에 밝힌다).
MAX_GROUPS = 100


@dataclass(frozen=True)
class BoundQuery:
    query: FunctionQuery
    plan: ValidatedPlan


@dataclass(frozen=True)
class ValidatedFunction:
    fn: FunctionDef
    # 파라미터 이름 → 변환된 값(기본값 포함). 값이 없는 선택 인자는 None.
    args: dict[str, Any]
    # 질문에서 온 인자 이름(기본값으로 채운 것과 구분 — 조건 문장에 '기본값'으로 밝힌다).
    given: frozenset[str]
    queries: tuple[BoundQuery, ...]
    group_by: tuple[PropertyDef, ...]


def _as_prop(p: FunctionParamDef) -> PropertyDef:
    return PropertyDef(
        api_name=p.api_name,
        display_name=p.display_name,
        data_type=p.data_type,
        enum_values=p.enum_values,
    )


def _scalar(v: Any) -> Scalar:
    """변환된 값 → 계획 JSON 값. 검증기가 다시 속성 자료형으로 변환한다."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v  # type: ignore[no-any-return]


def _filters(fs: list[FnFilter], args: dict[str, Any]) -> list[Filter]:
    out: list[Filter] = []
    for f in fs:
        if isinstance(f.value, ParamRef):
            v = args.get(f.value.param)
            if v is None:
                continue  # 값 없는 선택 인자 — 그 조건을 걸지 않는다
            out.append(Filter(property=f.property, op=f.op, value=_scalar(v)))
        else:
            out.append(Filter(property=f.property, op=f.op, value=f.value))
    return out


def _plan(q: FunctionQuery, args: dict[str, Any]) -> QueryPlan:
    return QueryPlan(
        target=q.target,
        filters=_filters(q.filters, args),
        traverse=[
            Traverse(
                link=tr.link, direction=tr.direction, to=tr.to, filters=_filters(tr.filters, args)
            )
            for tr in q.traverse
        ],
        aggregate=Aggregate(
            fn=q.aggregate.fn, property=q.aggregate.property, group_by=q.aggregate.group_by
        ),
        limit=MAX_GROUPS if q.aggregate.group_by else None,
    )


def _args(
    fn: FunctionDef, call: FunctionCall, errors: list[str]
) -> tuple[dict[str, Any], set[str]]:
    args: dict[str, Any] = {}
    given: set[str] = set()
    for a in call.args:
        p = fn.param(a.name)
        if p is None:
            names = ", ".join(x.api_name for x in fn.params) or "없음"
            errors.append(
                f"Function '{fn.api_name}' 에 인자 '{a.name}' 이 없습니다 (인자: {names})"
            )
            continue
        if a.name in given:
            errors.append(f"인자 '{a.name}' 을 두 번 넣었습니다")
            continue
        if a.value is None:
            continue  # 빈 값은 넣지 않은 것과 같다(아래에서 필수 여부를 본다)
        try:
            args[a.name] = coerce(_as_prop(p), a.value)
            given.add(a.name)
        except ValueError as e:
            errors.append(str(e))
    for p in fn.params:
        if p.api_name in args or p.api_name in {a.name for a in call.args if a.value is not None}:
            continue
        if p.required:
            errors.append(
                f"Function '{fn.api_name}' 의 필수 인자 '{p.api_name}'({p.display_name}) 가 "
                "없습니다 — "
                "질문에 그 값이 없으면 cannot_answer 를 내세요"
            )
        elif p.default is not None:
            args[p.api_name] = coerce(_as_prop(p), p.default)
        else:
            args[p.api_name] = None
    return args, given


def bind(
    doc: OntologyDocument,
    readable: Set[str],
    call: FunctionCall,
    scopes: Mapping[str, Scope] | None = None,
) -> ValidatedFunction:
    """``doc`` 은 활성 문서(폐기 제외)다.

    실패하면 ``PlanInvalid`` — 메시지는 재계획 프롬프트에 실린다.
    """
    fn = doc.function(call.name)
    if fn is None:
        known = ", ".join(sorted(f.api_name for f in doc.functions if f.types() <= set(readable)))
        raise PlanInvalid(
            [f"Function '{call.name}' 이 없습니다 (있는 Function: {known or '없음'})"]
        )
    if not fn.types() <= set(readable):
        # 대상 타입과 같은 규칙 — 있지만 읽을 수 없는 것은 계획 오류가 아니라 권한 없음이다.
        raise PlanInvalid([f"호출할 수 없는 Function 입니다: {call.name}"], forbidden=True)
    errors: list[str] = []
    args, given = _args(fn, call, errors)
    if errors:
        raise PlanInvalid(errors)
    bound: list[BoundQuery] = []
    for q in fn.queries:
        try:
            vp = validate(doc, readable, _plan(q, args), scopes=scopes)
        except PlanInvalid as e:
            if e.forbidden:
                raise
            errors += [f"Function '{fn.api_name}' 의 조회 '{q.api_name}': {m}" for m in e.errors]
            continue
        bound.append(BoundQuery(q, vp))
    if errors:
        raise PlanInvalid(errors)
    group_by = next(
        (
            b.plan.aggregate.group_by
            for b in bound
            if b.plan.aggregate and b.plan.aggregate.group_by
        ),
        (),
    )
    return ValidatedFunction(fn, args, frozenset(given), tuple(bound), group_by)


def input_values(vf: ValidatedFunction) -> list[Any]:
    """질문 숫자 대조(``query.coverage``)에 쓰는 값 — 인자와 내부 조회의 상수 조건."""
    out: list[Any] = [v for v in vf.args.values() if v is not None]
    for b in vf.queries:
        for f in (*b.plan.filters, *(f for h in b.plan.hops for f in h.filters)):
            out.extend(f.value if isinstance(f.value, tuple) else [f.value])
    return out
