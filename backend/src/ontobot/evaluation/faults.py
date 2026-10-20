"""장애 주입(TRD 13.1) — 문항의 ``fault`` 로 실행기 오류·Neo4j 불가·타임아웃을 흉내 낸다.

파이프라인은 실행기(``executor``)를 의존성으로 받는다. 여기서 바꿔 끼우는 것은 그 하나뿐이다 —
계획·검증·조회 조건·실패 문구는 운영과 같은 코드가 돈다. 평가가 보려는 것은 "실패가 정상 답처럼
보이지 않는가"(실패 은폐 0건)이므로, 실행기가 실패를 **분류한 뒤**의 경로를 실제로 탄다.

- ``executor_error``·``timeout``: 계획을 컴파일한 SQL 을 실행하지 않고 그 사유로 실패를 돌려준다
  (분류 자체는 ``query.executor`` 단위 테스트가 지킨다).
- ``graph_down``: 닿을 수 없는 Bolt 주소의 **실제 드라이버**로 운영 실행기를 돌린다. 탐색이 있는
  계획이면 드라이버 오류 → ``graph_unavailable`` 분류까지 실제로 탄다. 그래서 이 문항은 탐색
  질문이어야 한다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.chat.pipeline import Executor
from ontobot.graph import client as graph
from ontobot.objects.reader import render_sql
from ontobot.query.compiler import compile_plan
from ontobot.query.executor import ExecResult, execute_plan
from ontobot.query.validator import ValidatedPlan

Fault = Literal["none", "executor_error", "graph_down", "timeout"]
FAULTS: tuple[Fault, ...] = ("none", "executor_error", "graph_down", "timeout")
# 포트 1 — 어떤 서버도 듣지 않는다. 연결이 곧바로 거부돼 드라이버 오류 경로를 탄다.
DEAD_BOLT = "bolt://127.0.0.1:1"


def _failing(reason: str) -> Executor:
    async def _run(
        _db: AsyncSession, _driver: graph.AsyncDriver | None, vp: ValidatedPlan
    ) -> ExecResult:
        return ExecResult(
            "failed", failure_reason=reason, sql=render_sql(compile_plan(vp)), error="injected"
        )

    return _run


@asynccontextmanager
async def injected(
    fault: str, driver: graph.AsyncDriver | None
) -> AsyncIterator[tuple[Executor, graph.AsyncDriver | None]]:
    """(실행기, 드라이버). ``graph_down`` 의 죽은 드라이버는 나갈 때 닫는다."""
    if fault == "executor_error":
        yield _failing("execution_error"), driver
    elif fault == "timeout":
        yield _failing("timeout"), driver
    elif fault == "graph_down":
        dead = graph.create_driver(DEAD_BOLT, "neo4j", "unreachable")
        try:
            yield execute_plan, dead
        finally:
            await dead.close()
    else:
        yield execute_plan, driver
