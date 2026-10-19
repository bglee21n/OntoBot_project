"""채팅 SSE 이벤트 순서·모양(TRD 14.2) — 화면이 기대는 계약이라 고정한다."""

import json

import pytest
from pydantic import TypeAdapter

from ontobot.chat import stream, templates
from ontobot.chat.pipeline import PipelineResult

IDS = {"conversation_id": 1, "message_id": 2, "trace_id": 3}


def _names(res: PipelineResult) -> list[str]:
    return [e for e, _ in stream.result_events(res, **IDS)]


@pytest.mark.parametrize(
    ("res", "names"),
    [
        (PipelineResult("ok", "답", grade=1), ["answer", "citations", "grade", "done"]),
        (PipelineResult("empty", "없음", grade=1), ["answer", "citations", "grade", "done"]),
        (
            PipelineResult("not_query", templates.NOT_QUERY),
            ["answer", "citations", "grade", "done"],
        ),
        (
            PipelineResult("failed", templates.FAILED["timeout"], failure_reason="timeout"),
            ["error", "done"],
        ),
    ],
)
def test_event_order(res: PipelineResult, names: list[str]) -> None:
    assert _names(res) == names


def test_failed_never_goes_out_as_an_answer() -> None:
    # 실패가 answer 로 나가면 화면은 그것을 0건 답처럼 그린다(TRD 7.3).
    res = PipelineResult(
        "failed", templates.FAILED["graph_unavailable"], failure_reason="graph_unavailable"
    )
    events = dict(stream.result_events(res, **IDS))
    assert "answer" not in events and "grade" not in events
    assert events["error"] == {
        "status": "failed",
        "reason": "graph_unavailable",
        "message": templates.FAILED["graph_unavailable"],
    }


def test_payload_shapes() -> None:
    res = PipelineResult(
        "ok",
        "(추정) 본문",
        grade=2,
        estimated_sentences=[0],
        citations=[{"ref": "o:Facility/F-1#name", "title": "시설", "synced_at": None}],
    )
    events = dict(stream.result_events(res, **IDS))
    assert events["answer"] == {"text": "(추정) 본문", "estimated_sentences": [0]}
    assert events["citations"] == res.citations
    assert events["grade"] == {"status": "ok", "grade": 2}
    assert events["done"] == IDS


def test_encoding_is_one_data_line() -> None:
    chunk = stream.encode("answer", {"text": "첫 줄\n둘째 줄"})
    lines = chunk.split("\n")
    assert lines[0] == "event: answer"
    assert lines[1].startswith("data: ") and json.loads(lines[1][6:])["text"] == "첫 줄\n둘째 줄"
    assert chunk.endswith("\n\n") and len([x for x in lines if x.startswith("data:")]) == 1


def test_payloads_match_the_documented_models() -> None:
    # 프론트는 OpenAPI 의 ChatEvents 로 이벤트를 읽는다 — 실제 페이로드가 그 모양이어야 한다.
    fields = stream.ChatEvents.model_fields
    results = [
        PipelineResult(
            "ok",
            "답",
            grade=1,
            citations=[{"ref": "o:Facility/F-1#name", "title": "시설", "synced_at": None}],
        ),
        PipelineResult("empty", "없음", grade=1),
        PipelineResult("not_query", templates.NOT_QUERY),
        PipelineResult("failed", templates.FAILED["timeout"], failure_reason="timeout"),
    ]
    for res in results:
        for event, data in stream.result_events(res, **IDS):
            annotation = fields[event].annotation
            assert annotation is not None
            TypeAdapter(annotation).validate_python(data)
