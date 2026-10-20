# ADR-0010: 서비스 래퍼는 WinSW, 설치는 릴리스 폴더 + `current` 정션

- 상태: **대체됨** — ADR-0013(Docker·WSL2)·ADR-0014(배포 콘솔), 2026-10-20. 처음 채택: 2026-10-20, Phase 8
- 근거: TRD 3절(패키징)·15절(가용성·폐쇄망), ADR-0007(Docker 미사용)

## 결정

### 1. 서비스 래퍼: WinSW 2.12.0 (x64)

api·worker·Ollama 를 Windows 서비스로 등록하는 래퍼로 **WinSW 2.12.0** 을 쓴다. 래퍼 실행 파일을
서비스 이름으로 복사하고(`ontobot-api.exe`) 같은 이름의 XML(`ontobot-api.xml`)에 설정을 둔다.

| | WinSW 2.12.0 | NSSM 2.24 |
| --- | --- | --- |
| 설정 | 서비스마다 XML 파일 — 저장소의 템플릿(`deploy/windows/services/`)과 비교·검토할 수 있다 | 레지스트리 — 설치 뒤 무엇이 바뀌었는지 파일로 남지 않는다 |
| 마지막 정식 배포 | 2.12.0 (안정판) | 2.24 (2014), 프리릴리스 2.24-101 (2017) |
| 로그 | 크기 회전 내장(`roll-by-size`) | 회전 지원(설정 키 다수) |
| 실패 시 재시작·지연 | `onfailure` | `AppExit` |
| 라이선스 | MIT | 퍼블릭 도메인 |

- 3.x 는 알파라서 쓰지 않는다. .NET Framework 4.x 빌드(`WinSW-x64.exe`)는 Windows Server 기본 구성으로 돈다.
- PostgreSQL(EDB 설치본)과 Neo4j(`neo4j windows-service install`)는 자체 서비스 등록을 쓴다 — 래퍼는 우리 프로세스와
  Ollama(독립 실행 zip, 사용자 트레이 앱이 아닌 서비스로 돌리기 위해)에만 쓴다.
- 서비스 계정은 MVP 에서 LocalSystem(WinSW 기본)이다. 가상 계정(`NT SERVICE\ontobot-api`)으로 낮추는 것은
  로그·데이터 폴더 ACL 과 함께 운영 강화 항목으로 남긴다.

### 2. 설치 배치

```
C:\OntoBot\                       (-Root, 기본값)
  releases\<버전>\                번들의 app\ 을 그대로 — 저장소와 같은 배치(backend\ config\ domain-packs\ scripts\ frontend\dist\)
    backend\.venv\                릴리스마다 따로 만든 가상환경(ontobot 은 편집 가능 설치)
    .env  →  shared\.env          심볼릭 링크 — 비밀값은 한 곳에만
  current  →  releases\<버전>     정션. 서비스 XML 은 언제나 current 를 가리킨다
  python\                         번들의 CPython 3.12(uv 관리본 — 옮겨도 동작한다)
  shared\.env                     설치가 처음 한 번 만든다(접속 비밀번호는 무작위 생성)
  services\                       WinSW 실행 파일·XML
  neo4j\  jre\  ollama\  ollama-models\  pgdata\
  logs\  backups\  state.json     state.json = 현재·이전 릴리스, 마지막 백업
```

- **ontobot 을 편집 가능(editable)으로 설치한다.** `ontobot.config` 가 `.env`·`config/llm.yaml` 을 패키지 위치에서
  저장소 루트(`parents[3]`)로 찾는다. site-packages 에 넣으면 그 경로가 `.venv` 가 된다. 개발과 같은 배치를 쓰면
  설치본에서도 `scripts/*.py` 가 그대로 돈다(설치 확인·시드·대사 재구축).
- 가상환경은 옮길 수 없으므로 릴리스마다 새로 만든다(`uv venv` + `uv pip install --offline --no-index --find-links`).
  wheelhouse 에는 `uv.lock` 에서 뽑은 해시 고정 요구 목록과 편집 가능 설치의 빌드 백엔드(hatchling)가 들어 있다.
- **업그레이드** = 새 릴리스 폴더·가상환경 준비 → `pg_dump -Fc` 백업 → 서비스 중지 → 마이그레이션 → 정션 전환 →
  서비스 시작 → `/readyz` 확인. 서비스 등록은 다시 하지 않는다(XML 이 `current` 를 가리킨다).
- **롤백** = 서비스 중지 → 정션을 이전 릴리스로 → (선택) 업그레이드 직전 백업으로 DB 복원 → 시작. 마이그레이션에
  내림(downgrade)을 믿지 않는다 — 업그레이드 뒤에 쓰인 데이터는 복원하면 사라진다는 것을 롤백 명령이 묻고 진행한다.

### 3. 번들은 받지 않고 모은다

`deploy/offline/build_bundle.ps1` 은 인터넷이 되는 빌드 장비에서 돈다. 파이썬 패키지(PyPI)와 프론트 빌드는 스크립트가
만들지만, **제3자 설치본(PostgreSQL·Neo4j·JRE·Ollama·WinSW·pgvector)은 운영자가 공식 배포처에서 받아 폴더에 둔다**.
스크립트가 URL 을 품으면 버전 고정이 스크립트와 문서 두 곳으로 갈라지고, 공급처 검증(서명·체크섬)을 건너뛰게 된다.
번들은 모든 파일의 SHA-256 을 `manifest.json` 에 적고, 설치 스크립트가 시작할 때 대조한다(운반 중 손상 검출).

## 결과

- 설치 스크립트 하나(`deploy/windows/install.ps1 -Mode Install|Upgrade|Rollback|Status`)가 절차의 정본이다.
- 같은 스크립트를 `-SkipPrerequisites -NoServices` 로 돌리면 관리자 권한 없이 앱 부분만 설치된다(개발 PC 검증용).

## 재검토 조건

WinSW 3 이 안정판이 되면 옮긴다. 다중 노드 운영이 필요해지면 설치 배치(공유 `.env`, 정션 전환)를 다시 본다.
