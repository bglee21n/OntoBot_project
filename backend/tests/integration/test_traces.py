"""Phase 15 — 트레이스 화면 API: 목록(필터·쪽 넘김)·질문 붙이기(패턴 마스킹)·관리자만·열람 감사."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select

from ontobot.db.models.chat import Conversation, Message, Trace
from ontobot.db.models.security import AuditLog
from ontobot.db.session import session_scope
from tests.integration.conftest import MakeUser, login

pytestmark = pytest.mark.integration


async def _traced(user_id: int, question: str, **trace: object) -> int:
    async with session_scope() as db:
        conv = Conversation(user_id=user_id, title="t")
        db.add(conv)
        await db.flush()
        db.add(Message(conversation_id=conv.id, role="user", content=question))
        await db.flush()
        answer = Message(conversation_id=conv.id, role="assistant", content="답", status="ok")
        db.add(answer)
        await db.flush()
        t = Trace(user_id=user_id, message_id=answer.id, **trace)
        db.add(t)
        await db.flush()
        answer.trace_id = t.id
        trace_id = t.id
        await db.commit()
    return trace_id


async def test_trace_list_and_detail(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    _, _, asker = await make_user("user")
    tag = uuid.uuid4().hex[:6]
    ok_id = await _traced(
        asker,
        f"담당 010-1234-5678 시설 {tag}",
        status="ok",
        grade=1,
        row_count=3,
        fact_sentences=2,
        verified_sentences=2,
        removed_citations=[{"key": "o:Facility/X"}],
        stage_latency_ms={"planning": 100, "querying": 20},
    )
    failed_id = await _traced(
        asker, f"실패 {tag}", status="failed", failure_reason="plan_invalid", stage_latency_ms={}
    )

    username, password, _ = await make_user("user")
    assert (await login(client, username, password)).status_code == 200
    assert (await client.get("/api/v1/traces")).status_code == 403

    username, password, admin_id = await make_user("admin")
    assert (await login(client, username, password)).status_code == 200
    rows = (await client.get("/api/v1/traces", params={"limit": 100})).json()
    by_id = {r["id"]: r for r in rows}
    assert by_id[ok_id]["question"] == f"담당 *** 시설 {tag}"  # 질문은 패턴으로 가린다
    assert by_id[ok_id]["row_count"] == 3
    assert by_id[ok_id]["removed_citations"] == 1
    assert by_id[ok_id]["total_ms"] == 120
    assert rows[0]["id"] > rows[-1]["id"]  # 최근 것부터

    failed = (await client.get("/api/v1/traces", params={"failure_reason": "plan_invalid"})).json()
    assert failed_id in {r["id"] for r in failed}
    assert all(r["failure_reason"] == "plan_invalid" for r in failed)
    older = (await client.get("/api/v1/traces", params={"before_id": failed_id})).json()
    assert failed_id not in {r["id"] for r in older} and ok_id in {r["id"] for r in older}

    detail = (await client.get(f"/api/v1/traces/{ok_id}")).json()
    assert detail["question"] == f"담당 *** 시설 {tag}"
    assert detail["row_count"] == 3
    async with session_scope() as db:
        seen = list(
            await db.scalars(
                select(AuditLog.target).where(
                    AuditLog.actor_id == admin_id, AuditLog.event == "security.trace_viewed"
                )
            )
        )
    assert "trace:list" in seen and f"trace:{ok_id}" in seen
