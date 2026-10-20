"""준비 확인(``/readyz``, TRD 15절) — PostgreSQL·Neo4j·LLM 에 지금 닿는가.

``/healthz`` 는 프로세스 생존만 본다. 여기는 외부 의존을 보므로 **재시작 판단에 쓰지 않는다**
(DB 가 잠깐 끊긴 것만으로 멀쩡한 프로세스를 죽이게 된다). 설치 확인·감시·부하 측정 전 점검용이다.

판정:
- PostgreSQL 이나 LLM 이 안 되면 ``unavailable``(503) — 어떤 질의도 답할 수 없다.
- Neo4j 만 안 되면 ``degraded``(200) — 탐색 질의만 ``failed(graph_unavailable)`` 이고 나머지는
  답한다(TRD 5.5). 설정이 없는 것(``not_configured``)도 같다.

응답은 인증 없이 열리므로 구성 요소 상태와 오류 종류(예외 이름·없는 모델 이름)만 싣는다.
호스트·접속 문자열은 싣지 않는다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text

from ontobot.config import get_settings
from ontobot.db.session import session_scope
from ontobot.graph import client as graph
from ontobot.llm.config import load as load_llm_config
from ontobot.llm.gateway import check_providers

# 구성 요소 하나의 확인 시간 상한. 셋을 동시에 보므로 응답은 이 값 남짓에 끝난다.
CHECK_TIMEOUT_S = 3.0

ComponentStatus = Literal["ok", "down", "not_configured"]
OverallStatus = Literal["ok", "degraded", "unavailable"]


@dataclass(frozen=True)
class Component:
    status: ComponentStatus
    detail: str | None = None


@dataclass(frozen=True)
class Readiness:
    status: OverallStatus
    postgres: Component
    neo4j: Component
    llm: Component


async def _bounded(check: Awaitable[Component]) -> Component:
    try:
        return await asyncio.wait_for(check, CHECK_TIMEOUT_S)
    except TimeoutError:
        return Component("down", "timeout")
    except Exception as e:  # 확인 중 무슨 오류든 "닿지 못함" 이다 — 준비 확인이 500 을 내면 안 된다
        return Component("down", type(e).__name__)


async def _postgres() -> Component:
    async with session_scope() as db:
        await db.execute(text("SELECT 1"))
    return Component("ok")


async def _neo4j(driver: graph.AsyncDriver | None) -> Component:
    if driver is None:
        return Component("not_configured")
    try:
        await graph.server_info(driver)
    except graph.GraphUnavailable as e:
        return Component("down", str(e))
    return Component("ok")


async def _llm() -> Component:
    cfg = load_llm_config(get_settings().llm_config_path)
    providers = await check_providers(cfg, timeout_s=CHECK_TIMEOUT_S)
    bad = {name: p for name, p in providers.items() if not p.ok}
    if not bad:
        return Component("ok")
    return Component("down", "; ".join(f"{n}: {p.detail}" for n, p in bad.items()))


async def readiness(driver: graph.AsyncDriver | None) -> Readiness:
    pg, neo, llm = await asyncio.gather(
        _bounded(_postgres()), _bounded(_neo4j(driver)), _bounded(_llm())
    )
    status: OverallStatus
    if pg.status != "ok" or llm.status != "ok":
        status = "unavailable"
    elif neo.status != "ok":
        status = "degraded"
    else:
        status = "ok"
    return Readiness(status=status, postgres=pg, neo4j=neo, llm=llm)
