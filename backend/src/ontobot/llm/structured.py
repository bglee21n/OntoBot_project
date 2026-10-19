"""구조화 출력 — JSON 스키마로 요청 → Pydantic 검증 → 실패하면 오류를 붙여 1회 재시도(TRD 10절).

그래도 실패하면 ``StructuredOutputError`` 를 던진다. 호출부가 ``failed(plan_invalid)`` 등으로
**밝혀서** 처리한다 — 반쯤 맞는 JSON 을 고쳐 쓰지 않는다.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from ontobot.llm.types import Message

MAX_ATTEMPTS = 2  # 최초 1회 + 재시도 1회


class StructuredOutputError(Exception):
    def __init__(self, attempts: list[str], errors: list[str]) -> None:
        super().__init__(errors[-1] if errors else "structured output failed")
        self.attempts = attempts
        self.errors = errors


def parse[M: BaseModel](text: str, schema: type[M]) -> M:
    """모델 출력 → 스키마. 코드 블록 울타리(```json)만 벗긴다 — 그 밖의 '수리'는 하지 않는다."""
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body.split("\n", 1)[1] if "\n" in body else body
        body = body.removeprefix("json").strip()
    return schema.model_validate(json.loads(body))


def retry_messages(messages: list[Message], bad_output: str, error: str) -> list[Message]:
    return [
        *messages,
        Message("assistant", bad_output),
        Message(
            "user",
            "위 출력은 요구한 JSON 스키마를 만족하지 않습니다.\n"
            f"오류: {error}\n"
            "설명 없이, 스키마를 만족하는 JSON 하나만 다시 출력하세요.",
        ),
    ]


def describe(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:5]
        )
    return f"{type(e).__name__}: {e}"
