"""통합 테스트 공용 픽스처 — 테스트 전용 PostgreSQL DB(``test_db_name``)를 쓴다(ADR-0007).

세션 시작 때 그 DB의 public 스키마를 지우고 마이그레이션을 처음부터 적용한다. 그래서
**개발 DB를 가리키면 안 된다** — 이름에 ``test`` 가 없으면 아예 거부한다.

테스트 사이에는 데이터를 지우지 않는다. ``audit_log`` 는 TRUNCATE·DELETE 가 트리거로 막혀 있고
(추가 전용), 그 표가 ``app_user`` 를 참조하므로 사용자도 지울 수 없다. 대신 각 테스트가
고유한 이름(uuid)으로 자기 데이터를 만든다.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from pathlib import Path

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from ontobot.config import get_settings
from ontobot.db.models.security import (
    AppUser,
    DataGroup,
    GroupMember,
    GroupTypePermission,
    Role,
    UserRole,
)
from ontobot.db.session import dispose_engine, session_scope
from ontobot.ontology import importer, registry, versioning
from ontobot.security.auth import hash_password

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
async def test_database() -> AsyncIterator[str]:
    base = get_settings()
    if not base.db_password.get_secret_value():
        pytest.skip("ONTOBOT_DB_PASSWORD 미설정 (.env) — 통합 테스트를 건너뜀")
    name = base.test_db_name
    if "test" not in name:
        pytest.exit(
            f"test_db_name='{name}' 은 테스트 DB 같지 않습니다 — 스키마를 지우므로 중단합니다"
        )

    os.environ["ONTOBOT_DB_NAME"] = name
    get_settings.cache_clear()
    await dispose_engine()
    registry.reset()

    url = get_settings().database_url()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["url"] = url
    # env.py 가 asyncio.run 을 부르므로 이미 도는 루프 밖(스레드)에서 실행한다.
    await asyncio.to_thread(command.upgrade, cfg, "head")
    yield name
    await dispose_engine()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    from ontobot.main import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


MakeUser = Callable[..., Awaitable[tuple[str, str, int]]]


@pytest.fixture
def make_user() -> MakeUser:
    """역할을 가진 로컬 계정을 만든다. (username, password, user_id) 를 돌려준다."""

    async def _make(*roles: str, status: str = "active") -> tuple[str, str, int]:
        username = f"u_{uuid.uuid4().hex[:10]}"
        password = uuid.uuid4().hex  # 테스트 전용 값 — 32자
        async with session_scope() as db:
            user = AppUser(
                username=username,
                display_name=username,
                password_hash=hash_password(password),
                status=status,
            )
            db.add(user)
            await db.flush()
            for name in roles:
                role_id = await db.scalar(select(Role.id).where(Role.name == name))
                db.add(UserRole(user_id=user.id, role_id=role_id))
                # 역할과 같은 이름의 데이터 그룹이 있으면 구성원으로(마이그레이션 0012 와 같다).
                group_id = await db.scalar(select(DataGroup.id).where(DataGroup.name == name))
                if group_id is not None:
                    db.add(GroupMember(group_id=group_id, user_id=user.id))
            await db.commit()
            return username, password, user.id
        raise AssertionError("unreachable")

    return _make


async def login(client: httpx.AsyncClient, username: str, password: str) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/local/login", json={"username": username, "password": password}
    )


# ---------------------------------------------------------------- 원천 DB (동기화 테스트)

SOURCE_ENV = "ONTOBOT_TESTSRC"
SOURCE_DB = "ontobot_source_test"
SAMPLE_PACK = BACKEND.parent / "domain-packs" / "sample" / "ontology.yaml"


@pytest.fixture(scope="session")
async def source_env() -> AsyncIterator[None]:
    s = get_settings()
    for k, v in {
        "HOST": s.db_host,
        "PORT": str(s.db_port),
        "DB": SOURCE_DB,
        "USER": s.db_user,
        "PASSWORD": s.db_password.get_secret_value(),
    }.items():
        os.environ[f"{SOURCE_ENV}_{k}"] = v
    try:
        conn = await source_connect()
    except (OSError, asyncpg.PostgresError) as e:
        pytest.skip(f"원천 테스트 DB({SOURCE_DB}) 에 접속할 수 없음: {type(e).__name__}")
    await conn.close()
    # 매핑이 게시 버전에 맞아야 하므로 샘플 온톨로지를 게시해 둔다.
    async with session_scope() as db:
        doc = importer.parse(SAMPLE_PACK.read_text(encoding="utf-8"), "yaml")
        current = await registry.reload(db)
        if current is None or current.content_hash != doc.content_hash():
            draft = await versioning.create_draft(db, actor_id=None, parent_id=None)
            await versioning.replace_document(db, draft.id, doc, actor_id=None)
            await versioning.publish(db, draft.id, actor_id=None)
            await db.commit()
            await registry.reload(db)
    yield


async def source_connect() -> asyncpg.Connection:
    s = get_settings()
    return await asyncpg.connect(
        host=s.db_host,
        port=s.db_port,
        user=s.db_user,
        password=s.db_password.get_secret_value(),
        database=SOURCE_DB,
    )


async def add_group(db: AsyncSession, name: str, types: Iterable[str], *, edit: bool = True) -> int:
    """테스트용 데이터 그룹 — ``types`` 읽기(와 편집) 권한.

    역할과 같은 이름이면 ``make_user`` 가 그 역할 사용자를 구성원으로 넣는다.
    """
    g = DataGroup(name=name, display_name=name)
    db.add(g)
    await db.flush()
    for t in types:
        db.add(GroupTypePermission(group_id=g.id, object_type=t, can_read=True, can_edit=edit))
    await db.flush()
    return g.id
