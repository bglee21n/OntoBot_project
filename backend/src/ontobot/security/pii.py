"""PII 마스킹(TRD 9.2).

Phase 1은 **패턴 기반 마스킹**만 둔다 — 감사 로그·로그에 남는 값이 대상이다.
``property_type.pii_kind`` 기반 마스킹과 외부 LLM용 가역 토큰화는 Phase 5에서 여기에 더한다.

패턴은 전체 일치가 아니라 문장 안에서 찾는다(감사 payload·로그 문장에 섞여 들어오므로).
대신 구분자·자릿수를 엄격히 잡아 업무 코드·일시(12자리 숫자 등)를 오탐하지 않게 한다.
"""

from __future__ import annotations

import re
from typing import Any

MASK = "***"

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("rrn", re.compile(r"(?<!\d)\d{6}-[1-8]\d{6}(?!\d)")),  # 주민·외국인등록번호
    ("phone", re.compile(r"(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)")),
    ("phone", re.compile(r"(?<!\d)0\d{1,2}-\d{3,4}-\d{4}(?!\d)")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
)

# 이름만으로 값 전체를 가리는 키 — 비밀번호·토큰은 패턴과 무관하게 남기지 않는다.
_SECRET_KEYS = re.compile(r"(password|passwd|secret|token|api_key|authorization|cookie)", re.I)


def mask_text(text: str) -> str:
    for _, pattern in _PATTERNS:
        text = pattern.sub(MASK, text)
    return text


def mask_payload(value: Any) -> Any:
    """dict·list·str 를 재귀로 훑어 마스킹한 사본을 돌려준다. 원본은 바꾸지 않는다."""
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {
            k: (MASK if isinstance(k, str) and _SECRET_KEYS.search(k) else mask_payload(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [mask_payload(v) for v in value]
    return value
