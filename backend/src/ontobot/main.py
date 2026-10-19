"""앱 팩토리.

``/healthz``는 프로세스 생존만 본다(TRD 15절). PostgreSQL·Neo4j·LLM 연결은 ``/readyz``의
몫이고 Phase 8에서 붙인다 — 생존 확인이 외부 의존을 보면 DB가 잠깐 끊긴 것만으로 오케스트레이터가
멀쩡한 프로세스를 재시작한다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ontobot.db.session import dispose_engine


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(title="OntoBot", version="0.1.0", lifespan=_lifespan)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
