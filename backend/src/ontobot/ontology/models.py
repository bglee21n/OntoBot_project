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
from collections.abc import Mapping, Set
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
    # 폐기(Foundry 의 deprecated) — 새 질문의 계획에는 쓰지 않고, 이미 있는 데이터·근거 카드는
    # 읽는다.
    # 게시본에 있던 타입을 지우면 객체·대화 맥락·감사 기록이 고아가 되므로 지우는 대신 이것을 쓴다.
    deprecated: bool = False

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
    deprecated: bool = False


def drop_default_flags(data: dict[str, Any]) -> dict[str, Any]:
    """``model_dump`` 결과에서 거짓인 ``deprecated`` 를 뺀다(제자리 수정).

    폐기 표시가 생기기 전의 문서와 같은 정규 표현·내보내기를 내야 게시본 ``content_hash`` 가 맞는다.
    """
    for key in ("object_types", "link_types"):
        for element in data.get(key, []):
            if not element.get("deprecated", True):
                del element["deprecated"]
    return data


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


# ---------------------------------------------------------------- Function (PRD 5.10, TRD 17.1-7)
#
# 선언형이다 — 사용자 코드를 받지 않는다. 내부 조회(``queries``)는 QueryPlan 과 같은 모양의 **집계**
# 조회이고, 조건 값 자리에 ``{param: 이름}`` 을 둘 수 있다. 본문(``body``)은 그 집계 값·파라미터·
# 상수를 정해진 연산으로 잇는 식 트리다. 실행은 ``ontobot.functions`` 가 한다(값은 전부 바인드,
# 호출자 권한으로 조회).

FilterOp = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "between", "is_null"]
# 속성 자료형별로 쓸 수 있는 조건 연산자 — 계획 검증(``query.validator``)과 Function 게시 전 검사가
# 함께 쓴다.
ORDERED_TYPES = frozenset({"int", "decimal", "date", "datetime"})
OPS_BY_TYPE: dict[str, set[FilterOp]] = {
    "string": {"eq", "ne", "in", "contains", "is_null"},
    "enum": {"eq", "ne", "in", "is_null"},
    "bool": {"eq", "ne", "is_null"},
    **{
        t: {"eq", "ne", "gt", "gte", "lt", "lte", "in", "between", "is_null"} for t in ORDERED_TYPES
    },
}
FnAggFn = Literal["count", "sum", "avg", "min", "max"]
ExprOp = Literal[
    "query",
    "param",
    "const",
    "add",
    "sub",
    "mul",
    "div",
    "round",
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "and",
    "or",
    "not",
    "if",
]
# 연산 → 인자 수. None 은 잎(인자 없음).
EXPR_ARITY: dict[str, int] = {
    "query": 0,
    "param": 0,
    "const": 0,
    "add": 2,
    "sub": 2,
    "mul": 2,
    "div": 2,
    "round": 1,
    "eq": 2,
    "ne": 2,
    "gt": 2,
    "gte": 2,
    "lt": 2,
    "lte": 2,
    "and": 2,
    "or": 2,
    "not": 1,
    "if": 3,
}
MAX_EXPR_DEPTH = 8
MAX_EXPR_NODES = 40
MAX_FUNCTION_QUERIES = 4
FnResultType = Literal["int", "decimal", "bool"]


class ParamRef(_Strict):
    param: MemberName


class FunctionParamDef(_Strict):
    api_name: MemberName
    display_name: DisplayName
    data_type: ActionParamType
    enum_values: list[str] | None = None
    required: bool = True
    # 선택 파라미터의 기본값. 질문에 값이 없으면 이것을 쓴다(조회 조건 문장에도 그 값이 보인다).
    default: Scalar = None

    @model_validator(mode="after")
    def _rules(self) -> FunctionParamDef:
        if self.data_type == "enum" and not self.enum_values:
            raise ValueError("enum 파라미터는 enum_values 가 하나 이상 있어야 합니다")
        if self.data_type != "enum" and self.enum_values is not None:
            raise ValueError("enum 이 아닌 파라미터에는 enum_values 를 둘 수 없습니다")
        if self.required and self.default is not None:
            raise ValueError("필수 파라미터에는 기본값을 둘 수 없습니다")
        return self


class FnFilter(_Strict):
    property: MemberName
    op: FilterOp
    value: Scalar | list[Scalar] | ParamRef = None


class FnTraverse(_Strict):
    link: MemberName
    direction: Literal["out", "in"] = "out"
    to: TypeName
    filters: list[FnFilter] = Field(default_factory=list)


class FnAggregate(_Strict):
    fn: FnAggFn
    property: MemberName | None = None
    group_by: list[MemberName] = Field(default_factory=list)


class FunctionQuery(_Strict):
    """내부 조회 하나 — 언제나 집계다(결과가 값이거나 그룹별 값). 근거 행에 그 값이 함께 실린다."""

    api_name: MemberName
    display_name: DisplayName
    target: TypeName
    filters: list[FnFilter] = Field(default_factory=list)
    traverse: list[FnTraverse] = Field(default_factory=list)
    aggregate: FnAggregate


class Expr(_Strict):
    """식 트리의 마디.

    ``query``·``param`` 은 ``name``, ``const`` 는 ``value``, ``round`` 는 ``digits`` 를 쓴다.
    """

    op: ExprOp
    name: MemberName | None = None
    value: Scalar = None
    digits: int | None = Field(default=None, ge=0, le=6)
    args: list[Expr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self) -> Expr:
        want = EXPR_ARITY[self.op]
        if len(self.args) != want:
            raise ValueError(f"'{self.op}' 는 인자가 {want}개여야 합니다")
        if (self.op in {"query", "param"}) != (self.name is not None):
            raise ValueError("name 은 query·param 에만, 그리고 반드시 둡니다")
        if self.op == "const" and (self.value is None or isinstance(self.value, str)):
            raise ValueError("const 는 숫자나 참/거짓이어야 합니다")
        if self.op != "const" and self.value is not None:
            raise ValueError("value 는 const 에만 둡니다")
        if (self.op == "round") != (self.digits is not None):
            raise ValueError("digits 는 round 에만, 그리고 반드시 둡니다")
        return self

    def walk(self) -> list[Expr]:
        out = [self]
        for a in self.args:
            out.extend(a.walk())
        return out

    def depth(self) -> int:
        return 1 + max((a.depth() for a in self.args), default=0)


class FunctionDef(_Strict):
    api_name: MemberName
    display_name: DisplayName
    description: str | None = None
    params: list[FunctionParamDef] = Field(default_factory=list)
    queries: list[FunctionQuery] = Field(min_length=1, max_length=MAX_FUNCTION_QUERIES)
    body: Expr
    # 결과 값의 이름(근거 행·데이터 블록의 라벨)과 자료형.
    # decimal 은 서버가 ``decimals`` 자리로 반올림한다.
    result_label: DisplayName
    result_type: FnResultType = "decimal"
    decimals: int = Field(default=2, ge=0, le=6)

    @model_validator(mode="after")
    def _shape(self) -> FunctionDef:
        for label, names in (
            ("파라미터", [p.api_name for p in self.params]),
            ("조회", [q.api_name for q in self.queries]),
        ):
            dup = sorted({n for n in names if names.count(n) > 1})
            if dup:
                raise ValueError(f"{label} 이름이 중복됩니다: {', '.join(dup)}")
        if self.body.depth() > MAX_EXPR_DEPTH:
            raise ValueError(f"식은 {MAX_EXPR_DEPTH}단계까지입니다")
        if len(self.body.walk()) > MAX_EXPR_NODES:
            raise ValueError(f"식 마디는 {MAX_EXPR_NODES}개까지입니다")
        return self

    def param(self, name: str) -> FunctionParamDef | None:
        return next((p for p in self.params if p.api_name == name), None)

    def query(self, name: str) -> FunctionQuery | None:
        return next((q for q in self.queries if q.api_name == name), None)

    def types(self) -> set[str]:
        """내부에서 조회하는 객체 타입 전부(대상·탐색 끝). 권한·폐기 판정에 쓴다."""
        return {q.target for q in self.queries} | {tr.to for q in self.queries for tr in q.traverse}

    def links(self) -> set[str]:
        return {tr.link for q in self.queries for tr in q.traverse}

    def group_by(self) -> list[str]:
        """결과의 그룹 열. 그룹이 있는 조회는 모두 같은 그룹 열을 쓴다(``check_document``)."""
        return next((q.aggregate.group_by for q in self.queries if q.aggregate.group_by), [])

    def props_on(self, type_name: str) -> set[str]:
        """이 Function 이 ``type_name`` 에서 쓰는 속성(조건·집계·그룹·탐색 끝 조건)."""
        used: set[str] = set()
        for q in self.queries:
            if q.target == type_name:
                used.update(f.property for f in q.filters)
                used.update(q.aggregate.group_by)
                if q.aggregate.property:
                    used.add(q.aggregate.property)
            for tr in q.traverse:
                if tr.to == type_name:
                    used.update(f.property for f in tr.filters)
        return used


class OntologyDocument(_Strict):
    object_types: list[ObjectTypeDef] = Field(default_factory=list)
    link_types: list[LinkTypeDef] = Field(default_factory=list)
    action_types: list[ActionTypeDef] = Field(default_factory=list)
    functions: list[FunctionDef] = Field(default_factory=list)

    def object_type(self, name: str) -> ObjectTypeDef | None:
        return next((t for t in self.object_types if t.api_name == name), None)

    def action_type(self, name: str) -> ActionTypeDef | None:
        return next((a for a in self.action_types if a.api_name == name), None)

    def function(self, name: str) -> FunctionDef | None:
        return next((f for f in self.functions if f.api_name == name), None)

    def active(self) -> OntologyDocument:
        """폐기된 요소를 뺀 문서 — 플래너 요약·계획 검증이 본다(새 질문은 폐기 요소를 쓰지 않는다).

        폐기된 타입에 닿는 링크와 그 타입을 대상으로 하는 Action, 폐기 요소를 조회하는 Function 도
        뺀다. 폐기가 없으면 같은 문서다.
        """
        gone = {t.api_name for t in self.object_types if t.deprecated}
        if not gone and not any(lt.deprecated for lt in self.link_types):
            return self
        links = [
            lt
            for lt in self.link_types
            if not lt.deprecated and lt.from_type not in gone and lt.to_type not in gone
        ]
        kept_links = {lt.api_name for lt in links}
        return OntologyDocument(
            object_types=[t for t in self.object_types if not t.deprecated],
            link_types=links,
            action_types=[a for a in self.action_types if a.target_type not in gone],
            functions=[
                f for f in self.functions if not f.types() & gone and f.links() <= kept_links
            ],
        )

    def without_properties(self, hidden: Mapping[str, Set[str]]) -> OntologyDocument:
        """숨긴 속성을 뺀 문서 — 사용자별 "보이는 문서"(Phase 14b, 판정은 ``security.rbac.access``).

        pk·제목 속성은 숨기지 않는다(식별자). 숨긴 속성을 쓰는 Action(바꿀 속성·사전
        조건·연락처)·Function 은
        통째로 뺀다 — 미리보기·계산 결과로 숨긴 값이 새지 않게. 숨긴 것이 없으면 같은 문서다.
        """
        gone = {
            t.api_name: set(hidden.get(t.api_name, ())) - {t.pk_property, t.title_property}
            for t in self.object_types
        }
        gone = {k: v for k, v in gone.items() if v}
        if not gone:
            return self

        def action_ok(a: ActionTypeDef) -> bool:
            used = {c.property for c in a.changes} | {c.property for c in a.preconditions}
            used |= {n.to_property for n in a.notify if n.to_property}
            return not used & gone.get(a.target_type, set())

        return OntologyDocument(
            object_types=[
                t.model_copy(
                    update={
                        "properties": [
                            p for p in t.properties if p.api_name not in gone.get(t.api_name, ())
                        ]
                    }
                )
                for t in self.object_types
            ],
            link_types=self.link_types,
            action_types=[a for a in self.action_types if action_ok(a)],
            functions=[
                f
                for f in self.functions
                if not any(f.props_on(t) & props for t, props in gone.items())
            ],
        )

    def canonical_json(self) -> str:
        """순서·공백과 무관한 정규 표현. 같은 정의면 같은 문자열이다."""
        data = drop_default_flags(self.model_dump(mode="json"))
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
        # Function 도 같다 — 없으면 Phase 12 이전과 같은 해시.
        if data["functions"]:
            data["functions"] = sorted(data["functions"], key=lambda f: f["api_name"])
        else:
            del data["functions"]
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
    for fn in doc.functions:
        issues.extend(_check_function(doc, fn))
    names = [f.api_name for f in doc.functions]
    for dup in sorted({n for n in names if names.count(n) > 1}):
        issues.append(Issue(f"functions.{dup}", "Function 이름이 중복됩니다"))
    return issues


_NUMERIC = {"int", "decimal"}


def _check_fn_filter(
    fn: FunctionDef, t: ObjectTypeDef, f: FnFilter, path: str, issues: list[Issue]
) -> None:
    prop = t.property(f.property)
    if prop is None:
        issues.append(Issue(path, f"'{t.api_name}' 에 속성 '{f.property}' 이 없습니다"))
        return
    if f.op not in OPS_BY_TYPE[prop.data_type]:
        issues.append(
            Issue(path, f"'{prop.api_name}'({prop.data_type}) 에 연산자 '{f.op}' 를 쓸 수 없습니다")
        )
    if not isinstance(f.value, ParamRef):
        return
    param = fn.param(f.value.param)
    if param is None:
        issues.append(Issue(path, f"파라미터 '{f.value.param}' 이 없습니다"))
    elif f.op in {"in", "between", "is_null"}:
        issues.append(Issue(path, f"'{f.op}' 의 값에는 파라미터를 쓸 수 없습니다"))
    elif not (
        param.data_type == prop.data_type
        or (param.data_type == "int" and prop.data_type == "decimal")
        or (param.data_type == "string" and prop.data_type == "enum")
    ):
        issues.append(
            Issue(
                path,
                f"파라미터 자료형 {param.data_type} 을 "
                f"속성 자료형 {prop.data_type} 조건에 쓸 수 없습니다",
            )
        )


def _expr_type(fn: FunctionDef, e: Expr, path: str, issues: list[Issue]) -> str | None:
    """식의 자료형('number'·'bool'). 어긋나면 issue 를 남기고 None."""
    if e.op == "query":
        if fn.query(e.name or "") is None:
            issues.append(Issue(path, f"조회 '{e.name}' 이 없습니다"))
            return None
        return "number"
    if e.op == "param":
        param = fn.param(e.name or "")
        if param is None:
            issues.append(Issue(path, f"파라미터 '{e.name}' 이 없습니다"))
            return None
        if param.data_type in _NUMERIC:
            return "number"
        if param.data_type == "bool":
            return "bool"
        issues.append(Issue(path, f"식에는 숫자·참거짓 파라미터만 쓸 수 있습니다: {e.name}"))
        return None
    if e.op == "const":
        return "bool" if isinstance(e.value, bool) else "number"
    args = [_expr_type(fn, a, path, issues) for a in e.args]
    if None in args:
        return None
    want = {
        **dict.fromkeys(["add", "sub", "mul", "div", "round"], ("number", "number")),
        **dict.fromkeys(["gt", "gte", "lt", "lte"], ("number", "bool")),
        **dict.fromkeys(["and", "or", "not"], ("bool", "bool")),
    }
    if e.op in want:
        arg_t, out = want[e.op]
        if any(a != arg_t for a in args):
            issues.append(Issue(path, f"'{e.op}' 의 인자는 {arg_t} 여야 합니다"))
            return None
        return out
    if e.op in {"eq", "ne"}:
        if args[0] != args[1]:
            issues.append(Issue(path, f"'{e.op}' 의 두 인자 자료형이 다릅니다"))
            return None
        return "bool"
    # if
    if args[0] != "bool" or args[1] != args[2]:
        issues.append(Issue(path, "'if' 는 (참거짓, 값, 같은 자료형의 값) 이어야 합니다"))
        return None
    return args[1]


def _check_function(doc: OntologyDocument, fn: FunctionDef) -> list[Issue]:
    """Function 교차 검사 — 타입·속성·링크 실재, 연산자·파라미터 자료형, 그룹 열 일치, 식 자료형.

    경로는 언제나 ``functions.<이름>`` 이다(영향 분석 경로와 같아야 화면이 같은 요소에 붙인다).
    내부 조회 문제는 메시지 앞에 조회 이름을 붙인다.
    """
    path = f"functions.{fn.api_name}"
    issues: list[Issue] = []
    groups: list[tuple[str, ...]] = []
    for q in fn.queries:
        qpath = path
        start_at = len(issues)
        t = doc.object_type(q.target)
        if t is None:
            issues.append(Issue(qpath, f"조회 '{q.api_name}': 대상 타입 '{q.target}' 이 없습니다"))
            continue
        if t.deprecated:
            issues.append(Issue(qpath, f"대상 타입 '{q.target}' 이 폐기되었습니다"))
        for f in q.filters:
            _check_fn_filter(fn, t, f, qpath, issues)
        for tr in q.traverse:
            link = next((lk for lk in doc.link_types if lk.api_name == tr.link), None)
            if link is None:
                issues.append(Issue(qpath, f"링크 '{tr.link}' 이 없습니다"))
                continue
            if link.deprecated:
                issues.append(Issue(qpath, f"링크 '{tr.link}' 이 폐기되었습니다"))
            start, end = (
                (link.from_type, link.to_type)
                if tr.direction == "out"
                else (link.to_type, link.from_type)
            )
            if start != t.api_name or end != tr.to:
                issues.append(
                    Issue(
                        qpath,
                        f"링크 '{tr.link}'({link.from_type}→{link.to_type}) 를 "
                        f"'{tr.direction}' 으로 "
                        f"'{t.api_name}' 에서 '{tr.to}' 로 건널 수 없습니다",
                    )
                )
                continue
            to = doc.object_type(end)
            if to is None:
                issues.append(Issue(qpath, f"링크 '{tr.link}' 의 끝 타입 '{end}' 이 없습니다"))
                continue
            if to.deprecated:
                issues.append(Issue(qpath, f"끝 타입 '{end}' 이 폐기되었습니다"))
            for f in tr.filters:
                _check_fn_filter(fn, to, f, qpath, issues)
        a = q.aggregate
        prop = t.property(a.property) if a.property else None
        if a.property and prop is None:
            issues.append(Issue(qpath, f"집계 속성 '{a.property}' 이 '{t.api_name}' 에 없습니다"))
        elif a.fn in {"sum", "avg"} and (prop is None or prop.data_type not in _NUMERIC):
            issues.append(Issue(qpath, f"'{a.fn}' 는 숫자 속성에만 쓸 수 있습니다"))
        elif a.fn in {"min", "max"} and (prop is None or prop.data_type not in _NUMERIC):
            # 식에 넣을 값이라 숫자만 받는다(날짜 최솟값은 Function 이 아니라 조회 질문이다).
            issues.append(Issue(qpath, f"'{a.fn}' 는 숫자 속성에만 쓸 수 있습니다"))
        for g in a.group_by:
            if t.property(g) is None:
                issues.append(Issue(qpath, f"그룹 속성 '{g}' 이 '{t.api_name}' 에 없습니다"))
        if a.group_by:
            groups.append(tuple(a.group_by))
        issues[start_at:] = [
            Issue(i.path, f"조회 '{q.api_name}': {i.message}") for i in issues[start_at:]
        ]
    if len(set(groups)) > 1:
        issues.append(Issue(path, "그룹이 있는 조회는 모두 같은 group_by 를 써야 합니다"))
    # 근거 행의 칸 이름 = 그룹 열·조회 이름·value·inputs 이므로 겹치면 안 된다.
    clash = {q.api_name for q in fn.queries} & ({"value", "inputs"} | set(fn.group_by()))
    if clash:
        issues.append(
            Issue(path, f"조회 이름이 그룹 열이나 예약 이름과 겹칩니다: {', '.join(sorted(clash))}")
        )
    body_t = _expr_type(fn, fn.body, path, issues)
    if body_t is not None:
        want = "bool" if fn.result_type == "bool" else "number"
        if body_t != want:
            issues.append(Issue(path, f"식의 결과가 {fn.result_type} 이 아닙니다"))
    for p in fn.params:
        if (
            p.default is not None
            and p.data_type == "enum"
            and str(p.default) not in (p.enum_values or [])
        ):
            issues.append(Issue(path, f"파라미터 '{p.api_name}' 기본값이 enum 값이 아닙니다"))
    return issues


def _check_action(doc: OntologyDocument, a: ActionTypeDef) -> list[Issue]:
    """Action 교차 검사. 파라미터 자료형은 바꿀 속성 자료형과 같아야 한다(enum 은 값 포함)."""
    path = f"action_types.{a.api_name}"
    t = doc.object_type(a.target_type)
    if t is None:
        return [Issue(path, f"대상 타입 '{a.target_type}' 이 없습니다")]
    issues: list[Issue] = []
    if t.deprecated:
        issues.append(Issue(path, f"대상 타입 '{a.target_type}' 이 폐기되었습니다"))
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
