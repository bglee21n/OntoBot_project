"""공급자 어댑터 인터페이스. SDK·HTTP 세부는 각 어댑터 안에만 있다(TRD 16.3-4)."""

from __future__ import annotations

from typing import Any, Protocol

from ontobot.llm.types import Completion, Message


class Adapter(Protocol):
    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        json_schema: dict[str, Any] | None,
        timeout_s: float,
    ) -> Completion: ...

    async def embed(self, model: str, texts: list[str], *, timeout_s: float) -> list[list[float]]:
        """``texts`` 와 같은 순서의 벡터. 임베딩을 못 하는 공급자는 ``ValueError``."""
        ...
