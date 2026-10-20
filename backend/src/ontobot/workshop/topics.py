"""인터뷰 주제 상태기계(TRD 12절) — 순수 함수. LLM·DB 를 모른다.

순서: 역할 → 업무 흐름 → 의사결정 지점 → 사용 데이터 → 예외 상황 → 평소 찾아보는 질문.
마지막 주제는 골든 질의 초안을 모으려고 둔다(PRD 5.8 "평소 무엇을 찾아보나요?").

주제가 **채워졌다**는 판정은 두 조건의 AND 다.

1. 면담자 평가(``interviewer``)가 답을 ``vague`` 로 보지 않았다(평가가 실패하면 이 조건은 본다고 치지 않는다).
2. 그 주제가 요구하는 요소 종류가 세션에 하나 이상 추출돼 있다(예: 사용 데이터 → ``property``).

채워지지 않으면 같은 주제를 되묻는다. 되묻기는 주제마다 ``MAX_FOLLOW_UPS`` 번까지 — 넘으면
``incomplete`` 로 남기고 다음 주제로 간다(검토 화면이 빈 주제를 보여 준다). 같은 질문을 끝없이
되풀이하면 인터뷰가 멈춘 것과 같다.

질문 문장은 여기 템플릿이 정본이다. 되묻는 문장만 면담자 LLM 이 쓸 수 있고, 없으면 템플릿을 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

MAX_FOLLOW_UPS = 2

TopicStatus = Literal["pending", "asking", "done", "incomplete"]
Verdict = Literal["sufficient", "vague"]


@dataclass(frozen=True)
class Topic:
    key: str
    title: str
    opening: str
    # 요구 요소가 아직 없을 때 되묻는 문장(면담자가 문장을 주지 못했을 때도 쓴다).
    follow_up: str
    requires: frozenset[str] = frozenset()


TOPICS: tuple[Topic, ...] = (
    Topic(
        "role",
        "역할",
        "안녕하세요. 업무를 이해하려고 몇 가지 여쭙겠습니다. 어떤 일을 맡고 계신지 소개해 주세요.",
        "맡고 계신 일을 조금 더 구체적으로 말씀해 주시겠어요? 주로 무엇을 관리하시나요?",
    ),
    Topic(
        "workflow",
        "업무 흐름",
        "그 일은 보통 어떤 순서로 진행되나요? 다루는 대상(예: 시설·장비·요청)을 넣어 설명해 주세요.",
        "업무에서 직접 다루는 대상은 무엇인가요? 예를 들어 시설·장비·민원처럼 이름으로 불리는 것들입니다.",
        frozenset({"object_type"}),
    ),
    Topic(
        "decisions",
        "의사결정 지점",
        "일하시면서 무엇을 보고 판단하거나 결정하시나요? 판단한 뒤에 하는 조치도 알려 주세요.",
        "어떤 기준으로 판단하시는지 예를 하나 들어 주시겠어요?",
    ),
    Topic(
        "data",
        "사용 데이터",
        "판단할 때 확인하는 정보는 무엇인가요? 대상마다 어떤 항목(예: 등급·날짜·용량)을 보시는지 알려 주세요.",
        "대상마다 확인하는 항목을 이름으로 알려 주시겠어요? 예: 위험등급, 마지막 점검일.",
        frozenset({"property"}),
    ),
    Topic(
        "exceptions",
        "예외 상황",
        "평소와 다르게 처리해야 하는 예외 상황이 있나요? 그때는 어떻게 하시나요?",
        "예외가 생겼던 경우를 하나 예로 들어 주시겠어요?",
    ),
    Topic(
        "queries",
        "찾아보는 질문",
        "마지막으로, 평소 자주 찾아보는 질문을 실제로 묻듯이 말씀해 주세요. 기대하는 답도 함께 알려 주시면 좋습니다.",
        "자주 찾아보는 질문을 한 문장으로 말씀해 주시겠어요? 예: 위험등급 4 이상인 시설은 몇 개야?",
        frozenset({"golden_query"}),
    ),
)

CLOSING = "인터뷰를 마쳤습니다. 감사합니다. 검토 목록에서 추출한 요소를 확정·수정·거절한 뒤 제출해 주세요."

_BY_KEY = {t.key: t for t in TOPICS}


def topic(key: str) -> Topic:
    return _BY_KEY[key]


def initial_state() -> dict[str, Any]:
    return {
        "current": TOPICS[0].key,
        "topics": {
            t.key: {"status": "asking" if i == 0 else "pending", "follow_ups": 0}
            for i, t in enumerate(TOPICS)
        },
    }


def current(state: dict[str, Any]) -> Topic | None:
    key = state.get("current")
    return _BY_KEY[key] if key else None


@dataclass(frozen=True)
class Decision:
    kind: Literal["follow_up", "next", "finish"]
    question: str
    topic: str | None  # 다음 질문이 속한 주제(끝났으면 None)
    missing: frozenset[str] = frozenset()  # 아직 없는 요구 요소 종류


def decide(
    state: dict[str, Any],
    *,
    verdict: Verdict | None,
    present_kinds: set[str],
    follow_up: str | None,
) -> tuple[dict[str, Any], Decision]:
    """답 하나를 받은 뒤 다음 질문을 고른다. ``state`` 는 바꾸지 않고 새 상태를 돌려준다.

    ``verdict`` 가 None 이면 면담자 평가가 실패한 것이다 — 요구 요소만으로 판정한다.
    """
    now = current(state)
    if now is None:
        return state, Decision("finish", CLOSING, None)
    topics = {k: dict(v) for k, v in state["topics"].items()}
    entry = topics[now.key]
    missing = frozenset(now.requires - present_kinds)
    satisfied = verdict != "vague" and not missing
    if not satisfied and entry["follow_ups"] < MAX_FOLLOW_UPS:
        entry["follow_ups"] += 1
        # 요구 요소가 비었으면 그것을 겨냥한 템플릿이 낫다 — 면담자 문장은 모호함을 되묻는 데 쓴다.
        text = (follow_up or "").strip() if verdict == "vague" else ""
        question = text or now.follow_up
        return {**state, "topics": topics}, Decision("follow_up", question, now.key, missing)

    entry["status"] = "done" if satisfied else "incomplete"
    order = [t.key for t in TOPICS]
    nxt = next(
        (k for k in order[order.index(now.key) + 1 :] if topics[k]["status"] == "pending"), None
    )
    if nxt is None:
        return {**state, "current": None, "topics": topics}, Decision("finish", CLOSING, None)
    topics[nxt]["status"] = "asking"
    nxt_topic = _BY_KEY[nxt]
    return (
        {**state, "current": nxt, "topics": topics},
        Decision("next", nxt_topic.opening, nxt, missing),
    )
