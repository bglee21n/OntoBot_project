"""시연 저장소(Phase 18c) — draft 버전마다 격리 스키마 ``demo_v<id>`` 의
``object``·``link``·``object_edit``.

워크숍에서 만든 draft 를 게시하기 **전에** 샘플 행으로 질문해 보는 자리다. 운영 데이터와 섞이지 않게
같은 모양의 표를 별도 스키마에 두고, 요청 트랜잭션의 ``search_path`` 맨 앞에 그 스키마를 넣는다
(``use``). 객체 읽기·쓰기 SQL 은 모두 스키마를 적지 않으므로(``objects.reader``·``objects.store``)
**이 한 곳만** 바꾸면 운영과 같은 코드가 시연 표를 본다 — 파이프라인에 다른 주입 지점을 만들지
않는다(평가의 장애 주입과 같은 원칙).

- ``SET LOCAL`` 과 같은 범위(``set_config(..., true)``)라 트랜잭션이 끝나면 연결 풀로 돌아가기 전에
  풀린다.
- 스키마 이름은 정수 id 로만 만든다(식별자는 바인드할 수 없다 — 형식을 다시 확인한다).
- 그래프(Neo4j)는 시연에 없다 — 탐색 조건이 있는 질문은 ``graph_unavailable`` 로 실패한다(PG
  ``link`` 로 대신하지 않는 규칙 그대로).
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TABLES = ("object", "link", "object_edit")
_NAME = re.compile(r"^demo_v[0-9]{1,18}$")


class DemoNotFound(Exception):
    pass


def schema_name(version_id: int) -> str:
    name = f"demo_v{int(version_id)}"
    if not _NAME.match(name):
        raise ValueError(name)
    return name


async def exists(db: AsyncSession, version_id: int) -> bool:
    return bool(
        await db.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :n)"),
            {"n": schema_name(version_id)},
        )
    )


async def ensure(db: AsyncSession, version_id: int) -> str:
    """스키마와 표를 만든다(있으면 그대로).

    표 모양은 지금 ``public`` 의 것을 베낀다(FK 는 베끼지 않는다).
    """
    s = schema_name(version_id)
    await db.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{s}"'))
    for t in TABLES:
        await db.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{s}"."{t}" (LIKE public."{t}" '
                "INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING INDEXES)"
            )
        )
    return s


async def use(db: AsyncSession, version_id: int) -> None:
    """이 트랜잭션 동안 객체 표를 시연 스키마에서 읽고 쓴다. 없으면 ``DemoNotFound``."""
    if not await exists(db, version_id):
        raise DemoNotFound(version_id)
    current = await db.scalar(text("SELECT current_setting('search_path')"))
    await db.execute(
        text("SELECT set_config('search_path', :p, true)"),
        {"p": f'"{schema_name(version_id)}", {current}'},
    )


async def counts(db: AsyncSession, version_id: int) -> dict[str, int]:
    if not await exists(db, version_id):
        return {}
    s = schema_name(version_id)
    rows = await db.execute(
        text(
            f'SELECT type, count(*) AS n FROM "{s}".object '  # noqa: S608 — 스키마 이름은 확인한 정수
            "WHERE deleted_at IS NULL GROUP BY type ORDER BY type"
        )
    )
    return {r.type: int(r.n) for r in rows}


async def drop(db: AsyncSession, version_id: int) -> None:
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name(version_id)}" CASCADE'))
