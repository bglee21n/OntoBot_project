"""요소 영향 — 지우거나 폐기하려는 요소를 무엇이 쓰는가(Phase G1), 게시 영향(Phase 17a).

``references`` 는 정의 문서 안의 참조를 찾는 순수 함수다. 경로는 게시 전 검사(``Issue.path``)와
같은 꼴이라 화면이 그래프의 노드·엣지를 가리키는 데 쓴다. ``usage`` 는 문서 밖의 참조(동기화 매핑·
골든 문항·데이터 존재)를 더한다 — **이름과 개수만** 낸다. 매핑 설정(원천 테이블·컬럼·질의)이나
골든 문항 본문·기대값은 싣지 않는다. 이름 대조는 정확 일치뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.documents import DocumentLink
from ontobot.db.models.evaluation import GoldenItem
from ontobot.db.models.security import (
    DataGroup,
    GroupPropertyHide,
    GroupRowPolicy,
    GroupTypePermission,
)
from ontobot.db.models.sync import DataSource, Mapping
from ontobot.db.models.workshop import DraftElement
from ontobot.objects import reader
from ontobot.ontology.diff import Change, diff
from ontobot.ontology.models import Issue, OntologyDocument

ElementKind = Literal["object_type", "property", "link_type"]


def references(
    doc: OntologyDocument, kind: ElementKind, name: str, type_name: str | None = None
) -> list[Issue]:
    """``kind`` 요소 ``name``(속성이면 ``type_name`` 의 속성)을 가리키는 문서 안의 참조."""
    out: list[Issue] = []
    if kind == "object_type":
        for lt in doc.link_types:
            ends = [x for x, t in (("출발", lt.from_type), ("도착", lt.to_type)) if t == name]
            if ends:
                out.append(
                    Issue(
                        f"link_types.{lt.api_name}",
                        f"링크 '{lt.api_name}' 의 {'·'.join(ends)} 타입",
                    )
                )
        for a in doc.action_types:
            if a.target_type == name:
                out.append(
                    Issue(f"action_types.{a.api_name}", f"Action '{a.api_name}' 의 대상 타입")
                )
        for fn in doc.functions:
            if name in fn.types():
                out.append(
                    Issue(f"functions.{fn.api_name}", f"Function '{fn.api_name}' 이 조회하는 타입")
                )
    elif kind == "link_type":
        for fn in doc.functions:
            if name in fn.links():
                out.append(
                    Issue(f"functions.{fn.api_name}", f"Function '{fn.api_name}' 이 건너는 링크")
                )
    elif kind == "property":
        t = doc.object_type(type_name or "")
        if t is None:
            return out
        path = f"object_types.{t.api_name}"
        if t.pk_property == name:
            out.append(Issue(path, f"'{t.api_name}' 의 pk 속성"))
        if t.title_property == name:
            out.append(Issue(path, f"'{t.api_name}' 의 제목 속성"))
        for lt in doc.link_types:
            if (
                lt.from_type == t.api_name
                and lt.source.kind == "fk_property"
                and lt.source.property == name
            ):
                out.append(Issue(f"link_types.{lt.api_name}", f"링크 '{lt.api_name}' 의 FK 속성"))
        for a in doc.action_types:
            if a.target_type != t.api_name:
                continue
            uses = [
                *(["바꿀 속성"] if any(c.property == name for c in a.changes) else []),
                *(["사전 조건"] if any(c.property == name for c in a.preconditions) else []),
                *(["알림 수신 속성"] if any(n.to_property == name for n in a.notify) else []),
            ]
            if uses:
                out.append(
                    Issue(
                        f"action_types.{a.api_name}", f"Action '{a.api_name}' 의 {'·'.join(uses)}"
                    )
                )
        for fn in doc.functions:
            if name in fn.props_on(t.api_name):
                out.append(
                    Issue(f"functions.{fn.api_name}", f"Function '{fn.api_name}' 이 쓰는 속성")
                )
    return out


@dataclass(frozen=True)
class MappingRef:
    id: int
    data_source: str
    object_type: str


@dataclass(frozen=True)
class Usage:
    in_document: list[Issue] = field(default_factory=list)
    mappings: list[MappingRef] = field(default_factory=list)
    golden_items: list[int] = field(default_factory=list)
    # 타입이면 살아 있는 객체가, 링크 타입이면 살아 있는 링크가 하나라도 있는가
    # (개수·값은 내지 않는다).
    has_data: bool = False


async def usage(
    db: AsyncSession,
    doc: OntologyDocument,
    kind: ElementKind,
    name: str,
    type_name: str | None = None,
) -> Usage:
    refs = references(doc, kind, name, type_name)
    owner = name if kind == "object_type" else type_name

    rows = await db.execute(
        select(
            Mapping.id, Mapping.object_type, Mapping.column_map, Mapping.link_rules, DataSource.name
        )
        .join(DataSource, DataSource.id == Mapping.source_id)
        .order_by(Mapping.id)
    )
    mappings = [
        MappingRef(r.id, r.name, r.object_type)
        for r in rows
        if (kind == "object_type" and r.object_type == name)
        or (kind == "property" and r.object_type == owner and name in (r.column_map or {}).values())
        or (kind == "link_type" and name in (r.link_rules or []))
    ]

    # 골든 문항: 대화 맥락의 타입, 기대 Action 이 이 요소를 쓰는 Action 인 것.
    actions = {
        r.path.removeprefix("action_types.") for r in refs if r.path.startswith("action_types.")
    }
    golden = await db.execute(select(GoldenItem.id, GoldenItem.context, GoldenItem.expected_action))
    golden_ids = sorted(
        g.id
        for g in golden
        if (kind == "object_type" and any(c.get("type") == name for c in (g.context or [])))
        or ((g.expected_action or {}).get("action_type") in actions)
    )

    if kind == "object_type":
        has_data = await reader.has_objects(db, name)
    elif kind == "link_type":
        has_data = await reader.has_links(db, name)
    else:
        has_data = False
    return Usage(refs, mappings, golden_ids, has_data)


# ---------------------------------------------------------------- 게시 영향(Phase 17a)

RefKind = Literal[
    "document",
    "mapping",
    "golden_item",
    "row_policy",
    "property_hide",
    "type_permission",
    "workshop_element",
    "document_link",
    "data",
]


@dataclass(frozen=True)
class Ref:
    """바뀌는 요소를 쓰는 곳 하나. ``blocking`` 이면 게시를 막는다."""

    kind: RefKind
    label: str
    blocking: bool = False


@dataclass(frozen=True)
class ChangeImpact:
    change: Change
    refs: list[Ref]


def _uses_property(filters: list[dict[str, Any]] | None, prop: str) -> bool:
    return any(f.get("property") == prop for f in filters or [])


_POLICIES = (
    select(
        GroupRowPolicy.object_type,
        GroupRowPolicy.applies_to,
        GroupRowPolicy.filters,
        DataGroup.name,
    )
    .join(DataGroup, DataGroup.id == GroupRowPolicy.group_id)
    .order_by(DataGroup.name, GroupRowPolicy.applies_to)
)
_HIDES = (
    select(GroupPropertyHide.object_type, GroupPropertyHide.property, DataGroup.name)
    .join(DataGroup, DataGroup.id == GroupPropertyHide.group_id)
    .order_by(DataGroup.name)
)
_PERMS = (
    select(GroupTypePermission.object_type, DataGroup.name)
    .join(DataGroup, DataGroup.id == GroupTypePermission.group_id)
    .order_by(DataGroup.name)
)


class _Context:
    """한 번 읽어 두는 문서 밖 참조처(골든·권한·워크숍 요소)."""

    def __init__(
        self,
        golden: list[Any],
        policies: list[Any],
        hides: list[Any],
        perms: list[Any],
        elements: list[Any],
    ) -> None:
        self.golden, self.policies, self.hides = golden, policies, hides
        self.perms, self.elements = perms, elements

    def golden_with(self, fragment: str) -> list[Ref]:
        return [
            Ref("golden_item", f"골든 문항 #{g.id} 의 조건 조각에 '{fragment}'")
            for g in self.golden
            if any(fragment in c for c in (g.required_conditions or []))
        ]


async def _context(db: AsyncSession) -> _Context:
    golden = list(
        await db.execute(
            select(
                GoldenItem.id,
                GoldenItem.expected_action,
                GoldenItem.required_conditions,
            ).order_by(GoldenItem.id)
        )
    )
    elements = list(
        await db.execute(
            select(DraftElement.id, DraftElement.kind, DraftElement.payload)
            .where(DraftElement.status != "rejected")
            .order_by(DraftElement.id)
        )
    )
    return _Context(
        golden,
        list(await db.execute(_POLICIES)),
        list(await db.execute(_HIDES)),
        list(await db.execute(_PERMS)),
        elements,
    )


async def _object_type_refs(
    db: AsyncSession, ctx: _Context, base: OntologyDocument, draft: OntologyDocument, ch: Change
) -> list[Ref]:
    name = ch.path
    removed = ch.kind == "removed"
    refs: list[Ref] = []
    if removed:
        refs += [
            Ref("document", i.message, blocking=True)
            for i in references(draft, "object_type", name)
        ]
    u = await usage(db, base, "object_type", name)
    refs += [Ref("mapping", f"매핑 #{m.id}({m.data_source})") for m in u.mappings]
    refs += [Ref("golden_item", f"골든 문항 #{g} 의 대화 맥락·기대 Action") for g in u.golden_items]
    if not removed:
        return refs
    refs += [
        Ref("type_permission", f"데이터 그룹 '{p.name}' 의 타입 권한")
        for p in ctx.perms
        if p.object_type == name
    ]
    refs += [
        Ref("row_policy", f"데이터 그룹 '{p.name}' 의 {p.applies_to} 행 정책")
        for p in ctx.policies
        if p.object_type == name
    ]
    links = await db.scalar(
        select(func.count()).select_from(DocumentLink).where(DocumentLink.object_type == name)
    )
    if links:
        refs.append(Ref("document_link", f"문서 링크 {links}개"))
    refs += [
        Ref("workshop_element", f"워크숍 요소 #{e.id}")
        for e in ctx.elements
        if e.kind == "object_type" and (e.payload or {}).get("api_name") == name
    ]
    if u.has_data:
        refs.append(Ref("data", "살아 있는 객체가 있다 — 게시하면 화면·질의에서 보이지 않는다"))
    return refs


async def _property_refs(
    db: AsyncSession, ctx: _Context, base: OntologyDocument, draft: OntologyDocument, ch: Change
) -> list[Ref]:
    type_name, prop = ch.path.split(".", 1)
    before, after = ch.before or {}, ch.after or {}
    removed = ch.kind == "removed"
    retyped = not removed and (
        before.get("data_type") != after.get("data_type")
        or before.get("enum_values") != after.get("enum_values")
    )
    refs: list[Ref] = []
    if removed or retyped:
        refs += [
            Ref("document", i.message, blocking=removed)
            for i in references(draft, "property", prop, type_name)
        ]
    u = await usage(db, base, "property", prop, type_name)
    refs += [Ref("mapping", f"매핑 #{m.id}({m.data_source})") for m in u.mappings]
    shown = before.get("display_name")
    if shown and (removed or retyped or shown != after.get("display_name")):
        refs += ctx.golden_with(shown)
    refs += [
        Ref(
            "row_policy",
            f"데이터 그룹 '{p.name}' 의 {p.applies_to} 행 정책이 이 속성을 쓴다",
            blocking=removed or retyped,
        )
        for p in ctx.policies
        if p.object_type == type_name and _uses_property(p.filters, prop)
    ]
    if removed:
        refs += [
            Ref("property_hide", f"데이터 그룹 '{h.name}' 의 숨김")
            for h in ctx.hides
            if h.object_type == type_name and h.property == prop
        ]
        refs += [
            Ref("workshop_element", f"워크숍 요소 #{e.id}")
            for e in ctx.elements
            if e.kind == "property" and (e.payload or {}).get("api_name") == prop
        ]
    return refs


async def change_impact(
    db: AsyncSession, base: OntologyDocument, draft: OntologyDocument
) -> list[ChangeImpact]:
    """게시본(``base``) → draft 의 지우기·바꾸기마다 참조처 목록(TRD 17.12).

    막는 것(``blocking``)은 둘뿐이다.

    - draft 안의 깨진 참조 — 게시 전 검사(``check_document``)가 이미 막는다(여기서는 목록에
      보일 뿐).
    - **행 정책이 지우거나 자료형을 바꾸는 속성을 쓴다** — 게시하면 그 정책은 잘못된 정책이 되어
      닫힌 쪽(볼 행 없음)으로 실패한다. 그 그룹 사용자가 조용히 아무것도 못 보게 되므로 먼저 정책을
      고치게 한다.

    나머지(매핑·골든 문항·숨김·타입 권한·워크숍 요소·문서 링크·데이터)는 알리기만 한다. 이름
    대조는 정확 일치, 골든 조건 조각은 표시 이름을 **그대로 포함**하는지만 본다. 값·본문·설정은
    싣지 않는다.
    """
    ctx = await _context(db)
    out: list[ChangeImpact] = []
    for ch in diff(base, draft):
        if ch.kind == "added":
            continue
        refs: list[Ref] = []
        if ch.element == "object_type":
            refs = await _object_type_refs(db, ctx, base, draft, ch)
        elif ch.element == "property":
            refs = await _property_refs(db, ctx, base, draft, ch)
        elif ch.element == "link_type":
            if ch.kind == "removed":
                refs += [
                    Ref("document", i.message, blocking=True)
                    for i in references(draft, "link_type", ch.path)
                ]
            u = await usage(db, base, "link_type", ch.path)
            refs += [
                Ref("mapping", f"매핑 #{m.id}({m.data_source}) 의 링크 규칙") for m in u.mappings
            ]
            if ch.kind == "removed" and u.has_data:
                refs.append(Ref("data", "살아 있는 링크가 있다"))
        elif ch.element == "action_type":
            refs += [
                Ref("golden_item", f"골든 문항 #{g.id} 의 기대 Action")
                for g in ctx.golden
                if (g.expected_action or {}).get("action_type") == ch.path
            ]
        elif ch.element == "function":
            shown = (ch.before or {}).get("display_name")
            if shown:
                refs += ctx.golden_with(shown)
        out.append(ChangeImpact(ch, refs))
    return out


def blocking_issues(impacts: list[ChangeImpact]) -> list[Issue]:
    """게시를 막는 참조 — 정의 문서 밖의 것만(문서 안은 ``check_document`` 가 낸다)."""
    return [
        Issue(i.change.path, r.label)
        for i in impacts
        for r in i.refs
        if r.blocking and r.kind != "document"
    ]
