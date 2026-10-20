"""Anthropic 어댑터 — Messages API(``POST /v1/messages``)를 HTTP 로 직접 부른다.

SDK 를 들이지 않는다(폐쇄망 설치 번들을 줄이고, 외부 호출 경로를 이 파일 하나로 둔다). 외부 공급자라
게이트웨이가 PII 를 가역 토큰으로 바꾼 뒤에만 이 어댑터를 부른다(TRD 9.2).

- system 역할 메시지는 최상위 ``system`` 으로 옮긴다(Messages API 는 messages 에 system 을
  받지 않는다).
- 구조화 출력은 도구 하나를 강제 호출시키고(``tool_choice``) 그 ``input`` 을 JSON 으로 돌려준다.
  검증은 언제나 Pydantic 이 다시 한다(``llm.structured``).
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from pydantic import SecretStr

from ontobot.llm.types import Completion, Message

# 테스트가 가짜 서버로 바꿔 끼운다.
transport: httpx.AsyncBaseTransport | None = None

API_VERSION = "2023-06-01"
DEFAULT_BASE_URL = "https://api.anthropic.com"
MAX_TOKENS = 4096
_TOOL = "emit_output"


class AnthropicAdapter:
    def __init__(self, base_url: str | None, api_key: SecretStr | None) -> None:
        if api_key is None:
            raise ValueError("Anthropic 공급자는 api_key_env 가 가리키는 환경변수가 필요합니다")
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        json_schema: dict[str, Any] | None,
        timeout_s: float,
    ) -> Completion:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "temperature": 0,
            "messages": [
                {"role": m.role, "content": m.content} for m in messages if m.role != "system"
            ],
        }
        if system:
            body["system"] = system
        if json_schema is not None:
            body["tools"] = [
                {
                    "name": _TOOL,
                    "description": "요청한 JSON 스키마에 맞는 결과를 낸다.",
                    "input_schema": json_schema,
                }
            ]
            body["tool_choice"] = {"type": "tool", "name": _TOOL}
        headers = {
            "x-api-key": self.api_key.get_secret_value(),
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        started = time.perf_counter()
        async with httpx.AsyncClient(transport=transport, timeout=timeout_s) as client:
            res = await client.post(f"{self.base_url}/v1/messages", json=body, headers=headers)
            res.raise_for_status()
            data = res.json()
        blocks = data.get("content") or []
        if json_schema is not None:
            tool = next((b for b in blocks if b.get("type") == "tool_use"), None)
            text = json.dumps(tool["input"], ensure_ascii=False) if tool else ""
        else:
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        usage = data.get("usage") or {}
        return Completion(
            text=text,
            model=model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
        )
