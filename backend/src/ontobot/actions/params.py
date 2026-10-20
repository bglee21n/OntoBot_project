"""채팅의 Action 제안 검증(TRD 17.1-5, PRD 5.3) — 대조되지 않는 것은 미리보기 전에 막는다.

플래너는 ``ActionProposal``(Action 이름·대상을 가리키는 말·파라미터 값)만 낸다. 여기서 정하는 것:

- **대상**: 가리키는 말이 질문에 **그대로** 있거나 대화 맥락의 pk 여야 한다(지어낸 대상 금지 —
  실재하는 다른 객체를 골라도 막는다). 그다음 그 타입의 pk, 없으면 제목과 **정확히** 같은 객체를
  찾는다. 제목이 같은 객체가 여럿이면 후보로 돌려 사용자가 고른다. 유사도는 쓰지 않는다.
- **값 파라미터**(string·int·decimal·date·datetime): 질문에 그 값이 그대로 있어야 한다(근거 대조와
  같은 정규화 + 수 값 같음, ``query.coverage`` 와 같은 결). enum·bool 은 닫힌 집합이라 자료형 변환을
  통과하면 된다. 질문에 없는 값은 버리고 오류로 알린다 — 필수 값이면 사용자가 채운다.

오류는 **한꺼번에** 모아 돌려준다 — 재계획은 1회뿐이다(TRD 7.1 3단계). 재계획 뒤에도 남은 오류는
사용자에게 보이는 확인 사항이 되고, 미리보기(실행 기록)는 만들지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Set
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.actions import rules
from ontobot.grounding.normalize import contains
from ontobot.objects.reader import pks_with_value, resolve
from ontobot.ontology.models import ActionTypeDef, ObjectTypeDef, OntologyDocument
from ontobot.query.plan import ActionProposal
from ontobot.security import rbac

MAX_CANDIDATES = 10
# 닫힌 집합 — 자료형 변환(목록 안의 값인가)을 통과하면 지어낸 값일 수 없다.
_CLOSED = frozenset({"enum", "bool"})
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


def requestable(
    doc: OntologyDocument, readable: Set[str], roles: Set[str] | None
) -> frozenset[str]:
    """이 사용자가 요청할 수 있는 Action — 대상 타입을 읽을 수 있고 요청 역할을 가졌다.

    ``roles`` 가 None 이면 역할을 따지지 않는다(평가의 기본 문항 — 모든 타입을 읽는 것과 같은 뜻).
    """
    return frozenset(
        a.api_name
        for a in doc.action_types
        if a.target_type in readable
        and (roles is None or rbac.can_request_action(roles, a.requester_roles))
    )


@dataclass(frozen=True)
class Candidate:
    pk: str
    title: str


@dataclass
class ProposalCheck:
    action: ActionTypeDef
    target: ObjectTypeDef
    target_pk: str | None
    target_title: str | None
    params: dict[str, Any]  # 변환을 통과한 값(저장 표현). 빠지거나 버린 값은 없다
    errors: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.errors and self.target_pk is not None

    def as_dict(self) -> dict[str, Any]:
        """파이프라인 결과·메시지 기록·평가가 함께 쓰는 모양."""
        return {
            "action_type": self.action.api_name,
            "display_name": self.action.display_name,
            "target_type": self.target.api_name,
            "target_pk": self.target_pk,
            "target_title": self.target_title,
            "params": self.params,
            "issues": list(self.errors),
            "candidates": [{"pk": c.pk, "title": c.title} for c in self.candidates],
            "ready": self.ready,
            "run_id": None,
        }


class UnknownAction(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(errors[0])
        self.errors = errors


def _numbers(text: str) -> set[Decimal]:
    out = set()
    for n in _NUMBER.findall(_THOUSANDS.sub("", text)):
        try:
            out.add(Decimal(n))
        except InvalidOperation:  # pragma: no cover — 정규식이 숫자만 고른다
            continue
    return out


def in_question(question: str, value: Any) -> bool:
    """값이 질문에 그대로 있는가. 수는 표기(``4``·``4.0``·``1,000``)가 달라도 값이 같으면 있다."""
    if contains(question, str(value)):
        return True
    if isinstance(value, int | float | Decimal) and not isinstance(value, bool):
        try:
            return Decimal(str(value)) in _numbers(question)
        except InvalidOperation:
            return False
    return False


def ground_params(
    a: ActionTypeDef, proposal: ActionProposal, question: str
) -> tuple[dict[str, Any], list[str]]:
    """파라미터 값 → 저장 표현. 질문에 없는 값 파라미터는 버리고 오류로 알린다. 순수 함수."""
    errors: list[str] = []
    raw: dict[str, Any] = {}
    names = [p.name for p in proposal.params]
    for name in sorted({n for n in names if names.count(n) > 1}):
        errors.append(f"파라미터 '{name}' 를 두 번 넣었습니다")
    for pv in proposal.params:
        if pv.name in raw or pv.value is None:
            continue
        p = a.parameter(pv.name)
        if p is not None and p.data_type not in _CLOSED and not in_question(question, pv.value):
            errors.append(
                f"'{p.display_name}'({p.api_name}) 값 {pv.value!r} 이 질문에 없습니다. "
                "질문에 적힌 값만 넣고, 없으면 비워 두세요(사용자가 채웁니다)"
            )
            continue
        raw[pv.name] = pv.value
    params, conv = rules.convert_params(a, raw)
    for msg in conv:
        # 빠진 필수 값은 지어내라는 말로 읽히지 않게 안내를 붙인다.
        errors.append(
            f"{msg} — 질문에 값이 있으면 넣고, 없으면 비워 두세요"
            if msg.endswith("필요합니다")
            else msg
        )
    return {k: v for k, v in params.items() if v is not None}, errors


async def _resolve_target(
    db: AsyncSession,
    t: ObjectTypeDef,
    target: str | None,
    question: str,
    context: list[dict[str, str]],
) -> tuple[str | None, str | None, list[str], list[Candidate]]:
    """(pk, 제목, 오류, 후보). 못 찾으면 맥락의 같은 타입 객체를 후보로 준다."""
    ctx = [c for c in context if c["type"] == t.api_name]
    ctx_candidates = [Candidate(c["pk"], c.get("title") or c["pk"]) for c in ctx][:MAX_CANDIDATES]
    label = t.display_name
    said = (target or "").strip()
    if not said:
        return (
            None,
            None,
            [f"대상 {label}(target)이 없습니다 — 질문에 적힌 이름이나 식별자를 그대로 쓰세요"],
            ctx_candidates,
        )
    if said not in {c["pk"] for c in ctx} and not contains(question, said):
        return (
            None,
            None,
            [
                f"대상 '{said}' 이 질문에도 맥락에도 없습니다 — "
                "지어내지 말고 질문에 적힌 그대로 쓰세요"
            ],
            ctx_candidates,
        )
    declared = [t.title_property]
    found = await resolve(db, t.api_name, [said], declared=declared)
    if said in found:
        return said, str(found[said].properties.get(t.title_property) or said), [], []
    pks = await pks_with_value(db, t.api_name, t.title_property, said, MAX_CANDIDATES + 1)
    if len(pks) == 1:
        return pks[0], said, [], []
    if pks:
        return (
            None,
            None,
            [f"이름이 '{said}' 인 {label} 이 여러 개입니다 — 식별자(pk)로 고르세요"],
            [Candidate(pk, said) for pk in pks[:MAX_CANDIDATES]],
        )
    return (
        None,
        None,
        [f"'{said}' 에 해당하는 {label} 객체가 없습니다(식별자나 이름이 정확히 같아야 합니다)"],
        ctx_candidates,
    )


async def check_proposal(
    db: AsyncSession,
    doc: OntologyDocument,
    allowed: Set[str],
    proposal: ActionProposal,
    question: str,
    context: list[dict[str, str]],
) -> ProposalCheck:
    """``allowed`` 는 ``requestable`` 결과. 그 밖의 Action 이면 ``UnknownAction``."""
    a = doc.action_type(proposal.action_type) if proposal.action_type in allowed else None
    t = doc.object_type(a.target_type) if a is not None else None
    if a is None or t is None:
        names = sorted(allowed)
        hint = (
            f"다음 중에서 고르세요: {', '.join(names)}"
            if names
            else "요청할 수 있는 Action 이 없습니다"
        )
        raise UnknownAction(
            [
                f"'{proposal.action_type}' 은 action_types 에 없습니다. {hint}. "
                "맞는 Action 이 없으면 cannot_answer 를 내세요"
            ]
        )
    params, errors = ground_params(a, proposal, question)
    pk, title, target_errors, candidates = await _resolve_target(
        db, t, proposal.target, question, context
    )
    return ProposalCheck(a, t, pk, title, params, target_errors + errors, candidates)
