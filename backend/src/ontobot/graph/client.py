"""Neo4j 드라이버 — 이 파일만 ``neo4j``를 import한다(TRD 16.3-3).

다른 모듈은 Cypher 문자열과 파라미터를 만들어 ``write``·``read`` 로 넘긴다. 값은 언제나 파라미터다.
라벨·관계 타입은 파라미터가 될 수 없으므로 ``label``·``rel`` 이 식별자 형식을 **다시** 확인한 뒤
백틱으로 감싼다 — 스키마 검증(ontology.models)에 더한 이중 방어다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncManagedTransaction
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired

from ontobot.ontology.models import IDENT_MEMBER_RE, IDENT_TYPE_RE

Statement = tuple[str, dict[str, Any]]


class GraphUnavailable(Exception):
    """Neo4j 에 닿지 못했다. 탐색 질의는 ``failed(graph_unavailable)`` 로 밝힌다(TRD 5.5)."""


@dataclass(frozen=True)
class ServerInfo:
    version: str
    edition: str


# 드라이버 기본값은 연결 실패 시 30초 넘게 재시도한다. 그동안 워커의 한 주기가 통째로 멈춰
# 다른 작업(동기화)까지 밀리므로 짧게 둔다.
# 실패는 빨리 밝히고, 밀린 이벤트는 다음 주기에 다시 반영한다.
CONNECT_TIMEOUT_S = 5.0
RETRY_TIME_S = 5.0


def create_driver(uri: str, user: str, password: str) -> AsyncDriver:
    return AsyncGraphDatabase.driver(
        uri,
        auth=(user, password),
        connection_timeout=CONNECT_TIMEOUT_S,
        max_transaction_retry_time=RETRY_TIME_S,
        connection_acquisition_timeout=CONNECT_TIMEOUT_S,
        # 빈 그래프에서 "관계 타입이 없다" 같은 정보성 경고가 질의마다 로그를 채운다 — 끈다.
        notifications_min_severity="OFF",
    )


def label(name: str) -> str:
    if not IDENT_TYPE_RE.match(name):
        raise ValueError(f"라벨로 쓸 수 없는 타입 이름: {name!r}")
    return f"`{name}`"


def rel(name: str) -> str:
    if not IDENT_MEMBER_RE.match(name):
        raise ValueError(f"관계 타입으로 쓸 수 없는 이름: {name!r}")
    return f"`{name}`"


_UNAVAILABLE = (ServiceUnavailable, SessionExpired, OSError)


async def server_info(driver: AsyncDriver) -> ServerInfo:
    """접속을 확인하고 서버 버전·에디션을 돌려준다(/readyz 와 설치 확인용)."""
    try:
        await driver.verify_connectivity()
        records, _, _ = await driver.execute_query(
            "CALL dbms.components() YIELD versions, edition RETURN versions[0] AS v, edition AS e"
        )
    except _UNAVAILABLE as e:
        raise GraphUnavailable(type(e).__name__) from e
    row = records[0]
    return ServerInfo(version=row["v"], edition=row["e"])


async def write(driver: AsyncDriver, statements: Sequence[Statement]) -> None:
    """여러 문장을 **한 트랜잭션**으로 실행한다(아웃박스 묶음 하나 = Neo4j 트랜잭션 하나)."""

    async def work(tx: AsyncManagedTransaction) -> None:
        for cypher, params in statements:
            result = await tx.run(cypher, params)
            await result.consume()

    try:
        async with driver.session() as session:
            await session.execute_write(work)
    except _UNAVAILABLE as e:
        raise GraphUnavailable(type(e).__name__) from e


async def run_schema(driver: AsyncDriver, cypher: str) -> None:
    """제약·인덱스 DDL — Neo4j 는 스키마 변경을 데이터 트랜잭션과 섞지 못한다."""
    try:
        await driver.execute_query(cypher)
    except _UNAVAILABLE as e:
        raise GraphUnavailable(type(e).__name__) from e


async def read(
    driver: AsyncDriver, cypher: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    try:
        records, _, _ = await driver.execute_query(cypher, params or {}, routing_="r")
    except _UNAVAILABLE as e:
        raise GraphUnavailable(type(e).__name__) from e
    return [dict(r) for r in records]


__all__ = [
    "AsyncDriver",
    "GraphUnavailable",
    "Neo4jError",
    "ServerInfo",
    "Statement",
    "create_driver",
    "label",
    "read",
    "rel",
    "run_schema",
    "server_info",
    "write",
]
