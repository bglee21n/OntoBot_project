"""로그 형식·마스킹·요청 ID(TRD 9.3)."""

import json
import logging

import httpx
import pytest

from ontobot import logs
from ontobot.main import create_app


def _render(record: logging.LogRecord) -> dict[str, object]:
    assert logs.MaskFilter().filter(record)
    out: dict[str, object] = json.loads(logs.JsonFormatter().format(record))
    return out


def test_json_line_masks_message_and_args() -> None:
    record = logging.LogRecord(
        "ontobot.x", logging.WARNING, __file__, 1, "연락처 %s 로 실패", ("010-1234-5678",), None
    )
    token = logs.request_id.set("req-12345678")
    try:
        out = _render(record)
    finally:
        logs.request_id.reset(token)
    assert out["msg"] == "연락처 *** 로 실패"
    assert out["level"] == "WARNING" and out["request_id"] == "req-12345678"


def test_exception_text_is_masked() -> None:
    try:
        raise ValueError("bad email a.b@example.com")
    except ValueError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "실패", None, sys.exc_info())
    out = _render(record)
    assert "a.b@example.com" not in str(out["exc"]) and "ValueError" in str(out["exc"])


@pytest.mark.parametrize(
    ("incoming", "kept"),
    [("abcdef12-3456", True), ("x", False), ("<script>alert(1)</script>", False), (None, False)],
)
async def test_request_id_header(incoming: str | None, kept: bool) -> None:
    transport = httpx.ASGITransport(app=create_app())
    headers = {"X-Request-ID": incoming} if incoming else {}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        rid = (await client.get("/healthz", headers=headers)).headers["X-Request-ID"]
    assert (rid == incoming) is kept and len(rid) >= 8
