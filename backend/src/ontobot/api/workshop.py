"""인터뷰 워크숍 라우터(TRD 14.1 워크숍 묶음) — 검증과 위임만 한다.

규칙은 ``ontobot.workshop`` 에 있다.

접근: 세션을 만든 현업 담당자 본인, 또는 모델러. 남의 세션은 없는 세션과 같은 말로 거른다.
게시는 모델러만 한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from ontobot.api.deps import CurrentUser, Db, SettingsDep, require_role
from ontobot.api.errors import ProblemError
from ontobot.db.models.workshop import DraftElement, InterviewSession, InterviewUtterance
from ontobot.llm.config import load as load_llm_config
from ontobot.llm.gateway import LLMGateway, LlmUnavailable
from ontobot.security import rbac
from ontobot.security.rbac import ROLE_DOMAIN_EXPERT, ROLE_MODELER
from ontobot.workshop import elements, publish, review, sessions, topics
from ontobot.workshop import merge as merge_mod

router = APIRouter(prefix="/workshop", tags=["workshop"])

Member = Annotated[CurrentUser, Depends(require_role(ROLE_DOMAIN_EXPERT, ROLE_MODELER))]
Modeler = Annotated[CurrentUser, Depends(require_role(ROLE_MODELER))]

SessionStatus = Literal["open", "submitted", "published"]
ElementKind = Literal["object_type", "property", "link_type", "action_candidate", "golden_query"]
ElementStatus = Literal["proposed", "confirmed", "edited", "rejected"]


# ---------------------------------------------------------------- 요청·응답 모델


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class UtteranceIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class UtteranceOut(BaseModel):
    id: int
    speaker: Literal["agent", "interviewee"]
    text: str
    seq: int
    created_at: datetime


class TopicOut(BaseModel):
    key: str
    title: str
    status: Literal["pending", "asking", "done", "incomplete"]
    follow_ups: int


class SessionSummary(BaseModel):
    id: int
    title: str | None
    interviewee_id: int
    status: SessionStatus
    current_topic: str | None
    published_version_id: int | None
    created_at: datetime


class SessionDetail(SessionSummary):
    topics: list[TopicOut]
    utterances: list[UtteranceOut]


class EvidenceOut(BaseModel):
    utterance_id: int
    quote: str
    ok: bool


class ElementOut(BaseModel):
    id: int
    session_id: int
    kind: ElementKind
    payload: dict[str, Any]
    evidence: list[EvidenceOut]
    evidence_ok: bool
    status: ElementStatus
    reviewed_by: int | None


class ElementReview(BaseModel):
    status: Literal["confirmed", "rejected"]
    # 있으면 '수정 후 확정'(edited). 종류별 모양은 workshop.elements 가 검증한다.
    payload: dict[str, Any] | None = None


class TurnOut(BaseModel):
    answer: UtteranceOut
    question: UtteranceOut
    decision: Literal["follow_up", "next", "finish"]
    topic: str | None
    finished: bool
    added: list[ElementOut]
    updated: list[ElementOut]
    # extraction_failed / assessment_failed — 그 단계가 빠졌다는 사실을 화면이 밝힌다.
    warnings: list[str]


class PublishOut(BaseModel):
    version_id: int
    golden_set_id: int | None
    golden_items: int
    added: dict[str, list[str]]
    reused: list[str]


def _utt(u: InterviewUtterance) -> UtteranceOut:
    return UtteranceOut(
        id=u.id,
        speaker=u.speaker,  # type: ignore[arg-type]
        text=u.text,
        seq=u.seq,
        created_at=u.created_at,
    )


def _summary(s: InterviewSession) -> SessionSummary:
    return SessionSummary(
        id=s.id,
        title=s.title,
        interviewee_id=s.interviewee_id,
        status=s.status,  # type: ignore[arg-type]
        current_topic=s.topic_state.get("current"),
        published_version_id=s.published_version_id,
        created_at=s.created_at,
    )


def _topics(s: InterviewSession) -> list[TopicOut]:
    state = s.topic_state.get("topics", {})
    return [
        TopicOut(
            key=t.key,
            title=t.title,
            status=state.get(t.key, {}).get("status", "pending"),
            follow_ups=state.get(t.key, {}).get("follow_ups", 0),
        )
        for t in topics.TOPICS
    ]


def _element(e: DraftElement) -> ElementOut:
    return ElementOut(
        id=e.id,
        session_id=e.session_id,
        kind=e.kind,  # type: ignore[arg-type]
        payload=e.payload,
        evidence=[EvidenceOut.model_validate(ev) for ev in e.evidence],
        evidence_ok=e.evidence_ok,
        status=e.status,  # type: ignore[arg-type]
        reviewed_by=e.reviewed_by,
    )


async def _detail(db: Db, s: InterviewSession) -> SessionDetail:
    said = await sessions.utterances(db, s.id)
    return SessionDetail(
        **_summary(s).model_dump(), topics=_topics(s), utterances=[_utt(u) for u in said]
    )


@contextmanager
def _domain_errors() -> Iterator[None]:
    """업무 예외 → HTTP. 상태 코드 규칙을 한 곳에 둔다."""
    try:
        yield
    except sessions.SessionNotFound as e:
        raise ProblemError(404, "인터뷰를 찾을 수 없습니다", code="session_not_found") from e
    except sessions.SessionClosed as e:
        raise ProblemError(409, "진행 중인 인터뷰가 아닙니다", code="session_closed") from e
    except sessions.Unreviewed as e:
        raise ProblemError(
            409,
            "검토하지 않은 요소가 남아 있습니다",
            detail=f"{e.count}개",
            code="unreviewed_elements",
            extra={"count": e.count},
        ) from e
    except review.ElementNotFound as e:
        raise ProblemError(404, "요소를 찾을 수 없습니다", code="element_not_found") from e
    except review.ElementLocked as e:
        raise ProblemError(
            409, "게시된 인터뷰의 요소는 바꿀 수 없습니다", code="element_locked"
        ) from e
    except review.EvidenceRequired as e:
        raise ProblemError(
            409,
            "근거 발언을 확인하지 못한 요소는 수정 없이 확정할 수 없습니다",
            code="evidence_required",
        ) from e
    except review.ExpectedAnswerRequired as e:
        raise ProblemError(
            422,
            "골든 질의는 기대 답변을 적어야 확정할 수 있습니다",
            code="expected_answer_required",
        ) from e
    except elements.PayloadInvalid as e:
        raise ProblemError(
            422,
            "요소 내용이 올바르지 않습니다",
            code="invalid_element",
            extra={"issues": [{"path": "payload", "message": m} for m in e.errors]},
        ) from e
    except publish.NotSubmitted as e:
        raise ProblemError(409, "제출된 인터뷰만 게시할 수 있습니다", code="not_submitted") from e
    except publish.PublishBlocked as e:
        raise ProblemError(
            409,
            "확정 요소를 온톨로지에 합칠 수 없습니다",
            code="publish_blocked",
            extra={"issues": [{"path": i.path, "message": i.message} for i in e.issues]},
        ) from e
    except LlmUnavailable as e:
        raise ProblemError(
            503,
            "인터뷰 모델에 연결하지 못했습니다. 잠시 뒤 다시 보내 주세요",
            code="llm_unavailable",
        ) from e


async def _session(
    db: Db, user: CurrentUser, session_id: int, *, lock: bool = False
) -> InterviewSession:
    with _domain_errors():
        s = await sessions.get(db, session_id, lock=lock)
    if s.interviewee_id != user.id and not rbac.has_role(user.roles, ROLE_MODELER):
        # 남의 인터뷰는 없는 인터뷰와 같은 말로 거른다 — 있다는 사실도 정보다.
        raise ProblemError(404, "인터뷰를 찾을 수 없습니다", code="session_not_found")
    return s


# ---------------------------------------------------------------- 세션


@router.post("/sessions", status_code=201)
async def create_session(body: SessionCreate, db: Db, user: Member) -> SessionDetail:
    s, _ = await sessions.start(db, user_id=user.id, title=body.title)
    return await _detail(db, s)


@router.get("/sessions")
async def list_sessions(db: Db, user: Member) -> list[SessionSummary]:
    stmt = select(InterviewSession).order_by(InterviewSession.id.desc()).limit(200)
    if not rbac.has_role(user.roles, ROLE_MODELER):
        stmt = stmt.where(InterviewSession.interviewee_id == user.id)
    return [_summary(s) for s in await db.scalars(stmt)]


@router.get("/sessions/{session_id}")
async def get_session(session_id: int, db: Db, user: Member) -> SessionDetail:
    return await _detail(db, await _session(db, user, session_id))


@router.post("/sessions/{session_id}/utterances")
async def post_utterance(
    session_id: int, body: UtteranceIn, db: Db, user: Member, settings: SettingsDep
) -> TurnOut:
    s = await _session(db, user, session_id, lock=True)
    if s.interviewee_id != user.id:
        # 모델러는 남의 인터뷰를 볼 수 있지만 대신 답하지 않는다 — 발언은 그 사람의 근거다.
        raise ProblemError(403, "인터뷰 대상자만 답할 수 있습니다", code="not_interviewee")
    llm = LLMGateway(load_llm_config(settings.llm_config_path))
    with _domain_errors():
        turn = await sessions.answer(db, llm, s, body.text)
    return TurnOut(
        answer=_utt(turn.answer),
        question=_utt(turn.question),
        decision=turn.decision.kind,
        topic=turn.decision.topic,
        finished=turn.decision.kind == "finish",
        added=[_element(e) for e in turn.added],
        updated=[_element(e) for e in turn.updated],
        warnings=turn.warnings,
    )


@router.get("/sessions/{session_id}/elements")
async def list_elements(session_id: int, db: Db, user: Member) -> list[ElementOut]:
    s = await _session(db, user, session_id)
    return [_element(e) for e in await sessions.session_elements(db, s.id)]


@router.patch("/elements/{element_id}")
async def review_element(element_id: int, body: ElementReview, db: Db, user: Member) -> ElementOut:
    with _domain_errors():
        el = await review.get(db, element_id)
    s = await _session(db, user, el.session_id)
    with _domain_errors():
        review.review(db, s, el, decision=body.status, payload=body.payload, actor_id=user.id)
    await db.flush()
    return _element(el)


@router.post("/sessions/{session_id}/submit")
async def submit_session(session_id: int, db: Db, user: Member) -> SessionSummary:
    s = await _session(db, user, session_id, lock=True)
    with _domain_errors():
        await sessions.submit(db, s, actor_id=user.id)
    return _summary(s)


@router.post("/sessions/{session_id}/publish")
async def publish_session(session_id: int, db: Db, user: Modeler) -> PublishOut:
    s = await _session(db, user, session_id, lock=True)
    with _domain_errors():
        done = await publish.publish(
            db, s, await sessions.session_elements(db, s.id), actor_id=user.id
        )
    return PublishOut(
        version_id=done.version_id,
        golden_set_id=done.golden_set_id,
        golden_items=done.golden_items,
        added=done.added,
        reused=done.reused,
    )


# ---------------------------------------------------------------- 세션 병합(Phase 18b)


class MergeIn(BaseModel):
    session_ids: list[int] = Field(min_length=2, max_length=20)


class MergeMemberOut(BaseModel):
    session_id: int
    element_id: int
    display_name: str


class MergeGroupOut(BaseModel):
    kind: str
    key: str
    state: Literal["agree", "conflict", "single"]
    members: list[MergeMemberOut]
    differs: list[str]


class SameDisplayOut(BaseModel):
    kind: str
    display_name: str
    keys: list[str]


class MergeCandidatesOut(BaseModel):
    groups: list[MergeGroupOut]
    same_display: list[SameDisplayOut]


async def _merge_sessions(
    db: Db, user: CurrentUser, ids: list[int], *, lock: bool
) -> list[tuple[InterviewSession, list[DraftElement]]]:
    if len(set(ids)) != len(ids):
        raise ProblemError(422, "같은 인터뷰를 두 번 고를 수 없습니다", code="duplicate_session")
    out = []
    for sid in sorted(ids):  # 잠그는 순서를 고정한다(교착 방지)
        s = await _session(db, user, sid, lock=lock)
        out.append((s, await sessions.session_elements(db, s.id)))
    return out


@router.post("/merge/candidates", response_model=MergeCandidatesOut)
async def merge_candidates(body: MergeIn, db: Db, user: Modeler) -> MergeCandidatesOut:
    """고른 인터뷰들의 확정 요소를 이름 정확 일치로 묶어 보인다 — 병합하지 않는다."""
    picked = await _merge_sessions(db, user, body.session_ids, lock=False)
    groups, same = merge_mod.candidates(
        [(s.id, [(e.id, e.kind, e.payload, e.status) for e in els]) for s, els in picked]
    )
    return MergeCandidatesOut(
        groups=[MergeGroupOut.model_validate(g, from_attributes=True) for g in groups],
        same_display=[SameDisplayOut.model_validate(x, from_attributes=True) for x in same],
    )


@router.post("/merge", response_model=PublishOut)
async def merge_publish(body: MergeIn, db: Db, user: Modeler) -> PublishOut:
    """제출된 인터뷰 여럿을 draft 하나로.

    이름이 같고 정의가 다르면 막힌다(검토에서 고친 뒤 다시).
    """
    picked = await _merge_sessions(db, user, body.session_ids, lock=True)
    with _domain_errors():
        done = await publish.publish_many(db, picked, actor_id=user.id)
    return PublishOut(
        version_id=done.version_id,
        golden_set_id=done.golden_set_id,
        golden_items=done.golden_items,
        added=done.added,
        reused=done.reused,
    )


# ---------------------------------------------------------------- Action 후보(v1, PRD 5.3)


class ActionCandidateOut(BaseModel):
    """확정한 Action 후보 — 모델러가 온톨로지 편집기에서 Action 정의를 시작할 때 쓴다."""

    element_id: int
    session_id: int
    session_title: str | None
    name: str
    description: str | None
    quotes: list[str]  # 근거 발언(마스킹 저장본)


@router.get("/action-candidates", response_model=list[ActionCandidateOut])
async def action_candidates(db: Db, _: Modeler) -> list[ActionCandidateOut]:
    """검토에서 확정·수정된 Action 후보.

    게시 여부와 무관하다 — 후보는 워크숍 게시 때 온톨로지에 들어가지 않는다(TRD 12절).
    """
    rows = await db.execute(
        select(DraftElement, InterviewSession.title)
        .join(InterviewSession, InterviewSession.id == DraftElement.session_id)
        .where(
            DraftElement.kind == "action_candidate",
            DraftElement.status.in_(["confirmed", "edited"]),
        )
        .order_by(DraftElement.id.desc())
        .limit(200)
    )
    return [
        ActionCandidateOut(
            element_id=e.id,
            session_id=e.session_id,
            session_title=title,
            name=str(e.payload.get("name") or ""),
            description=e.payload.get("description"),
            quotes=[
                str(q.get("quote")) for q in e.evidence if q.get("ok", True) and q.get("quote")
            ],
        )
        for e, title in rows.all()
    ]
