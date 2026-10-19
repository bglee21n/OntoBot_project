"""QueryPlan 검증(TRD 7.1 3단계) — 실재·연산자·값 타입·enum·권한·깊이.

검증을 통과한 계획만 컴파일된다. 값은 여기서 속성 자료형으로 **변환**해 둔다 — 컴파일러는
변환된 값을 바인드로 넘기기만 한다. 실패하면 사람이 읽을 수 있는 오류 목록을 돌려주고,
플래너가 그 오류를 붙여 1회 재계획한다.

순수 함수다(DB 를 모른다). 권한은 ``readable`` 집합으로 받는다 — 계산은 ``security.rbac`` 의 몫이다.
"""

from __future__ import annotations

from collections.abc import Set
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ontobot.ontology.models import ObjectTypeDef, OntologyDocument, PropertyDef
from ontobot.query.plan import (
    AGG_VALUE,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    AggFn,
    Op,
    QueryPlan,
    Scalar,
)

MAX_IN_VALUES = 100

_ORDERED = {"int", "decimal", "date", "datetime"}
_OPS_BY_TYPE: dict[str, set[Op]] = {
    "string": {"eq", "ne", "in", "contains", "is_null"},
    "enum": {"eq", "ne", "in", "is_null"},
    "bool": {"eq", "ne", "is_null"},
    **{t: {"eq", "ne", "gt", "gte", "lt", "lte", "in", "between", "is_null"} for t in _ORDERED},
}


@dataclass(frozen=True)
class CheckedFilter:
    prop: PropertyDef
    op: Op
    # 자료형으로 변환된 값. in·between 은 tuple, is_null 은 bool(참 = 값 없음).
    value: Any


@dataclass(frozen=True)
class CheckedAggregate:
    fn: AggFn
    prop: PropertyDef | None
    group_by: tuple[PropertyDef, ...]


@dataclass(frozen=True)
class CheckedSort:
    key: PropertyDef | str  # str 이면 집계 값(AGG_VALUE)
    desc: bool


@dataclass(frozen=True)
class ValidatedPlan:
    target: ObjectTypeDef
    filters: tuple[CheckedFilter, ...]
    aggregate: CheckedAggregate | None
    sort: tuple[CheckedSort, ...]
    limit: int


class PlanInvalid(Exception):
    def __init__(self, errors: list[str], *, forbidden: bool = False) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors
        # 권한 때문에 막혔는가 — 상태가 plan_invalid 가 아니라 forbidden 이 된다(TRD 7.3).
        self.forbidden = forbidden


def coerce(prop: PropertyDef, raw: Scalar) -> Any:
    """값 하나를 속성 자료형으로. 실패하면 ValueError(메시지는 재계획 프롬프트에 실린다)."""
    if raw is None:
        raise ValueError(f"'{prop.api_name}' 값이 비어 있습니다")
    t = prop.data_type
    if t == "string":
        return str(raw)
    if t == "enum":
        if str(raw) not in (prop.enum_values or []):
            allowed = ", ".join(prop.enum_values or [])
            raise ValueError(
                f"'{prop.api_name}' 는 [{allowed}] 중 하나여야 합니다 (받은 값: {raw!r})"
            )
        return str(raw)
    if t == "bool":
        if isinstance(raw, bool):
            return raw
        if str(raw).lower() in {"true", "false"}:
            return str(raw).lower() == "true"
        raise ValueError(f"'{prop.api_name}' 는 true/false 여야 합니다")
    if isinstance(raw, bool):
        raise ValueError(f"'{prop.api_name}' 에 참/거짓을 쓸 수 없습니다")
    try:
        if t == "int":
            d = Decimal(str(raw))
            if d != d.to_integral_value():
                raise ValueError
            return int(d)
        if t == "decimal":
            return Decimal(str(raw))
        if t == "date":
            return date.fromisoformat(str(raw))
        if t == "datetime":
            return datetime.fromisoformat(str(raw))
    except (ValueError, InvalidOperation):
        pass
    fmt = {"int": "정수", "decimal": "숫자", "date": "YYYY-MM-DD", "datetime": "ISO 8601 일시"}[t]
    raise ValueError(f"'{prop.api_name}' 는 {fmt} 형식이어야 합니다 (받은 값: {raw!r})")


def _check_filter(t: ObjectTypeDef, f: Any, errors: list[str]) -> CheckedFilter | None:
    prop = t.property(f.property)
    if prop is None:
        names = ", ".join(p.api_name for p in t.properties)
        errors.append(f"'{t.api_name}' 에 속성 '{f.property}' 이 없습니다 (있는 속성: {names})")
        return None
    if f.op not in _OPS_BY_TYPE[prop.data_type]:
        ops = ", ".join(sorted(_OPS_BY_TYPE[prop.data_type]))
        errors.append(
            f"'{prop.api_name}'({prop.data_type}) 에 연산자 '{f.op}' 를 쓸 수 없습니다 ({ops})"
        )
        return None
    try:
        if f.op == "is_null":
            return CheckedFilter(prop, f.op, True if f.value is None else bool(f.value))
        if f.op in {"in", "between"}:
            if not isinstance(f.value, list):
                raise ValueError(f"'{f.op}' 의 값은 목록이어야 합니다")
            if f.op == "between" and len(f.value) != 2:
                raise ValueError("'between' 은 [하한, 상한] 두 값이어야 합니다")
            if f.op == "in" and not 0 < len(f.value) <= MAX_IN_VALUES:
                raise ValueError(f"'in' 은 값이 1~{MAX_IN_VALUES}개여야 합니다")
            return CheckedFilter(prop, f.op, tuple(coerce(prop, v) for v in f.value))
        if isinstance(f.value, list):
            raise ValueError(f"'{f.op}' 의 값은 하나여야 합니다")
        return CheckedFilter(prop, f.op, coerce(prop, f.value))
    except ValueError as e:
        errors.append(str(e))
        return None


def validate(doc: OntologyDocument, readable: Set[str], plan: QueryPlan) -> ValidatedPlan:
    t = doc.object_type(plan.target)
    if t is None or plan.target not in readable:
        # 없는 타입과 읽을 수 없는 타입을 같은 말로 거른다 — 존재 여부도 정보다.
        known = ", ".join(sorted(x.api_name for x in doc.object_types if x.api_name in readable))
        if t is not None:
            raise PlanInvalid([f"조회할 수 없는 타입입니다: {plan.target}"], forbidden=True)
        raise PlanInvalid([f"객체 타입 '{plan.target}' 이 없습니다 (있는 타입: {known})"])
    errors: list[str] = []
    if plan.traverse:
        errors.append(
            "링크 탐색(traverse)은 아직 지원하지 않습니다 — 단일 타입 조건으로 표현하세요"
        )
    if plan.context_refs:
        errors.append("대화 맥락 참조(context_refs)는 아직 지원하지 않습니다")

    filters = [cf for f in plan.filters if (cf := _check_filter(t, f, errors)) is not None]

    agg: CheckedAggregate | None = None
    if plan.aggregate is not None:
        a = plan.aggregate
        prop = t.property(a.property) if a.property else None
        if a.property and prop is None:
            errors.append(f"집계 속성 '{a.property}' 이 '{t.api_name}' 에 없습니다")
        if a.fn in {"sum", "avg"} and (prop is None or prop.data_type not in {"int", "decimal"}):
            errors.append(f"'{a.fn}' 는 숫자 속성(int·decimal)에만 쓸 수 있습니다")
        if a.fn in {"min", "max"} and (prop is None or prop.data_type not in _ORDERED):
            errors.append(f"'{a.fn}' 는 숫자·날짜 속성에만 쓸 수 있습니다")
        groups = []
        for g in a.group_by:
            gp = t.property(g)
            if gp is None:
                errors.append(f"그룹 속성 '{g}' 이 '{t.api_name}' 에 없습니다")
            else:
                groups.append(gp)
        agg = CheckedAggregate(a.fn, prop, tuple(groups))

    sorts: list[CheckedSort] = []
    for s in plan.sort:
        if agg is not None:
            if s.property == AGG_VALUE:
                sorts.append(CheckedSort(AGG_VALUE, s.dir == "desc"))
                continue
            gp = next((g for g in agg.group_by if g.api_name == s.property), None)
            if gp is None:
                errors.append(f"집계 결과는 그룹 속성이나 '{AGG_VALUE}' 로만 정렬할 수 있습니다")
                continue
            sorts.append(CheckedSort(gp, s.dir == "desc"))
            continue
        sp = t.property(s.property)
        if sp is None:
            errors.append(f"정렬 속성 '{s.property}' 이 '{t.api_name}' 에 없습니다")
        else:
            sorts.append(CheckedSort(sp, s.dir == "desc"))

    if plan.limit is not None and not 1 <= plan.limit <= MAX_LIMIT:
        errors.append(f"limit 은 1~{MAX_LIMIT} 이어야 합니다")
    if errors:
        raise PlanInvalid(errors)
    return ValidatedPlan(
        target=t,
        filters=tuple(filters),
        aggregate=agg,
        sort=tuple(sorts),
        limit=plan.limit or (MAX_LIMIT if agg is not None else DEFAULT_LIMIT),
    )
