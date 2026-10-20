# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# OntoBot (가칭) — 온톨로지 기반 챗봇

게시된 온톨로지(객체 타입·속성·링크 타입) 위에서 자연어 질문을 **검증된 조회 계획**으로 바꿔 실행하고,
답변 문장마다 조회 결과와 **정확 일치로 대조한 인용**을 붙여 등급(①②③)을 매기는 서비스다.

정본 문서: [TRD](docs/trd/OntoBot_project-trd.md)(현재 v0.15) · [PRD](docs/prd/OntoBot_project-prd.md) ·
[구현 계획·진행 기록](docs/plan/OntoBot_project-implementation-plan.md)(MVP, Phase 0~8) ·
[v1 구현 계획](docs/plan/OntoBot_project-v1-implementation-plan.md)(Phase 9~) · [ADR](docs/adr/).
구현 결정이 TRD 와 달라지면 TRD 를 고치고 개정 이력(22절)에 남긴다. 현재 진행: MVP(Phase 0~8) 완료(실제 폐쇄망 장비
설치는 미검증), v1 착수 결정(TRD 17.1), Phase 9(편집 레이어·값 계보)·10(Action·메일·SMS)·11(채팅에서 Action) 완료 —
다음은 Phase 12(Function). 설치 정본은
[deploy/INSTALL.md](deploy/INSTALL.md).

## 운영 전제

- **폐쇄망 온프레미스(Windows Server)가 기본, Docker 를 쓰지 않는다**(ADR-0007). 개발·CI·운영 모두 네이티브 설치.
  실행 중 외부 네트워크를 호출하는 코드를 넣지 않는다(예외는 외부 LLM 공급자와, v1 Phase 10 부터 운영자가 설정한
  사내 메일·SMS 게이트웨이뿐 — TRD 17.1-4). 외부 서버를 부르는 화면 기능(음성 입력)은 `ONTOBOT_CLOSED_NETWORK` 면 끈다.
- LLM 은 로컬 Ollama. 역할별 모델은 [config/llm.yaml](config/llm.yaml) 에만 적는다(코드에 모델 ID 금지).
  planner·answerer 기본값 `gemma4:e4b` 는 ADR-0004 의 측정 결과다 — 바꾸려면 다시 잰다.
- 저장소: PostgreSQL 이 **기록의 원본**, Neo4j Community 는 링크 **투영**(언제든 PostgreSQL 에서 재구축).
- 개발 PC: PostgreSQL 18(`ontobot_db` 개발, `ontobot_test`·`ontobot_source_test` 통합 테스트,
  `ontobot_source_dev` 개발용 원천), Neo4j 개발 7687 / 테스트 7688(Windows 서비스 `neo4j-ontobot-dev`·`-test`).
  상세는 [deploy/windows/README.md](deploy/windows/README.md). 비밀값은 루트 `.env` 에만 있다(커밋 금지).

## 명령

명령의 정본은 [scripts/task.py](scripts/task.py) 다(Windows 에 make 가 없다. `Makefile` 은 이것을 부르기만 한다).
가상환경은 uv 가 `backend/.venv` 에 만든다.

```bash
uv run --project backend python scripts/task.py lint          # ruff·ruff format --check·mypy·import-linter·eslint·tsc
uv run --project backend python scripts/task.py test-unit
uv run --project backend python scripts/task.py test-int      # 실제 PostgreSQL·Neo4j(테스트 전용 DB·인스턴스)
uv run --project backend python scripts/task.py test-contract # OpenAPI 스냅샷
uv run --project backend python scripts/task.py migrate       # alembic upgrade head (.env 의 DB)
uv run --project backend python scripts/task.py openapi       # API 를 바꾸면 반드시 — 프론트 타입 재생성
uv run --project backend python scripts/task.py dev-api       # :8000
uv run --project backend python scripts/task.py dev-web       # :5173 (/api 를 :8000 으로 프록시)
uv run --project backend python scripts/task.py worker        # 동기화·그래프 투영·주기 작업·평가 실행
# 평가(TRD 13절) — 평가 전용 DB·테스트 Neo4j 를 환경변수로 가리킨 뒤(아래):
uv run --project backend python scripts/task.py eval-prepare  # 마이그레이션·샘플 팩 시드(FK 링크)·그래프 재구축
uv run --project backend python scripts/task.py eval          # 실제 모델 게이트
uv run --project backend python scripts/task.py eval-record   # 실제 모델 게이트 + 카세트 녹화(커밋한다)
uv run --project backend python scripts/task.py eval-replay   # 녹화 응답 회귀(CI 와 같다)
# 운영 패키징(ADR-0010) — PowerShell:
powershell -ExecutionPolicy Bypass -File deploy/offline/build_bundle.ps1 -AppOnly   # 앱만(설치본은 -PrereqDir)
powershell -ExecutionPolicy Bypass -File dist/bundle/<번들>/install.ps1 -Mode Install -Root D:/trial -SkipPrerequisites -NoServices
# 부하 측정(API 가 떠 있어야 한다, 비밀번호는 환경변수 ONTOBOT_LOAD_PASSWORD — .env 에 개발 DB loadtest 계정):
uv run --project backend python scripts/load_test.py --username loadtest --users 1,4,8,16 --rounds 8
```

평가 환경: `ONTOBOT_DB_NAME=ontobot_eval`, `ONTOBOT_NEO4J_URI=bolt://localhost:7688`,
`ONTOBOT_NEO4J_PASSWORD`=테스트 Neo4j 비밀번호(.env 의 `ONTOBOT_TEST_NEO4J_PASSWORD`). 개발 DB 로 돌리면 시드가
개발 데이터를 덮고, 카세트 키(데이터 블록 포함)가 CI 와 달라진다.

테스트 하나만(backend 에서):

```bash
cd backend && uv run pytest tests/unit/test_grounding.py::test_grade_one_when_every_fact_is_cited -q
cd backend && uv run pytest tests/integration/test_sync_graph.py -q -m integration
```

그 밖: `scripts/seed_domain_pack.py --pack sample`(샘플 온톨로지 게시 + 시드 객체),
`scripts/spike_plan.py --models <모델…>`(골든 문항으로 모델 측정, 결과는 `docs/adr/data/`),
`scripts/spike_workshop.py --models <모델…>`(인터뷰 대본 `domain-packs/sample/interview.yaml` 으로 면담자·추출기 측정),
`scripts/graph_rebuild.py [--check]`(Neo4j 재구축/대사), 사용자 생성
`cd backend && uv run python -m ontobot.security.cli create-user --username … --display-name … --role modeler`.

- 통합 테스트는 세션 시작 때 `test_db_name`(이름에 `test` 필수) 의 public 스키마를 지우고 마이그레이션을 다시 건다.
  `audit_log` 가 추가 전용(트리거)이라 테스트 사이에 데이터를 지우지 않는다 — 각 테스트가 uuid 로 고유 데이터를 만든다.
- 테스트 Neo4j(`Start-Service neo4j-ontobot-test`)가 없으면 그래프 테스트만 건너뛴다.
- `alembic.ini` 는 **ASCII 만**(configparser 가 cp949 로 읽는다). 새 마이그레이션은 `alembic revision --autogenerate`
  후 검토, `test_schema.py::test_migration_matches_models` 가 모델과 어긋남을 잡는다.
- 한국어 Windows 콘솔(cp949) 때문에 스크립트 출력이 깨지면 `PYTHONUTF8=1`.

## 아키텍처

```
backend/src/ontobot/
  api/        라우터 — 검증과 위임만(업무 규칙 금지). deps.py(세션·현재 사용자·require_role), errors.py(RFC 7807)
  ontology/   정의 문서 모델·임포트/익스포트·버전 비교·draft→게시(versioning)·게시 버전 캐시(registry)·플래너 요약
  objects/    reader.py = object 표의 유일한 읽기 경로 / store.py = 쓰기·스테이징 병합 / indexes.py = 속성 인덱스 DDL
  sync/       커넥터(MVP 는 postgres) · mapping(검증·행 변환) · runner(스테이징 → 한 트랜잭션 병합 → 아웃박스)
  graph/      client.py(neo4j import 는 여기만) · outbox(쓰기) · projector(아웃박스 → Neo4j) · reconcile(대사·재구축)
  query/      plan(QueryPlan) → planner(LLM) → validator → references(FK 값 실재) → compiler → executor, conditions·records
  grounding/  인용 파싱·정규화·사실 문장 판정·등급 — 순수 함수(LLM·DB 를 모른다)
  chat/       pipeline(7.1 단계 오케스트레이션)·answerer·templates(사용자 문구의 정본)
  llm/        gateway(역할 → 공급자·모델) · structured(스키마 검증·1회 재시도) · adapters/(SDK·HTTP 는 여기만)
  security/   auth(로컬 계정 argon2 + 서버 세션)·oidc(Code + PKCE)·rbac(권한 판정의 유일한 자리)·audit·pii·secrets
  jobs/       queue(job 표 + SKIP LOCKED)·handlers·schedules·worker
  evaluation/ golden(팩 ↔ DB)·runner(파이프라인 인프로세스)·faults(실행기 주입)·metrics·gate(+gate.yaml)·replay(카세트)
  workshop/   topics(주제 상태기계, 순수)·interviewer·extractor·evidence(순수)·elements(payload 검증)·sessions(턴)·review·publish
  actions/    rules(파라미터·사전 조건·바뀔 값·템플릿, 순수) · params(채팅 Action 제안 대조) · runner(미리보기 → 승인 = 실행 → 거부 → 롤백)
  notify/     config(notify.yaml, 채널 꺼짐이 기본) · channels(SMTP·SMS 게이트웨이 — 네트워크는 여기만) · delivery(아웃박스)
frontend/src/ React + TanStack Query. api/schema.d.ts 는 생성물(손대지 말 것), features/<화면>/
domain-packs/sample/  ontology.yaml · seed/*.csv(합성) · golden.yaml(기대값은 시드에서 계산)
```

### 질의 한 건 (chat/pipeline.py)

계획(LLM 구조화 출력: `query` / `not_query` / `cannot_answer`) → 검증(속성 자료형으로 값 변환, 권한) →
참조 값 대조 → 실패면 오류를 붙여 **1회 재계획** → 컴파일(값·속성 키 전부 바인드) → 실행(타임아웃) →
결정론적 조회 조건 문장 → `ok` 일 때만 답변 LLM → 근거 대조·등급. 상태(`ok/empty/failed/not_query`)와
등급은 다른 축이다 — `failed` 를 `empty` 처럼 보이게 하지 않는다. `traverse` 는 대상에서 1홉 조건의 AND:
끝 객체 조건은 PG 에서 pk 집합 → Neo4j `EXISTS` 로 대상 pk → PG 대상 조회(Neo4j 불가면 `graph_unavailable`,
PG `link` 로 대체하지 않는다). API(`api/chat.py`)가 메시지·트레이스·맥락을 저장하고, 파이프라인은 저장하지 않는다
(평가가 같은 함수를 인프로세스로 돈다). SSE 이벤트 순서와 모양의 정본은 `chat/stream.py`(`ChatEvents`).

### 쓰기 경로 (sync/runner.py → graph/projector.py)

동기화는 `sync_run(running)` 을 따로 커밋한 뒤, 스테이징 임시 표 → row_hash 변경 판별 → upsert →
(전체 모드) 원천별 삭제 표시 → FK 링크 규칙 → **같은 트랜잭션에서** `graph_outbox` → `succeeded`.
워커의 투영기가 아웃박스를 id 순서로 묶음 반영한다(묶음 = Neo4j 트랜잭션 = 처리 표시 하나, 실패하면 다시).

## 깨면 조용히 틀리는 규칙

TRD 16.3 의존 규칙은 import-linter(`backend/pyproject.toml`)와 `tests/unit/test_dependency_rules.py` 가 막는다:
어떤 모듈도 `api` 를 import 하지 않는다 · `grounding` 은 llm·db·graph·sync·sqlalchemy 금지 ·
`neo4j` 는 `graph/client.py` 만 · LLM SDK 는 `llm/adapters/` 만 · `object` 표 SQL 은 `objects/` 안에서만 ·
권한 판정 함수는 `security/rbac.py` 만.

- **DB 세션은 `async with session_scope() as db:` 로만 연다.** 비동기 제너레이터를 `async for` 로 돌리고 본문에서
  `return` 하면 트랜잭션이 열린 채 남는다 — 워커가 그 상태로 `CREATE INDEX CONCURRENTLY` 를 실행해 자기 자신을 기다렸다.
  워커는 작업을 집은 세션을 닫은 뒤 처리기를 돌린다(`queue.claim` 은 ORM 이 아니라 값을 돌려준다).
- **근거 대조에 유사도를 넣지 않는다.** 정규화는 NFC·공백·천 단위 구분·날짜 ISO 뿐이고 판정은 "그대로 포함". 등급은 LLM 이
  매기지 않는다. 사실 문장 판정 전에 조회 조건 값을 걷어 낸다(TRD 8.2-2 v0.5).
- **LLM 은 SQL·Cypher 를 쓰지 않는다.** 값·속성 키는 바인드, 라벨·관계 타입은 바인드할 수 없으므로 식별자 정규식
  (`ontology/models.py` 의 `TYPE_NAME`/`MEMBER_NAME`)을 **조립 직전에 다시** 확인한다. 정규식을 느슨하게 하지 말 것.
- 데이터 블록은 줄마다 `인용키 | 이름 | 값` 이다(ADR-0004). 라벨을 키 옆에 괄호로 적으면 모델이 인용에 베낀다.
  평균은 서버가 반올림한다. 깨진 인용도 `removed_citations` 에 남긴다.
- 플래너가 표현할 수 없는 조건은 **빼지 말고** `cannot_answer` → `failed(plan_invalid)`. 코드 속성에 이름을 넣는 계획은
  `query/references.py` 가 막는다(값이 모두 그 타입 제목과 **정확히** 같으면 `traverse` 로 옮기고, 아니면 재계획).
  질문의 숫자가 계획에 없으면 `query/coverage.py` 가 재계획시킨다. 그 밖의 조건 누락은 결정론적으로 막을 수 없다
  (골든 `required_conditions` 로 감시). 재계획은 1회뿐이니 오류는 **한꺼번에** 붙인다.
- **플래너 프롬프트 예시의 키 순서는 `QueryPlan` 필드 순서와 같아야 한다.** 구조화 출력은 스키마 순서대로 생성돼,
  예시가 순서를 어기면 모델이 앞 필드(`filters`)를 비운 채 지나간다(측정 0/6 → 6/6, 단위 테스트가 고정).
- 답변 모델은 레코드를 스스로 다시 거르지 않는다(프롬프트 규칙). 걸러 버리면 조건 누락이 감춰진다.
- 프롬프트·스키마 요약을 바꾸면 `scripts/spike_plan.py --models gemma4:e4b` 로 다시 잰다(골든 16문항, 근거는
  `docs/adr/data/`). 측정 결과를 ADR-0004 에 남긴다.
- 동기화에서 행 하나라도 변환 실패면 **실행 전체 실패**(건너뛰지 않는다). Neo4j 노드에는 `pk`·`title`·`stub` 만
  (속성 값·PII 금지, PII 제목은 null). 링크 끝점이 없으면 `stub` 노드, 대사가 수를 보고한다.
- 게시본은 불변(편집은 draft 만, `ontology/versioning.py`). 게시 버전은 부분 유일 인덱스로 하나뿐, 번호 발급·게시는
  advisory lock 으로 직렬화. 레지스트리는 **커밋 뒤** reload 한다. 타입·속성 `api_name` 은 바꾸지 않는다.
- `audit.record` 는 호출자 트랜잭션에 넣는다(변경과 기록이 함께). payload 는 마스킹된다. 감사 로그는 DB 트리거로 추가 전용
  (보존 기간 작업도 지우지 않는다). 관리 규칙(자기 잠금 방지·세션 폐기)은 `security/admin.py` 에만.
- **PII 값은 `LLMGateway.protect` 로 알린다.** 외부 공급자(`external: true`)면 게이트웨이가 가역 토큰으로 바꿔 보내고
  되돌린다(호출부는 원값만 본다, 근거 대조도 원값). 호출 기록·트레이스는 그 값과 패턴으로 마스킹된다. 새 LLM 호출
  경로를 만들면 그 요청의 PII 값을 `protect` 하는 것을 잊지 말 것. 어댑터는 SDK 없이 HTTP 로 부른다.
- **워크숍: 다음 질문은 `workshop.topics` 가 고른다**(LLM 은 평가·되묻는 문장·추출만). 발언은 마스킹 저장하고
  추출·근거 확인 모두 저장본을 본다(원문과 갈라지면 근거가 안 맞는다). 근거 확인은 정확 포함뿐, 요소 병합·게시
  합치기도 이름 **정확 일치**뿐(유사도 금지). 골든 질의는 `queries` 주제에서만, 면담자 질문을 베낀 후보는
  `extractor.keep` 이 버린다(quote 가 맞아 근거 확인으로는 못 잡는다 — ADR-0008). 추출 프롬프트·스키마를 바꾸면
  `scripts/spike_workshop.py --models gemma4:e4b` 로 다시 잰다.
- **평가 게이트의 불변식(`evaluation/gate.yaml` invariants)을 느슨하게 하지 않는다.** 모델·프롬프트·추출 스키마를
  바꾸면 평가 DB 에서 `eval-record` 로 실제 모델 게이트를 통과시키고 `domain-packs/sample/llm_cassette.json` 을
  함께 커밋한다 — 안 하면 CI 재생이 카세트 놓침으로 실패한다(놓친 요청을 지어내지 않는 것이 의도). 파이프라인에
  장애 주입 말고 다른 주입 지점을 만들지 말 것(평가가 운영과 다른 코드를 돌게 된다). `graph_down` 문항은 끝 객체가
  실재하는 탐색 질문이어야 하고, 권한 위반은 객체 근거(`o:Type/pk`)만 본다(집계 `q:N` 은 타입이 없다).
- **`/healthz` 만 재시작 판단에 쓴다.** `/readyz` 는 PG·Neo4j·LLM 을 보므로 서비스 관리자에 물리면 DB 가 잠깐
  끊긴 것만으로 멀쩡한 프로세스를 죽인다. Neo4j 만 안 되면 `degraded`(200)이지 503 이 아니다. 외부 LLM 공급자는
  준비 확인에서 호출하지 않는다(키 설정만).
- 설치본은 ontobot 을 **편집 가능(editable)** 으로 설치한다 — `config.py` 가 `.env`·`config/llm.yaml` 을 패키지
  위치의 `parents[3]`(저장소 루트)에서 찾기 때문이다. 이 경로 규칙을 바꾸면 설치본이 설정을 못 찾는다.
  번들 wheelhouse 에는 hatchling 과 `editables` 가 함께 있어야 한다(오프라인 설치 시험에서 발견).
- `deploy/**/*.ps1` 은 **UTF-8 BOM** 으로 저장한다(PowerShell 5.1 은 BOM 없는 파일을 cp949 로 읽는다). 문자열에서
  변수 바로 뒤에 한글이 오면 `${n}개` 처럼 감싼다(`$n개` 는 변수 이름이 된다). 네이티브 명령 출력은 `| Out-Host`
  로 보낸다 — 함수 반환값에 섞인다.
- 채팅 한 건은 답이 끝날 때까지 DB 연결을 쥔다. 연결 풀(`ONTOBOT_DB_POOL_SIZE`+`MAX_OVERFLOW`)이 동시 채팅 상한이다.
- 로그는 `ontobot.logs.setup` 으로만 꾸민다(JSON·마스킹·요청 ID). `logging.basicConfig` 를 새로 쓰지 말 것.
- `objects.reader` 가 삭제 표시 제외·**게시 버전에 선언된 속성만** 내보내기·**편집 레이어 덮어 읽기**를 맡는다.
  객체 값을 읽는 SQL 은 `FROM object` 가 아니라 reader 의 파생 행(`_OBJECTS`)에서 읽어야 한다 — 카드는 편집 값인데
  필터·집계가 원천 값이면 결과가 조용히 틀린다. P3 시나리오도 여기서 덮는다. 동기화는 `object_edit` 를 건드리지 않고
  `on_source_change` 훅(`objects.edits.record_source_changes`)이 충돌만 남긴다. 편집은 Action(Phase 10)만 만든다.
- **Action: 승인 = 실행, 한 트랜잭션에서 재검증**(게시 버전·대상·사전 조건). 재검증 실패는 예외가 아니라 `failed`
  기록이다. 메일·SMS 는 **트랜잭션 안에서 보내지 않는다** — `notification_delivery` 에 넣고 커밋 뒤 워커가 보낸다
  (롤백된 실행의 문자가 나가면 안 된다, 최소 한 번 발송 — ADR-0012). 설정하지 않은 채널은 꺼지고 그 Action 은
  미리보기에서 `blocked`(보낸 척 금지). 알림 문구는 `{이름}` 치환뿐(`str.format` 금지, LLM 이 쓰지 않는다).
  수신자 연락처는 API·감사·로그에 `mask_contact` 로만 낸다. Action 은 pk·제목·FK 속성을 바꾸지 못한다(게시 전 검사).
- **채팅 Action 은 미리보기까지만**(TRD 17.3). 플래너 `kind: action` 의 제안은 `actions.params` 가 대조한다: 대상을
  가리키는 말이 질문에 **그대로** 있거나 맥락 pk 여야 하고(실재하는 다른 객체도 지어낸 것), pk → 제목 **정확 일치**,
  값 파라미터는 질문에 그대로(enum·bool 만 자료형 변환). 이 규칙을 느슨하게 하지 말 것 — 평가 불변식
  `action_fabrications` = 0 이 기댄다. 미리보기 기록은 파이프라인이 아니라 `api/chat.py` 가 만든다(`origin=chat`).
  Action 이 요약에 없으면 플래너 프롬프트에 Action 규칙도 없다(측정한 프롬프트 유지) — 요약에 싣는 조건을 바꾸면 재측정.
- 설정의 비밀번호는 `URL.create` 로만 조립한다(문자열 조립 금지 — `!@` 가 구분자로 읽힌다). 시크릿 참조는
  `env:PREFIX`(프로세스 환경 → `.env`) 또는 `file:경로`.
- 프론트는 OpenAPI 생성 타입만 쓴다. API 를 바꾸고 `task.py openapi` 를 빼먹으면 계약 테스트가 실패한다.
  화면 문구는 `frontend/src/i18n/ko/index.ts`, 서버 안내 문구는 `chat/templates.py` 가 정본.
- TypeScript 는 5.9.3 고정(openapi-typescript·typescript-eslint 호환). 프롬프트·안내 문구 파일
  (`chat/templates.py`·`chat/answerer.py`·`query/planner.py`)만 줄 길이 규칙(E501)에서 뺐다.
