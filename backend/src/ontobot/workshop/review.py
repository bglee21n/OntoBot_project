"""요소 검토(TRD 12절) — 확정·수정·거절. 호출자의 트랜잭션 안에서 돈다.

규칙:

- **근거 확인에 실패한 요소(``evidence_ok=false``)는 수정 없이 확정할 수 없다.** 사람이 내용을
  고치면(``edited``) 그 사람이 근거를 대신 진다.
- 확정·수정 때 payload 를 종류별 모델로 엄격히 검증한다(이름 형식·자료형·enum 규칙).
- 골든 질의는 기대 답변이 있어야 확정된다(답할 수 있는 질문일 때). 현업이 확정한 기대 답변만
  평가셋에 들어간다(PRD 5.8).
- 게시된 세션의 요소는 바꾸지 않는다 — 이미 draft 버전·평가셋으로 복사됐다.
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.workshop import DraftElement, InterviewSession
from ontobot.security import audit
from ontobot.workshop import elements


class ElementNotFound(Exception):
    pass


class ElementLocked(Exception):
    """게시된 세션의 요소다."""


class EvidenceRequired(Exception):
    """근거 확인에 실패한 요소를 수정 없이 확정하려 했다."""


class ExpectedAnswerRequired(Exception):
    """기대 답변 없이 골든 질의를 확정하려 했다."""


async def get(db: AsyncSession, element_id: int) -> DraftElement:
    el = await db.get(DraftElement, element_id, with_for_update=True)
    if el is None:
        raise ElementNotFound(element_id)
    return el


def _check_golden(kind: str, payload: dict[str, Any]) -> None:
    if (
        kind == "golden_query"
        and payload.get("answerable", True)
        and not (payload.get("expected_answer") or "").strip()
    ):
        raise ExpectedAnswerRequired


def review(
    db: AsyncSession,
    session: InterviewSession,
    el: DraftElement,
    *,
    decision: Literal["confirmed", "rejected"],
    payload: dict[str, Any] | None,
    actor_id: int,
) -> DraftElement:
    if session.status == "published":
        raise ElementLocked(el.id)
    before = el.status
    if decision == "rejected":
        el.status = "rejected"
    elif payload is None:
        if not el.evidence_ok:
            raise EvidenceRequired(el.id)
        checked = elements.validate(el.kind, el.payload)
        _check_golden(el.kind, checked)
        el.payload = checked
        el.status = "confirmed"
    else:
        checked = elements.validate(el.kind, payload)
        _check_golden(el.kind, checked)
        el.payload = checked
        el.status = "edited"
    el.reviewed_by = actor_id
    audit.record(
        db,
        audit.WORKSHOP_ELEMENT_REVIEWED,
        actor_id=actor_id,
        target=f"draft_element:{el.id}",
        payload={
            "session_id": session.id,
            "kind": el.kind,
            "from": before,
            "to": el.status,
            "evidence_ok": el.evidence_ok,
        },
    )
    return el
