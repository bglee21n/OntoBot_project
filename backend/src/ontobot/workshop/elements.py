"""요소 후보(``draft_element``)의 종류별 payload 모양(TRD 6.7, 12절).

추출기가 낸 payload 는 느슨하게 저장한다(이름 형식이 틀려도 버리지 않는다 — 사람이 고칠 수 있다).
**확정**할 때는 여기 모델로 엄격히 검증한다. 식별자 형식은 온톨로지 정의 문서와 같은
``TYPE_NAME``/``MEMBER_NAME`` 이다(Neo4j 라벨 안전성, 느슨하게 하지 말 것).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, model_validator

from ontobot.ontology.models import (
    Cardinality,
    DataType,
    DisplayName,
    MemberName,
    PropertyDef,
    TypeName,
)
from ontobot.workshop.evidence import normalize

Kind = Literal["object_type", "property", "link_type", "action_candidate", "golden_query"]
KINDS: tuple[Kind, ...] = (
    "object_type",
    "property",
    "link_type",
    "action_candidate",
    "golden_query",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObjectTypePayload(_Strict):
    api_name: TypeName
    display_name: DisplayName
    description: str | None = None
    # 인터뷰는 키를 모른다. 기본 이름을 두고, 속성이 없으면 draft 의 게시 전 검사가 알려 준다.
    pk_property: MemberName = "id"
    title_property: MemberName = "name"


class PropertyPayload(_Strict):
    object_type: TypeName
    api_name: MemberName
    display_name: DisplayName
    data_type: DataType
    enum_values: list[str] | None = None
    description: str | None = None

    @model_validator(mode="after")
    def _as_property(self) -> PropertyPayload:
        self.to_def()  # enum 규칙 등 정의 문서와 같은 검사
        return self

    def to_def(self) -> PropertyDef:
        return PropertyDef(
            api_name=self.api_name,
            display_name=self.display_name,
            description=self.description,
            data_type=self.data_type,
            enum_values=self.enum_values,
        )


class LinkTypePayload(_Strict):
    api_name: MemberName
    display_name: DisplayName
    from_type: TypeName
    to_type: TypeName
    cardinality: Cardinality = "many"
    # 원천에서 링크를 만드는 FK 속성(출발 타입). 모르면 비워 두고 모델러가 매핑을 정한다.
    fk_property: MemberName | None = None


class ActionPayload(_Strict):
    name: DisplayName
    description: str | None = None


class GoldenQueryPayload(_Strict):
    question: Annotated[
        str, StringConstraints(min_length=1, max_length=1000, strip_whitespace=True)
    ]
    # 현업 담당자가 확정한 기대 답변. 확정 전에는 비어 있을 수 있다.
    expected_answer: str | None = None
    answerable: bool = True


PAYLOADS: dict[str, type[_Strict]] = {
    "object_type": ObjectTypePayload,
    "property": PropertyPayload,
    "link_type": LinkTypePayload,
    "action_candidate": ActionPayload,
    "golden_query": GoldenQueryPayload,
}


class PayloadInvalid(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def validate(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """확정용 엄격 검증. 통과하면 기본값까지 채운 payload 를 돌려준다."""
    try:
        model = PAYLOADS[kind].model_validate(payload)
    except ValidationError as e:
        raise PayloadInvalid(
            [f"{'.'.join(str(p) for p in err['loc']) or kind}: {err['msg']}" for err in e.errors()]
        ) from e
    return model.model_dump(mode="json")


def key(kind: str, payload: dict[str, Any]) -> str:
    """같은 세션에서 같은 요소인지 가리는 키 — 이름의 **정확 일치**만 본다(유사도 병합 금지)."""

    def g(name: str) -> str:
        return normalize(str(payload.get(name) or ""))

    if kind == "property":
        return f"{g('object_type')}.{g('api_name')}"
    if kind == "action_candidate":
        return g("name")
    if kind == "golden_query":
        return g("question")
    return g("api_name")
