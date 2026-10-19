"""작업 종류별 처리기. 워커가 ``kind`` 로 고른다. 처리기는 결과 dict 를 돌려주거나 예외를 던진다."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ontobot.config import get_settings
from ontobot.db.session import get_engine, session_scope
from ontobot.graph import client as graph
from ontobot.graph.reconcile import rebuild, reconcile
from ontobot.objects.indexes import ensure_property_index
from ontobot.ontology import registry
from ontobot.sync.runner import run_mapping

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

SYNC = "sync_mapping"
GRAPH_REBUILD = "graph_rebuild"
GRAPH_RECONCILE = "graph_reconcile"
PROPERTY_INDEX = "create_property_index"


class JobFailed(Exception):
    pass


def graph_driver() -> graph.AsyncDriver | None:
    s = get_settings()
    if not s.neo4j_password.get_secret_value():
        return None
    return graph.create_driver(s.neo4j_uri, s.neo4j_user, s.neo4j_password.get_secret_value())


async def _schema() -> Any:
    schema = registry.current()
    if schema is None:
        async with session_scope() as db:
            schema = await registry.reload(db)
    if schema is None:
        raise JobFailed("게시된 온톨로지가 없습니다")
    return schema


async def sync_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    out = await run_mapping(int(payload["mapping_id"]))
    if out.status != "succeeded":
        # 사유는 실행 기록(sync_run)에 남았다. 작업도 실패로 끝내 백오프 재시도에 맡긴다 —
        # 원천 일시 장애면 다시 돌아 성공하고, 데이터 오류면 같은 사유로 실패가 쌓여 드러난다.
        raise JobFailed(f"동기화 실패(sync_run {out.run_id}): {out.error}")
    return {
        "run_id": out.run_id,
        "status": out.status,
        "rows_read": out.rows_read,
        "rows_upserted": out.rows_upserted,
        "rows_deleted": out.rows_deleted,
    }


async def _with_graph(fn: Callable[[Any, graph.AsyncDriver, Any], Awaitable[Any]]) -> Any:
    driver = graph_driver()
    if driver is None:
        raise JobFailed("Neo4j 접속 정보가 없습니다(ONTOBOT_NEO4J_*)")
    try:
        schema = await _schema()
        async with session_scope() as db:
            return await fn(db, driver, schema.document)
    finally:
        await driver.close()


async def graph_rebuild(_: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = await _with_graph(rebuild)
    return counts


async def graph_reconcile(_: dict[str, Any]) -> dict[str, Any]:
    report = await _with_graph(reconcile)
    result: dict[str, Any] = report.as_dict()
    return result


async def create_property_index(payload: dict[str, Any]) -> dict[str, Any]:
    name = await ensure_property_index(get_engine(), payload["type"], payload["property"])
    return {"index": name}


HANDLERS: dict[str, Handler] = {
    SYNC: sync_mapping,
    GRAPH_REBUILD: graph_rebuild,
    GRAPH_RECONCILE: graph_reconcile,
    PROPERTY_INDEX: create_property_index,
}
