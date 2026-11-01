"""양식 → 제품 보내기의 미리보기(TRD 17.17 결정 A5, Phase 21). 순수 모듈 — DB·LLM 을 모른다.

보내기는 모두 미리보기 → 적용이다. 여기서는 양식 payload 와 받는 곳의 지금 상태로 **바뀔 것의 목록**
(``Change``)만 만든다. 적용(``adoption.send``)은 이 목록을 다시 계산해 막히지 않은 것만 기존 서비스
함수로 넘긴다. 같은 입력을 두 번 보내면 두 번째에는 적용할 것이 없어야 한다.

맞추기는 이름 **정확 일치**뿐이다(유사도 금지). 지우기·자료형 바꾸기는 보내기로 하지 않는다 — 사람이
설계도·관리 화면에서 한다. 막는 까닭은 ``blocked`` 에 적어 미리보기에 보인다.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from typing import Any, Literal

from pydantic import ValidationError

from ontobot.ontology.models import MEMBER_NAME, TYPE_NAME, OntologyDocument, PropertyDef

Bridge = Literal["draft", "golden", "targets", "accounts"]
BRIDGES: tuple[Bridge, ...] = ("draft", "golden", "targets", "accounts")

Payloads = Mapping[str, Mapping[str, Any]]

SAME = ""  # blocked 값 — 이미 같다(할 일 없음)

# 막는 까닭(미리보기 문구). {…} 은 str.format 이 아니라 아래 _why 의 치환이다.
WHY = {
    "no_element": "설계도에 없는 이름입니다 — 설계도 화면에서 먼저 만드세요",
    "bad_name": "API 이름 규칙(타입은 대문자·속성은 소문자로 시작하는 영문)에 맞지 않습니다",
    "ambiguous": "여러 타입에 있는 속성입니다({owners}) — 타입.속성 으로 적으세요",
    "repeated": "앞 행에서 이미 보낸 이름입니다",
    "not_enum": "목록 값 속성이 아닙니다({data_type}) — 자료형은 보내기로 바꾸지 않습니다",
    "enum_removed": "빠지는 목록 값이 있습니다({values}) — 값 빼기는 설계도 화면에서 합니다",
    "no_target": "성공 지표 목표에 없는 지표입니다 — 양식에만 남깁니다",
    "unknown_role": "모르는 역할입니다({roles}) — {known} 중에서 적으세요",
    "service": "서비스 계정입니다 — 사람 계정만 다룹니다",
    "bad_group": "없는 그룹입니다 — 새 그룹 이름은 영문 소문자로 시작하는 a-z0-9_ 입니다",
    "no_role": "새 계정에는 역할이 하나 이상 있어야 합니다",
}


def _why(key: str, **values: str) -> str:
    text = WHY[key]
    for k, v in values.items():
        text = text.replace("{" + k + "}", v)
    return text


@dataclass(frozen=True)
class Change:
    op: str  # update·create·set·add_role·add_member·skip
    target: str  # 받는 곳에서 그것을 가리키는 이름(Facility.risk_grade, group:…, accuracy …)
    field: str | None
    before: str | None
    after: str | None
    source: str  # 양식 자리("36.11 용어집 2행")
    # None = 적용한다. 글자 = 막힌 까닭(적용하지 않는다). SAME("") = 이미 같다.
    blocked: str | None = None


def _skip(target: str, source: str, why: str, **fields: str | None) -> Change:
    return Change(
        "skip",
        target,
        fields.get("field"),
        fields.get("before"),
        fields.get("after"),
        source,
        blocked=why,
    )


def applicable(changes: Iterable[Change]) -> list[Change]:
    return [c for c in changes if c.blocked is None]


def fingerprint(changes: Iterable[Change]) -> str:
    """미리보기와 적용 사이에 받는 곳이나 양식이 바뀌었는지 보는 지문."""
    body = json.dumps([asdict(c) for c in changes], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


def _rows(payloads: Payloads, kind: str, section: str) -> list[dict[str, Any]]:
    rows = payloads.get(kind, {}).get(section)
    return [r if isinstance(r, dict) else {} for r in rows] if isinstance(rows, list) else []


def _text(row: Mapping[str, Any], key: str) -> str:
    v = row.get(key)
    return v.strip() if isinstance(v, str) else ""


def split_list(text: str) -> list[str]:
    """목록 칸 — 쉼표·가운뎃점으로 나눈다(순서 유지, 겹침 제거)."""
    return list(dict.fromkeys(p.strip() for p in re.split(r"[,·]", text) if p.strip()))


# ---------------------------------------------------------------- 21a 용어 → 설계도(draft)


def glossary_to_draft(payloads: Payloads, doc: OntologyDocument) -> list[Change]:
    """36.11 용어집 → 표시 이름·설명·목록 값.

    ``API 이름`` 칸은 ``Type``·``Type.prop``·``prop`` 셋 중 하나다. ``prop`` 만 적으면 그 속성이
    **한 타입에만** 있을 때만 맞춘다. 맞는 것이 없으면 추가하지 않고 막는다(자료형·소속 타입은
    양식이 정하지 않는다 — 설계도 화면에서 만든다).
    """
    out: list[Change] = []
    seen: set[str] = set()
    for i, row in enumerate(_rows(payloads, "glossary", "terms"), start=1):
        name = _text(row, "api_name")
        if not name:
            continue
        source = f"36.11 용어집 {i}행"
        resolved = _resolve(doc, name)
        if isinstance(resolved, str):
            out.append(_skip(name, source, resolved))
            continue
        type_name, prop_name = resolved
        target = f"{type_name}.{prop_name}" if prop_name else type_name
        if target in seen:
            out.append(_skip(target, source, _why("repeated")))
            continue
        seen.add(target)
        t = doc.object_type(type_name)
        p = t.property(prop_name) if t and prop_name else None
        if t is None or (prop_name and p is None):  # _resolve 가 찾았다
            continue
        if p is None:
            out.extend(_field_changes(target, source, row, t.display_name, t.description))
            continue
        changes = _field_changes(target, source, row, p.display_name, p.description)
        values = split_list(_text(row, "enum_values"))
        if values:
            changes.append(_enum_change(target, source, p, values))
        # 바뀐 정의가 정의 문서 규칙을 지나는지 미리 본다(길이 등).
        try:
            PropertyDef.model_validate({**p.model_dump(), **_patch(changes)})
        except ValidationError as e:
            msg = str(e.errors()[0]["msg"])
            changes = [c if c.blocked is not None else replace(c, blocked=msg) for c in changes]
        out.extend(changes)
    return out


def _resolve(doc: OntologyDocument, name: str) -> tuple[str, str | None] | str:
    """→ (타입, 속성 또는 None), 또는 막는 까닭."""
    if "." in name:
        type_name, _, prop_name = name.partition(".")
        t = doc.object_type(type_name)
        if t is None or t.property(prop_name) is None:
            return _why("no_element")
        return type_name, prop_name
    if re.match(TYPE_NAME, name):
        return (name, None) if doc.object_type(name) else _why("no_element")
    if not re.match(MEMBER_NAME, name):
        return _why("bad_name")
    owners = [t.api_name for t in doc.object_types if t.property(name) is not None]
    if not owners:
        return _why("no_element")
    if len(owners) > 1:
        return _why("ambiguous", owners=", ".join(owners))
    return owners[0], name


def _field_changes(
    target: str, source: str, row: Mapping[str, Any], display: str, description: str | None
) -> list[Change]:
    out = []
    new_display = _text(row, "display_name")
    if new_display and new_display != display:
        out.append(Change("update", target, "display_name", display, new_display, source))
    new_desc = _text(row, "description")
    if new_desc and new_desc != (description or ""):
        out.append(Change("update", target, "description", description, new_desc, source))
    return out


def _enum_change(target: str, source: str, p: PropertyDef, values: list[str]) -> Change:
    before = p.enum_values or []
    shown = ", ".join(values)
    if p.data_type != "enum":
        why = _why("not_enum", data_type=p.data_type)
        return _skip(target, source, why, field="enum_values", after=shown)
    removed = [v for v in before if v not in values]
    if removed:
        why = _why("enum_removed", values=", ".join(removed))
        return _skip(
            target, source, why, field="enum_values", before=", ".join(before), after=shown
        )
    added = [v for v in values if v not in before]
    if not added:
        return _skip(target, source, SAME, field="enum_values")
    after = ", ".join(before + added)
    return Change("update", target, "enum_values", ", ".join(before), after, source)


def _patch(changes: Iterable[Change]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for c in applicable(changes):
        if c.field is None or c.after is None:
            continue
        out[c.field] = split_list(c.after) if c.field == "enum_values" else c.after
    return out


def element_patch(changes: Iterable[Change], target: str) -> dict[str, Any]:
    """적용용 — 그 대상(타입 또는 타입.속성)의 막히지 않은 변경을 정의 필드로."""
    return _patch(c for c in changes if c.target == target)


# ---------------------------------------------------------------- 21b 질문 → 골든 후보

QUESTION_SLOTS = (
    ("observation", "log", "question", "36.7 관찰 기록"),
    ("concepts", "rows", "golden", "36.10 개념 추출표"),
    ("tacit", "followups", "question", "36.9 더 확인할 것"),
)


def normalize_question(text: str) -> str:
    """NFC·공백만(골든 중복 판정 — 유사도 없음)."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def questions_to_golden(payloads: Payloads, existing: Iterable[str]) -> list[Change]:
    """관찰·개념·암묵지 양식의 질문 → 골든 후보. 이미 있는 문항(정규화 뒤 정확 일치)은 건너뛴다."""
    known = {normalize_question(q) for q in existing}
    out = []
    for kind, section, key, label in QUESTION_SLOTS:
        for i, row in enumerate(_rows(payloads, kind, section), start=1):
            q = normalize_question(_text(row, key))
            if not q:
                continue
            source = f"{label} {i}행"
            if q in known:
                out.append(_skip(q, source, SAME))
                continue
            known.add(q)
            out.append(Change("create", q, "question", None, q, source))
    return out


# ---------------------------------------------------------------- 21c KPI → 성공 지표 목표

Unit = Literal["ratio", "seconds"]

# 36.14 성공 지표 표의 행 → 지표 키. 키가 있는 시스템 지표만(근거 등급 ① 비율은 목표 지표가 아니다).
TARGET_ROWS: dict[int, tuple[str, Unit]] = {
    0: ("accuracy", "ratio"),
    2: ("p95_seconds", "seconds"),
    3: ("action_success_rate", "ratio"),
}


def parse_target(text: str, unit: Unit) -> Decimal | str:
    """→ 값, 또는 읽지 못한 까닭. 비율은 ``90%`` 나 ``0.9``, 시간은 ``25`` 나 ``25초``."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(%|초)?\s*", text)
    if not m:
        return "숫자로 읽을 수 없습니다(예: 90%, 0.9, 25초)"
    value = Decimal(m.group(1))
    mark = m.group(2)
    if unit == "ratio":
        if mark == "초":
            return "비율 지표입니다 — % 로 적으세요"
        if mark == "%":
            value = value / 100
        if value > 1:
            return "비율은 0~1 또는 0~100% 입니다"
    elif mark == "%":
        return "시간 지표입니다 — 초로 적으세요"
    return value.normalize()


def kpis_to_targets(payloads: Payloads, current: Mapping[str, Decimal | None]) -> list[Change]:
    """``current`` = 지표 키 → 지금 목표(설정값, 없으면 제안값). 평가 게이트는 건드리지 않는다."""
    out = []
    for i, row in enumerate(_rows(payloads, "pilot", "metrics")):
        text = _text(row, "target")
        if not text:
            continue
        source = f"36.14 성공 지표 {i + 1}행"
        if i not in TARGET_ROWS:
            out.append(_skip(f"row:{i + 1}", source, _why("no_target"), field="target", after=text))
            continue
        key, unit = TARGET_ROWS[i]
        value = parse_target(text, unit)
        before = current.get(key)
        before_s = None if before is None else str(before.normalize())
        if isinstance(value, str):
            out.append(_skip(key, source, value, field="target", before=before_s, after=text))
        elif before is not None and value == before:
            out.append(_skip(key, source, SAME, field="target", before=before_s, after=str(value)))
        else:
            out.append(Change("set", key, "target", before_s, str(value), source))
    return out


# ---------------------------------------------------------------- 21d 이해관계자 → 계정·그룹 초안


@dataclass(frozen=True)
class UserState:
    roles: frozenset[str]
    groups: frozenset[str]  # 그룹 이름


def stakeholders_to_accounts(
    payloads: Payloads,
    *,
    users: Mapping[str, UserState],  # 사람 계정 아이디 → 지금 역할·그룹
    groups: Mapping[str, str],  # 그룹 이름 → 표시 이름
    known_roles: frozenset[str],
    service_usernames: frozenset[str] = frozenset(),
) -> list[Change]:
    """36.6 사람 표 → 그룹 만들기·계정 만들기·역할 더하기·그룹 구성원 넣기.

    아이디·그룹 이름은 정확 일치다. 그룹 칸은 그룹 이름 또는 이미 있는 그룹의 **표시 이름**(정확
    일치)이다. 역할은 더하기만 한다(빼지 않는다 — 권한 축소·자기 잠금은 관리 화면의 일). SSO 계정은
    만들지 않는다.
    """
    by_display = {d: n for n, d in groups.items()}
    out: list[Change] = []
    new_groups: set[str] = set()
    planned: dict[str, UserState] = {}
    for i, row in enumerate(_rows(payloads, "stakeholders", "people"), start=1):
        username = _text(row, "username")
        if not username:
            continue
        source = f"36.6 이해관계자 {i}행"
        target = f"user:{username}"
        roles = split_list(_text(row, "role"))
        unknown = [r for r in roles if r not in known_roles]
        if unknown:
            why = _why(
                "unknown_role", roles=", ".join(unknown), known=", ".join(sorted(known_roles))
            )
            out.append(_skip(target, source, why, field="roles", after=", ".join(roles)))
            continue
        if username in service_usernames:
            out.append(_skip(target, source, _why("service")))
            continue
        group_text = _text(row, "group")
        group = group_text if group_text in groups else by_display.get(group_text)
        if group_text and group is None:
            if not re.match(MEMBER_NAME, group_text):
                out.append(_skip(f"group:{group_text}", source, _why("bad_group")))
                continue
            group = group_text
            if group not in new_groups:
                new_groups.add(group)
                out.append(Change("create", f"group:{group}", "group", None, group, source))
        state = planned.get(username) or users.get(username)
        if state is None:
            if not roles:
                out.append(_skip(target, source, _why("no_role")))
                continue
            out.append(
                Change("create", target, "user", None, _text(row, "name") or username, source)
            )
            out.append(Change("add_role", target, "roles", None, ", ".join(roles), source))
            state = UserState(frozenset(roles), frozenset())
        else:
            missing = [r for r in roles if r not in state.roles]
            if missing:
                before = ", ".join(sorted(state.roles))
                out.append(Change("add_role", target, "roles", before, ", ".join(missing), source))
                state = UserState(state.roles | set(missing), state.groups)
        if group and group not in state.groups:
            out.append(Change("add_member", f"group:{group}", "member", None, username, source))
            state = UserState(state.roles, state.groups | {group})
        planned[username] = state
    return out
