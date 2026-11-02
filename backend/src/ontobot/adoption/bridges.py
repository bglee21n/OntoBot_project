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
    "need_property": "목록 값을 더할 속성(타입.속성)을 적으세요",
    "note_exists": "참고 — 설계도에 이미 있습니다",
    "note_missing": "참고 — 설계도에 없습니다. 설계도 화면에서 만드세요(보내기는 만들지 않습니다)",
    "external_conflict": "그룹에 다른 SSO 그룹 ID({current})가 있습니다 — 관리 화면에서 고칩니다",
    "external_taken": "다른 그룹({group})이 이 SSO 그룹 ID 를 씁니다",
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
    op: str  # update·create·set·add_role·add_member·set_external_id·skip·note(참고, 적용 안 함)
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
    """36.11 용어집 → 표시 이름·설명·목록 값, 흔들리는 말 → 목록 값, 36.8 설계도 후보 → 참고.

    ``API 이름`` 칸은 ``Type``·``Type.prop``·``prop`` 셋 중 하나다. ``prop`` 만 적으면 그 속성이
    **한 타입에만** 있을 때만 맞춘다. 맞는 것이 없으면 추가하지 않고 막는다(자료형·소속 타입은
    양식이 정하지 않는다 — 설계도 화면에서 만든다). 같은 속성의 목록 값은 용어집·흔들리는 말을
    합쳐 한 번에 본다(더하기만).
    """
    # 양식 행 순서대로 — 막힌 행은 그 자리에, 대상은 처음 나온 자리에 모아 보인다.
    order: list[str | Change] = []
    entries: dict[str, _Entry] = {}

    def entry(target: str, type_name: str, prop_name: str | None) -> _Entry:
        if target not in entries:
            entries[target] = _Entry(type_name, prop_name)
            order.append(target)
        return entries[target]

    for i, row in enumerate(_rows(payloads, "glossary", "terms"), start=1):
        name = _text(row, "api_name")
        if not name:
            continue
        source = f"36.11 용어집 {i}행"
        resolved = _resolve(doc, name)
        if isinstance(resolved, str):
            order.append(_skip(name, source, resolved))
            continue
        type_name, prop_name = resolved
        target = f"{type_name}.{prop_name}" if prop_name else type_name
        e = entry(target, type_name, prop_name)
        if e.row is not None:
            order.append(_skip(target, source, _why("repeated")))
            continue
        e.row, e.source = row, source
        e.add_values(split_list(_text(row, "enum_values")), source)

    for i, row in enumerate(_rows(payloads, "glossary", "unstable"), start=1):
        name, definition = _text(row, "api_name"), _text(row, "definition")
        if not name or not definition:
            continue
        source = f"36.11 흔들리는 말 {i}행"
        resolved = _resolve(doc, name)
        if isinstance(resolved, str):
            order.append(_skip(name, source, resolved))
            continue
        type_name, prop_name = resolved
        if prop_name is None:
            order.append(_skip(name, source, _why("need_property")))
            continue
        entry(f"{type_name}.{prop_name}", type_name, prop_name).add_values(
            [agreed_value(definition)], source
        )

    out: list[Change] = []
    for x in order:
        out.extend([x] if isinstance(x, Change) else _entry_changes(doc, x, entries[x]))
    out.extend(candidate_notes(payloads, doc))
    return out


class _Entry:
    """한 대상(타입 또는 타입.속성)에 모인 요청 — 용어집 행 하나와 목록 값들."""

    def __init__(self, type_name: str, prop_name: str | None) -> None:
        self.type_name, self.prop_name = type_name, prop_name
        self.row: Mapping[str, Any] | None = None
        self.source = ""
        self.values: list[tuple[str, str]] = []  # (값, 그 값을 적은 양식 자리)

    def add_values(self, values: list[str], source: str) -> None:
        known = {v for v, _ in self.values}
        self.values.extend((v, source) for v in values if v and v not in known)


def agreed_value(definition: str) -> str:
    """흔들리는 말의 합의한 정의 ``값 = 뜻`` → 값(따옴표·백틱은 뗀다). ``=`` 이 없으면 칸 전체."""
    value = definition.split("=", 1)[0] if "=" in definition else definition
    return value.strip().strip("`'\"“”‘’").strip()


def _entry_changes(doc: OntologyDocument, target: str, e: _Entry) -> list[Change]:
    t = doc.object_type(e.type_name)
    if t is None:  # _resolve 가 찾았다
        return []
    row: Mapping[str, Any] = e.row or {}
    if e.prop_name is None:
        return _field_changes(target, e.source, row, t.display_name, t.description)
    p = t.property(e.prop_name)
    if p is None:
        return []
    changes = _field_changes(target, e.source, row, p.display_name, p.description)
    if e.values:
        # 목록 값은 더하기만 — 용어집에 적은 목록이 지금 값을 빼면 막는다(흔들리는 말은 더할
        # 값만 준다). 양식 자리는 실제로 더해지는 값을 적은 곳만 보인다.
        current = p.enum_values or []
        listed = split_list(_text(row, "enum_values"))
        base = listed or current
        extra = [v for v, _ in e.values if v not in base]
        sources = [src for v, src in e.values if v not in current] or [e.values[0][1]]
        changes.append(_enum_change(target, ", ".join(dict.fromkeys(sources)), p, base + extra))
    # 바뀐 정의가 정의 문서 규칙을 지나는지 미리 본다(길이 등).
    try:
        PropertyDef.model_validate({**p.model_dump(), **_patch(changes)})
    except ValidationError as err:
        msg = str(err.errors()[0]["msg"])
        changes = [c if c.blocked is not None else replace(c, blocked=msg) for c in changes]
    return changes


# 영문 낱말이 섞인 한국어 글에서 이름이 아닌 것 — 제품 용어와 대문자 줄임말(API·DB·PK …)
_NOT_NAMES = frozenset({"Action", "Function", "OntoBot", "Neo4j", "PostgreSQL", "Excel"})
_IDENT = re.compile(r"\b[A-Z][A-Za-z0-9_]*(?:\.[a-z][a-z0-9_]*)?\b|\b[a-z][a-z0-9]*_[a-z0-9_]+\b")


def candidate_notes(payloads: Payloads, doc: OntologyDocument) -> list[Change]:
    """36.8 설계도 후보 열의 API 이름 → 참고(``op=note``, 적용하지 않는다). 있는지만 알려 준다.

    보내기는 타입·속성·Action 을 만들지 않는다 — 자료형과 규칙은 사람이 설계도 화면에서 정한다.
    """
    out = []
    seen: set[str] = set()
    for i, row in enumerate(_rows(payloads, "asis", "steps"), start=1):
        for name in _IDENT.findall(_text(row, "candidate")):
            if name in seen or name in _NOT_NAMES or name.isupper():
                continue
            seen.add(name)
            found = not isinstance(_resolve(doc, name), str)
            why = _why("note_exists") if found else _why("note_missing")
            out.append(Change("note", name, None, None, None, f"36.8 AS-IS {i}행", blocked=why))
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


# 36.4 첫 미팅 질문지에서 "찾아보는 질문" 주제 행(마지막 행) — 메모의 줄마다 질문 하나
KICKOFF_QUESTION_ROW = 11


def _question_texts(payloads: Payloads) -> list[tuple[str, str]]:
    """(질문, 양식 자리) — 관찰·개념·암묵지·첫 미팅 순서."""
    out = []
    for kind, section, key, label in QUESTION_SLOTS:
        for i, row in enumerate(_rows(payloads, kind, section), start=1):
            out.append((_text(row, key), f"{label} {i}행"))
    rows = _rows(payloads, "kickoff", "questions")
    if len(rows) > KICKOFF_QUESTION_ROW:
        memo = _text(rows[KICKOFF_QUESTION_ROW], "memo")
        for n, line in enumerate((x for x in memo.splitlines() if x.strip()), start=1):
            out.append((line.strip().lstrip("-·•* ").strip(), f"36.4 찾아보는 질문 {n}줄"))
    return out


def questions_to_golden(payloads: Payloads, existing: Iterable[str]) -> list[Change]:
    """관찰·개념·암묵지·첫 미팅 양식의 질문 → 골든 후보. 이미 있는 문항(정규화 뒤 정확 일치)은
    건너뛴다."""
    known = {normalize_question(q) for q in existing}
    out = []
    for text, source in _question_texts(payloads):
        q = normalize_question(text)
        if not q:
            continue
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
class GroupState:
    display_name: str
    external_id: str | None = None


@dataclass(frozen=True)
class UserState:
    roles: frozenset[str]
    groups: frozenset[str]  # 그룹 이름


def stakeholders_to_accounts(
    payloads: Payloads,
    *,
    users: Mapping[str, UserState],  # 사람 계정 아이디 → 지금 역할·그룹
    groups: Mapping[str, GroupState],  # 그룹 이름 → 표시 이름·SSO 그룹 ID
    known_roles: frozenset[str],
    service_usernames: frozenset[str] = frozenset(),
) -> list[Change]:
    """36.6 사람 표 → 그룹 만들기·계정 만들기·역할 더하기·그룹 구성원 넣기.

    아이디·그룹 이름은 정확 일치다. 그룹 칸은 그룹 이름 또는 이미 있는 그룹의 **표시 이름**(정확
    일치)이다. 역할은 더하기만 한다(빼지 않는다 — 권한 축소·자기 잠금은 관리 화면의 일). SSO 계정은
    만들지 않는다 — 그룹의 SSO 그룹 ID(``external_id``)만 적는다(비었을 때만, 바꾸지 않는다).
    """
    by_display = {g.display_name: n for n, g in groups.items()}
    by_external = {g.external_id: n for n, g in groups.items() if g.external_id}
    external_planned: dict[str, str] = {}  # 그룹 → 이번에 넣을 SSO 그룹 ID
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
        external = _text(row, "external_id")
        if group and external:
            out.extend(
                _external_change(group, external, source, groups, by_external, external_planned)
            )
        if group and group not in state.groups:
            out.append(Change("add_member", f"group:{group}", "member", None, username, source))
            state = UserState(state.roles, state.groups | {group})
        planned[username] = state
    return out


def _external_change(
    group: str,
    external: str,
    source: str,
    groups: Mapping[str, GroupState],
    by_external: Mapping[str, str],
    planned: dict[str, str],
) -> list[Change]:
    target = f"group:{group}"
    current = planned.get(group) or (groups[group].external_id if group in groups else None)
    if current == external:
        return (
            []
            if group in planned
            else [_skip(target, source, SAME, field="external_id", after=external)]
        )
    if current:
        why = _why("external_conflict", current=current)
        return [_skip(target, source, why, field="external_id", before=current, after=external)]
    owner = by_external.get(external) or next(
        (g for g, x in planned.items() if x == external), None
    )
    if owner and owner != group:
        why = _why("external_taken", group=owner)
        return [_skip(target, source, why, field="external_id", after=external)]
    planned[group] = external
    return [Change("set_external_id", target, "external_id", None, external, source)]
