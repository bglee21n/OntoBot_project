"""PostgreSQL 접속 스모크 — 설정(.env)의 계정으로 실제 DB에 붙는지 본다."""

import pytest
from sqlalchemy import text

from ontobot.config import get_settings
from ontobot.db.session import dispose_engine, get_engine

pytestmark = pytest.mark.integration


async def test_connects_with_configured_account() -> None:
    settings = get_settings()
    if not settings.db_password.get_secret_value():
        pytest.skip("ONTOBOT_DB_PASSWORD 미설정 (.env)")
    try:
        async with get_engine().connect() as conn:
            row = (await conn.execute(text("select current_database(), current_user"))).one()
    finally:
        await dispose_engine()
    assert tuple(row) == (settings.db_name, settings.db_user)
