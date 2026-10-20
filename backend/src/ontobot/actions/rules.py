"""Action 규칙 — 파라미터 변환·사전 조건·바뀔 값·문구 치환. 순수 함수(DB·LLM 을 모른다)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ontobot.ontology.models import (
    PLACEHOLDER_RE,
    ActionCondition,
    ActionParamDef,
    ActionTypeDef,
    ObjectTypeDef,
    PropertyDef,
)
from ontobot.query.records import display
from ontobot.query.validator import coerce
from ontobot.sync.mapping import to_value


def _as_prop(p: ActionParamDef) -> PropertyDef:
    return PropertyDef(
        api_name=p.api_name,
        display_name=p.display_name,
        data_type=p.data_type,
        enum_values=p.enum_values,
        nullable=not p.required,
    )


def convert_params(a: ActionTypeDef, raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """요청 파라미터 → 저장 표현(객체 속성과 같은 JSON 표현). 오류는 **한꺼번에** 돌려준다."""
    errors: list[str] = []
    out: dict[str, Any] = {}
    for name in sorted(set(raw) - {p.api_name for p in a.parameters}):
        errors.append(f"알 수 없는 파라미터: {name}")
    for p in a.parameters:
        value = raw.get(p.api_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            if p.required:
                errors.append(f"'{p.display_name}' 값이 필요합니다")
            out[p.api_name] = None
            continue
        try:
            out[p.api_name] = to_value(_as_prop(p), value)
        except ValueError as e:
            errors.append(f"'{p.display_name}': {e}")
    return out, errors


@dataclass(frozen=True)
class ConditionResult:
    property: str
    display_name: str
    op: str
    expected: str
    actual: str
    ok: bool


def _cmp(prop: PropertyDef, op: str, actual: Any, expected: Any) -> bool:
    if op == "is_null":
        return actual is None
    if op == "not_null":
        return actual is not None
    if actual is None:
        return False
    a = coerce(prop, actual)
    if op == "in":
        values = expected if isinstance(expected, list) else [expected]
        return a in [coerce(prop, v) for v in values]
    e = coerce(prop, expected)
    ops = {
        "eq": a == e,
        "ne": a != e,
        "gt": a > e,
        "gte": a >= e,
        "lt": a < e,
        "lte": a <= e,
    }
    return bool(ops[op])


def check_conditions(
    t: ObjectTypeDef, conditions: list[ActionCondition], visible: dict[str, Any]
) -> list[ConditionResult]:
    """대상 객체의 **보이는 값**(편집 레이어 포함)으로 사전 조건을 평가한다."""
    out = []
    for c in conditions:
        prop = t.property(c.property)
        if prop is None:  # 게시 전 검사가 막는다
            out.append(ConditionResult(c.property, c.property, c.op, str(c.value), "—", False))
            continue
        actual = visible.get(c.property)
        try:
            ok = _cmp(prop, c.op, actual, c.value)
        except (ValueError, TypeError):
            ok = False
        expected = (
            ", ".join(display(prop, v) for v in c.value)
            if isinstance(c.value, list)
            else display(prop, c.value)
        )
        out.append(
            ConditionResult(
                c.property, prop.display_name, c.op, expected, display(prop, actual), ok
            )
        )
    return out


@dataclass(frozen=True)
class Change:
    property: str
    display_name: str
    before: Any  # 저장 표현
    after: Any
    before_display: str
    after_display: str


def planned_changes(
    a: ActionTypeDef, t: ObjectTypeDef, params: dict[str, Any], visible: dict[str, Any]
) -> list[Change]:
    """바뀔 값. 상수는 속성 자료형으로 바꿔 저장 표현으로 둔다(게시 전 검사가 자료형을 맞췄다)."""
    out = []
    for c in a.changes:
        prop = t.property(c.property)
        if prop is None:
            continue
        after = params.get(c.param) if c.param is not None else to_value(prop, c.value)
        before = visible.get(c.property)
        out.append(
            Change(
                c.property,
                prop.display_name,
                before,
                after,
                display(prop, before),
                display(prop, after),
            )
        )
    return out


def render(template: str, values: dict[str, str]) -> str:
    """``{이름}`` 을 값으로 바꾼다. 모르는 이름은 빈 문자열(게시 전 검사가 막는다).

    ``str.format`` 을 쓰지 않는다 — 형식 지정자·속성 접근(``{x.__class__}``)이 끼어들 수 없게.
    """
    return PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), ""), template)


def template_values(
    a: ActionTypeDef, t: ObjectTypeDef, params: dict[str, Any], target_pk: str, title: str
) -> dict[str, str]:
    values = {"action": a.display_name, "target": title, "target_pk": target_pk}
    for p in a.parameters:
        values[p.api_name] = display(_as_prop(p), params.get(p.api_name))
    return values
