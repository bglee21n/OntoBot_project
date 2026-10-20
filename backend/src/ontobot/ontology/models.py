"""온톨로지 정의 문서(Pydantic) — 임포트·익스포트·버전 비교·레지스트리가 함께 쓰는 한 모양.

검증은 두 층이다.

1. **요소 규칙**(모델 검증기): 식별자 형식, enum 규칙, 속성 이름 중복. 언제나 강제한다.
2. **교차 참조**(``check_document``): pk·제목 속성 실재, 링크 양 끝 타입 실재, FK 속성 실재.
   draft 는 조각조각 편집되므로 중간 상태가 일시적으로 어긋날 수 있다 — 그래서 이것은
   **임포트와 게시 시점에** 강제하고, 편집 중에는 화면에 '게시 전 검사'로 보여 준다.

타입 ``api_name`` 은 Neo4j 라벨이 된다. 라벨은 바인드할 수 없으므로 이 형식 검사가 Cypher
조립 안전성의 근거다(TRD v0.2 정정 7). 정규식을 느슨하게 하지 말 것.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

TYPE_NAME = r"^[A-Z][A-Za-z0-9_]{0,63}$"
MEMBER_NAME = r"^[a-z][a-z0-9_]{0,63}$"

TypeName = Annotated[str, StringConstraints(pattern=TYPE_NAME)]
MemberName = Annotated[str, StringConstraints(pattern=MEMBER_NAME)]
DisplayName = Annotated[str, StringConstraints(min_length=1, max_length=128, strip_whitespace=True)]

DataType = Literal["string", "int", "decimal", "bool", "date", "datetime", "enum"]
PiiKind = Literal["name", "phone", "email", "rrn", "address", "account", "other"]
Cardinality = Literal["one", "many"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PropertyDef(_Strict):
    api_name: MemberName
    display_name: DisplayName
    description: str | None = None
    data_type: DataType
    enum_values: list[str] | None = None
    nullable: bool = True
    indexed: bool = False
    pii_kind: PiiKind | None = None

    @model_validator(mode="after")
    def _enum_rules(self) -> PropertyDef:
        if self.data_type == "enum":
            if not self.enum_values:
                raise ValueError("enum 속성은 enum_values 가 하나 이상 있어야 합니다")
            if len(set(self.enum_values)) != len(self.enum_values):
                raise ValueError("enum_values 에 중복 값이 있습니다")
        elif self.enum_values is not None:
            raise ValueError("enum 이 아닌 속성에는 enum_values 를 둘 수 없습니다")
        return self


class ObjectTypeDef(_Strict):
    api_name: TypeName
    display_name: DisplayName
    description: str | None = None
    pk_property: MemberName
    title_property: MemberName
    properties: list[PropertyDef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_properties(self) -> ObjectTypeDef:
        names = [p.api_name for p in self.properties]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"속성 이름이 중복됩니다: {', '.join(dup)}")
        return self

    def property(self, name: str) -> PropertyDef | None:
        return next((p for p in self.properties if p.api_name == name), None)


class LinkSource(_Strict):
    """링크를 원천에서 어떻게 만드는가. ``join_mapping`` 의 상세는 Phase 2(동기화)에서 정한다."""

    kind: Literal["fk_property", "join_mapping"]
    property: MemberName | None = None  # fk_property: from_type 의 FK 속성
    config: dict[str, Any] | None = None  # join_mapping

    @model_validator(mode="after")
    def _shape(self) -> LinkSource:
        if self.kind == "fk_property" and not self.property:
            raise ValueError("fk_property 링크는 property 가 필요합니다")
        if self.kind == "join_mapping" and self.property:
            raise ValueError("join_mapping 링크에는 property 를 두지 않습니다")
        return self


class LinkTypeDef(_Strict):
    api_name: MemberName
    display_name: DisplayName
    from_type: TypeName
    to_type: TypeName
    cardinality: Cardinality
    source: LinkSource


# ---------------------------------------------------------------- Action Type (PRD 5.3, TRD 17.1)

ActionParamType = Literal["string", "int", "decimal", "bool", "date", "datetime", "enum"]
ConditionOp = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "is_null", "not_null"]
Channel = Literal["app", "email", "sms"]
# 알림 문구의 자리표시자 ``{이름}`` — 파라미터 이름이거나 아래 예약 이름이다.
PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]{0,63})\}")
RESERVED_PLACEHOLDERS = frozenset({"action", "target", "target_pk"})
Template = Annotated[str, StringConstraints(min_length=1, max_length=1000, strip_whitespace=True)]
Scalar = str | int | float | bool | None


class ActionParamDef(_Strict):
    api_name: MemberName
    display_name: DisplayName
    data_type: ActionParamType
    enum_values: list[str] | None = None
    required: bool = True

    @model_validator(mode="after")
    def _enum_rules(self) -> ActionParamDef:
        if self.data_type == "enum" and not self.enum_values:
            raise ValueError("enum 파라미터는 enum_values 가 하나 이상 있어야 합니다")
        if self.data_type != "enum" and self.enum_values is not None:
            raise ValueError("enum 이 아닌 파라미터에는 enum_values 를 둘 수 없습니다")
        if self.api_name in RESERVED_PLACEHOLDERS:
            raise ValueError(f"'{self.api_name}' 은 예약된 이름입니다")
        return self


class ActionCondition(_Strict):
    """사전 조건 — 대상 객체의 **보이는 값**(편집 레이어 포함)에 건다. 모두 만족해야 실행한다."""

    property: MemberName
    op: ConditionOp
    value: Scalar | list[Scalar] = None


class ActionChange(_Strict):
    """대상 객체의 속성 하나를 파라미터 값 또는 상수로 바꾼다(편집 레이어에 쓴다)."""

    property: MemberName
    param: MemberName | None = None
    value: Scalar = None

    @model_validator(mode="after")
    def _one_source(self) -> ActionChange:
        if (self.param is None) == (self.value is None):
            raise ValueError("변경은 param 과 value 중 하나만 가져야 합니다")
        return self


class ActionNotify(_Strict):
    """부수효과(TRD 17.1-4). 수신자는 역할의 사용자 연락처 또는 대상 객체의 연락처 속성이다.

    ``app`` 은 역할에만 보낸다(앱 안 알림). 문구는 템플릿 + 값 치환이다 — LLM 이 쓰지 않는다.
    """

    channel: Channel
    to_roles: list[str] = Field(default_factory=list)
    to_property: MemberName | None = None
    template: Template

    @model_validator(mode="after")
    def _recipients(self) -> ActionNotify:
        if not self.to_roles and self.to_property is None:
            raise ValueError("알림에는 수신 역할이나 연락처 속성이 있어야 합니다")
        if self.channel == "app" and self.to_property is not None:
            raise ValueError("앱 안 알림은 역할에만 보낼 수 있습니다")
        return self


class ActionApproval(_Strict):
    """누가 승인하는가. ``roles`` 가 비면 요청자가 미리보기를 보고 스스로 실행한다."""

    roles: list[str] = Field(default_factory=list)
    allow_self: bool = False

    @model_validator(mode="after")
    def _someone(self) -> ActionApproval:
        if not self.roles and not self.allow_self:
            raise ValueError("승인 역할이 없으면 allow_self 가 참이어야 합니다")
        return self


class ActionTypeDef(_Strict):
    api_name: MemberName
    display_name: DisplayName
    description: str | None = None
    target_type: TypeName
    # 요청할 수 있는 역할. 비면 대상 타입을 읽을 수 있는 누구나.
    requester_roles: list[str] = Field(default_factory=list)
    parameters: list[ActionParamDef] = Field(default_factory=list)
    preconditions: list[ActionCondition] = Field(default_factory=list)
    changes: list[ActionChange] = Field(default_factory=list)
    notify: list[ActionNotify] = Field(default_factory=list)
    approval: ActionApproval
    # 롤백 때 이미 나간 메일·SMS 수신자에게 보내는 정정 문구(되돌릴 수 없으므로 정정한다).
    compensation: Template | None = None

    @model_validator(mode="after")
    def _shape(self) -> ActionTypeDef:
        names = [p.api_name for p in self.parameters]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"파라미터 이름이 중복됩니다: {', '.join(dup)}")
        props = [c.property for c in self.changes]
        dup = sorted({n for n in props if props.count(n) > 1})
        if dup:
            raise ValueError(f"같은 속성을 두 번 바꿉니다: {', '.join(dup)}")
        if not self.changes and not self.notify:
            raise ValueError("변경이나 알림이 하나는 있어야 합니다")
        return self

    def parameter(self, name: str) -> ActionParamDef | None:
        return next((p for p in self.parameters if p.api_name == name), None)


class OntologyDocument(_Strict):
    object_types: list[ObjectTypeDef] = Field(default_factory=list)
    link_types: list[LinkTypeDef] = Field(default_factory=list)
    action_types: list[ActionTypeDef] = Field(default_factory=list)

    def object_type(self, name: str) -> ObjectTypeDef | None:
        return next((t for t in self.object_types if t.api_name == name), None)

    def action_type(self, name: str) -> ActionTypeDef | None:
        return next((a for a in self.action_types if a.api_name == name), None)

    def canonical_json(self) -> str:
        """순서·공백과 무관한 정규 표현. 같은 정의면 같은 문자열이다."""
        data = self.model_dump(mode="json")
        data["object_types"] = sorted(
            (
                {**t, "properties": sorted(t["properties"], key=lambda p: p["api_name"])}
                for t in data["object_types"]
            ),
            key=lambda t: t["api_name"],
        )
        data["link_types"] = sorted(data["link_types"], key=lambda link: link["api_name"])
        # Action 이 없는 문서는 Phase 9 이전과 같은 해시를 낸다(게시본 content_hash 가 맞는다).
        if data["action_types"]:
            data["action_types"] = sorted(data["action_types"], key=lambda a: a["api_name"])
        else:
            del data["action_types"]
        return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Issue:
    """교차 참조 검사 결과 하나. ``path`` 는 화면이 해당 요소를 가리키는 데 쓴다."""

    path: str
    message: str


def check_document(doc: OntologyDocument) -> list[Issue]:
    issues: list[Issue] = []
    if not doc.object_types:
        # 빈 스키마를 게시하면 읽기 경로가 통째로 비어 어떤 질문도 조회 조건이 되지 못한다.
        issues.append(Issue("object_types", "객체 타입이 하나도 없습니다"))
    seen_types: set[str] = set()
    for t in doc.object_types:
        path = f"object_types.{t.api_name}"
        if t.api_name in seen_types:
            issues.append(Issue(path, "객체 타입 이름이 중복됩니다"))
        seen_types.add(t.api_name)
        pk = t.property(t.pk_property)
        if pk is None:
            issues.append(Issue(path, f"pk 속성 '{t.pk_property}' 이 없습니다"))
        elif pk.nullable:
            issues.append(Issue(f"{path}.{pk.api_name}", "pk 속성은 nullable 일 수 없습니다"))
        if t.property(t.title_property) is None:
            issues.append(Issue(path, f"제목 속성 '{t.title_property}' 이 없습니다"))

    seen_links: set[str] = set()
    for link in doc.link_types:
        path = f"link_types.{link.api_name}"
        if link.api_name in seen_links:
            issues.append(Issue(path, "링크 타입 이름이 중복됩니다"))
        seen_links.add(link.api_name)
        from_t = doc.object_type(link.from_type)
        if from_t is None:
            issues.append(Issue(path, f"출발 타입 '{link.from_type}' 이 없습니다"))
        if doc.object_type(link.to_type) is None:
            issues.append(Issue(path, f"도착 타입 '{link.to_type}' 이 없습니다"))
        if (
            from_t is not None
            and link.source.kind == "fk_property"
            and link.source.property
            and from_t.property(link.source.property) is None
        ):
            issues.append(
                Issue(path, f"FK 속성 '{link.source.property}' 이 '{link.from_type}' 에 없습니다")
            )
    for action in doc.action_types:
        issues.extend(_check_action(doc, action))
    names = [a.api_name for a in doc.action_types]
    for dup in sorted({n for n in names if names.count(n) > 1}):
        issues.append(Issue(f"action_types.{dup}", "Action 이름이 중복됩니다"))
    return issues


def _check_action(doc: OntologyDocument, a: ActionTypeDef) -> list[Issue]:
    """Action 교차 검사. 파라미터 자료형은 바꿀 속성 자료형과 같아야 한다(enum 은 값 포함)."""
    path = f"action_types.{a.api_name}"
    issues: list[Issue] = []
    t = doc.object_type(a.target_type)
    if t is None:
        return [Issue(path, f"대상 타입 '{a.target_type}' 이 없습니다")]
    # 바꿀 수 없는 속성: pk·제목(Neo4j 노드·FK 이름 재작성), FK(링크 원천) — TRD 17.1-3.
    fk = {
        lt.source.property
        for lt in doc.link_types
        if lt.from_type == t.api_name and lt.source.kind == "fk_property" and lt.source.property
    }
    frozen = {t.pk_property: "pk", t.title_property: "제목", **dict.fromkeys(fk, "링크(FK)")}
    for c in a.changes:
        prop = t.property(c.property)
        cpath = f"{path}.changes.{c.property}"
        if prop is None:
            issues.append(Issue(cpath, f"'{t.api_name}' 에 속성 '{c.property}' 이 없습니다"))
            continue
        if c.property in frozen:
            issues.append(Issue(cpath, f"{frozen[c.property]} 속성은 Action 으로 바꿀 수 없습니다"))
        if c.param is not None:
            param = a.parameter(c.param)
            if param is None:
                issues.append(Issue(cpath, f"파라미터 '{c.param}' 이 없습니다"))
            elif param.data_type != prop.data_type:
                issues.append(
                    Issue(
                        cpath,
                        f"파라미터 자료형 {param.data_type} 이 "
                        f"속성 자료형 {prop.data_type} 과 다릅니다",
                    )
                )
            elif param.data_type == "enum" and not set(param.enum_values or []) <= set(
                prop.enum_values or []
            ):
                issues.append(Issue(cpath, "파라미터 enum 값이 속성 enum 값 밖에 있습니다"))
            elif not param.required and not prop.nullable:
                issues.append(Issue(cpath, "비울 수 없는 속성을 선택 파라미터로 바꿀 수 없습니다"))
        elif prop.data_type == "enum" and str(c.value) not in (prop.enum_values or []):
            issues.append(Issue(cpath, f"상수 '{c.value}' 가 enum 값이 아닙니다"))
    for cond in a.preconditions:
        if t.property(cond.property) is None:
            issues.append(
                Issue(
                    f"{path}.preconditions",
                    f"'{t.api_name}' 에 속성 '{cond.property}' 이 없습니다",
                )
            )
    known = {p.api_name for p in a.parameters} | RESERVED_PLACEHOLDERS
    for i, n in enumerate(a.notify):
        npath = f"{path}.notify.{i}"
        if n.to_property is not None:
            prop = t.property(n.to_property)
            want = {"email": "email", "sms": "phone"}.get(n.channel)
            if prop is None:
                issues.append(Issue(npath, f"'{t.api_name}' 에 속성 '{n.to_property}' 이 없습니다"))
            elif prop.pii_kind != want:
                issues.append(
                    Issue(npath, f"{n.channel} 수신 속성은 pii_kind 가 {want} 여야 합니다")
                )
        unknown = sorted(set(PLACEHOLDER_RE.findall(n.template)) - known)
        if unknown:
            issues.append(
                Issue(npath, f"문구에 모르는 자리표시자가 있습니다: {', '.join(unknown)}")
            )
    if any(n.channel in {"email", "sms"} for n in a.notify):
        if a.compensation is None:
            issues.append(
                Issue(
                    path,
                    "메일·SMS 를 보내면 롤백 때 보낼 정정 문구(compensation)가 필요합니다",
                )
            )
        else:
            unknown = sorted(set(PLACEHOLDER_RE.findall(a.compensation)) - known)
            if unknown:
                issues.append(
                    Issue(f"{path}.compensation", f"모르는 자리표시자: {', '.join(unknown)}")
                )
    return issues


IDENT_TYPE_RE = re.compile(TYPE_NAME)
IDENT_MEMBER_RE = re.compile(MEMBER_NAME)
