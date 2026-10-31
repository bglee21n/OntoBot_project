"""여러 인터뷰 세션 병합 후보(Phase 18b, PRD 5.8 P1) — 순수 함수.

세션마다 확정한 요소를 **같은 이름**(정확 일치)끼리 묶어 보여 준다. 유사도·형태소로 묶지 않는다 —
"시설"과 "설비"가 같은 것인지는 모델러가 정한다(검토 화면에서 이름을 고쳐 맞춘다).

- 묶음의 상태: ``agree``(정의까지 같다 — 병합하면 하나), ``conflict``(이름은 같은데 정의가 다르다 —
  병합하면 게시가 막힌다, ``publish.merge`` 와 같은 판정), ``single``(한 세션에만 있다).
- 이름은 다른데 **표시 이름이 정확히 같은** 요소는 ``same_display`` 로 따로 알린다 — 같은 것을
  다른 영문 이름으로 뽑았을 가능성. 자동으로 합치지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

CONFIRMED = frozenset({"confirmed", "edited"})
State = Literal["agree", "conflict", "single"]


@dataclass(frozen=True)
class Member:
    session_id: int
    element_id: int
    display_name: str


@dataclass(frozen=True)
class Group:
    kind: str
    key: str
    state: State
    members: list[Member]
    # conflict 일 때 세션마다 다른 정의 필드 이름
    differs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SameDisplay:
    kind: str
    display_name: str
    keys: list[str]


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _key(kind: str, p: dict[str, Any]) -> str:
    if kind == "property":
        return f"{_s(p.get('object_type'))}.{_s(p.get('api_name'))}"
    if kind == "action_candidate":
        return _s(p.get("name"))
    if kind == "golden_query":
        return _s(p.get("question"))
    return _s(p.get("api_name"))


# 병합에서 같아야 하는 정의 필드(``publish.merge`` 가 막는 것과 같다).
# 표시 이름·설명은 처음 것이 이긴다.
_DEFINING: dict[str, tuple[str, ...]] = {
    "property": ("data_type", "enum_values"),
    "link_type": ("from_type", "to_type"),
}


def candidates(
    sessions: Sequence[tuple[int, Sequence[tuple[int, str, dict[str, Any], str]]]],
) -> tuple[list[Group], list[SameDisplay]]:
    """``sessions`` = [(세션 id, [(요소 id, 종류, payload, 상태)])]. 확정한 요소만 본다."""
    by_key: dict[tuple[str, str], list[tuple[int, int, dict[str, Any]]]] = {}
    for sid, els in sessions:
        for eid, kind, payload, status in els:
            if status not in CONFIRMED:
                continue
            key = _key(kind, payload)
            if key.strip(".") == "":
                continue
            by_key.setdefault((kind, key), []).append((sid, eid, payload))
    groups: list[Group] = []
    for (kind, key), rows in sorted(by_key.items()):
        members = [
            Member(sid, eid, _s(p.get("display_name") or p.get("name"))) for sid, eid, p in rows
        ]
        if len({sid for sid, _, _ in rows}) < 2:
            groups.append(Group(kind, key, "single", members))
            continue
        fields = _DEFINING.get(kind, ())
        differs = [f for f in fields if len({repr(p.get(f)) for _, _, p in rows}) > 1]
        groups.append(Group(kind, key, "conflict" if differs else "agree", members, differs))

    by_display: dict[tuple[str, str], set[str]] = {}
    for (kind, key), rows in by_key.items():
        if kind not in ("object_type", "property", "link_type"):
            continue
        for _, _, p in rows:
            shown = _s(p.get("display_name")).strip()
            if shown:
                scope = _s(p.get("object_type")) if kind == "property" else ""
                by_display.setdefault((kind, f"{scope}|{shown}"), set()).add(key)
    same = [
        SameDisplay(kind, scoped.split("|", 1)[1], sorted(keys))
        for (kind, scoped), keys in sorted(by_display.items())
        if len(keys) > 1
    ]
    return groups, same
