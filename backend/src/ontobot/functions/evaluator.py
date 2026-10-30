"""식 트리 계산과 근거 레코드(TRD 17.1-7·8). 순수 함수 — DB·LLM 을 모른다.

내부 조회 값은 그룹 키(그룹 열 값의 튜플, 그룹이 없으면 ``()``)로 맞춘다. 그룹이 있는 Function 의
결과 행은 그룹이 있는 조회들에 나온 키의 합집합이다. 어떤 조회에 키가 없으면 그 그룹에는 해당 객체가
없다는 뜻이다 — ``count`` 는 0, 그 밖의 집계는 값 없음(None)이다.

계산은 ``Decimal`` 로 한다. 값 없음은 그대로 번진다(0 으로 꾸미지 않는다).
0 으로 나누면 값 없음이다.
결과는 서버가 ``decimals`` 자리로 반올림한다(평균과 같은 이유 — 모델이 제멋대로 줄이면 근거 대조가
실패한다).

근거 레코드(``q:N``)에는 그룹 열, **내부 조회 값마다 한 칸**, 결과 값, (인자가 있으면) 입력 조건이
실린다. 답이 "10곳 중 3곳(30%)"처럼 계산 근거를 말해도 칸마다 인용할 수 있다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from ontobot.functions.binding import MAX_GROUPS, ValidatedFunction
from ontobot.ontology.models import Expr, PropertyDef
from ontobot.query import conditions
from ontobot.query.plan import AGG_VALUE
from ontobot.query.records import Field, Record, RecordSet, display

INPUTS = "inputs"
Key = tuple[Any, ...]


@dataclass(frozen=True)
class ResultRow:
    key: Key
    values: dict[str, Any]  # 조회 이름 → 집계 값
    value: Any  # 결과(None = 값 없음)


def _dec(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _round(d: Decimal, digits: int) -> Decimal:
    return d.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)


def _eval(e: Expr, values: dict[str, Any], args: dict[str, Any]) -> Any:
    if e.op == "query":
        return _dec(values.get(e.name or ""))
    if e.op == "param":
        v = args.get(e.name or "")
        return v if isinstance(v, bool) or v is None else _dec(v)
    if e.op == "const":
        return e.value if isinstance(e.value, bool) else _dec(e.value)
    if e.op == "if":
        cond = _eval(e.args[0], values, args)
        if cond is None:
            return None
        return _eval(e.args[1] if cond else e.args[2], values, args)
    xs = [_eval(a, values, args) for a in e.args]
    if any(x is None for x in xs):
        return None
    if e.op == "not":
        return not xs[0]
    if e.op == "round":
        return _round(xs[0], e.digits or 0)
    a, b = xs
    match e.op:
        case "add":
            return a + b
        case "sub":
            return a - b
        case "mul":
            return a * b
        case "div":
            return None if b == 0 else a / b
        case "eq":
            return a == b
        case "ne":
            return a != b
        case "gt":
            return a > b
        case "gte":
            return a >= b
        case "lt":
            return a < b
        case "lte":
            return a <= b
        case "and":
            return bool(a and b)
        case "or":
            return bool(a or b)
    raise AssertionError(e.op)  # pragma: no cover — 모델 검증이 연산 집합을 닫는다


def _final(vf: ValidatedFunction, v: Any) -> Any:
    fn = vf.fn
    if v is None:
        return None
    if fn.result_type == "bool":
        return bool(v)
    d = _dec(v)
    if d is None:
        return None
    if fn.result_type == "int":
        return int(_round(d, 0))
    return _round(d, fn.decimals).normalize() if d != 0 else Decimal(0)


def _sort_key(key: Key) -> tuple[Any, ...]:
    # 값 없음은 뒤로. 같은 자료형끼리만 비교되도록 (없음 여부, 문자열) 로 둔다.
    return tuple((v is None, str(v) if v is not None else "") for v in key)


def evaluate(vf: ValidatedFunction, results: dict[str, list[dict[str, Any]]]) -> list[ResultRow]:
    """``results`` 는 조회 이름 → 집계 행(``g0``…·``value``). 행 순서는 그룹 키 오름차순이다."""
    by_query: dict[str, dict[Key, Any]] = {}
    grouped: set[str] = set()
    for b in vf.queries:
        name = b.query.api_name
        agg = b.plan.aggregate
        if agg is not None and agg.group_by:
            grouped.add(name)
        by_query[name] = {
            tuple(r[f"g{i}"] for i in range(len(agg.group_by) if agg else 0)): r[AGG_VALUE]
            for r in results.get(name, [])
        }
    if grouped:
        keys = sorted({k for q in grouped for k in by_query[q]}, key=_sort_key)[:MAX_GROUPS]
    else:
        keys = [()]
    rows: list[ResultRow] = []
    for key in keys:
        values: dict[str, Any] = {}
        for b in vf.queries:
            name = b.query.api_name
            got = by_query[name].get(key if name in grouped else ())
            if got is None and b.query.aggregate.fn == "count":
                got = 0
            values[name] = got
        rows.append(ResultRow(key, values, _final(vf, _eval(vf.fn.body, values, vf.args))))
    return rows


def _param_prop(vf: ValidatedFunction, name: str) -> PropertyDef | None:
    p = vf.fn.param(name)
    if p is None:
        return None
    return PropertyDef(
        api_name=p.api_name,
        display_name=p.display_name,
        data_type=p.data_type,
        enum_values=p.enum_values,
    )


def inputs(vf: ValidatedFunction) -> str:
    """입력 조건(인자) 문장 — '최소 위험등급 = 4(기본값)'. 인자가 없으면 빈 문자열."""
    parts = []
    for p in vf.fn.params:
        v = vf.args.get(p.api_name)
        shown = "지정 안 함" if v is None else display(_param_prop(vf, p.api_name), v)
        tail = "" if p.api_name in vf.given or v is None else "(기본값)"
        parts.append(f"{p.display_name} = {shown}{tail}")
    return ", ".join(parts)


def describe(vf: ValidatedFunction) -> str:
    """조회 조건 문장 — Function 이름·인자, 그리고 내부 조회마다 그 조건(결정론적, TRD 7.1 5단계).

    고위험 시설 비율(최소 위험등급 = 4(기본값)) · 고위험 시설 수: 시설 · 위험등급 ≥ 4
    · 시설 유형별 건수 / 전체 시설 수: 시설 · 시설 유형별 건수
    """
    head = vf.fn.display_name
    args = inputs(vf)
    if args:
        head += f"({args})"
    inner = " / ".join(
        f"{b.query.display_name}: {conditions.describe(b.plan, with_limit=False)}"
        for b in vf.queries
    )
    tail = f" · 최대 {MAX_GROUPS}개 그룹" if vf.group_by else ""
    return f"{head} · {inner}{tail}"


def build(vf: ValidatedFunction, rows: Sequence[ResultRow]) -> RecordSet:
    rs = RecordSet()
    for g in vf.group_by:
        rs.enum_values.update(g.enum_values or [])
    args = inputs(vf)
    for i, row in enumerate(rows, start=1):
        fields = [
            Field(g.api_name, g.display_name, row.key[j], display(g, row.key[j]))
            for j, g in enumerate(vf.group_by)
        ]
        for b in vf.queries:
            v = row.values[b.query.api_name]
            agg = b.plan.aggregate
            fields.append(
                Field(
                    b.query.api_name,
                    b.query.display_name,
                    v,
                    display(agg.prop if agg is not None else None, v),
                )
            )
        fields.append(Field(AGG_VALUE, vf.fn.result_label, row.value, display(None, row.value)))
        if args:
            fields.append(Field(INPUTS, "입력 조건", args, args))
        rs.records.append(Record(f"q:{i}", f"결과 {i}", None, tuple(fields)))
    return rs
