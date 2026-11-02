"""도입 진행 판정 — 단계마다 "다음 단계에 넘길 것"이 채워졌는지(TRD 17.17). 순수 모듈.

매뉴얼 36장 단계표의 "다음 단계에 넘길 것"을 양식 칸 하나 이상에 대응시킨다. 판정은 빈 글자가
아닌지뿐이고 내용의 좋고 나쁨은 보지 않는다. 표 칸은 행 번호가 있으면 그 행만, 없으면 어느 행이든
채워졌으면 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ontobot.adoption.sheets import STAGES


@dataclass(frozen=True)
class Slot:
    kind: str
    section: str
    field: str
    row: int | None = None


@dataclass(frozen=True)
class Handoff:
    stage: int
    label: str
    slots: tuple[Slot, ...]  # 하나라도 채워지면 넘길 것이 있다


DECIDED_LABEL = "계속할지 판단"

HANDOFFS: tuple[Handoff, ...] = (
    Handoff(1, "할지 말지", (Slot("fit", "decision", "decision"),)),
    Handoff(1, "대상 업무 하나", (Slot("fit", "overview", "task"),)),
    Handoff(1, "원천 DB 접근 경로", (Slot("systems", "areas", "now", row=0),)),
    Handoff(2, "풀 문제 한 문장", (Slot("request", "problem", "statement"),)),
    Handoff(
        2,
        "계정·데이터 그룹 초안",
        (Slot("stakeholders", "people", "role"), Slot("stakeholders", "people", "group")),
    ),
    Handoff(
        3,
        "객체 후보",
        (Slot("asis", "steps", "candidate"), Slot("tacit", "hints", "found", row=0)),
    ),
    Handoff(
        3,
        "판단 기준",
        (Slot("tacit", "questions", "summary", row=0), Slot("tacit", "hints", "found", row=3)),
    ),
    Handoff(3, "골든 질문 후보", (Slot("observation", "log", "question"),)),
    Handoff(
        4,
        "게시할 설계도(draft)",
        (Slot("glossary", "terms", "display_name"), Slot("elements", "summary", "summary")),
    ),
    Handoff(4, "Action 정의", (Slot("action_flow", "steps", "rule"),)),
    Handoff(5, "계속할지 판단", (Slot("pilot", "decision", "decision"),)),
    Handoff(5, "다음 도입에 쓸 도메인 팩", (Slot("assets", "registration", "decision"),)),
)


def _filled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and bool(value.strip())


def slot_filled(payloads: dict[str, dict[str, Any]], slot: Slot) -> bool:
    section = payloads.get(slot.kind, {}).get(slot.section)
    if isinstance(section, dict):
        return _filled(section.get(slot.field))
    if isinstance(section, list):
        rows = section if slot.row is None else section[slot.row : slot.row + 1]
        return any(isinstance(r, dict) and _filled(r.get(slot.field)) for r in rows)
    return False


@dataclass(frozen=True)
class HandoffState:
    label: str
    filled: bool
    sheets: tuple[str, ...]  # 채울 양식 종류


@dataclass(frozen=True)
class StageState:
    stage: int
    title: str
    handoffs: tuple[HandoffState, ...]

    @property
    def done(self) -> bool:
        return all(h.filled for h in self.handoffs)


def evaluate(payloads: dict[str, dict[str, Any]], *, decided: bool = False) -> list[StageState]:
    """양식 종류 → 저장된 payload 로 단계별 진행. ``decided`` = 판단 기록(Phase 22)이 있다."""
    out = []
    for stage, title in STAGES.items():
        states = tuple(
            HandoffState(
                label=h.label,
                filled=any(slot_filled(payloads, s) for s in h.slots)
                or (decided and h.label == DECIDED_LABEL),
                sheets=tuple(dict.fromkeys(s.kind for s in h.slots)),
            )
            for h in HANDOFFS
            if h.stage == stage
        )
        out.append(StageState(stage=stage, title=title, handoffs=states))
    return out
