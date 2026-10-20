"""LLM 게이트웨이(TRD 10절) — 역할(planner·answerer·…)로 부르고, 공급자·모델은 설정이 정한다.

모든 호출은 ``calls`` 에 기록된다(트레이스의 ``llm_calls``). 게이트웨이 하나 = 질의 한 건이라는
가정으로 기록을 모은다 — 파이프라인이 질의마다 새로 만든다.

PII(TRD 9.2): 호출부가 ``protect`` 로 이번 요청의 PII 값(``pii_kind`` 속성의 실제 값)을 알려 준다.
- 외부 공급자(``external: true``)면 요청을 가역 토큰(``⟦P1⟧``)으로 바꿔 보내고 응답을 되돌린다.
  호출부는 언제나 원값만 본다 — 근거 대조도 원값으로 한다.
- 호출 기록(``calls``)의 프롬프트·응답은 공급자와 무관하게 **마스킹**해서 남긴다.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ontobot.llm import structured
from ontobot.llm.adapters.anthropic import AnthropicAdapter
from ontobot.llm.adapters.base import Adapter
from ontobot.llm.adapters.ollama import OllamaAdapter
from ontobot.llm.adapters.openai_compat import OpenAICompatAdapter
from ontobot.llm.config import LlmConfig, ProviderConfig
from ontobot.llm.types import CallRecord, Completion, Message
from ontobot.security.pii import Tokenizer, mask_text
from ontobot.security.secrets import env_value

T = TypeVar("T", bound=BaseModel)


class LlmUnavailable(Exception):
    """공급자에 닿지 못했다(연결·타임아웃·HTTP 오류). 호출부가 실패로 밝힌다."""


def _adapter(p: ProviderConfig) -> Adapter:
    key = env_value(p.api_key_env) if p.api_key_env else None
    if p.adapter == "ollama":
        return OllamaAdapter(p.base_url or "http://localhost:11434")
    if p.adapter == "openai_compat":
        if not p.base_url:
            raise ValueError("openai_compat 공급자는 base_url 이 필요합니다")
        return OpenAICompatAdapter(p.base_url, key)
    return AnthropicAdapter(p.base_url, key)


@dataclass(frozen=True)
class ProviderReadiness:
    ok: bool
    # 실제로 공급자에 물어봤는가. 외부 공급자는 묻지 않는다 — 준비 확인이 주기적으로 외부 호출을
    # 만들면 폐쇄망 원칙(TRD 15절)과 과금에 어긋난다. 설정(키 존재)만 본다.
    probed: bool
    detail: str | None = None


async def check_providers(config: LlmConfig, *, timeout_s: float) -> dict[str, ProviderReadiness]:
    """역할이 쓰는 공급자마다 준비 상태(``/readyz``). Ollama 는 모델 설치 여부까지 본다."""
    used: dict[str, set[str]] = {}
    for role in config.roles.values():
        used.setdefault(role.provider, set()).add(role.model)
    out: dict[str, ProviderReadiness] = {}
    for name, models in sorted(used.items()):
        p = config.providers[name]
        if p.adapter != "ollama":
            has_key = p.api_key_env is None or env_value(p.api_key_env) is not None
            out[name] = ProviderReadiness(
                ok=has_key, probed=False, detail=None if has_key else "api_key_missing"
            )
            continue
        adapter = OllamaAdapter(p.base_url or "http://localhost:11434")
        try:
            missing = await adapter.missing_models(models, timeout_s=timeout_s)
        except (httpx.HTTPError, KeyError, ValueError) as e:
            out[name] = ProviderReadiness(ok=False, probed=True, detail=type(e).__name__)
            continue
        out[name] = ProviderReadiness(
            ok=not missing,
            probed=True,
            detail=f"missing_models: {', '.join(sorted(missing))}" if missing else None,
        )
    return out


class LLMGateway:
    def __init__(self, config: LlmConfig, *, overrides: dict[str, str] | None = None) -> None:
        self.config = config
        # 역할 → 모델 덮어쓰기. 평가·스파이크가 모델을 비교할 때만 쓴다.
        self.overrides = overrides or {}
        self.calls: list[CallRecord] = []
        self._tokens = Tokenizer()

    def protect(self, values: Iterable[str]) -> None:
        """이번 요청의 PII 값. 외부 공급자에는 토큰으로, 기록에는 마스킹해서 남긴다."""
        self._tokens.add(values)

    @property
    def protected(self) -> frozenset[str]:
        """``protect`` 로 받은 값(트레이스의 계획 마스킹에 쓴다)."""
        return self._tokens.known

    def _mask(self, text: str) -> str:
        return mask_text(text, self._tokens.known)

    def _record(
        self,
        role: str,
        model: str,
        messages: list[Message],
        output: str,
        latency_ms: int,
        *,
        external: bool,
        ok: bool,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.calls.append(
            CallRecord(
                role=role,
                model=model,
                messages=[{"role": m.role, "content": self._mask(m.content)} for m in messages],
                output=self._mask(output),
                latency_ms=latency_ms,
                ok=ok,
                error=error,
                extra={"external": external, **(extra or {})},
            )
        )

    def model_for(self, role: str) -> str:
        return self.overrides.get(role) or self.config.roles[role].model

    async def _call(
        self, role: str, messages: list[Message], schema: dict[str, Any] | None
    ) -> Completion:
        role_cfg, provider = self.config.role(role)
        model = self.model_for(role)
        external = provider.external
        sent = (
            [Message(m.role, self._tokens.tokenize(m.content)) for m in messages]
            if external
            else messages
        )
        started = time.perf_counter()
        try:
            out = await _adapter(provider).chat(
                model, sent, json_schema=schema, timeout_s=role_cfg.timeout_s
            )
        except (httpx.HTTPError, KeyError, ValueError) as e:
            self._record(
                role,
                model,
                messages,
                "",
                int((time.perf_counter() - started) * 1000),
                external=external,
                ok=False,
                error=f"{type(e).__name__}: {self._mask(str(e))}",
            )
            raise LlmUnavailable(f"{role}: {type(e).__name__}") from e
        if external:
            out = replace(out, text=self._tokens.restore(out.text))
        self._record(
            role,
            model,
            messages,
            out.text,
            out.latency_ms,
            external=external,
            ok=True,
            extra={"prompt_tokens": out.prompt_tokens, "completion_tokens": out.completion_tokens},
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
