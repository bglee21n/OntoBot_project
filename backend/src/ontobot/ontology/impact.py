"""요소 영향 — 지우거나 폐기하려는 요소를 무엇이 쓰는가(Phase G1, Phase 17 의 첫 조각).

``references`` 는 정의 문서 안의 참조를 찾는 순수 함수다. 경로는 게시 전 검사(``Issue.path``)와
같은 꼴이라 화면이 그래프의 노드·엣지를 가리키는 데 쓴다. ``usage`` 는 문서 밖의 참조(동기화 매핑·
골든 문항·데이터 존재)를 더한다 — **이름과 개수만** 낸다. 매핑 설정(원천 테이블·컬럼·질의)이나
골든 문항 본문·기대값은 싣지 않는다. 이름 대조는 정확 일치뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.evaluation import GoldenItem
from ontobot.db.models.sync import DataSource, Mapping
from ontobot.objects import reader
from ontobot.ontology.models import FunctionDef, Issue, OntologyDocument

ElementKind = Literal["object_type", "property", "link_type"]


def _function_props(fn: FunctionDef, type_name: str) -> set[str]:
    """Function 이 ``type_name`` 에서 쓰는 속성(조건·집계·그룹·탐색 끝 조건)."""
    used: set[str] = set()
    for q in fn.queries:
        if q.target == type_name:
            used.update(f.property for f in q.filters)
            used.update(q.aggregate.group_by)
            if q.aggregate.property:
                used.add(q.aggregate.property)
        for tr in q.traverse:
            if tr.to == type_name:
                used.update(f.property for f in tr.filters)
    return used


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
            if name in _function_props(fn, t.api_name):
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
