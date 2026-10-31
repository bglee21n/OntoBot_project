"""Alembic 실행 환경(비동기).

접속 URL 우선순위: ``config.attributes["url"]``(테스트가 넣는다) → ``ontobot.config`` 설정.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine

from ontobot.config import get_settings
from ontobot.db.models import Base
from ontobot.db.models.documents import Vector

# pgvector 열을 반영(reflect)할 때 알아보게 한다 — 모르면 자료형 비교를 건너뛴다(Phase 16).
ischema_names["vector"] = Vector

# 런타임 DDL 이 만드는 인덱스(속성 인덱스 objects.indexes·청크 벡터 인덱스 documents.vector_index)는
# 마이그레이션의 대상이 아니다 — 모델과 비교할 때 빼지 않으면 autogenerate 가 지우려 든다.
RUNTIME_INDEX_PREFIXES = ("ix_obj_", "ix_chunk_vec_")


def _include_object(obj, name, type_, reflected, compare_to) -> bool:  # type: ignore[no-untyped-def]
    return not (type_ == "index" and reflected and (name or "").startswith(RUNTIME_INDEX_PREFIXES))


config = context.config
target_metadata = Base.metadata


def _url() -> URL:
    url = config.attributes.get("url")
    return url if url is not None else get_settings().database_url()


def _do_run(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as conn:
        await conn.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=_url().render_as_string(hide_password=True),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_online())
