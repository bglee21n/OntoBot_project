"""근거 대조 입력 — 조회 결과를 순수 데이터로 받는다(이 패키지는 DB·LLM 을 모른다, TRD 16.3-2)."""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Evidence:
    """레코드 하나. ``values`` 는 속성 → (원값 문자열, 표시 문자열)."""

    ref: str
    object_type: str | None  # 집계 행(q:n)은 None
    title: str
    values: Mapping[str, tuple[str, str]]


@dataclass(frozen=True)
class GroundingContext:
    evidence: Mapping[str, Evidence]
    readable: Set[str]
    # 사실 문장 판정(8.2-2)에 쓰는 낱말: 결과 객체 제목, 대상 타입의 enum 값.
    titles: Set[str] = field(default_factory=frozenset)
    enum_values: Set[str] = field(default_factory=frozenset)
    # 조회 조건에 쓰인 값(원값·표시 문자열). 문장에서 이 값을 걷어 낸 뒤 사실 여부를 본다 —
    # "2015년 이전에 설치된 철탑은 다음과 같습니다"는 조건을 되풀이할 뿐 결과에 대한 주장이 아니다.
    condition_terms: Set[str] = field(default_factory=frozenset)
