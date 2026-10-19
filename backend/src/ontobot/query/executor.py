"""조회 실행 — 타임아웃·실패 분류·상태(TRD 7.1 4단계, 7.3).

``failed`` 는 어떤 경우에도 ``empty`` 처럼 보이게 하지 않는다. 0건은 결과이고, 실행 오류는
실패다 — 둘을 섞으면 "자료 없음"이라는 그럴듯한 답이 조회 장애를 가린다.

탐색이 있는 계획은 Neo4j 가 필요하다. Neo4j 에 닿지 못하면 ``failed(graph_unavailable)`` 이다 —
PostgreSQL 의 ``link`` 표로 대신 답하지 않는다(TRD 5.5). 대체 경로가 조용히 다른 결과를 내면
(투영과 원본이 어긋난 순간) 그 차이를 아무도 알아채지 못한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.graph import client as graph
from ontobot.objects.reader import CompiledQuery, ObjectRow, render_sql, run_query
from ontobot.query.compiler import PK_CAP, compile_hop, compile_plan, compile_traverse
from ontobot.query.validator import ValidatedPlan

Status = Literal["ok", "empty", "failed"]
SQL_TIMEOUT_MS = 10_000
CYPHER_TIMEOUT_S = 5.0


@dataclass
class ExecResult:
    status: Status
    rows: list[ObjectRow] | list[dict[str, Any]] = field(default_factory=list)
    failure_reason: str | None = None  # execution_error / timeout
    sql: str = ""
    cypher: str | None = None
    error: str | None = None


def _is_timeout(e: DBAPIError) -> bool:
    # asyncpg QueryCanceledError(SQLSTATE 57014) — statement_timeout 이 걸렸다.
    code = getattr(e.orig, "sqlstate", None) or getattr(
        getattr(e.orig, "__cause__", None), "sqlstate", None
    )
    return code == "57014" or "statement timeout" in str(e).lower()


async def execute(
    db: AsyncSession, q: CompiledQuery, *, timeout_ms: int = SQL_TIMEOUT_MS
) -> ExecResult:
    sql = render_sql(q)
    try:
        # 실패해도 바깥 트랜잭션(대화·트레이스 기록)이 망가지지 않게 savepoint 안에서 돈다.
        async with db.begin_nested():
            await db.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
            rows = await run_query(db, q)
    except DBAPIError as e:
        reason = "timeout" if _is_timeout(e) else "execution_error"
        return ExecResult("failed", failure_reason=reason, sql=sql, error=type(e.orig).__name__)
    return ExecResult("ok" if rows else "empty", rows=rows, sql=sql)


async def _pks(db: AsyncSession, q: CompiledQuery, timeout_ms: int) -> list[str] | ExecResult:
    res = await execute(db, q, timeout_ms=timeout_ms)
    if res.status == "failed":
        return res
    pks = [r.pk for r in res.rows if isinstance(r, ObjectRow)]
    if len(pks) > PK_CAP:
        return ExecResult("failed", failure_reason="too_broad", sql=res.sql)
    return pks


async def execute_plan(
    db: AsyncSession,
    driver: graph.AsyncDriver | None,
    vp: ValidatedPlan,
    *,
    timeout_ms: int = SQL_TIMEOUT_MS,
    cypher_timeout_s: float = CYPHER_TIMEOUT_S,
) -> ExecResult:
    """계획 하나를 끝까지 실행한다. 탐색이 없으면 SQL 한 번이다."""
    if not vp.hops:
        return await execute(db, compile_plan(vp), timeout_ms=timeout_ms)

    hop_pks: list[list[str] | None] = []
    for hop in vp.hops:
        q = compile_hop(hop)
        if q is None:
            hop_pks.append(None)
            continue
        got = await _pks(db, q, timeout_ms)
        if isinstance(got, ExecResult):
            return got
        if not got:
            # 링크 끝 조건을 만족하는 객체가 없다 — 탐색할 것 없이 0건이다(실패가 아니라 결과).
            return ExecResult("empty", sql=render_sql(compile_plan(vp, ())))
        hop_pks.append(got)

    cypher, params = compile_traverse(vp, hop_pks)
    if driver is None:
        return ExecResult("failed", failure_reason="graph_unavailable", cypher=cypher)
    try:
        rows = await graph.read(driver, cypher, params, timeout_s=cypher_timeout_s)
    except graph.GraphUnavailable as e:
        return ExecResult("failed", failure_reason="graph_unavailable", cypher=cypher, error=str(e))
    except graph.GraphTimeout as e:
        return ExecResult("failed", failure_reason="timeout", cypher=cypher, error=str(e))
    except graph.GraphQueryError as e:
        return ExecResult("failed", failure_reason="execution_error", cypher=cypher, error=str(e))
    targets = [str(r["pk"]) for r in rows]
    if len(targets) > PK_CAP:
        return ExecResult("failed", failure_reason="too_broad", cypher=cypher)
    if not targets:
        return ExecResult("empty", sql=render_sql(compile_plan(vp, ())), cypher=cypher)
    res = await execute(db, compile_plan(vp, targets), timeout_ms=timeout_ms)
    res.cypher = cypher
    return res
