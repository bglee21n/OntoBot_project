"""외부 공급자 어댑터(TRD 10절) — 가짜 서버로 요청 모양과 응답 해석만 고정한다(네트워크 없음)."""

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from ontobot.llm.adapters import anthropic, openai_compat
from ontobot.llm.adapters.anthropic import AnthropicAdapter
from ontobot.llm.adapters.openai_compat import OpenAICompatAdapter
from ontobot.llm.config import LlmConfig
from ontobot.llm.gateway import LLMGateway, LlmUnavailable
from ontobot.llm.types import Message

MESSAGES = [Message("system", "규칙"), Message("user", "질문")]


class Out(BaseModel):
    answer: str


def _capture(reply: dict[str, Any], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=reply)

    return httpx.MockTransport(handler)


async def test_anthropic_moves_system_and_forces_a_tool_for_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []
    reply = {
        "content": [{"type": "tool_use", "name": "emit_output", "input": {"answer": "네"}}],
        "usage": {"input_tokens": 5, "output_tokens": 2},
    }
    monkeypatch.setattr(anthropic, "transport", _capture(reply, seen))
    out = await AnthropicAdapter(None, SecretStr("k")).chat(
        "m", MESSAGES, json_schema=Out.model_json_schema(), timeout_s=5
    )
    body = json.loads(seen[0].content)
    assert seen[0].url == "https://api.anthropic.com/v1/messages"
    assert seen[0].headers["x-api-key"] == "k" and seen[0].headers["anthropic-version"]
    assert body["system"] == "규칙" and body["messages"] == [{"role": "user", "content": "질문"}]
    assert body["tool_choice"] == {"type": "tool", "name": "emit_output"}
    assert json.loads(out.text) == {"answer": "네"}
    assert (out.prompt_tokens, out.completion_tokens) == (5, 2)


async def test_anthropic_plain_text(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []
    reply = {"content": [{"type": "text", "text": "안녕"}], "usage": {}}
    monkeypatch.setattr(anthropic, "transport", _capture(reply, seen))
    out = await AnthropicAdapter(None, SecretStr("k")).chat(
        "m", MESSAGES, json_schema=None, timeout_s=5
    )
    assert out.text == "안녕" and "tools" not in json.loads(seen[0].content)


def test_anthropic_without_key_is_a_configuration_error() -> None:
    with pytest.raises(ValueError, match="api_key_env"):
        AnthropicAdapter(None, None)


async def test_openai_compat_uses_json_schema_and_optional_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []
    reply = {
        "choices": [{"message": {"content": '{"answer": "네"}'}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
    }
    monkeypatch.setattr(openai_compat, "transport", _capture(reply, seen))
    await OpenAICompatAdapter("http://vllm/v1/", None).chat(
        "m", MESSAGES, json_schema=Out.model_json_schema(), timeout_s=5
    )
    body = json.loads(seen[0].content)
    assert seen[0].url == "http://vllm/v1/chat/completions"
    assert "authorization" not in seen[0].headers  # 사내 서버는 키 없이도 쓴다
    assert body["response_format"]["type"] == "json_schema"
    assert body["messages"][0] == {"role": "system", "content": "규칙"}


async def test_gateway_reports_unavailable_provider_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "overloaded"})

    monkeypatch.setattr(openai_compat, "transport", httpx.MockTransport(down))
    cfg = LlmConfig.model_validate(
        {
            "providers": {"c": {"adapter": "openai_compat", "base_url": "http://x/v1"}},
            "roles": {"answerer": {"provider": "c", "model": "m"}},
        }
    )
    llm = LLMGateway(cfg)
    with pytest.raises(LlmUnavailable):
        await llm.complete("answerer", MESSAGES)
    assert llm.calls[0].ok is False and "HTTPStatusError" in (llm.calls[0].error or "")
