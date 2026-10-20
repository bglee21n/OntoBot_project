# ADR-0007: Docker 미사용, Windows Server 네이티브 설치

- 상태: **대체됨** — ADR-0013(Docker·WSL2, 2026-10-20). 아래는 당시 결정의 기록이다
- 처음 채택: 2026-10-19, 사용자 결정
- 근거: TRD 3절(패키징), 15절(가용성), 19절(통합 테스트)

## 결정

개발·CI·운영 어디에도 Docker를 쓰지 않는다. 운영은 **Windows Server에 각 구성 요소를 직접 설치**한다.

| 구성 요소 | 설치 방식 |
| --- | --- |
| PostgreSQL + pgvector | Windows 설치본(EDB) + pgvector 빌드본. `CREATE EXTENSION vector`는 설치 시 슈퍼유저가 1회 실행 |
| Neo4j Community | zip + JRE(번들 포함). `neo4j windows-service install`로 서비스 등록 |
| Ollama | Windows 설치본, 모델은 오프라인 번들로 복사 |
| api · worker | uv 가상환경 + 서비스 래퍼로 Windows 서비스 등록(래퍼는 Phase 8에서 확정) |

통합 테스트는 로컬에 설치한 PostgreSQL·Neo4j를 쓰되 개발 데이터와 분리한다.

- PostgreSQL: 테스트 전용 DB(`ontobot_test`, 원천 역할 `ontobot_source_test`). 앱 계정에는 DB 생성 권한이 없으므로 DBA가 한 번 만든다.
- Neo4j Community는 사용자 DB가 하나뿐이다 — 테스트가 그래프를 비우므로 **별도 포트의 테스트 인스턴스**를 둔다. 개발 그래프에 테스트를 돌리지 않는다.
- CI는 GitHub의 Windows 러너에 이미 설치된 PostgreSQL 서비스를 켜서 쓴다(운영과 같은 OS).

## 결과

- 오프라인 번들이 이미지 tar 대신 **설치본과 설치 스크립트**가 된다. 설치·업그레이드·롤백 절차를 스크립트가 맡는다.
- 구성 요소별 버전 고정을 설치본 파일로 한다(이미지 다이제스트 대신).

## 재검토 조건

Linux 운영 요구가 생기면 systemd 설치 스크립트를 추가한다. 구성 요소가 늘어 설치 절차 유지 비용이 커지면 재검토한다.
