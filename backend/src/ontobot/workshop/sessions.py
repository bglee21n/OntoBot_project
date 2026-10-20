"""인터뷰 세션 — 시작·답 한 턴·제출(TRD 12절). 호출자의 트랜잭션 안에서 돈다.

답 한 턴:

1. 답을 **마스킹해서** 저장한다(TRD 9.2 — 패턴 기반). 이후 추출·평가·근거 확인 모두 이 마스킹 문장을
   본다. 근거 ``quote`` 가 저장된 발언에 그대로 있어야 하므로 원문과 저장본이 갈라지면 안 된다.
2. 추출기 → 근거 확인 → 요소 후보 저장. 같은 종류·같은 이름(정확 일치)이 이미 있으면 새로 만들지
   않고 근거만 덧붙인다.
3. 면담자 평가 → 주제 상태기계가 다음 질문을 고른다.

공급자에 닿지 못하면(``LlmUnavailable``) 턴 전체를 실패시킨다 — 호출부가 롤백하므로 답도 남지 않고
사용자가 다시 보낸다. 모델이 스키마를 두 번 어기면(``StructuredOutputError``) 그 단계만 빠진 채
진행하고 ``warnings`` 로 밝힌다(추출 실패를 "요소 없음"처럼 보이게 하지 않는다).

같은 세션에 답이 동시에 두 개 오면 ``seq`` 가 겹친다 — 세션 행을 ``FOR UPDATE`` 로 잠가 줄을 세운다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.workshop import DraftElement, InterviewSession, InterviewUtterance
from ontobot.llm.structured import StructuredOutputError
from ontobot.llm.types import Message
from ontobot.security import audit
from ontobot.security.pii import mask_text
from ontobot.workshop import elements, evidence, extractor, interviewer, topics

log = logging.getLogger(__name__)

WARN_EXTRACTION = "extraction_failed"
WARN_ASSESSMENT = "assessment_failed"


class SessionNotFound(Exception):
    pass


class SessionClosed(Exception):
    """열린 세션이 아니거나 인터뷰가 이미 끝났다."""


class Unreviewed(Exception):
    def __init__(self, count: int) -> None:
        super().__init__(f"{count} element(s) not reviewed")
        self.count = count


async def get(db: AsyncSession, session_id: int, *, lock: bool = False) -> InterviewSession:
    stmt = select(InterviewSession).where(InterviewSession.id == session_id)
    if lock:
        stmt = stmt.with_for_update()
    s = await db.scalar(stmt)
    if s is None:
        raise SessionNotFound(session_id)
    return s


async def utterances(db: AsyncSession, session_id: int) -> list[InterviewUtterance]:
    rows = await db.scalars(
        select(InterviewUtterance)
        .where(InterviewUtterance.session_id == session_id)
        .order_by(InterviewUtterance.seq)
    )
    return list(rows)


async def session_elements(db: AsyncSession, session_id: int) -> list[DraftElement]:
    rows = await db.scalars(
        select(DraftElement).where(DraftElement.session_id == session_id).order_by(DraftElement.id)
    )
    return list(rows)


async def _say(db: AsyncSession, session_id: int, speaker: str, text: str) -> InterviewUtterance:
    last = await db.scalar(
        select(func.coalesce(func.max(InterviewUtterance.seq), 0)).where(
            InterviewUtterance.session_id == session_id
        )
    )
    u = InterviewUtterance(
        session_id=session_id, speaker=speaker, text=mask_text(text), seq=int(last or 0) + 1
    )
    db.add(u)
    await db.flush()
    return u


async def start(
    db: AsyncSession, *, user_id: int, title: str | None
) -> tuple[InterviewSession, InterviewUtterance]:
    s = InterviewSession(
        interviewee_id=user_id,
        title=mask_text(title) if title else None,
        topic_state=topics.initial_state(),
        status="open",
    )
    db.add(s)
    await db.flush()
    first = await _say(db, s.id, "agent", topics.TOPICS[0].opening)
    audit.record(db, audit.WORKSHOP_STARTED, actor_id=user_id, target=f"interview_session:{s.id}")
    return s, first


@dataclass
class Turn:
    answer: InterviewUtterance
    question: InterviewUtterance
    decision: topics.Decision
    added: list[DraftElement] = field(default_factory=list)
    updated: list[DraftElement] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class TurnLLM(Protocol):
    """추출기·면담자가 함께 쓰는 구조화 출력 호출(``LLMGateway.structured`` 와 같은 모양)."""

    async def structured[M: BaseModel](
        self, role: str, messages: list[Message], schema: type[M]
    ) -> M: ...


async def answer(db: AsyncSession, llm: TurnLLM, session: InterviewSession, text: str) -> Turn:
    """답 하나를 처리한다. ``session`` 은 ``get(..., lock=True)`` 로 잠근 행이어야 한다."""
    topic = topics.current(session.topic_state)
    if session.status != "open" or topic is None:
        raise SessionClosed(session.id)
    said = await utterances(db, session.id)
    question = next((u.text for u in reversed(said) if u.speaker == "agent"), topic.opening)
    reply = await _say(db, session.id, "interviewee", text)
    turn_warnings: list[str] = []

    existing = await session_elements(db, session.id)
    known_types = [
        f"{e.payload.get('api_name')}: {e.payload.get('display_name')}"
        for e in existing
        if e.kind == "object_type" and e.status != "rejected"
    ]
    try:
        found = await extractor.extract(llm, topic, question, reply.text, known_types)
    except StructuredOutputError as e:
        log.warning("요소 추출 실패 session=%s: %s", session.id, e)
        found = []
        turn_warnings.append(WARN_EXTRACTION)

    by_key = {(e.kind, elements.key(e.kind, e.payload)): e for e in existing}
    added: list[DraftElement] = []
    updated: list[DraftElement] = []
    for c in found:
        ok = evidence.verify(c.quote, reply.text)
        ev = {"utterance_id": reply.id, "quote": c.quote, "ok": ok}
        k = (c.kind, elements.key(c.kind, c.payload))
        if not k[1]:
            continue  # 이름이 빈 후보는 가리킬 수도, 검토할 수도 없다
        same = by_key.get(k)
        if same is not None:
            # JSONB 는 새 리스트를 대입해야 변경으로 잡힌다.
            same.evidence = [*same.evidence, ev]
            same.evidence_ok = same.evidence_ok or ok
            if same not in updated and same not in added:
                updated.append(same)
            continue
        el = DraftElement(
            session_id=session.id,
            kind=c.kind,
            payload=c.payload,
            evidence=[ev],
            evidence_ok=ok,
            status="proposed",
        )
        db.add(el)
        by_key[k] = el
        added.append(el)
    await db.flush()

    present = {k for (k, _), e in by_key.items() if e.status != "rejected"}
    verdict: topics.Verdict | None = None
    follow_up: str | None = None
    try:
        assessed = await interviewer.assess(llm, topic, question, reply.text)
        verdict, follow_up = assessed.verdict, assessed.follow_up
    except StructuredOutputError as e:
        log.warning("답 평가 실패 session=%s: %s", session.id, e)
        turn_warnings.append(WARN_ASSESSMENT)

    state, decision = topics.decide(
        session.topic_state, verdict=verdict, present_kinds=present, follow_up=follow_up
    )
    session.topic_state = state
    asked = await _say(db, session.id, "agent", decision.question)
    return Turn(reply, asked, decision, added, updated, turn_warnings)


async def submit(db: AsyncSession, session: InterviewSession, *, actor_id: int) -> None:
    """검토를 마치고 모델러에게 넘긴다. 검토하지 않은 요소가 남으면 넘기지 않는다."""
    if session.status != "open":
        raise SessionClosed(session.id)
    left = await db.scalar(
        select(func.count())
        .select_from(DraftElement)
        .where(DraftElement.session_id == session.id, DraftElement.status == "proposed")
    )
    if left:
        raise Unreviewed(int(left))
    session.status = "submitted"
    audit.record(
        db,
        audit.WORKSHOP_SUBMITTED,
        actor_id=actor_id,
        target=f"interview_session:{session.id}",
        payload={"topics": session.topic_state.get("topics", {})},
    )
