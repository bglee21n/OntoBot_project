"""문서 인용 대조(Phase 16d, TRD 17.1 결정 12) — 청크 원문과 **정확 일치**만 본다.

문서 인용 ``[[d:N/M]]`` 이 붙은 문장은 둘을 모두 만족해야 통과한다.

1. 문장의 숫자가 **모두** 그 청크에 그대로 있다 — 숫자 바로 뒤에 글자(단위)가 붙어 있으면 그
   글자까지("3회"는 "3회"로, 청크의 "위험등급 3" 으로는 안 된다), 앞뒤에 다른 숫자가 붙지 않은 채.
   정규화는 근거 대조와 같다(NFC·공백·천 단위·날짜 ISO) — "2,000회"와 "2000회"는 같고, "30"과
   "300"은 다르다.
2. 문장과 청크가 공백을 접은 글자 ``MIN_SPAN`` 자 이상을 **그대로** 함께 가진다 — 숫자가 없는
   문장("송전 정지를 요청합니다")도 청크에서 옮긴 말이 있어야 한다.

유사도·형태소·동의어를 넣지 말 것(``normalize`` 와 같은 이유). 바꿔 말한 문장이 2를 못 넘기면
그 인용은 지워진다 — 맞는 말을 놓치는 쪽이 지어낸 말을 통과시키는 쪽보다 낫다.
"""

from __future__ import annotations

import re

from ontobot.grounding.normalize import normalize

MIN_SPAN = 6
# 숫자와, 바로 뒤에 붙은 글자 하나(단위: 회·일·kV·% …). 띄어 쓴 뒤의 말은 단위로 보지 않는다.
_NUMBER = re.compile(r"(\d+(?:\.\d+)?)([^\W\d_]|%)?")


def _has_number(text: str, number: str, unit: str | None) -> bool:
    tail = re.escape(unit) if unit else r"(?![\d]|\.\d)"
    return re.search(rf"(?<![\d.]){re.escape(number)}{tail}", text) is not None


def _shares_span(sentence: str, chunk: str, size: int) -> bool:
    return any(sentence[i : i + size] in chunk for i in range(len(sentence) - size + 1))


def supports(sentence_plain: str, chunk_text: str) -> bool:
    s, t = normalize(sentence_plain), normalize(chunk_text)
    if not all(_has_number(t, n, u) for n, u in _NUMBER.findall(s)):
        return False
    return len(s) >= MIN_SPAN and _shares_span(s, t, MIN_SPAN)
