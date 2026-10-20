"""근거 발언 확인(TRD 12절) — 순수 함수.

추출기가 낸 ``quote`` 가 그 발언에 **그대로** 있는지 본다. 정규화는 NFC 와 공백(연속 공백·줄바꿈 →
공백 하나, 앞뒤 제거)뿐이다. 유사도·부분 단어 일치를 넣지 않는다 — 근거가 있다고 말하려면 사람이
발언에서 그 문장을 찾을 수 있어야 한다. 실패한 요소는 검토 화면에서 수정 없이 확정할 수 없다.
"""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
# 한 글자 인용은 어느 발언에나 있다 — 근거라고 할 수 없다.
MIN_QUOTE_CHARS = 2


def normalize(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip()


def verify(quote: str, utterance: str) -> bool:
    q = normalize(quote)
    return len(q) >= MIN_QUOTE_CHARS and q in normalize(utterance)
