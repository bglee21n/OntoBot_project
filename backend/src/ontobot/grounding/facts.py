"""문장 나누기와 사실 문장 판정(TRD 8.2-1·2).

사실 문장 = 숫자·날짜를 담았거나, 조회 결과의 객체 제목·enum 값을 담은 문장. 그 밖의 연결 문장
("다음과 같습니다")은 판정에서 뺀다 — 근거가 필요 없는 말까지 근거를 요구하면 ③이 남발된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ontobot.grounding.citations import strip
from ontobot.grounding.normalize import normalize
from ontobot.grounding.types import GroundingContext

_BOUNDARY = re.compile(r"(?<=[.!?。])\s+")
_DIGIT = re.compile(r"\d")
_LEADING_CITES = re.compile(r"^(?:\s*\[\[[^\]]*\]\])+\s*")


@dataclass(frozen=True)
class Sentence:
    index: int
    raw: str  # 인용 표기 포함 원문
    sep: str  # 이 문장 뒤의 구분자(재조립용)

    @property
    def plain(self) -> str:
        return strip(self.raw)


def split(text: str) -> list[Sentence]:
    """줄 단위로 나눈 뒤, 줄 안에서 문장 끝 기호 뒤 공백으로 나눈다. 이어 붙이면 원문이 된다."""
    out: list[Sentence] = []
    lines = text.split("\n")
    for li, line in enumerate(lines):
        line_sep = "\n" if li < len(lines) - 1 else ""
        parts = _BOUNDARY.split(line)
        seps = _BOUNDARY.findall(line)
        for pi, part in enumerate(parts):
            sep = seps[pi] if pi < len(seps) else line_sep
            lead = _LEADING_CITES.match(part)
            if lead and out and strip(part):
                # "…입니다. [[o:…]] 다음 문장." — 앞머리 인용은 앞 문장의 것이다.
                prev = out[-1]
                out[-1] = Sentence(prev.index, prev.raw + prev.sep + lead.group(0).rstrip(), " ")
                part = part[lead.end() :]
            if part.strip() and not strip(part) and out:
                # 인용만 있는 조각("입니다. [[o:…]]" 의 뒤쪽)은 앞 문장의 인용이다 — 따로 세면
                # 근거가 빈 문장에 붙은 것으로 보여 언제나 대조에 실패한다(스파이크에서 실측).
                prev = out[-1]
                out[-1] = Sentence(prev.index, prev.raw + prev.sep + part, sep)
            elif part.strip():
                out.append(Sentence(len(out), part, sep))
            elif out:
                # 빈 줄은 앞 문장의 구분자에 붙여 서식을 보존한다.
                prev = out[-1]
                out[-1] = Sentence(prev.index, prev.raw, prev.sep + part + sep)
    return out


def is_fact(sentence: Sentence, ctx: GroundingContext) -> bool:
    text = normalize(sentence.plain)
    # 조건 값은 긴 것부터 걷어 낸다("2015-01-01" 을 "2015" 보다 먼저). 정확 일치만 지운다.
    for term in sorted((normalize(t) for t in ctx.condition_terms if t), key=len, reverse=True):
        text = text.replace(term, " ")
    if _DIGIT.search(text):
        return True
    words = [w for w in (*ctx.titles, *ctx.enum_values) if w]
    return any(normalize(w) in text for w in words)
