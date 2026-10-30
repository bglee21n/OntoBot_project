"""객체 탐색기의 이웃 조회(Phase 15) — 시작 객체에서 **1홉**만.

깊이 N 은 화면이 1홉 호출을 되풀이해 만든다(가변 길이 경로를 쓰지 않는다). 경로 중간 객체의 권한을
홉마다 다시 검사해야 하고, 응답 크기가 깊이에 따라 폭증하기 때문이다(그래프 편집 제안서 보강 4).

호출부가 **읽을 수 있는 끝 타입의 링크만** 가지(``Branch``)로 넘긴다 — 라벨을 Cypher 에 넣기 전에
권한으로 거르고, 응답을 받은 뒤에 거르지 않는다. 라벨·관계 타입은 바인드할 수 없으므로
``client.label``·``rel`` 이 조립 직전에 식별자 형식을 다시 확인한다. stub(원천에 없는 객체) 끝은
내지 않는다. 노드에는 pk 만 돌려받는다 — 제목·값은 호출부가 ``objects.reader`` 로 읽는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ontobot.graph import client
from ontobot.graph.client import label, rel


@dataclass(frozen=True)
class Branch:
    link_type: str
    outgoing: bool  # 시작 객체가 링크의 from 쪽이면 참
    other_type: str


@dataclass(frozen=True)
class Neighbor:
    link_type: str
    outgoing: bool
    other_type: str
    pk: str


def compile_neighbors(
    start_type: str, branches: Sequence[Branch], per_branch: int
) -> tuple[str, dict[str, object]]:
    parts = []
    for i, b in enumerate(branches):
        arrow = f"-[:{rel(b.link_type)}]->" if b.outgoing else f"<-[:{rel(b.link_type)}]-"
        parts.append(
            f"MATCH (s:{label(start_type)} {{pk: $pk}}){arrow}(n:{label(b.other_type)}) "
            f"WHERE NOT coalesce(n.stub, false) "
            f"WITH n.pk AS pk ORDER BY pk LIMIT $cap "
            f"RETURN {i} AS branch, pk"
        )
    return " UNION ALL ".join(parts), {"cap": per_branch}


async def neighbors(
    driver: client.AsyncDriver,
    start_type: str,
    pk: str,
    branches: Sequence[Branch],
    *,
    per_branch: int,
    timeout_s: float,
) -> list[Neighbor]:
    """가지마다 pk 순서로 ``per_branch`` 개까지. 가지가 없으면 Neo4j 를 부르지 않는다."""
    if not branches:
        return []
    cypher, params = compile_neighbors(start_type, branches, per_branch)
    rows = await client.read(driver, cypher, {**params, "pk": pk}, timeout_s=timeout_s)
    out = []
    for r in rows:
        b = branches[int(r["branch"])]
        out.append(Neighbor(b.link_type, b.outgoing, b.other_type, str(r["pk"])))
    return out
