"""Ollama 어댑터 — ``/api/chat`` HTTP 를 직접 부른다(SDK 를 들이지 않는다).

구조화 출력은 ``format`` 에 JSON 스키마를 넘긴다. 디코더가 스키마를 강제해도 **의미까지
맞는다는 보장은 없다** — 검증은 언제나 Pydantic 이 다시 한다(``llm.structured``).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ontobot.llm.types import Completion, Message

# 테스트가 가짜 서버로 바꿔 끼운다.
transport: httpx.AsyncBaseTransport | None = None


class OllamaAdapter:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        json_schema: dict[str, Any] | None,
        timeout_s: float,
    ) -> Completion:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            # 결정적으로 — 같은 질문에 다른 계획이 나오면 평가가 흔들린다.
            "options": {"temperature": 0},
            # 사고 모델(qwen3 등)의 추론 출력을 끈다. 켜 두면 지연이 몇 배가 된다.
            "think": False,
        }
        if json_schema is not None:
            body["format"] = json_schema
        started = time.perf_counter()
        async with httpx.AsyncClient(transport=transport, timeout=timeout_s) as client:
            res = await client.post(f"{self.base_url}/api/chat", json=body)
            res.raise_for_status()
            data = res.json()
        return Completion(
            text=data["message"]["content"],
            model=model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
        )
