"""Phase 15 — 탐색기 Cypher·검색 조립(순수)."""

from __future__ import annotations

import pytest

from ontobot.graph.traversal import Branch, compile_neighbors
from ontobot.query.compiler import compile_search


def test_neighbors_cypher_is_one_hop_per_branch_with_bound_values() -> None:
    cypher, params = compile_neighbors(
        "Organization",
        [Branch("owned_by", False, "Facility"), Branch("manages", True, "Region")],
        per_branch=50,
    )
    parts = cypher.split(" UNION ALL ")
    assert len(parts) == 2
    assert "(s:`Organization` {pk: $pk})<-[:`owned_by`]-(n:`Facility`)" in parts[0]
    assert "-[:`manages`]->(n:`Region`)" in parts[1]
    assert all("NOT coalesce(n.stub, false)" in p and "LIMIT $cap" in p for p in parts)
    assert "*" not in cypher  # 가변 길이 경로 금지
    assert params == {"cap": 50}


def test_neighbors_rechecks_identifiers_before_assembly() -> None:
    with pytest.raises(ValueError):
        compile_neighbors("Organization", [Branch("x]->(m) DETACH DELETE m //", True, "Region")], 1)
    with pytest.raises(ValueError):
        compile_neighbors("Org`) MATCH", [Branch("owned_by", True, "Region")], 1)


def test_search_binds_text_and_escapes_wildcards() -> None:
    q = compile_search("Facility", "name", "50%_" + "\\", None, 20)
    assert "50%" not in " ".join(q.where)
    assert "%50" + "\\%" + "\\_" + "\\\\" + "%" in q.params.values()
    assert "name" in q.params.values()  # 속성 키도 바인드
    assert q.limit == 20


def test_search_with_empty_scope_sees_nothing() -> None:
    q = compile_search("Facility", "name", "a", (), 20)
    assert q.where[-1] == "false"
