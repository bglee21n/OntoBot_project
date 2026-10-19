"""마이그레이션이 ORM 모델과 일치하고, 감사 로그가 추가 전용인지 본다."""

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from ontobot.config import get_settings
from ontobot.db.models.security import Role
from ontobot.db.session import session_scope
from ontobot.security.rbac import KNOWN_ROLES

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]


async def test_migration_matches_models() -> None:
    # 모델만 고치고 마이그레이션을 안 만들면 운영 DB에는 그 변경이 영영 없다.
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["url"] = get_settings().database_url()
    try:
        await asyncio.to_thread(command.check, cfg)
    except CommandError as e:  # pragma: no cover — 실패 메시지를 그대로 보여 준다
        pytest.fail(f"모델과 마이그레이션이 다릅니다: {e}")


async def test_seeded_roles_match_code_constants() -> None:
    async with session_scope() as db:
        names = set(await db.scalars(select(Role.name)))
    # 테스트가 만든 역할(test_ 접두어)은 시드가 아니다.
    assert {n for n in names if not n.startswith("test_")} == set(KNOWN_ROLES)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_log SET event = 'tampered'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log CASCADE",
    ],
)
async def test_audit_log_is_append_only(sql: str) -> None:
    async with session_scope() as db:
        await db.execute(text("INSERT INTO audit_log (event) VALUES ('probe')"))
        await db.commit()
        with pytest.raises(DBAPIError, match="append-only"):
            await db.execute(text(sql))
        await db.rollback()
