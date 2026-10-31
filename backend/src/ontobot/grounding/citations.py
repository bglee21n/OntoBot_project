"""인용 표기 파싱(TRD 8.1) — ``[[o:Facility/F-001#risk_grade]]`` · ``[[q:1#value]]`` ·
``[[d:12/3]]``(문서 12 의 청크 3, 결정 12 — 속성이 없다)."""

from __future__ import annotations

import re
from dataclasses import dataclass

CITATION = re.compile(
    r"\[\[(?:((?:o:[A-Z][A-Za-z0-9_]{0,63}/[^#\]\s]+)|(?:q:\d+))#([a-z][a-z0-9_]{0,63})"
    r"|(d:\d{1,18}/\d{1,9}))\]\]"
)
# 형식이 깨진 인용(괄호만 맞는 것)도 지운다 — 남기면 화면에 원문 표기가 그대로 보인다.
ANY_BRACKET = re.compile(r"\[\[[^\]]*\]\]")


@dataclass(frozen=True)
class Citation:
    ref: str
    field: str  # 문서 인용은 "" — 청크 전체가 근거다

    @property
    def key(self) -> str:
        return f"{self.ref}#{self.field}" if self.field else self.ref


def find(text: str) -> list[Citation]:
    return [
        Citation(m.group(3), "") if m.group(3) else Citation(m.group(1), m.group(2))
        for m in CITATION.finditer(text)
    ]


def strip(text: str) -> str:
    """인용 표기를 모두 지운 본문(공백 정리 포함). 대조는 이 본문으로 한다."""
    return re.sub(
        r"\s+([.,!?])", r"\1", re.sub(r"[ \t]{2,}", " ", ANY_BRACKET.sub("", text))
    ).strip()
