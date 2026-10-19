"""앱 팩토리.

``/healthz``는 프로세스 생존만 본다(TRD 15절). PostgreSQL·Neo4j·LLM 연결은 ``/readyz``의
몫이고 Phase 8에서 붙인다 — 생존 확인이 외부 의존을 보면 DB가 잠깐 끊긴 것만으로 오케스트레이터가
멀쩡한 프로세스를 재시작한다.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from ontobot.api import auth as auth_api
from ontobot.api import errors
from ontobot.api import graph as graph_api
from ontobot.api import ontology as ontology_api
from ontobot.api import sources as sources_api
from ontobot.config import get_settings
from ontobot.db.session import dispose_engine, session_scope
from ontobot.ontology import registry

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
_UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    try:
        async with session_scope() as db:
            await registry.reload(db)
    except (SQLAlchemyError, OSError):
        # DB 없이도 프로세스는 뜬다(/healthz). 게시 스키마가 없다는 사실은 읽는 쪽이 밝힌다.
        log.exception("게시 스키마를 읽지 못했습니다 — 레지스트리가 비어 있습니다")
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="OntoBot", version="0.1.0", lifespan=_lifespan)
    errors.install(app)

    @app.middleware("http")
    async def _origin_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # 상태를 바꾸는 요청은 허용된 Origin 에서만 받는다(SameSite=Lax 쿠키에 더한 CSRF 방어).
        # Origin 이 없는 요청(서버 간 호출·CLI)은 쿠키도 없으므로 인증 단계에서 걸러진다.
        origin = request.headers.get("origin")
        if request.method in _UNSAFE and origin and origin not in settings.allowed_origins:
            return errors.problem_response(
                403, "허용되지 않은 출처의 요청입니다", None, "origin_rejected", request.url.path
            )
        return await call_next(request)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_api.router, prefix=API_PREFIX)
    app.include_router(ontology_api.router, prefix=API_PREFIX)
    app.include_router(sources_api.router, prefix=API_PREFIX)
    app.include_router(graph_api.router, prefix=API_PREFIX)
    app.include_router(graph_api.jobs_router, prefix=API_PREFIX)
    return app


app = create_app()
