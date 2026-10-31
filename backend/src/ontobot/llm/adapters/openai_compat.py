"""OpenAI 호환 어댑터 — ``POST {base_url}/chat/completions`` 를 HTTP 로 직접 부른다.

사내 vLLM·LM Studio 같은 OpenAI 호환 서버와 OpenAI 자체가 대상이다. SDK 를 들이지 않는다(폐쇄망
설치 번들을 줄인다). 구조화 출력은 ``response_format: json_schema`` — 서버가 지원하지 않아도
검증은 언제나 Pydantic 이 다시 한다(``llm.structured``).
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import SecretStr

from ontobot.llm.types import Completion, Message

# 테스트가 가짜 서버로 바꿔 끼운다.
transport: httpx.AsyncBaseTransport | None = None


class OpenAICompatAdapter:
    def __init__(self, base_url: str, api_key: SecretStr | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

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
            "temperature": 0,
        }
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": json_schema},
            }
        headers = {"Content-Type": "application/json"}
        if self.api_key is not None:
            headers["Authorization"] = f"Bearer {self.api_key.get_secret_value()}"
        started = time.perf_counter()
        async with httpx.AsyncClient(transport=transport, timeout=timeout_s) as client:
            res = await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            res.raise_for_status()
            data = res.json()
        usage = data.get("usage") or {}
        return Completion(
            text=data["choices"][0]["message"]["content"] or "",
            model=model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    async def embed(self, model: str, texts: list[str], *, timeout_s: float) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self.api_key is not None:
            headers["Authorization"] = f"Bearer {self.api_key.get_secret_value()}"
        async with httpx.AsyncClient(transport=transport, timeout=timeout_s) as client:
            res = await client.post(
                f"{self.base_url}/embeddings",
                json={"model": model, "input": texts},
                headers=headers,
            )
            res.raise_for_status()
            data = sorted(res.json()["data"], key=lambda d: d["index"])
        if len(data) != len(texts):
            raise ValueError(f"임베딩 수가 다릅니다: {len(data)} != {len(texts)}")
        return [[float(v) for v in d["embedding"]] for d in data]
