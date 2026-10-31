"""문서 본문에서 객체 제목·pk 찾기(Phase 16c) — 순수 함수.

판정은 **그대로 포함**뿐이다(유사도·형태소·대소문자 무시 없음 — 근거 대조와 같은 원칙).
본문과 이름은 같은 정규화(NFC)를 거친다. 오탐을 줄이는 규칙은 결정론적인 것만 둔다.

- 제목은 2자 이상, pk 는 3자 이상이고 숫자만으로 된 것은 뺀다(``11`` 같은 코드는 본문의
  아무 숫자와 맞는다).
- 더 긴 다른 이름의 일치 안에 통째로 들어간 일치는 세지 않는다(``강남지사`` 안의 ``강남``).
  남은 일치가 없으면 후보가 아니다.

후보는 모델러가 확정한다 — 여기서 고르는 것은 "볼 만한 것"이지 링크가 아니다.
"""

from __future__ import annotations

import bisect
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

MIN_TITLE = 2
MIN_PK = 3

Kind = Literal["title", "pk"]


@dataclass(frozen=True)
class Name:
    object_type: str
    pk: str
    title: str | None  # PII 제목은 호출부가 None 으로 넘긴다


@dataclass(frozen=True)
class Match:
    object_type: str
    pk: str
    kind: Kind
    text: str
    occurrences: int
    first_start: int


def _needles(n: Name) -> list[tuple[Kind, str]]:
    out: list[tuple[Kind, str]] = []
    title = unicodedata.normalize("NFC", (n.title or "").strip())
    if len(title) >= MIN_TITLE:
        out.append(("title", title))
    pk = unicodedata.normalize("NFC", n.pk.strip())
    if len(pk) >= MIN_PK and not pk.isdigit() and pk != title:
        out.append(("pk", pk))
    return out


def _spans(text: str, needle: str) -> list[int]:
    starts, i = [], text.find(needle)
    while i >= 0:
        starts.append(i)
        i = text.find(needle, i + 1)
    return starts


def find(text: str, names: Sequence[Name]) -> list[Match]:
    """객체마다 하나(제목 일치가 pk 일치보다 앞선다). 처음 나온 위치 순."""
    text = unicodedata.normalize("NFC", text)
    hits: list[tuple[int, int, Name, Kind, str]] = []  # (시작, 끝, 이름, 종류, 글)
    for n in names:
        for kind, needle in _needles(n):
            hits += [(s, s + len(needle), n, kind, needle) for s in _spans(text, needle)]
    # 더 긴 일치 안에 통째로 든 일치를 뺀다(같은 구간의 같은 글은 서로를 빼지 않는다).
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    ends = [h[1] for h in hits]
    starts = [h[0] for h in hits]
    kept: list[tuple[int, int, Name, Kind, str]] = []
    for h in hits:
        s, e = h[0], h[1]
        lo = bisect.bisect_right(starts, s)  # 시작이 s 이하인 후보 중 더 긴 것이 덮나
        inside = any(
            starts[j] <= s and ends[j] >= e and ends[j] - starts[j] > e - s for j in range(lo)
        )
        if not inside:
            kept.append(h)
    by_object: dict[tuple[str, str], list[tuple[int, int, Name, Kind, str]]] = {}
    for h in kept:
        by_object.setdefault((h[2].object_type, h[2].pk), []).append(h)
    out: list[Match] = []
    for (object_type, pk), hs in by_object.items():
        best: Kind = "title" if any(h[3] == "title" for h in hs) else "pk"
        mine = [h for h in hs if h[3] == best]
        out.append(Match(object_type, pk, best, mine[0][4], len(mine), min(h[0] for h in mine)))
    return sorted(out, key=lambda m: (m.first_start, m.object_type, m.pk))


def chunk_of(chunk_starts: Sequence[int], position: int) -> int:
    """글자 위치가 든 청크 번호(청크 시작 위치 목록은 오름차순, 번호 = 순번)."""
    return max(0, bisect.bisect_right(chunk_starts, position) - 1)
