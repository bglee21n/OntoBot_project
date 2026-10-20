# Windows 네이티브 설치 (Docker 미사용 — ADR-0007)

운영 설치(오프라인 번들·`install.ps1`·WinSW 서비스)는 [INSTALL.md](../INSTALL.md)와 ADR-0010 이 정본이다.
여기는 **개발 PC 구성**만 적는다.

## 개발 PC (2026-10-20 설치 완료)

| 구성 요소 | 상태 |
| --- | --- |
| PostgreSQL 18 | `postgresql-x64-18` 서비스. `ontobot`(슈퍼유저) 소유 DB 세 개 — `ontobot_db`(개발), `ontobot_test`(통합 테스트), `ontobot_source_test`(동기화 원천 역할) |
| pgvector 0.8.2 | `ontobot_db`·`ontobot_test`에 확장 설치됨(사용은 P1) |
| JDK 21 | `C:\java\jdk-21.0.2` (`JAVA_HOME`) — Neo4j 5.26이 요구하는 Java 17/21 충족 |
| Neo4j Community 5.26.32 (LTS) | 아래 두 인스턴스. 원본 zip은 `D:\App\neo4j\_download`(공식 SHA-256과 대조함) |
| Ollama | 실행 중(11434). 역할별 모델은 Phase 1.5 스파이크에서 정한다 |

### Neo4j 인스턴스

| | 개발 | 테스트 |
| --- | --- | --- |
| 설치 위치 | `D:\App\neo4j\dev` | `D:\App\neo4j\test` |
| Windows 서비스 | `neo4j-ontobot-dev` (자동 시작) | `neo4j-ontobot-test` (수동 시작) |
| Bolt / HTTP | `127.0.0.1:7687` / `:7474` | `127.0.0.1:7688` / `:7475` |
| 메모리 | 힙 1g · 페이지 캐시 512m | 힙 512m · 페이지 캐시 256m |
| `.env` 키 | `ONTOBOT_NEO4J_*` | `ONTOBOT_TEST_NEO4J_*` |

- Community는 사용자 DB가 하나뿐이고 테스트가 그래프를 비우므로 **인스턴스를 둘로 나눴다**.
  `tests/integration/test_neo4j_connect.py`가 두 URI가 서로 다른지까지 확인한다.
- 설정은 각 `conf\neo4j.conf` 맨 아래 `# --- OntoBot` 블록에 있다. 사용 통계 전송을 껐고
  (`dbms.usage_report.enabled=false` — 폐쇄망), localhost에서만 받는다.
- ⚠ **같은 키를 두 번 적으면 Neo4j가 기동하지 않는다**(`declared multiple times`). 기본값과 겹치는
  키는 위쪽 원래 줄을 주석 처리했다(`server.https.enabled`, `server.windows_service_name`).
- 비밀번호는 첫 기동 전에 `neo4j-admin dbms set-initial-password`로 정했고 `.env`에만 있다.
  첫 기동 뒤에는 이 명령이 효과가 없다 — 바꾸려면 `ALTER CURRENT USER SET PASSWORD`를 쓴다.
- 테스트 인스턴스는 수동 시작이다. 통합 테스트 전에 켠다.

```powershell
Get-Service neo4j-ontobot-*            # 상태
Start-Service neo4j-ontobot-test       # 통합 테스트 전
uv run --project backend python scripts/task.py test-int
```

### 개발용 원천 DB

`ontobot_source_dev` 에 샘플 팩 CSV(region·organization·facility)를 표로 넣어 두었다. 화면 '데이터 소스'에서
`env:ONTOBOT_SRCDEV` 로 등록하면 된다(`.env` 의 `ONTOBOT_SRCDEV_*`). 통합 테스트는 이 DB 가 아니라
`ontobot_source_test` 를 쓴다(테스트마다 고유 이름의 표를 만든다).

### 워커

동기화와 Neo4j 투영은 **워커 프로세스**가 한다. API 만 띄우면 '지금 동기화'가 대기열에만 쌓인다.

```powershell
uv run --project backend python scripts/task.py worker
```

