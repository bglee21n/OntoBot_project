"""비동기 DB 엔진·세션.

엔진은 처음 쓸 때 만든다 — import 시점에 접속하면 DB 없이 도는 단위 테스트까지 깨진다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ontobot.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url(), pool_pre_ping=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def session_scope() -> AsyncIterator[AsyncSession]:
    get_engine()
    if _sessionmaker is None:  # get_engine()이 언제나 채운다 — 타입 좁히기용
        raise RuntimeError("DB 세션 팩토리가 초기화되지 않았습니다")
    async with _sessionmaker() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
