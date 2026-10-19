"""워커 프로세스 — 그래프 투영 · 작업 실행 · 주기 발행.

    uv run --project backend python -m ontobot.jobs.worker

한 주기: (1) 아웃박스 한 묶음 투영 (2) 대기 작업 하나 실행 (3) 가끔 주기 작업 발행·고아 작업 회수.
할 일이 없으면 잠깐 쉰다. 워커는 ``SKIP LOCKED`` 덕에 여러 개 띄울 수 있지만 투영기는 advisory
lock 으로 하나만 돈다(TRD 15절).

Neo4j 가 없어도 워커는 돈다 — 투영만 쉬고, 밀린 이벤트는 반영 지연으로 드러난다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import time

from ontobot.db.session import dispose_engine, session_scope
from ontobot.graph import client as graph
from ontobot.graph.projector import ensure_constraints, project_pending
from ontobot.jobs import handlers, queue, schedules
from ontobot.ontology import registry

log = logging.getLogger("ontobot.worker")

IDLE_SLEEP_S = 1.0
SCHEDULE_EVERY_S = 30.0
RECOVER_EVERY_S = 60.0


class Worker:
    def __init__(self) -> None:
        self.id = f"{socket.gethostname()}:{os.getpid()}"
        self.stopping = False
        self.driver = handlers.graph_driver()
        self.constraints_for: str | None = None
        self.last_schedule = 0.0
        self.last_recover = 0.0

    async def project(self) -> int:
        if self.driver is None:
            return 0
        try:
            async with session_scope() as db:
                schema = registry.current() or await registry.reload(db)
                if schema is not None and self.constraints_for != schema.content_hash:
                    await ensure_constraints(self.driver, schema.document)
                    self.constraints_for = schema.content_hash
                return await project_pending(db, self.driver)
        except graph.GraphUnavailable as e:
            log.warning("Neo4j 에 닿지 못해 투영을 미룹니다: %s", e)
        return 0

    async def run_one_job(self) -> bool:
        # 집기·기록과 실행을 다른 세션으로 나눈다. 처리기가 도는 동안 이 워커의 트랜잭션이 열려
        # 있으면 CREATE INDEX CONCURRENTLY 같은 작업이 그 트랜잭션을 기다려 영원히 멈춘다(실측).
        async with session_scope() as db:
            job = await queue.claim(db, self.id)
        if job is None:
            return False
        handler = handlers.HANDLERS.get(job.kind)
        error: str | None = None
        result: dict[str, object] = {}
        try:
            if handler is None:
                raise handlers.JobFailed(f"알 수 없는 작업 종류: {job.kind}")
            result = await handler(job.payload)
        except Exception as e:  # 처리기 예외는 작업 실패로 기록하고 워커는 계속 돈다
            log.exception("작업 %s(%s) 실패", job.id, job.kind)
            error = f"{type(e).__name__}: {e}"
        async with session_scope() as db:
            if error is None:
                await queue.complete(db, job.id, result)
            else:
                await queue.fail(db, job.id, error)
        return True

    async def housekeeping(self) -> None:
        now = time.monotonic()
        if now - self.last_recover >= RECOVER_EVERY_S:
            self.last_recover = now
            async with session_scope() as db:
                if (n := await queue.recover_stale(db)) > 0:
                    log.warning("고아 작업 %d건을 되돌렸습니다", n)
        if now - self.last_schedule >= SCHEDULE_EVERY_S:
            self.last_schedule = now
            async with session_scope() as db:
                await schedules.enqueue_due(db)

    async def run(self) -> None:
        log.info("워커 시작 %s (투영 %s)", self.id, "켬" if self.driver else "끔 — Neo4j 설정 없음")
        try:
            while not self.stopping:
                await self.housekeeping()
                projected = await self.project()
                worked = await self.run_one_job()
                if not projected and not worked:
                    await asyncio.sleep(IDLE_SLEEP_S)
        finally:
            if self.driver is not None:
                await self.driver.close()
            await dispose_engine()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    worker = Worker()

    def stop(*_: object) -> None:
        worker.stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    asyncio.run(worker.run())


if __name__ == "__main__":
    main()
