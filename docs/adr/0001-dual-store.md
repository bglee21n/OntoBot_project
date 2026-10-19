# ADR-0001: 이중 저장소(PostgreSQL + Neo4j)

- 상태: 채택 (2026-10-19)
- 근거: TRD 5절

## 결정

PostgreSQL을 기록의 원본, Neo4j를 링크 투영으로 둔다. 쓰기는 트랜잭셔널 아웃박스(`graph_outbox`), 매일 대사, `scripts/graph_rebuild.py`로 재구축한다. Neo4j 장애 시 탐색 질의는 `failed(graph_unavailable)`로 명시적으로 실패한다(PostgreSQL `link`로 조용히 대체하지 않는다).

## 재검토 조건

투영 지연이 운영에서 반복되면 재검토한다.
