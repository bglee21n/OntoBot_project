"""PII 마스킹(TRD 9.2).

두 가지를 한다.

1. **마스킹**(되돌릴 수 없음) — 감사 로그·로그·트레이스에 남기는 값. 패턴(주민번호·전화·이메일)과,
   호출부가 알려 준 값(``pii_kind`` 가 선언된 속성의 실제 값)을 ``***`` 로 바꾼다.
2. **가역 토큰화**(``Tokenizer``) — 외부 LLM 공급자로 보내는 요청에서 같은 값들을 ``⟦P1⟧`` 로
   바꾸고, 응답에서 되돌린다. 대응표는 요청 하나의 메모리에만 있다(저장·로그 금지). 근거 대조는
   되돌린 원값으로 한다(TRD 9.2) — 모델이 토큰을 망가뜨리면 원값이 돌아오지 않아 대조에 실패한다
   (안전한 쪽).

패턴은 전체 일치가 아니라 문장 안에서 찾는다(감사 payload·로그 문장에 섞여 들어오므로).
대신 구분자·자릿수를 엄격히 잡아 업무 코드·일시(12자리 숫자 등)를 오탐하지 않게 한다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

MASK = "***"
TOKEN = re.compile(r"⟦P\d+⟧")

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("rrn", re.compile(r"(?<!\d)\d{6}-[1-8]\d{6}(?!\d)")),  # 주민·외국인등록번호
    ("phone", re.compile(r"(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)")),
    ("phone", re.compile(r"(?<!\d)0\d{1,2}-\d{3,4}-\d{4}(?!\d)")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
)

# 이름만으로 값 전체를 가리는 키 — 비밀번호·토큰은 패턴과 무관하게 남기지 않는다.
_SECRET_KEYS = re.compile(r"(password|passwd|secret|token|api_key|authorization|cookie)", re.I)


def _longest_first(values: Iterable[str]) -> list[str]:
    # 긴 값부터 — "김가상" 보다 "김가상 팀장" 을 먼저 바꿔야 부분만 바뀌지 않는다.
    return sorted({v for v in values if v and v.strip()}, key=len, reverse=True)


def mask_text(text: str, values: Iterable[str] = ()) -> str:
    """패턴과 ``values``(알려진 PII 값)를 가린다. 값은 **그대로 일치**만 찾는다."""
    for v in _longest_first(values):
        text = text.replace(v, MASK)
    for _, pattern in _PATTERNS:
        text = pattern.sub(MASK, text)
    return text


def mask_contact(value: str) -> str:
    """연락처를 알아볼 만큼만 남긴다 — 메일은 첫 글자와 도메인, 전화는 끝 네 자리."""
    if "@" in value:
        local, _, domain = value.partition("@")
        return f"{local[:1]}{MASK}@{domain}"
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"{MASK}-{digits[-4:]}" if len(digits) >= 4 else MASK


class Tokenizer:
    """요청 하나 동안만 사는 가역 토큰 대응표. 같은 값은 같은 토큰이 된다."""

    def __init__(self, values: Iterable[str] = ()) -> None:
        self._known: set[str] = set()
        self._by_value: dict[str, str] = {}
        self._by_token: dict[str, str] = {}
        self.add(values)

    def add(self, values: Iterable[str]) -> None:
        # 너무 짧은 값(한 글자)은 문장 곳곳을 망가뜨린다 — 그런 값은 PII 로서도 식별력이 약하다.
        self._known.update(v for v in values if v and len(v.strip()) >= 2)

    @property
    def known(self) -> frozenset[str]:
        return frozenset(self._known)

    def _token(self, value: str) -> str:
        tok = self._by_value.get(value)
        if tok is None:
            tok = f"⟦P{len(self._by_value) + 1}⟧"
            self._by_value[value] = tok
            self._by_token[tok] = value
        return tok

    def tokenize(self, text: str) -> str:
        for v in _longest_first(self._known):
            if v in text:
                text = text.replace(v, self._token(v))
        for _, pattern in _PATTERNS:
            text = pattern.sub(lambda m: self._token(m.group(0)), text)
        return text

    def restore(self, text: str) -> str:
        return TOKEN.sub(lambda m: self._by_token.get(m.group(0), m.group(0)), text)


def mask_payload(value: Any, values: Iterable[str] = ()) -> Any:
    """dict·list·str 를 재귀로 훑어 마스킹한 사본을 돌려준다. 원본은 바꾸지 않는다."""
    if isinstance(value, str):
        return mask_text(value, values)
    if isinstance(value, dict):
        return {
            k: (MASK if isinstance(k, str) and _SECRET_KEYS.search(k) else mask_payload(v, values))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [mask_payload(v, values) for v in value]
    return value
