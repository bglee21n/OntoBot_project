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


class OntologyDocument(_Strict):
    object_types: list[ObjectTypeDef] = Field(default_factory=list)
    link_types: list[LinkTypeDef] = Field(default_factory=list)

    def object_type(self, name: str) -> ObjectTypeDef | None:
        return next((t for t in self.object_types if t.api_name == name), None)

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
    return issues


IDENT_TYPE_RE = re.compile(TYPE_NAME)
IDENT_MEMBER_RE = re.compile(MEMBER_NAME)
