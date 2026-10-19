"""LLM 게이트웨이(TRD 10절) — 역할(planner·answerer·…)로 부르고, 공급자·모델은 설정이 정한다.

모든 호출은 ``calls`` 에 기록된다(트레이스의 ``llm_calls``). 게이트웨이 하나 = 질의 한 건이라는
가정으로 기록을 모은다 — 파이프라인이 질의마다 새로 만든다.
"""

from __future__ import annotations

import json
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ontobot.llm import structured
from ontobot.llm.adapters.base import Adapter
from ontobot.llm.adapters.ollama import OllamaAdapter
from ontobot.llm.config import LlmConfig, ProviderConfig
from ontobot.llm.types import CallRecord, Completion, Message

T = TypeVar("T", bound=BaseModel)


class LlmUnavailable(Exception):
    """공급자에 닿지 못했다(연결·타임아웃·HTTP 오류). 호출부가 실패로 밝힌다."""


def _adapter(p: ProviderConfig) -> Adapter:
    if p.adapter == "ollama":
        return OllamaAdapter(p.base_url or "http://localhost:11434")
    raise NotImplementedError(f"adapter {p.adapter!r} 는 Phase 5 에서 붙인다")


class LLMGateway:
    def __init__(self, config: LlmConfig, *, overrides: dict[str, str] | None = None) -> None:
        self.config = config
        # 역할 → 모델 덮어쓰기. 평가·스파이크가 모델을 비교할 때만 쓴다.
        self.overrides = overrides or {}
        self.calls: list[CallRecord] = []

    def model_for(self, role: str) -> str:
        return self.overrides.get(role) or self.config.roles[role].model

    async def _call(
        self, role: str, messages: list[Message], schema: dict[str, Any] | None
    ) -> Completion:
        role_cfg, provider = self.config.role(role)
        model = self.model_for(role)
        started = time.perf_counter()
        try:
            out = await _adapter(provider).chat(
                model, messages, json_schema=schema, timeout_s=role_cfg.timeout_s
            )
        except (httpx.HTTPError, KeyError) as e:
            self.calls.append(
                CallRecord(
                    role=role,
                    model=model,
                    messages=[m.__dict__ for m in messages],
                    output="",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    ok=False,
                    error=f"{type(e).__name__}: {e}",
                )
            )
            raise LlmUnavailable(f"{role}: {type(e).__name__}") from e
        self.calls.append(
            CallRecord(
                role=role,
                model=model,
                messages=[m.__dict__ for m in messages],
                output=out.text,
                latency_ms=out.latency_ms,
                ok=True,
                extra={
                    "prompt_tokens": out.prompt_tokens,
                    "completion_tokens": out.completion_tokens,
                },
            )
        )
        return out

    async def complete(self, role: str, messages: list[Message]) -> Completion:
        return await self._call(role, messages, None)

    async def structured(self, role: str, messages: list[Message], schema: type[T]) -> T:
        json_schema = schema.model_json_schema()
        attempts: list[str] = []
        errors: list[str] = []
        current = messages
        for _ in range(structured.MAX_ATTEMPTS):
            out = await self._call(role, current, json_schema)
            attempts.append(out.text)
            try:
                return structured.parse(out.text, schema)
            except (ValidationError, json.JSONDecodeError, ValueError) as e:
                errors.append(structured.describe(e))
                self.calls[-1].ok = False
                self.calls[-1].error = errors[-1]
                current = structured.retry_messages(messages, out.text, errors[-1])
        raise structured.StructuredOutputError(attempts, errors)
