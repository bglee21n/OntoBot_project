"""채팅 SSE 이벤트(TRD 14.2) — 순서와 모양의 정본.

진행 이벤트(``stage``·``conditions``)는 파이프라인이 단계마다 바로 낸다. 결과 이벤트는 대조가 끝난
``PipelineResult`` 하나에서 **이 함수만** 만든다 — 순서가 여러 곳에서 조립되면 언젠가 ``answer`` 가
``grade`` 보다 늦게 가거나, 실패가 ``answer`` 로 나가 0건처럼 보인다.

    성공·0건·질의 아님: answer → citations → grade → done
    Action 요청:        action_preview → done   (등급 없음 — 조회 답이 아니다)
    실패:               error → done

``failed`` 는 ``answer`` 이벤트로 나가지 않는다. 화면은 ``error`` 를 실패 모양으로 그린다(TRD 7.3).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel

from ontobot.chat.pipeline import PipelineResult

Event = tuple[str, dict[str, Any] | list[dict[str, Any]]]


# ---------------------------------------------------------------- 이벤트 데이터 모양(14.2)
# 스트림 본문은 OpenAPI 로 기술할 수 없어 ``ChatEvents`` 를 문서 모델로 싣는다 — 프론트가
# 생성 타입으로 이벤트를 읽게 하려는 것이다. 계약 테스트가 실제 페이로드를 이 모델로 검증한다.


class StageData(BaseModel):
    stage: Literal["planning", "querying", "answering", "verifying"]


class ConditionsData(BaseModel):
    text: str
    graph_as_of: str | None


class AnswerData(BaseModel):
    text: str
    estimated_sentences: list[int]


class CitationValue(BaseModel):
    name: str
    label: str
    display: str


class CitationData(BaseModel):
    ref: str
    title: str
    synced_at: str | None
    # 집계·Function 결과 행(q:N)만 — 답을 만들 때 대조한 값. 객체 근거는 카드가 다시 읽는다.
    values: list[CitationValue] | None = None


class GradeData(BaseModel):
    status: Literal["ok", "empty", "not_query"]
    grade: Literal[1, 2, 3] | None


class ProposalCandidateData(BaseModel):
    pk: str
    title: str


class ActionPreviewData(BaseModel):
    """채팅의 Action 제안(``actions.params``). ``run_id`` 가 있으면 미리보기(실행 기록)를 만들었고,
    없으면 ``issues`` 를 보고 사용자가 대상·값을 골라 Action API 로 미리보기를 요청한다."""

    text: str
    action_type: str
    display_name: str
    target_type: str
    target_pk: str | None
    target_title: str | None
    params: dict[str, Any]
    issues: list[str]
    candidates: list[ProposalCandidateData]
    ready: bool
    run_id: int | None


class ErrorData(BaseModel):
    status: Literal["failed"]
    reason: str
    message: str


class DoneData(BaseModel):
    conversation_id: int
    message_id: int
    trace_id: int


class ChatEvents(BaseModel):
    """이벤트 이름 → 데이터. 한 응답에 이 필드들이 모두 오는 것이 아니다(순서는 모듈 설명)."""

    stage: StageData
    conditions: ConditionsData
    answer: AnswerData
    citations: list[CitationData]
    grade: GradeData
    action_preview: ActionPreviewData
    error: ErrorData
    done: DoneData


def encode(event: str, data: Any) -> str:
    """SSE 한 덩어리. 데이터는 한 줄 JSON 이라 ``data:`` 줄이 하나다."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def result_events(
    res: PipelineResult, *, conversation_id: int, message_id: int, trace_id: int
) -> list[Event]:
    done: Event = (
        "done",
        {"conversation_id": conversation_id, "message_id": message_id, "trace_id": trace_id},
    )
    if res.status == "failed":
        return [
            (
                "error",
                {"status": "failed", "reason": res.failure_reason, "message": res.answer},
            ),
            done,
        ]
    if res.status == "action_preview" and res.action is not None:
        return [("action_preview", action_data(res)), done]
    return [
        ("answer", {"text": res.answer, "estimated_sentences": res.estimated_sentences}),
        ("citations", res.citations),
        ("grade", {"status": res.status, "grade": res.grade}),
        done,
    ]


def action_data(res: PipelineResult) -> dict[str, Any]:
    a = res.action or {}
    return ActionPreviewData.model_validate({**a, "text": res.answer}).model_dump(mode="json")


def message_meta(res: PipelineResult) -> dict[str, Any]:
    """대화 기록에서 답을 다시 그릴 때 쓰는 것(``message.meta``)."""
    return {
        "action": action_data(res) if res.action is not None else None,
        "conditions": res.conditions,
        "graph_as_of": res.graph_as_of,
        "citations": res.citations,
        "estimated_sentences": res.estimated_sentences,
        "failure_reason": res.failure_reason,
    }
