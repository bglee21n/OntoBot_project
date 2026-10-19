"""Neo4j 드라이버 — 이 파일만 ``neo4j``를 import한다(TRD 16.3-3).

투영·탐색·대사(projector·traversal·reconcile)는 Phase 2에서 이 모듈 위에 만든다.
"""

from __future__ import annotations

from dataclasses import dataclass

from neo4j import AsyncDriver, AsyncGraphDatabase


@dataclass(frozen=True)
class ServerInfo:
    version: str
    edition: str


def create_driver(uri: str, user: str, password: str) -> AsyncDriver:
    return AsyncGraphDatabase.driver(uri, auth=(user, password))


async def server_info(driver: AsyncDriver) -> ServerInfo:
    """접속을 확인하고 서버 버전·에디션을 돌려준다(/readyz 와 설치 확인용)."""
    await driver.verify_connectivity()
    records, _, _ = await driver.execute_query(
        "CALL dbms.components() YIELD versions, edition RETURN versions[0] AS v, edition AS e"
    )
    row = records[0]
    return ServerInfo(version=row["v"], edition=row["e"])
