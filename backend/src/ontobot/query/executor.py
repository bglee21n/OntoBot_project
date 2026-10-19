"""조회 실행 — 타임아웃·실패 분류·상태(TRD 7.1 4단계, 7.3).

``failed`` 는 어떤 경우에도 ``empty`` 처럼 보이게 하지 않는다. 0건은 결과이고, 실행 오류는
실패다 — 둘을 섞으면 "자료 없음"이라는 그럴듯한 답이 조회 장애를 가린다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.objects.reader import CompiledQuery, ObjectRow, render_sql, run_query

Status = Literal["ok", "empty", "failed"]
SQL_TIMEOUT_MS = 10_000


@dataclass
class ExecResult:
    status: Status
    rows: list[ObjectRow] | list[dict[str, Any]] = field(default_factory=list)
    failure_reason: str | None = None  # execution_error / timeout
    sql: str = ""
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
