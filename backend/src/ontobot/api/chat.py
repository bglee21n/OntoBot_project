"""채팅(SSE)·대화 기록(TRD 14.1 채팅 묶음, 14.2).

``POST /chat`` 의 응답 본문은 스트림이다. 스트림을 흘리는 동안에는 요청 의존성(``Db``)의 세션
수명에 기대지 않는다 — 응답이 끝나기 전에 그 세션이 닫힐 수 있다. 질문 기록은 스트림 전에 커밋하고,
답·트레이스·맥락은 생성기 안에서 연 세션에 함께 커밋한다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ontobot.api.deps import CurrentUser, Db, User
from ontobot.api.errors import ProblemError
from ontobot.chat import context as chat_context
from ontobot.chat import pipeline, stream, templates
from ontobot.config import get_settings
from ontobot.db.models.chat import Conversation, Message
from ontobot.db.session import session_scope
from ontobot.llm.config import load as load_llm_config
from ontobot.llm.gateway import LLMGateway
from ontobot.ontology import registry
from ontobot.security import rbac
from ontobot.tracing import recorder

log = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])

TITLE_CHARS = 60
# 연결이 끊겨도 끝까지 도는 처리 작업. 참조를 쥐고 있지 않으면 작업이 도중에 수거될 수 있다.
_running: set[asyncio.Task[None]] = set()


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    conversation_id: int | None = None


class ConversationOut(BaseModel):
    id: int
    title: str | None
    updated_at: datetime


class MessageMeta(BaseModel):
    conditions: str | None = None
    graph_as_of: str | None = None
    citations: list[stream.CitationData] = Field(default_factory=list)
    estimated_sentences: list[int] = Field(default_factory=list)
    failure_reason: str | None = None


class MessageOut(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    status: Literal["ok", "empty", "failed", "not_query"] | None
    grade: int | None
    meta: MessageMeta | None
    created_at: datetime


async def _owned(db: Db, user: CurrentUser, conversation_id: int) -> Conversation:
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        # 남의 대화는 없는 대화와 같은 말로 거른다 — 있다는 사실도 정보다.
        raise ProblemError(404, "대화를 찾을 수 없습니다", code="conversation_not_found")
    return conv


async def _answer(
    request: Request, user: CurrentUser, conversation_id: int, question: str
) -> AsyncIterator[str]:
    settings = get_settings()
    events: asyncio.Queue[str | None] = asyncio.Queue()

    async def emit(event: str, data: dict[str, Any]) -> None:
        await events.put(stream.encode(event, data))

    async def work() -> None:
        try:
            async with session_scope() as db:
                readable = await rbac.readable_types(db, user.id)
                schema = registry.current()
                refs = await chat_context.load(db, conversation_id)
                ctx = (
                    await chat_context.with_titles(db, schema.document, readable, refs)
                    if schema is not None
                    else []
                )
                llm = LLMGateway(load_llm_config(settings.llm_config_path))
                res = await pipeline.run(
                    db,
                    llm,
                    schema,
                    readable,
                    question,
                    grade2_enabled=settings.grade2_enabled,
                    context=ctx,
                    graph_driver=getattr(request.app.state, "graph", None),
                    emit=emit,
                )
                msg = Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=res.answer,
                    status=res.status,
                    grade=res.grade,
                    meta=stream.message_meta(res),
                )
                db.add(msg)
                await db.flush()
                trace = await recorder.record(
                    db, user_id=user.id, message_id=msg.id, result=res, llm_calls=llm.calls
                )
                msg.trace_id = trace.id
                if res.context_refs is not None:
                    await chat_context.save(db, conversation_id, res.context_refs)
                conv = await db.get(Conversation, conversation_id)
                if conv is not None:
                    conv.updated_at = datetime.now(UTC)
                await db.commit()
            for event, data in stream.result_events(
                res, conversation_id=conversation_id, message_id=msg.id, trace_id=trace.id
            ):
                await events.put(stream.encode(event, data))
        except Exception:
            # 예상하지 못한 오류도 실패로 밝힌다 — 스트림이 말없이 끊기면 화면은 기다리기만 한다.
            log.exception("채팅 처리 실패 conversation=%s", conversation_id)
            await events.put(
                stream.encode(
                    "error",
                    {
                        "status": "failed",
                        "reason": "internal_error",
                        "message": templates.FAILED["internal_error"],
                    },
                )
            )
        finally:
            await events.put(None)

    task = asyncio.create_task(work())
    _running.add(task)
    task.add_done_callback(_running.discard)
    try:
        while (chunk := await events.get()) is not None:
            yield chunk
    finally:
        # 사용자가 연결을 끊어도 답·트레이스 기록은 끝까지 남긴다(기다리지는 않는다).
        if not task.done():
            log.info("클라이언트 연결 종료 — 처리는 계속한다 conversation=%s", conversation_id)


@router.post(
    "/chat",
    response_class=StreamingResponse,
    responses={
        200: {
            # 이벤트 데이터 모양을 생성 타입으로 내보내려고 문서 모델을 싣는다(본문은 SSE 다).
            "model": stream.ChatEvents,
            "content": {"text/event-stream": {}},
            "description": "SSE(TRD 14.2). 이벤트 이름별 data 모양은 ChatEvents 의 같은 이름 필드",
        }
    },
)
async def chat(body: ChatRequest, request: Request, db: Db, user: User) -> StreamingResponse:
    if body.conversation_id is not None:
        conv = await _owned(db, user, body.conversation_id)
    else:
        conv = Conversation(user_id=user.id, title=body.question.strip()[:TITLE_CHARS])
        db.add(conv)
        await db.flush()
    db.add(Message(conversation_id=conv.id, role="user", content=body.question))
    await db.commit()
    return StreamingResponse(
        _answer(request, user, conv.id, body.question),
        media_type="text/event-stream",
        # 프록시가 모아 보내지 않게 한다(첫 stage 가 바로 보여야 한다).
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/conversations", response_model=list[ConversationOut])
async def conversations(db: Db, user: User) -> list[ConversationOut]:
    rows = await db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(50)
    )
    return [ConversationOut(id=c.id, title=c.title, updated_at=c.updated_at) for c in rows]


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def messages(conversation_id: int, db: Db, user: User) -> list[MessageOut]:
    await _owned(db, user, conversation_id)
    rows = await db.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
    )
    return [
        MessageOut(
            id=m.id,
            role=m.role,  # type: ignore[arg-type]  # CHECK 제약이 값을 보장한다
            content=m.content,
            status=m.status,  # type: ignore[arg-type]
            grade=m.grade,
            meta=MessageMeta.model_validate(m.meta) if m.meta else None,
            created_at=m.created_at,
        )
        for m in rows
    ]
