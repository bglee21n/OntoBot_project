"""원천 커넥터 인터페이스(TRD 11절).

커넥터는 **읽기만** 한다(write-back 없음, TRD 2절 8번). 행을 묶음(batch)으로 흘려보내 큰 표도
메모리에 통째로 올리지 않는다. 증분 모드면 ``watermark_column > since`` 인 행만,
워터마크 순서로 준다.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

# 원천의 표·열 이름. SQL 에 식별자로 들어가므로(바인드할 수 없다) 형식을 좁게 받는다.
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
TABLE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]{0,62}\.)?[A-Za-z_][A-Za-z0-9_]{0,62}$")

BATCH = 1000

Row = dict[str, Any]


@dataclass(frozen=True)
class SourceSpec:
    """무엇을 읽는가 — 표 또는 SELECT 하나(둘 중 하나만)."""

    table: str | None = None
    query: str | None = None
    watermark_column: str | None = None


class ConnectorError(Exception):
    """원천에 접속·조회하지 못했다. 메시지에 접속 정보를 싣지 않는다."""


class Connector(Protocol):
    async def test(self) -> list[str]:
        """접속을 확인하고 원천 서버 정보를 돌려준다(화면의 '접속 시험')."""
        ...

    async def columns(self, spec: SourceSpec) -> list[str]:
        """읽을 대상의 열 이름 — 매핑 저장 전에 대조한다."""
        ...

    def rows(self, spec: SourceSpec, *, since: Any | None) -> AsyncIterator[list[Row]]: ...
