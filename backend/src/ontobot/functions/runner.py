"""Function 실행 — 내부 조회를 차례로 돌리고 식을 계산한다(TRD 7.1 4단계와 같은 자리).

실행기는 파이프라인의 것을 받는다(``chat.pipeline.Executor``). 평가의 장애 주입이 조회와 똑같이
걸린다 — Function 만 다른 코드를 돌지 않는다. 내부 조회 하나라도 ``failed`` 면 Function 전체가 같은
사유로 ``failed`` 다(나머지 값으로 계산해 그럴듯한 답을 만들지 않는다).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.functions import evaluator
from ontobot.functions.binding import ValidatedFunction
from ontobot.graph import client as graph
from ontobot.query.executor import ExecResult, Status
from ontobot.query.validator import ValidatedPlan

Executor = Callable[[AsyncSession, graph.AsyncDriver | None, ValidatedPlan], Awaitable[ExecResult]]


@dataclass
class FunctionResult:
    status: Status
    rows: list[evaluator.ResultRow] = field(default_factory=list)
    failure_reason: str | None = None
    sql: str = ""
    cypher: str | None = None


async def run(
    db: AsyncSession,
    driver: graph.AsyncDriver | None,
    vf: ValidatedFunction,
    executor: Executor,
) -> FunctionResult:
    results: dict[str, list[dict[str, object]]] = {}
    sqls: list[str] = []
    cyphers: list[str] = []
    for b in vf.queries:
        ex = await executor(db, driver, b.plan)
        if ex.sql:
            sqls.append(ex.sql)
        if ex.cypher:
            cyphers.append(ex.cypher)
        if ex.status == "failed":
            return FunctionResult(
                "failed",
                failure_reason=ex.failure_reason or "execution_error",
                sql=";\n".join(sqls),
                cypher="\n".join(cyphers) or None,
            )
        results[b.query.api_name] = [r for r in ex.rows if isinstance(r, dict)]
    rows = evaluator.evaluate(vf, results)
    return FunctionResult(
        "ok" if rows else "empty", rows, sql=";\n".join(sqls), cypher="\n".join(cyphers) or None
    )
