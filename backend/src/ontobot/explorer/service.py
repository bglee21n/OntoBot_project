"""탐색기 검색·이웃 조회 — 권한은 근거 카드와 같은 경로다(``rbac.access`` 의 결과만 쓴다).

- 읽을 수 없는 타입·행 정책 밖의 객체는 결과·개수 어디에도 나오지 않는다(존재도 정보다). 시작 객체가
  그렇다면 ``NotFound`` — 없는 객체와 같은 말이다.
- 제목은 ``objects.reader`` 로 읽는다(Neo4j 의 ``title`` 은 PII 면 null 이고, 편집 값을 모른다).
  Neo4j 가 늦어 삭제된 객체를 가리켜도 reader 가 거른다.
- Neo4j 를 쓸 수 없으면 ``GraphUnavailable`` — PG ``link`` 로 대신하지 않는다(질의 파이프라인과
  같은 규칙).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.graph import client as graph
from ontobot.graph import traversal
from ontobot.objects.reader import ObjectRow, resolve, run_query
from ontobot.ontology.models import OntologyDocument
from ontobot.query.compiler import compile_search
from ontobot.query.executor import in_scope
from ontobot.query.validator import Scope, check_scopes
from ontobot.security.rbac import Access

Direction = Literal["out", "in", "both"]
# 가지마다 Neo4j 에서 받는 pk 상한. 넘으면 그 가지의 개수는 "이상"(truncated)으로 낸다.
FETCH_CAP = 1000
CYPHER_TIMEOUT_S = 5.0


class NotFound(Exception):
    pass


class GraphUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Hit:
    type: str
    type_display_name: str
    pk: str
    title: str


@dataclass(frozen=True)
class Group:
    link_type: str
    link_display_name: str
    direction: Literal["out", "in"]
    other_type: str
    other_display_name: str
    total: int
    truncated: bool


@dataclass(frozen=True)
class Item:
    link_type: str
    direction: Literal["out", "in"]
    type: str
    pk: str
    title: str


@dataclass(frozen=True)
class Neighbors:
    groups: list[Group]
    items: list[Item]


def _scopes(doc: OntologyDocument, access: Access) -> dict[str, Scope]:
    return check_scopes(doc, access.read_scopes())


async def _titles(
    db: AsyncSession,
    doc: OntologyDocument,
    object_type: str,
    pks: Sequence[str],
    scope: Scope | None,
) -> dict[str, str]:
    """행 정책 안에 있고 지금 있는 객체만 → 제목."""
    t = doc.object_type(object_type)
    if t is None or not pks:
        return {}
    allowed = await in_scope(db, object_type, pks, scope)
    found = await resolve(
        db, object_type, [p for p in pks if p in allowed], declared=[t.title_property]
    )
    return {pk: str(r.properties.get(t.title_property) or pk) for pk, r in found.items()}


async def search(
    db: AsyncSession,
    doc: OntologyDocument,
    access: Access,
    text: str,
    *,
    object_type: str | None,
    limit: int,
) -> list[Hit]:
    """pk·제목 부분 일치. 타입을 주면 그 타입만(읽을 수 없으면 빈 결과)."""
    text = text.strip()
    if not text:
        return []
    scopes = _scopes(doc, access)
    hits: list[Hit] = []
    for t in doc.object_types:
        if t.api_name not in access.readable or (object_type and t.api_name != object_type):
            continue
        q = compile_search(
            t.api_name, t.title_property, text, scopes.get(t.api_name), limit - len(hits)
        )
        for r in await run_query(db, q):
            if isinstance(r, ObjectRow):
                title = str(r.properties.get(t.title_property) or r.pk)
                hits.append(Hit(t.api_name, t.display_name, r.pk, title))
        if len(hits) >= limit:
            break
    return hits


def _branches(
    doc: OntologyDocument,
    access: Access,
    object_type: str,
    link_type: str | None,
    direction: Direction,
) -> list[traversal.Branch]:
    """읽을 수 있는 끝 타입의 링크만 — Cypher 를 만들기 전에 거른다."""
    out = []
    for lt in doc.link_types:
        if link_type is not None and lt.api_name != link_type:
            continue
        if lt.from_type == object_type and direction != "in" and lt.to_type in access.readable:
            out.append(traversal.Branch(lt.api_name, True, lt.to_type))
        if lt.to_type == object_type and direction != "out" and lt.from_type in access.readable:
            out.append(traversal.Branch(lt.api_name, False, lt.from_type))
    return out


async def check_start(
    db: AsyncSession, doc: OntologyDocument, access: Access, object_type: str, pk: str
) -> None:
    if object_type not in access.readable or doc.object_type(object_type) is None:
        raise NotFound(object_type)
    if not await _titles(db, doc, object_type, [pk], _scopes(doc, access).get(object_type)):
        raise NotFound(pk)


async def neighbors(
    db: AsyncSession,
    driver: graph.AsyncDriver | None,
    doc: OntologyDocument,
    access: Access,
    object_type: str,
    pk: str,
    *,
    link_type: str | None,
    direction: Direction,
    limit: int,
    offset: int = 0,
) -> Neighbors:
    """1홉 이웃. ``groups`` 는 가지(링크 타입·방향)마다 읽을 수 있는 이웃 수, ``items`` 는 그중
    ``offset`` 부터 ``limit`` 개(가지 순서 → pk 순서)."""
    await check_start(db, doc, access, object_type, pk)
    branches = _branches(doc, access, object_type, link_type, direction)
    if branches and driver is None:
        raise GraphUnavailable("no driver")
    try:
        found = await traversal.neighbors(
            driver,  # type: ignore[arg-type]  # 가지가 없으면 부르지 않는다
            object_type,
            pk,
            branches,
            per_branch=FETCH_CAP,
            timeout_s=CYPHER_TIMEOUT_S,
        )
    except (graph.GraphUnavailable, graph.GraphTimeout, graph.GraphQueryError) as e:
        raise GraphUnavailable(type(e).__name__) from e

    scopes = _scopes(doc, access)
    by_type: dict[str, list[str]] = {}
    for n in found:
        by_type.setdefault(n.other_type, []).append(n.pk)
    titles = {
        t: await _titles(db, doc, t, sorted(set(pks)), scopes.get(t)) for t, pks in by_type.items()
    }

    groups: list[Group] = []
    items: list[Item] = []
    for b in branches:
        raw = [n.pk for n in found if n.link_type == b.link_type and n.outgoing == b.outgoing]
        kept = [p for p in raw if p in titles.get(b.other_type, {})]
        if not kept:
            continue
        lt = next((x for x in doc.link_types if x.api_name == b.link_type), None)
        other = doc.object_type(b.other_type)
        d: Literal["out", "in"] = "out" if b.outgoing else "in"
        groups.append(
            Group(
                link_type=b.link_type,
                link_display_name=lt.display_name if lt else b.link_type,
                direction=d,
                other_type=b.other_type,
                other_display_name=other.display_name if other else b.other_type,
                total=len(kept),
                truncated=len(raw) >= FETCH_CAP,
            )
        )
        items.extend(Item(b.link_type, d, b.other_type, p, titles[b.other_type][p]) for p in kept)
    return Neighbors(groups=groups, items=items[offset : offset + limit])
