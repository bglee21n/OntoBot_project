"""보존 기간 정리(TRD 9.3) — 실제 DB. 시간을 앞당겨(now) 만료를 만든다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ontobot.db.models.chat import Conversation, ConversationContext, Message, Trace
from ontobot.db.models.security import AuditLog
from ontobot.db.session import session_scope
from ontobot.tracing.retention import Policy, purge
from tests.integration.conftest import MakeUser

pytestmark = pytest.mark.integration


async def test_expired_rows_are_purged_and_the_purge_is_audited(make_user: MakeUser) -> None:
    _, _, uid = await make_user("user")
    async with session_scope() as db:
        conv = Conversation(user_id=uid, title="보존 시험")
        db.add(conv)
        await db.flush()
        db.add(Message(conversation_id=conv.id, role="user", content="질문"))
        db.add(ConversationContext(conversation_id=conv.id, refs=[{"type": "Facility", "pk": "F"}]))
        trace = Trace(user_id=uid, status="ok")
        db.add(trace)
        await db.commit()
        conv_id, trace_id = conv.id, trace.id

    future = datetime.now(UTC) + timedelta(days=400)
    async with session_scope() as db:
        # 메시지 보존을 끄면(0) 메시지·대화는 남고 트레이스만 지워진다.
        counts = await purge(db, Policy(90, 0, 180), now=future)
        await db.commit()
    assert counts["trace"] >= 1 and counts["message"] == 0
    async with session_scope() as db:
        assert await db.get(Trace, trace_id) is None
        assert await db.get(Conversation, conv_id) is not None

        counts = await purge(db, Policy(90, 365, 180), now=future)
        await db.commit()
    assert counts["message"] >= 1 and counts["conversation"] >= 1
    async with session_scope() as db:
        assert await db.get(Conversation, conv_id) is None  # 빈 대화는 맥락과 함께 지운다
        ctx = await db.scalar(
            select(ConversationContext).where(ConversationContext.conversation_id == conv_id)
        )
        assert ctx is None
        last = await db.scalar(
            select(AuditLog)
            .where(AuditLog.event == "ops.retention_purged")
            .order_by(AuditLog.id.desc())
        )
        assert last is not None and last.payload["deleted"]["conversation"] >= 1


async def test_nothing_expired_writes_no_audit_row() -> None:
    async with session_scope() as db:
        before = await db.scalar(select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1))
        counts = await purge(db, Policy(90, 365, 180), now=datetime(2000, 1, 1, tzinfo=UTC))
        await db.commit()
        after = await db.scalar(select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1))
    assert not any(counts.values()) and before == after
