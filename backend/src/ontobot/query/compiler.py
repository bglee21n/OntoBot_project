"""검증된 계획 → 조회 명세(TRD 7.1 4단계).

**값과 속성 키는 예외 없이 바인드다.** SQL 조각에 들어가는 것은 이 파일의 고정 틀(연산자 기호,
형 변환, 집계 함수 이름)뿐이다 — 모두 검증기가 통과시킨 닫힌 집합에서 고른다.

``FROM object`` 와 타입·삭제 조건은 ``objects.reader`` 가 붙인다(16.3-5). GROUP BY 는 열 번호로
쓴다 — 같은 식을 바인드로 두 번 쓰면 자리표시자 번호가 달라져 Postgres 가 같은 식으로 보지 않는다.

링크 탐색(traverse)은 세 조각이다. Neo4j 노드에는 속성 값이 없으므로(``pk``·``title``·``stub`` 뿐)
링크 끝 객체의 조건은 PostgreSQL 에서 pk 집합으로 먼저 구하고(``compile_hop``), Neo4j 는 "그 집합의
객체와 연결된 대상"의 pk 만 돌려준다(``compile_traverse``). 대상 조회는 그 pk 집합으로 좁힌다.
Cypher 의 라벨·관계 타입은 바인드할 수 없으므로 ``graph.client.label``·``rel`` 이 식별자 형식을
조립 직전에 다시 확인한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ontobot.graph.client import label, rel
from ontobot.objects.reader import CompiledQuery
from ontobot.ontology.models import PropertyDef
from ontobot.query.plan import AGG_VALUE
from ontobot.query.validator import CheckedFilter, CheckedHop, Scope, ValidatedPlan

# 탐색이 돌려주는 pk 상한. 넘으면 failed(too_broad) — 큰 집합을 넘기는 대신 좁혀 달라고 한다.
PK_CAP = 10_000

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


def _where(b: _Binds, filters: Sequence[CheckedFilter]) -> list[str]:
    where: list[str] = []
    for f in filters:
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
    return where


def _scope(b: _Binds, scope: Scope | None) -> list[str]:
    """행 정책 → WHERE 조각 하나(정책들의 OR, 각 정책은 조건 AND). 값은 다른 조건처럼 바인드다."""
    if scope is None:
        return []
    if not scope:
        return ["false"]  # 볼 수 있는 행이 없다
    parts = []
    for policy in scope:
        cond = _where(b, policy)
        parts.append("(" + " AND ".join(cond) + ")" if cond else "true")
    return ["(" + " OR ".join(parts) + ")"]


def _pk_in(b: _Binds, pks: Sequence[str]) -> str:
    return f"o.pk = ANY(CAST({b.value(list(pks))} AS text[]))"


def compile_hop(hop: CheckedHop, scope: Scope | None = None) -> CompiledQuery | None:
    """링크 끝 객체 조건 → 그 타입의 pk 조회. 조건·맥락 제한·행 정책이 없으면 None(아무 객체나)."""
    if not hop.filters and hop.pks is None and scope is None:
        return None
    b = _Binds()
    where = _where(b, hop.filters) + _scope(b, scope)
    if hop.pks is not None:
        where.append(_pk_in(b, hop.pks))
    return CompiledQuery(
        object_type=hop.to.api_name,
        declared_properties=(),
        where=where,
        order_by=["o.pk"],
        limit=PK_CAP + 1,
        params=b.params,
    )


def compile_scope_check(object_type: str, pks: Sequence[str], scope: Scope) -> CompiledQuery:
    """``pks`` 중 행 정책을 만족하는 것(근거 카드 읽기·Action 대상 쓰기 확인, Phase 14b)."""
    b = _Binds()
    where = [_pk_in(b, pks), *_scope(b, scope)]
    return CompiledQuery(
        object_type=object_type,
        declared_properties=(),
        where=where,
        order_by=["o.pk"],
        limit=len(pks) or 1,
        params=b.params,
    )


def _like(text: str) -> str:
    """부분 일치 패턴 — 검색어의 ``%``·``_``·역슬래시는 글자 그대로 찾는다."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def compile_search(
    object_type: str, title_property: str, text: str, scope: Scope | None, limit: int
) -> CompiledQuery:
    """객체 탐색기 검색(Phase 15) — pk 또는 제목에 검색어가 들어 있는 객체. 행 정책을 함께 건다.

    사람이 고르는 목록이라 부분 일치를 쓴다(근거 대조·참조 값 대조와 무관하다). 값·키는 바인드다.
    """
    b = _Binds()
    pattern = b.value(_like(text))
    key = b.value(title_property)
    where = [
        f"(o.pk ILIKE {pattern} OR (o.properties ->> CAST({key} AS text)) ILIKE {pattern})",
        *_scope(b, scope),
    ]
    return CompiledQuery(
        object_type=object_type,
        declared_properties=(title_property,),
        where=where,
        order_by=["o.pk"],
        limit=limit,
        params=b.params,
    )


def compile_traverse(
    vp: ValidatedPlan, hop_pks: Sequence[Sequence[str] | None]
) -> tuple[str, dict[str, object]]:
    """대상 pk 를 돌려주는 Cypher. ``hop_pks[i]`` 는 i 번째 홉 끝 객체의 pk 집합(None = 제한 없음).

    링크 끝이 stub(원천에 없는 객체를 가리키는 링크)이면 연결된 것으로 치지 않는다 — 존재하지 않는
    객체와의 관계로 대상을 통과시키면 근거 없는 답이 된다.
    """
    params: dict[str, object] = {"cap": PK_CAP + 1}
    clauses = ["NOT coalesce(t.stub, false)"]
    for i, (hop, pks) in enumerate(zip(vp.hops, hop_pks, strict=True)):
        arrow = (
            f"-[:{rel(hop.link.api_name)}]->" if hop.outgoing else f"<-[:{rel(hop.link.api_name)}]-"
        )
        cond = "NOT coalesce(n.stub, false)"
        if pks is not None:
            params[f"p{i}"] = list(pks)
            cond += f" AND n.pk IN $p{i}"
        clauses.append(f"EXISTS {{ MATCH (t){arrow}(n:{label(hop.to.api_name)}) WHERE {cond} }}")
    if vp.target_pks is not None:
        params["tp"] = list(vp.target_pks)
        clauses.append("t.pk IN $tp")
    cypher = (
        f"MATCH (t:{label(vp.target.api_name)}) WHERE "
        + " AND ".join(clauses)
        + " RETURN t.pk AS pk ORDER BY pk LIMIT $cap"
    )
    return cypher, params


def compile_plan(vp: ValidatedPlan, target_pks: Sequence[str] | None = None) -> CompiledQuery:
    """``target_pks`` 는 탐색 결과(또는 맥락)로 좁힌 대상 pk. None 이면 제한 없음."""
    b = _Binds()
    where = _where(b, vp.filters) + _scope(b, vp.scopes.get(vp.target.api_name))
    pks = target_pks if target_pks is not None else vp.target_pks
    if pks is not None:
        where.append(_pk_in(b, pks))

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
