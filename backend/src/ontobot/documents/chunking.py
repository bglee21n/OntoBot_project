"""글 → 청크(Phase 16) — 순수 함수.

문단(빈 줄로 나뉜 덩어리)을 ``max_chars`` 까지 이어 붙인다. 문단 하나가 넘치면 문장 끝(``.``·``?``·
``!`` 뒤 공백)에서, 그래도 넘치면 글자 수로 자른다. **겹침이 없고**, 각 청크는 원문의 연속된
부분 문자열이다(``text[start:start+len(chunk)] == chunk``) — 문서 인용의 근거 대조가 청크 원문을
그대로 찾기 때문이다(TRD 17.1 결정 12).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_CHARS = 800
_PARAGRAPH = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.?!。])\s+")


@dataclass(frozen=True)
class Chunk:
    no: int
    start: int
    text: str


def _spans(text: str) -> list[tuple[int, int]]:
    """문단의 (시작, 끝) — 앞뒤 공백 없이."""
    out = []
    pos = 0
    for m in [*_PARAGRAPH.finditer(text), None]:
        end = m.start() if m else len(text)
        seg = text[pos:end]
        lead = len(seg) - len(seg.lstrip())
        trail = len(seg.rstrip())
        if trail > lead:
            out.append((pos + lead, pos + trail))
        if m:
            pos = m.end()
    return out


def _split_long(text: str, start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    """긴 문단 하나 → 문장 경계에서(없으면 글자 수로) 자른 조각들."""
    pieces: list[tuple[int, int]] = []
    s = start
    while end - s > max_chars:
        window = text[s : s + max_chars]
        cuts = [m.start() for m in _SENTENCE_END.finditer(window)]
        cut = cuts[-1] if cuts and cuts[-1] > 0 else max_chars
        piece = text[s : s + cut].rstrip()
        pieces.append((s, s + len(piece)))
        s += cut
        while s < end and text[s].isspace():
            s += 1
    if end > s:
        pieces.append((s, end))
    return pieces


def chunk(text: str, max_chars: int = MAX_CHARS) -> list[Chunk]:
    spans: list[tuple[int, int]] = []
    for a, b in _spans(text):
        spans.extend(_split_long(text, a, b, max_chars) if b - a > max_chars else [(a, b)])
    out: list[Chunk] = []
    cur: tuple[int, int] | None = None
    for a, b in spans:
        if cur is not None and b - cur[0] <= max_chars:
            cur = (cur[0], b)
            continue
        if cur is not None:
            out.append(Chunk(len(out), cur[0], text[cur[0] : cur[1]]))
        cur = (a, b)
    if cur is not None:
        out.append(Chunk(len(out), cur[0], text[cur[0] : cur[1]]))
    return out
