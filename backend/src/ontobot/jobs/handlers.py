"""작업 종류별 처리기. 워커가 ``kind`` 로 고른다. 처리기는 결과 dict 를 돌려주거나 예외를 던진다."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from ontobot.config import get_settings
from ontobot.db.session import get_engine, session_scope
from ontobot.evaluation import runner
from ontobot.graph import client as graph
from ontobot.graph.reconcile import rebuild, reconcile
from ontobot.llm.config import load as load_llm_config
from ontobot.notify import config as notify_config
from ontobot.notify import delivery
from ontobot.objects.indexes import ensure_property_index
from ontobot.ontology import registry
from ontobot.security import audit_chain
from ontobot.sync.runner import run_mapping
from ontobot.tracing.retention import Policy, purge

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

SYNC = "sync_mapping"
GRAPH_REBUILD = "graph_rebuild"
GRAPH_RECONCILE = "graph_reconcile"
PROPERTY_INDEX = "create_property_index"
RETENTION = "retention_purge"
_log = logging.getLogger(__name__)
AUDIT_VERIFY = "audit_verify"
EVAL_RUN = "eval_run"
NOTIFY = "notify_deliver"  # actions.runner.NOTIFY_JOB 와 같은 이름


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


async def retention_purge(_: dict[str, Any]) -> dict[str, Any]:
    s = get_settings()
    policy = Policy(s.retention_trace_days, s.retention_message_days, s.retention_utterance_days)
    async with session_scope() as db:
        counts = await purge(db, policy)
        await db.commit()
    return counts


async def audit_verify(_: dict[str, Any]) -> dict[str, Any]:
    """감사 해시 체인 매일 검증(PRD 5.5). 끊기면 경보 로그(error)를 남기고 결과를 작업 기록에 둔다 —
    관리 화면이 마지막 결과를 보여 준다. 끊긴 곳은 고치지 않는다(추가 전용)."""
    async with session_scope() as db:
        report = await audit_chain.verify(db)
    if not report.ok:
        _log.error(
            "감사 로그 해시 체인이 끊겼습니다",
            extra={"seq": report.broken_seq, "audit_id": report.broken_id, "reason": report.reason},
        )
    return asdict(report)


async def eval_run(payload: dict[str, Any]) -> dict[str, Any]:
    """평가 실행(TRD 13.1) — 실제 모델로 돈다.

    API 가 ``eval_run(running)`` 을 만든 뒤 이 작업을 넣는다.
    """
    s = get_settings()
    cfg = runner.RunConfig(
        llm=load_llm_config(s.llm_config_path),
        overrides=dict(payload.get("overrides") or {}),
        grade2_enabled=s.grade2_enabled,
    )
    driver = graph_driver()
    try:
        run = await runner.execute(int(payload["run_id"]), cfg, graph_driver=driver)
    finally:
        if driver is not None:
            await driver.close()
    if run.status != "succeeded":
        raise JobFailed(f"평가 실행 {run.id} 실패")
    return {"run_id": run.id, "gate_passed": run.gate_passed}


async def notify_deliver(_: dict[str, Any]) -> dict[str, Any]:
    """메일·SMS 발송 아웃박스(TRD 17.1-4).

    실패는 행마다 재시도 상태로 남는다 — 작업 자체는 성공이다.
    """
    cfg = notify_config.load(get_settings().notify_config_path)
    return await delivery.deliver_due(cfg)


HANDLERS: dict[str, Handler] = {
    SYNC: sync_mapping,
    GRAPH_REBUILD: graph_rebuild,
    GRAPH_RECONCILE: graph_reconcile,
    PROPERTY_INDEX: create_property_index,
    RETENTION: retention_purge,
    AUDIT_VERIFY: audit_verify,
    EVAL_RUN: eval_run,
    NOTIFY: notify_deliver,
}
