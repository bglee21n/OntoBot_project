"""속성 표현식 인덱스(TRD 6.2, v0.2 정정 8).

게시 때 발행되는 ``create_property_index`` 작업이 만든다.

스키마가 데이터라 Alembic 마이그레이션 파일로 고정할 수 없다. 그래서 런타임 DDL 이고,
``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` 로 멱등하게 만든다(운영 중 표를 잠그지 않는다).

DDL 은 식별자를 바인드할 수 없다. 타입·속성 이름은 정의 문서의 식별자 형식을 **다시** 확인한 뒤에만
넣는다. 인덱스 이름은 63자 제한이 있어 길면 해시로 줄인다.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ontobot.ontology.models import IDENT_MEMBER_RE, IDENT_TYPE_RE


def index_name(object_type: str, prop: str) -> str:
    name = f"ix_obj_{object_type.lower()}_{prop}"
    if len(name) <= 63:
        return name
    return f"ix_obj_{hashlib.sha256(f'{object_type}.{prop}'.encode()).hexdigest()[:24]}"


async def ensure_property_index(engine: AsyncEngine, object_type: str, prop: str) -> str:
    if not IDENT_TYPE_RE.match(object_type) or not IDENT_MEMBER_RE.match(prop):
        raise ValueError(f"인덱스를 만들 수 없는 이름: {object_type}.{prop}")
    name = index_name(object_type, prop)
    ddl = (
        f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{name}" ON object '
        f"((properties ->> '{prop}')) WHERE type = '{object_type}' AND deleted_at IS NULL"
    )
    # CONCURRENTLY 는 트랜잭션 안에서 돌 수 없다 — 자동 커밋 연결을 쓴다.
    async with engine.connect() as conn:
        auto = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await auto.execute(text(ddl))
    return name
