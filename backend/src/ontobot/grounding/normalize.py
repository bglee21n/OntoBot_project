"""비교 전 정규화(TRD 8.2-3). 이것뿐이다 — NFC, 공백 접기, 천 단위 구분 제거, 날짜 ISO 변환.

⚠ 유사도·형태소·동의어 처리를 넣지 말 것. 반쯤 맞는 대조는 지어낸 숫자를 '근거 있음'으로
통과시킨다. 잘못 붙은 근거는 근거가 없는 것보다 나쁘다.
"""

from __future__ import annotations

import re
import unicodedata

# 뒤에 숫자나 ",숫자" 가 이어지면 천 단위 표기가 아니다. 쉼표 하나는 문장 구두점일 수 있다 —
# "용량 2,400, 설치일 …" 을 놓쳐 맞는 인용이 지워졌다(Phase 4 측정에서 실측).
_THOUSANDS = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+)(?!\d|,\d)")
_DATE_DOT = re.compile(r"(?<!\d)(\d{4})[./](\d{1,2})[./](\d{1,2})(?!\d)")
_DATE_KO = re.compile(r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_SPACE = re.compile(r"\s+")


def _iso(m: re.Match[str]) -> str:
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def normalize(text: str) -> str:
    s = unicodedata.normalize("NFC", text)
    s = _THOUSANDS.sub(lambda m: m.group(1).replace(",", ""), s)
    s = _DATE_KO.sub(_iso, s)
    s = _DATE_DOT.sub(_iso, s)
    return _SPACE.sub(" ", s).strip()


def contains(sentence: str, value: str) -> bool:
    """정규화한 뒤 **그대로 포함**되는가. 빈 값·대시(값 없음 표기)는 근거가 될 수 없다."""
    v = normalize(value)
    if not v or v == "—":
        return False
    return v in normalize(sentence)
