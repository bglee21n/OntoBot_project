# Windows 네이티브 설치 (Docker 미사용 — ADR-0007)

설치 스크립트(`install.ps1`)와 서비스 등록은 Phase 8에서 만든다. 지금은 **개발 PC 준비 절차**만 적는다.

## 개발 PC

| 구성 요소 | 상태 | 할 일 |
| --- | --- | --- |
| PostgreSQL 18 | 설치됨(`postgresql-x64-18` 서비스) | `ontobot_db`(소유자 `ontobot`) 사용 중 |
| pgvector | 서버에 0.8.2 있음, DB에 미설치 | P1 착수 시 슈퍼유저가 `ontobot_db`에서 `CREATE EXTENSION vector;` |
| 테스트 DB | 없음 | Phase 2 전에 슈퍼유저가 만든다(아래) |
| Neo4j Community | 없음 | Phase 2 전에 설치(아래) |
| Ollama | 실행 중(11434) | 역할별 모델은 Phase 1.5 스파이크에서 정한다 |

### 테스트 DB (슈퍼유저로 1회)

앱 계정 `ontobot`에는 DB 생성 권한이 없다. 통합 테스트는 개발 데이터(`ontobot_db`)를 건드리지 않도록 전용 DB를 쓴다.

```sql
CREATE DATABASE ontobot_test OWNER ontobot;          -- 기록의 원본 역할
CREATE DATABASE ontobot_source_test OWNER ontobot;   -- 동기화 원천 역할(Phase 2)
```

### Neo4j Community (Phase 2 전)

1. JRE 21과 Neo4j Community 5.x Windows zip을 받아 풀고, `conf/neo4j.conf`에서 포트를 정한다.
2. Community는 사용자 DB가 하나뿐이다. 테스트가 그래프를 비우므로 **개발용(7687)과 테스트용(7688) 인스턴스를 따로** 둔다.
3. `bin\neo4j windows-service install`로 서비스 등록(개발용만). 테스트용은 `bin\neo4j console`로 필요할 때 띄운다.
4. `.env`의 `ONTOBOT_NEO4J_*`를 채운다.
