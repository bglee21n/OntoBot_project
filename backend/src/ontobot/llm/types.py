"""게이트웨이가 주고받는 값. 공급자 SDK 타입을 밖으로 새지 않게 한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class CallRecord:
    """트레이스용 호출 기록(TRD 6.5 ``llm_calls``).

    외부 공급자면 Phase 5 에서 마스킹한 값을 담는다.
    """

    role: str
    model: str
    messages: list[dict[str, str]]
    output: str
    latency_ms: int
    ok: bool
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
