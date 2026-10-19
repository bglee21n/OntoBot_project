"""검증된 계획 → 조회 명세(TRD 7.1 4단계).

**값과 속성 키는 예외 없이 바인드다.** SQL 조각에 들어가는 것은 이 파일의 고정 틀(연산자 기호,
형 변환, 집계 함수 이름)뿐이다 — 모두 검증기가 통과시킨 닫힌 집합에서 고른다.

``FROM object`` 와 타입·삭제 조건은 ``objects.reader`` 가 붙인다(16.3-5). GROUP BY 는 열 번호로
쓴다 — 같은 식을 바인드로 두 번 쓰면 자리표시자 번호가 달라져 Postgres 가 같은 식으로 보지 않는다.
"""

from __future__ import annotations

from typing import Any

from ontobot.objects.reader import CompiledQuery
from ontobot.ontology.models import PropertyDef
from ontobot.query.plan import AGG_VALUE
from ontobot.query.validator import ValidatedPlan

_CAST = {
    "string": "text",
    "enum": "text",
    "int": "bigint",
    "decimal": "numeric",
    "bool": "boolean",
    "date": "date",
    "datetime": "timestamptz",
}
AVG_DECIMALS = 2
_CMP = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


class _Binds:
    def __init__(self) -> None:
        self.params: dict[str, Any] = {}
        self._keys: dict[str, str] = {}

    def value(self, v: Any) -> str:
        name = f"v{len(self.params)}"
        self.params[name] = v
        return f":{name}"

    def prop(self, p: PropertyDef) -> str:
        """``(o.properties ->> 'x')::type`` — 키도 바인드로 넘긴다."""
        if p.api_name not in self._keys:
            name = f"k{len(self._keys)}"
            self._keys[p.api_name] = name
            self.params[name] = p.api_name
        return f"(o.properties ->> CAST(:{self._keys[p.api_name]} AS text))::{_CAST[p.data_type]}"


def compile_plan(vp: ValidatedPlan) -> CompiledQuery:
    b = _Binds()
    where: list[str] = []
    for f in vp.filters:
        expr = b.prop(f.prop)
        cast = _CAST[f.prop.data_type]
        if f.op in _CMP:
            where.append(f"{expr} {_CMP[f.op]} CAST({b.value(f.value)} AS {cast})")
        elif f.op == "in":
            where.append(f"{expr} = ANY(CAST({b.value(list(f.value))} AS {cast}[]))")
        elif f.op == "between":
            lo, hi = f.value
            where.append(
                f"{expr} BETWEEN CAST({b.value(lo)} AS {cast}) AND CAST({b.value(hi)} AS {cast})"
            )
        elif f.op == "contains":
            # LIKE 를 쓰지 않는다 — 값의 % _ 를 이스케이프할 일이 없다.
            where.append(f"position(lower(CAST({b.value(f.value)} AS text)) in lower({expr})) > 0")
        elif f.op == "is_null":
            where.append(f"{expr} IS NULL" if f.value else f"{expr} IS NOT NULL")
        else:  # pragma: no cover — 검증기가 막는다
            raise AssertionError(f.op)

    declared = [p.api_name for p in vp.target.properties]
    if vp.aggregate is None:
        order = [
            f"{b.prop(s.key)} {'DESC' if s.desc else 'ASC'} NULLS LAST"
            for s in vp.sort
            if isinstance(s.key, PropertyDef)
        ]
        order.append("o.pk")  # 동률에서도 순서를 고정한다(같은 질문 = 같은 답)
        return CompiledQuery(
            object_type=vp.target.api_name,
            declared_properties=declared,
            where=where,
            order_by=order,
            limit=vp.limit,
            params=b.params,
        )

    a = vp.aggregate
    select: list[tuple[str, str]] = [(f"g{i}", b.prop(g)) for i, g in enumerate(a.group_by)]
    if a.prop is None:
        value_expr = "count(*)"
    elif a.fn == "avg":
        # 평균은 서버가 반올림한다 — 긴 소수를 모델이 제멋대로 줄이면 근거 대조가 언제나 실패한다.
        value_expr = f"round(avg({b.prop(a.prop)})::numeric, {AVG_DECIMALS})"
    else:
        value_expr = f"{a.fn}({b.prop(a.prop)})"
    select.append((AGG_VALUE, value_expr))
    order = []
    for s in vp.sort:
        if isinstance(s.key, str):
            order.append(f"{AGG_VALUE} {'DESC' if s.desc else 'ASC'}")
        else:
            i = next(i for i, g in enumerate(a.group_by) if g.api_name == s.key.api_name)
            order.append(f"g{i} {'DESC' if s.desc else 'ASC'}")
    order.extend(f"g{i}" for i in range(len(a.group_by)))
    return CompiledQuery(
        object_type=vp.target.api_name,
        declared_properties=declared,
        where=where,
        aggregate_select=select,
        group_by=[str(i + 1) for i in range(len(a.group_by))],
        order_by=order,
        limit=vp.limit if a.group_by else None,
        params=b.params,
    )
