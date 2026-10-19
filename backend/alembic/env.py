"""Alembic 실행 환경(비동기).

접속 URL 우선순위: ``config.attributes["url"]``(테스트가 넣는다) → ``ontobot.config`` 설정.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine

from ontobot.config import get_settings
from ontobot.db.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> URL:
    url = config.attributes.get("url")
    return url if url is not None else get_settings().database_url()


def _do_run(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
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
