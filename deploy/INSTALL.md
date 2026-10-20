# OntoBot 설치 안내 (Windows Server, 폐쇄망)

Docker 를 쓰지 않는다(ADR-0007). 서비스 래퍼는 WinSW, 설치 배치는 릴리스 폴더 + `current` 정션이다(ADR-0010).
설치 중에도, 실행 중에도 인터넷을 쓰지 않는다.

## 1. 번들 만들기 (인터넷이 되는 빌드 장비)

준비물: Git, uv(0.11 이상), Node.js(npm), `uv python install 3.12`, 저장소 체크아웃(`uv run --project backend python scripts/task.py sync`).

제3자 설치본은 **공식 배포처에서 직접 받아** 한 폴더(예: `D:\ontobot-prereqs`)에 둔다. 받은 뒤 공급처가 밝힌 체크섬·서명을 확인한다.

| 파일(패턴) | 받는 곳 | 비고 |
| --- | --- | --- |
| `postgresql-18*-windows-x64.exe` | EDB PostgreSQL Windows 설치본 | 무인 설치(`--mode unattended`) |
| `neo4j-community-5.26*-windows.zip` | Neo4j Deployment Center (Community, 5.26 LTS) | |
| `*j*21*windows*x64*.zip` | Java 21 JRE/JDK zip (예: Eclipse Temurin) | Neo4j 5.26 은 Java 17/21 |
| `ollama-windows-amd64.zip` | Ollama GitHub Releases(독립 실행 zip) | 설치형(OllamaSetup.exe)이 아니다 — 서비스로 돌린다 |
| `WinSW-x64.exe` | WinSW GitHub Releases **v2.12.0** | |
| `pgvector\lib\vector.dll`, `pgvector\share\extension\vector*` | pgvector 소스를 PostgreSQL 18 용으로 빌드한 결과 | 선택(사용은 P1). 없으면 경고만 |

Ollama 모델은 빌드 장비의 `%USERPROFILE%\.ollama\models` 에서 `config\llm.yaml` 이 쓰는 모델만 복사한다(`ollama pull gemma4:e4b` 를 먼저).

```powershell
powershell -ExecutionPolicy Bypass -File deploy\offline\build_bundle.ps1 -PrereqDir D:\ontobot-prereqs
```

결과: `dist\bundle\ontobot-<버전>-<커밋>\`. 커밋하지 않은 변경이 있으면 버전에 `.dirty` 가 붙는다(운영에 쓰지 말 것).
폴더 통째로 반입 매체에 옮긴다. `manifest.json` 에 모든 파일의 SHA-256 이 있고, 설치가 시작할 때 대조한다.

## 2. 설치 (폐쇄망 서버)

요구: Windows Server 2019 이상, 관리자 PowerShell, 메모리 16GB 이상 권장(Neo4j 힙·페이지 캐시 각 1GB + LLM),
디스크 여유 30GB 이상. GPU 가 없으면 LLM 응답이 크게 느려진다(3절의 수치는 GPU 장비 기준).

```powershell
cd E:\ontobot-0.1.0-<커밋>
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Mode Install -AdminUsername admin -SeedSample
```

순서와 묻는 것:

1. 번들 무결성 대조.
2. `C:\OntoBot\shared\.env` 생성 — DB·Neo4j 비밀번호를 무작위로 만든다(관리자·SYSTEM 만 읽게 ACL 설정).
3. PostgreSQL 무인 설치 — **postgres 슈퍼유저 비밀번호를 묻는다**(스크립트는 저장하지 않는다). 앱 역할·DB 생성, pgvector.
4. JRE·Neo4j(zip 풀기, `conf\neo4j.conf` 끝에 OntoBot 블록 — localhost 만, 사용 통계 끔), 초기 비밀번호, 서비스 `neo4j-ontobot`.
5. Ollama 독립 실행본 + 모델 복사, 서비스 `ontobot-ollama`(127.0.0.1:11434).
6. 릴리스 `C:\OntoBot\releases\<버전>` — 가상환경을 wheelhouse 로만 만든다(`--offline --no-index`, 해시 고정).
7. 마이그레이션, `current` 정션.
8. `-SeedSample`: 샘플 팩(합성 데이터) 게시·적재, `user`·`admin` 역할에 샘플 타입 읽기 권한, Neo4j 재구축.
9. `-AdminUsername`: 관리자 계정(admin + modeler) — **비밀번호를 콘솔에서 묻는다**.
10. 서비스 `ontobot-api`(:8000)·`ontobot-worker` 등록·시작, `/readyz` 가 `ok` 가 될 때까지 기다린다(최대 2분).

이미 있는 구성 요소(같은 서비스 이름)는 건너뛴다. 사내 표준 PostgreSQL 을 쓰면 `-PgService`·`-PgPort` 로 가리킨다.

### 설치 확인 (완료 기준)

1. 브라우저에서 `http://<서버>:8000` → 관리자로 로그인.
2. 채팅에서 `위험등급이 3 이상인 시설은 몇 개야?` → 조회 조건·답(18)·등급 ①·근거 칩.
3. `.\install.ps1 -Mode Status` — 서비스 넷이 Running, `/readyz` 200 `ok`.

## 3. 용량 (Phase 8 부하 측정, ADR-0011)

개발 PC(GPU 1장, gemma4:e4b) 기준: 동시 8명 p95 23초, 동시 16명부터 처리량이 분당 40여 건에서 포화, 24명까지
실패 0. LLM 이 병목이라 사용자가 늘면 실패보다 지연이 먼저 는다. 채팅 한 건이 답이 끝날 때까지 DB 연결을 쥐므로
`ONTOBOT_DB_POOL_SIZE`+`ONTOBOT_DB_MAX_OVERFLOW`(기본 20+10)가 동시 채팅 상한이다. 운영 장비에서 다시 잰다:

```powershell
$env:ONTOBOT_LOAD_PASSWORD = "<측정용 계정 비밀번호>"
C:\OntoBot\current\backend\.venv\Scripts\python.exe C:\OntoBot\current\scripts\load_test.py --username <계정> --users 1,4,8,16
```

## 4. 업그레이드·롤백

```powershell
# 새 번들 폴더에서
.\install.ps1 -Mode Upgrade      # 새 릴리스 준비 → pg_dump 백업 → 중지 → 마이그레이션 → current 전환 → 시작 → /readyz
.\install.ps1 -Mode Rollback     # current 를 이전 릴리스로(DB 는 그대로)
.\install.ps1 -Mode Rollback -RestoreDatabase   # + 업그레이드 직전 백업으로 DB 복원(RESTORE 입력 확인)
.\install.ps1 -Mode Status
```

- 마이그레이션 내림(downgrade)은 쓰지 않는다. 새 스키마가 이전 코드와 맞지 않으면 `-RestoreDatabase` 로 백업을
  되살린다 — **업그레이드 뒤에 쓰인 데이터(대화·감사 로그 포함)는 사라진다.**
- 백업은 `C:\OntoBot\backups\` 에 쌓인다(자동으로 지우지 않는다). 감사 로그 보관 정책에 맞춰 운영자가 옮긴다.
- 이전 릴리스 폴더는 다음 업그레이드 뒤에 지워도 된다.

## 5. 운영 메모

| 항목 | 위치·방법 |
| --- | --- |
| 설정·비밀값 | `C:\OntoBot\shared\.env` (릴리스마다 심볼릭 링크). 바꾸면 `Restart-Service ontobot-api, ontobot-worker` |
| LLM 역할·모델 | `C:\OntoBot\current\config\llm.yaml` — 모델을 바꾸면 평가 게이트(TRD 13절)를 다시 통과시킨다 |
| 로그 | `C:\OntoBot\logs\api.log`·`worker.log`(JSON 한 줄, 크기 회전), 서비스 표준 출력은 `logs\service\` |
| 준비 확인 | `GET /readyz` — `ok` / `degraded`(Neo4j 만 안 됨: 탐색 질의만 실패) / `unavailable`(503: PG·LLM). 재시작 판단에는 `/healthz` 만 쓴다 |
| 매일 작업 | 워커가 그래프 대사(`graph_reconcile`)와 보존 기간 정리(`retention_purge`)를 하루 한 번 발행한다. 결과는 화면 '작업' 목록 |
| HTTPS | 앞단 역방향 프록시(IIS ARR 등)에서 종료하고 `ONTOBOT_COOKIE_SECURE=true`, `ONTOBOT_ALLOWED_ORIGINS` 에 `https://…` |
| 서비스 계정 | MVP 는 LocalSystem(ADR-0010). 가상 계정으로 낮추는 것은 운영 강화 항목 |
| 방화벽 | 8000 만 연다. PostgreSQL·Neo4j·Ollama 는 localhost 에서만 받는다 |

## 6. 개발 PC 에서 설치 스크립트 시험

관리자 권한 없이 앱 부분만(선행 구성 요소·서비스 없이) 설치해 본다. `shared\.env` 를 미리 만들어 기존 DB 를 가리킨다.

```powershell
powershell -ExecutionPolicy Bypass -File deploy\offline\build_bundle.ps1 -AppOnly
powershell -ExecutionPolicy Bypass -File dist\bundle\ontobot-<버전>\install.ps1 -Mode Install -Root D:\ontobot-trial -SkipPrerequisites -NoServices -SeedSample
```
