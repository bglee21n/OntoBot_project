"""PostgreSQL 원천 커넥터 — MVP 의 첫 커넥터(계획 Phase 2).

- 모든 조회는 **읽기 전용 트랜잭션**에서 돈다. 모델러가 적은 SELECT 가 무엇이든 원천을 바꿀 수 없다.
- 표·열 이름은 형식 검사 후 큰따옴표로 감싼다(식별자는 바인드할 수 없다). 값(워터마크)은 바인드다.
- 질의 원문은 서브쿼리로 감싸 워터마크 조건과 정렬을 바깥에서 건다 — 원문을 고치지 않는다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import asyncpg

from ontobot.security.secrets import DbCredentials
from ontobot.sync.connectors.base import BATCH, IDENT, TABLE, ConnectorError, Row, SourceSpec

TIMEOUT_S = 30


def _quote_table(name: str) -> str:
    if not TABLE.match(name):
        raise ConnectorError(f"표 이름 형식이 아닙니다: {name!r}")
    return ".".join(f'"{part}"' for part in name.split("."))


def _quote_column(name: str) -> str:
    if not IDENT.match(name):
        raise ConnectorError(f"열 이름 형식이 아닙니다: {name!r}")
    return f'"{name}"'


def _base_sql(spec: SourceSpec) -> str:
    if bool(spec.table) == bool(spec.query):
        raise ConnectorError("표(table)와 질의(query) 중 하나만 지정해야 합니다")
    if spec.table:
        return f"SELECT * FROM {_quote_table(spec.table)}"  # noqa: S608 — 형식 검사 후 인용
    query = (spec.query or "").strip().rstrip(";")
    if ";" in query:
        raise ConnectorError("질의는 SELECT 문 하나여야 합니다")
    if not query.lower().startswith(("select", "with")):
        raise ConnectorError("질의는 SELECT(또는 WITH) 로 시작해야 합니다")
    return query


class PostgresConnector:
    def __init__(self, creds: DbCredentials) -> None:
        self.creds = creds

    async def _connect(self) -> asyncpg.Connection:
        try:
            return await asyncpg.connect(
                host=self.creds.host,
                port=self.creds.port,
                database=self.creds.database,
                user=self.creds.user,
                password=self.creds.password.get_secret_value(),
                timeout=TIMEOUT_S,
                command_timeout=TIMEOUT_S * 10,
            )
        except (OSError, asyncpg.PostgresError) as e:
            raise ConnectorError(f"원천에 접속하지 못했습니다: {type(e).__name__}") from e

    async def test(self) -> list[str]:
        conn = await self._connect()
        try:
            version = await conn.fetchval("SELECT version()")
            return [str(version).split(",")[0]]
        finally:
            await conn.close()

    async def columns(self, spec: SourceSpec) -> list[str]:
        conn = await self._connect()
        try:
            async with conn.transaction(readonly=True):
                stmt = await conn.prepare(f"SELECT * FROM ({_base_sql(spec)}) q LIMIT 0")  # noqa: S608
                return [a.name for a in stmt.get_attributes()]
        except asyncpg.PostgresError as e:
            raise ConnectorError(f"원천 조회에 실패했습니다: {e.__class__.__name__}: {e}") from e
        finally:
            await conn.close()

    async def rows(self, spec: SourceSpec, *, since: Any | None) -> AsyncIterator[list[Row]]:
        sql = f"SELECT * FROM ({_base_sql(spec)}) q"  # noqa: S608
        args: list[Any] = []
        if spec.watermark_column:
            col = _quote_column(spec.watermark_column)
            if since is not None:
                sql += f" WHERE q.{col} > $1"
                args.append(since)
            sql += f" ORDER BY q.{col}"
        conn = await self._connect()
        try:
            async with conn.transaction(readonly=True):
                batch: list[Row] = []
                async for record in conn.cursor(sql, *args, prefetch=BATCH):
                    batch.append(dict(record))
                    if len(batch) >= BATCH:
                        yield batch
                        batch = []
                if batch:
                    yield batch
        except asyncpg.PostgresError as e:
            raise ConnectorError(f"원천 조회에 실패했습니다: {e.__class__.__name__}: {e}") from e
        finally:
            await conn.close()


def connector_for(kind: str, creds: DbCredentials) -> PostgresConnector:
    if kind == "postgres":
        return PostgresConnector(creds)
    # oracle·mysql·csv 는 인터페이스(base.Connector)만 있다 — 실제 원천이 정해지면 붙인다.
    raise ConnectorError(f"'{kind}' 커넥터는 아직 없습니다(MVP 는 postgres 만)")
