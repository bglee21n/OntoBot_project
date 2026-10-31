# TRD: 온톨로지 기반 챗봇 서비스 (OntoBot, 가칭)

| 항목      | 내용                                                             |
| --------- | ---------------------------------------------------------------- |
| 문서      | 기술 요구사항 문서(TRD) **v1.0** — v1(P1) 구현 확정                   |
| 작성일    | 2026-10-19 (v0.3: 2026-10-19 — 22절 개정 이력)                  |
| 기반 PRD  | `OntoBot_project-prd.md` v0.5                                    |
| 상세 범위 | MVP(P0)는 구현 가능한 수준으로, P1 이상은 끼워 넣을 자리(17절)와 v1 착수 결정(17.1)까지 |

## 1. 목적과 범위

PRD의 요구사항을 구현할 수 있는 기술 요구사항으로 옮긴다. 이 문서가 정하는 것은 다음과 같다.

- 기술 스택과 컴포넌트 경계
- 저장소 역할 분담과 데이터 모델
- 질의·근거 대조·보안·동기화·워크숍·평가의 처리 규칙
- MVP API
- 프로젝트 구조와 모듈 의존 규칙

PRD 절 번호는 `PRD 5.0`처럼 적는다.

## 2. 전제와 가정

PRD 10절에서 아직 확정되지 않은 항목은 아래 가정으로 설계한다. 가정이 바뀌면 오른쪽 열의 범위만 다시 본다.

| PRD 10절               | 이 문서의 가정                                        | 바뀌면 영향                                |
| ---------------------- | ----------------------------------------------------- | ------------------------------------------ |
| 1. 도메인              | 도메인 비의존 코어 + 도메인 팩(`domain-packs/`) 분리  | 도메인 팩만 새로 작성                      |
| 2. 배포 형태           | **폐쇄망 온프레미스 기본, SaaS도 지원** (사용자 결정) | SaaS 전용이면 멀티테넌시 추가              |
| 3. v1 Action           | v1에 포함(PRD 5.3 P1)                                 | 빼면 17절 Action 확장 지점이 v1.5로        |
| 4. 등급 ②              | 허용, 설정으로 끌 수 있음(끄면 ②를 ③으로 처리)        | 설정값만                                   |
| 5. MVP Action 미리보기 | 포함하지 않음                                         | 포함하면 `actions/` 일부(미리보기)를 MVP로 |
| 6. 저장소              | **PostgreSQL + Neo4j** (사용자 결정, 5절)             | —                                          |
| 7. 기존 시스템         | 신규 저장소로 구축                                    | 확장이면 이 문서 전체 재검토               |
| 8. write-back          | 하지 않음                                             | 하면 `actions/`에 원천 쓰기 어댑터 추가    |
| (추가) Action 부수효과 | **앱 안 알림·메일·SMS** — 사내 SMTP 릴레이·SMS 게이트웨이로만 (사용자 결정, 17.1) | 다른 외부 호출이 필요하면 `notify/channels/` 어댑터 추가 |
| (추가) 폐쇄망 여부     | `ONTOBOT_CLOSED_NETWORK`(기본 `true`) — 외부 서버를 부르는 화면 기능(음성 입력)을 끈다 (사용자 결정, 17.1) | SaaS 면 `false` |
| (추가) 설치 수단       | **Windows Server 2022+ 의 WSL2 + Docker Engine 컨테이너**, 배포는 화면(배포 콘솔) (사용자 결정, ADR-0013·0014 — ADR-0007 대체) | Linux 서버면 호스트 준비 스크립트만 바뀐다 |

## 3. 기술 스택

버전은 착수 시점의 안정 버전으로 고정(pinned)하고, 오프라인 설치 번들로 함께 배포한다.

| 구분             | 선택                                                                               | 이유                                   |
| ---------------- | ---------------------------------------------------------------------------------- | -------------------------------------- |
| 백엔드 언어      | Python 3.12                                                                        | LLM·평가 생태계, 팀 경험               |
| API              | FastAPI + Uvicorn, Pydantic v2                                                     | 타입 기반 검증, OpenAPI 자동 생성, SSE |
| ORM·마이그레이션 | SQLAlchemy 2 (async, asyncpg), Alembic                                             |                                        |
| 관계형 DB        | PostgreSQL (+ pgvector 확장 설치, 사용은 P1)                                       | 기록의 원본(5절)                       |
| 그래프 DB        | Neo4j Community + 공식 Python 드라이버                                             | 링크 탐색 투영(5절)                    |
| 작업 큐          | PostgreSQL `job` 테이블 + 워커 프로세스 (`SELECT … FOR UPDATE SKIP LOCKED`)        | Redis 등 운영 대상을 늘리지 않는다     |
| LLM              | 자체 게이트웨이(10절) + 어댑터: Ollama, OpenAI 호환(vLLM 등), Anthropic Claude API | 폐쇄망·SaaS 겸용, 모델 교체            |
| 인증             | OIDC(Authorization Code + PKCE), 서버 세션 쿠키(HttpOnly)                          | SSO, 세션 즉시 폐기 가능               |
| 프론트엔드       | React + TypeScript + Vite, TanStack Query, React Router                            | 상태가 많은 편집·검토 화면             |
| API 타입         | OpenAPI → `openapi-typescript`로 프론트 타입 생성                                  | 백엔드·프론트 스키마 일치              |
| 정적 분석        | ruff, mypy, import-linter / eslint, tsc                                            | 16절 의존 규칙 강제                    |
| 테스트           | pytest, pytest-asyncio, 운영과 같은 이미지의 PostgreSQL·Neo4j 컨테이너(`compose.test.yaml`) | 19절                                   |
| 관측성           | JSON 구조화 로그 + DB `trace` 테이블                                               | 외부 APM 의존 없음(폐쇄망)             |
| 패키징           | 컨테이너 이미지(앱 하나 — api·worker·migrate, 배포 콘솔) + 제3자 이미지(pgvector·Neo4j·Ollama, `images.lock` 다이제스트 고정) + Ollama 모델을 `docker save` 한 **서명된 오프라인 번들**(Ed25519 `SHA256SUMS.sig`). 운영은 WSL2 배포판 안의 Docker Engine + compose, 절차의 정본은 `ontobotctl`, 업그레이드·롤백·백업은 배포 콘솔 화면(ADR-0013·0014). WinSW·릴리스 폴더 방식(ADR-0010)은 대체됨 | 폐쇄망 설치, 운영 대상 최소화, 화면 배포 |

## 4. 시스템 아키텍처

PRD 6절의 5계층을 컴포넌트로 나누면 다음과 같다.

```
[브라우저: React SPA]
     │  HTTPS(세션 쿠키) · SSE
     ▼
[api: FastAPI 라우터] ──── security (인증 · RBAC · PII · 감사)
     │
     ├── chat.pipeline ──┬── query    (planner → validator → compiler → executor)
     │                   ├── grounding(verifier → grader)      ※ LLM·DB 비의존
     │                   └── llm.gateway ──► [Ollama / vLLM]  |  [Claude API]
     ├── ontology · sync · workshop · evaluation · tracing
     ▼
[PostgreSQL]  ◄── objects.reader / objects.store      (기록의 원본)
     │  graph_outbox
     ▼
[worker: jobs] ── graph.projector ──► [Neo4j]         (링크 투영)
               ── sync.runner     ◄── [원천 DB · 파일]
```

질의 한 건의 흐름(상세는 7절):

1. `POST /api/v1/chat` → 세션에서 사용자·역할 확인
2. 대화 맥락(객체 식별자) 로드 → 플래너가 QueryPlan 생성 → 검증
3. 단일 타입 조건은 PostgreSQL, 링크 탐색은 Neo4j(pk 집합만) → PostgreSQL에서 속성·권한 적용
4. 조회 조건 문장 생성(결정론적) → SSE `conditions` 전송
5. 답변 생성 → 근거 대조 → 등급 → SSE `answer`·`citations`·`grade` 전송
6. `trace` 기록

## 5. 저장소 역할 분담 — PostgreSQL + Neo4j

### 5.1 원칙

|              | PostgreSQL                                                                            | Neo4j                                     |
| ------------ | ------------------------------------------------------------------------------------- | ----------------------------------------- |
| 역할         | **기록의 원본(system of record)**                                                     | **링크 그래프 투영(projection)**          |
| 담는 것      | 스키마, 객체 속성, 링크 원본, 편집 레이어, 동기화·감사·평가·워크숍·트레이스, 벡터(P1) | 노드 `(type, pk, title)`, 관계(Link Type) |
| 담지 않는 것 | —                                                                                     | 속성 값, PII, 권한 정보                   |
| 복구         | 백업                                                                                  | **언제든 PostgreSQL에서 재구축**          |

Neo4j에 속성을 두지 않는 이유는 두 가지다. 권한·PII 판단을 한 곳(PostgreSQL)에서만 하기 위해서이고, 두 저장소의 값이 어긋날 자리를 없애기 위해서다.

### 5.2 쓰기 경로 — 트랜잭셔널 아웃박스

1. 동기화(또는 P1 Action)가 PostgreSQL의 `link`를 바꾸는 **같은 트랜잭션**에서 `graph_outbox`에 이벤트(`link_upsert` / `link_delete` / `node_upsert` / `node_delete`)를 쓴다.
2. 워커의 `graph.projector`가 아웃박스를 순서대로 읽어 Neo4j에 반영하고, 처리한 위치를 기록한다.
3. 반영 지연(`projection_lag` = 가장 오래된 미처리 이벤트의 나이)을 `/api/v1/graph/status`로 노출한다.

### 5.3 읽기 경로

- QueryPlan의 **탐색(traverse) 단계만** Neo4j에서 실행한다. 결과는 대상 타입의 **pk 집합**뿐이다.
- 속성 필터·정렬·집계·권한은 그 pk 집합을 받아 PostgreSQL에서 적용한다.
- **LLM은 SQL도 Cypher도 직접 쓰지 않는다.** 둘 다 검증된 QueryPlan에서 컴파일러가 파라미터 바인딩으로 만든다(7절).
- 탐색 결과 pk가 상한(기본 10,000)을 넘으면 `failed(too_broad)`로 처리하고 조건을 좁혀 달라고 안내한다.

### 5.4 권한

- 검증기는 **읽을 수 없는 타입을 거치는 탐색을 거부**한다. 거치기만 해도 연결 여부라는 정보가 새기 때문이다.
- 컴파일된 Cypher에도 허용된 라벨 조건을 함께 건다(이중 방어).

### 5.5 장애와 정합성

| 상황                             | 동작                                                                                                                                                                                                           |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Neo4j 접근 불가                  | 탐색이 필요한 질의는 `failed(graph_unavailable)`로 밝힌다. 단일 타입 질의는 PostgreSQL만으로 계속 동작. **PostgreSQL `link` 테이블로 조용히 대체하지 않는다**(결과가 같다는 보장을 테스트하지 않은 경로이므로) |
| 투영 지연이 임계(기본 10분) 초과 | 조회 조건 문장에 "관계 정보는 HH:MM 기준"을 덧붙인다                                                                                                                                                           |
| 불일치 의심                      | 매일 대사 작업이 타입·링크 타입별 건수와 해시를 비교하고, 차이를 트레이스·관리 화면에 표시                                                                                                                     |
| 복구                             | `scripts/graph_rebuild.py`(또는 `POST /graph/rebuild` 작업)가 Neo4j를 비우고 PostgreSQL에서 다시 투영한 뒤 시작 시점까지의 아웃박스를 처리됨으로 표시. 재구축 중 쌓인 이벤트는 뒤에 다시 반영된다(MERGE 라 멱등) |
| 참조 대상 없음                   | 링크 끝점 객체가 아직 없으면 `stub` 노드로 만든다. 그 객체가 동기화되면 실제 노드가 된다. 남은 stub 수는 대사가 보고한다 |
| 반영 실패                        | 투영 묶음(최대 500 이벤트) = Neo4j 트랜잭션 하나 = 처리 표시 하나. 실패하면 처리 표시도 롤백되어 다음 주기에 다시 반영한다. 드라이버 재시도는 5초로 줄였다(워커 한 주기가 멎지 않게) |

## 6. 데이터 모델 (PostgreSQL)

주요 컬럼만 적는다. 모든 테이블은 `id`, `created_at`을 가진다.

### 6.1 온톨로지

| 테이블             | 주요 컬럼                                                                                                                                                                            |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `ontology_version` | `version_no`, `status`(draft/published/archived), `parent_id`, `created_by`, `published_at`                                                                                          |
| `object_type`      | `version_id`, `api_name`, `display_name`, `description`, `pk_property`, `title_property`                                                                                             |

- 타입·속성·링크 타입의 `api_name`은 식별자 형식만 받는다. 타입은 `^[A-Z][A-Za-z0-9_]{0,63}$`, 속성·링크 타입은 `^[a-z][a-z0-9_]{0,63}$`. 타입 `api_name`은 Neo4j 라벨로 쓰이고 **라벨은 바인드할 수 없으므로**, 이 검증이 Cypher 조립 안전성의 근거다.
| `property_type`    | `object_type_id`, `api_name`, `display_name`, `data_type`(string/int/decimal/bool/date/datetime/enum), `enum_values`, `nullable`, `indexed`, `pii_kind`(null/name/phone/email/rrn/…) |
| `link_type`        | `version_id`, `api_name`, `from_type`, `to_type`, `cardinality`(one/many), `source`(fk_property / join_mapping)                                                                      |

- 게시된 버전은 수정하지 않는다. 고치려면 새 draft를 만든다.
- 읽기 경로는 **현재 게시 버전**에 선언된 속성만 내보낸다. 버전에서 빠진 속성의 값이 객체 jsonb에 남아 있어도 보이지 않는다.

### 6.2 객체와 링크

| 테이블   | 주요 컬럼                                                                                                   |
| -------- | ----------------------------------------------------------------------------------------------------------- |
| `object` | `type`, `pk`, `properties`(jsonb), `source_id`, `row_hash`, `synced_at`, `deleted_at` — 유일키 `(type, pk)` |
| `link`   | `link_type`, `from_pk`, `to_pk`, `source_id`, `synced_at`, `deleted_at` — 부분 유일키 `(link_type, from_pk, to_pk) WHERE deleted_at IS NULL` |

- 속성은 jsonb 한 열에 둔다. `indexed=true`인 속성에는 표현식 인덱스를 만든다. Alembic 마이그레이션이 아니라 **게시 시 발행되는 작업**(`job.kind = create_property_index`)이 `CREATE INDEX CONCURRENTLY IF NOT EXISTS`로 멱등하게 만든다(스키마가 데이터이므로 마이그레이션 파일로 고정할 수 없다).
- **객체 읽기는 `objects.reader.resolve()` 하나만 거친다.** 이 함수는 MVP에서 원천 값을 그대로 돌려주고, P1에서 편집 레이어를 덮고, P3에서 시나리오를 덮는다. 호출부는 바뀌지 않는다.

### 6.3 편집 레이어 (테이블은 P1, 읽기 경로는 MVP부터 고정)

| 테이블          | 주요 컬럼                                                                                                                          |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `object_edit`   | `type`, `pk`, `property`, `value`, `base_source_value`(편집 당시 원천 값), `action_run_id`, `edited_by`, `edited_at`, `cleared_at`, `cleared_by` — 활성 편집은 부분 유일 `(type, pk, property) WHERE cleared_at IS NULL` |
| `sync_conflict` | `edit_id`, `new_source_value`, `source_synced_at`, `status`(open/acknowledged/cleared), `resolved_by`, `resolved_at` — 살아 있는 충돌은 편집당 하나(부분 유일 `edit_id WHERE status <> 'cleared'`) |

규칙(PRD 5.3, 11절): 보이는 값 = 활성 편집 값이 있으면 그것, 없으면 원천 값. 충돌은 기록하되 편집 값이 이긴다.

v0.13 (Phase 9 구현):

- **덮어 읽기는 `objects.reader` 한 곳**: 객체를 읽는 SQL(`resolve`·`run_query`·`existing_values`)은 모두 `object` 에 활성 편집을 `jsonb_object_agg` 로 덮은 파생 행에서 읽는다. 그래서 근거 카드·조회 조건·집계·참조 값 대조·탐색의 끝 객체 조건이 같은 값을 본다. 그래프 대사·재구축 읽기는 원천 값을 본다(Neo4j 에는 pk·제목만 있고 Action 은 pk·FK·제목을 못 바꾼다 — 17.1-3). 편집이 있는 타입은 속성 표현식 인덱스가 덮인 값에 쓰이지 않는다(규모가 문제 되면 ADR-0003 과 함께 재검토).
- 값은 `object.properties` 와 같은 JSON 표현이다. JSON null 과 속성 없음은 같은 NULL 로 비교한다.
- 같은 속성을 다시 편집하면 이전 편집을 해제하고 새로 만든다(편집 당시 원천 값도 새로 적는다).
- **충돌 판정**(동기화 병합 트랜잭션, `sync.runner.on_source_change` → `objects.edits.record_source_changes`): 바뀐 객체의 활성 편집마다 지금 원천 값이 편집 당시 값과 같으면 살아 있는 충돌을 `cleared` 로 닫고, 다르면 살아 있는 충돌의 값·시각을 고쳐 `open` 으로 되돌리거나(기록한 값과 다를 때만) 새로 `open` 을 만든다. 편집을 해제하면 그 충돌도 `cleared`.
- **값 계보**(`objects.lineage`): 편집된 속성은 편집(편집자·시각·Action 실행·편집 당시 원천 값·충돌), 아니면 객체의 원천(데이터 소스 → 매핑 → `synced_at` 이전에 시작한 가장 늦은 성공 실행). 원천이 없는 객체(시드)는 원천 없음.
- 편집을 **만드는** API 는 없다 — Action 실행(Phase 10)만 만든다. 충돌 확인·편집 해제는 모델러·관리자, 읽을 수 있는 타입에만(읽을 수 없으면 404). 감사 이벤트 `objects.conflict_acknowledged`·`objects.edit_cleared`.

### 6.4 동기화

| 테이블        | 주요 컬럼                                                                                                                                                                           |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `data_source` | `kind`(postgres/oracle/mysql/csv), `name`, `secret_ref`(접속정보는 시크릿 저장소 참조, 평문 저장 금지), `status`                                                                    |
| `mapping`     | `source_id`, `object_type`, `source_query` 또는 `table`, `column_map`(jsonb: 컬럼→속성), `pk_column`, `mode`(incremental/full), `watermark_column`, `link_rules`(jsonb), `schedule` |
| `sync_run`    | `mapping_id`, `status`(running/succeeded/failed), `started_at`, `finished_at`, `rows_read`, `rows_upserted`, `rows_deleted`, `watermark`, `error`                                   |

### 6.5 대화·관측

| 테이블                 | 주요 컬럼                                                                                                                                                                                                                                             |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `conversation`         | `user_id`, `title`, `updated_at`                                                                                                                                                                                                                      |
| `message`              | `conversation_id`, `role`, `content`, `status`(ok/empty/failed/not_query), `grade`(1/2/3/null), `trace_id`, `meta`(jsonb: 조회 조건 문장·`graph_as_of`·인용 목록·추정 문장 번호·실패 사유 — 기록에서 답을 다시 그리는 데 쓴다. 근거 **값**은 담지 않는다) |
| `conversation_context` | `conversation_id`, `refs`(jsonb: `[{type, pk}]`) — **식별자만**, 값은 담지 않는다(PRD 5.2)                                                                                                                                                            |
| `trace`                | `user_id`, `message_id`, `plan`(jsonb), `sql_masked`, `cypher_masked`, `status`, `failure_reason`, `grade`, `fact_sentences`, `verified_sentences`, `removed_citations`(jsonb), `stage_latency_ms`(jsonb), `llm_calls`(jsonb: 역할·모델·지연·토큰·성공 여부·외부 여부와 **마스킹된** 프롬프트·응답) |

### 6.6 보안

| 테이블              | 주요 컬럼                                                                                                                   |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `app_user`          | `oidc_sub`(`iss|sub`), `username`·`password_hash`(로컬 계정, argon2), `display_name`, `status`                                                                                        |
| `user_session`      | `token_hash`(SHA-256 — 원문은 쿠키에만), `user_id`, `expires_at`, `revoked_at`, `auth_method`(local/oidc) |
| `auth_request`      | 진행 중인 OIDC 로그인: `state_hash`, `nonce`, `code_verifier`, `return_to`(같은 사이트 상대 경로만), `expires_at` — 콜백에서 한 번만 꺼내 지운다 |
| `role`, `user_role` |                                                                                                                             |
| `data_group`·`group_member`·`group_type_permission`·`group_link_permission` | 데이터 권한(v0.22, 17.6) — 그룹 × 타입 {읽기·생성·편집·삭제}, 그룹 × 링크 {연결}, 사용자 ↔ 그룹. MVP 의 `type_permission`(역할 × 타입 읽기)은 마이그레이션 0012 에서 같은 이름의 그룹으로 옮기고 지웠다 |
| `audit_log`         | `ts`, `actor_id`, `event`, `target`, `payload`(마스킹), `seq`·`prev_hash`·`hash`(해시 체인 — 트리거가 매김, 17.8) — 앱 DB 계정에 **INSERT·SELECT만** 허용 |

### 6.7 평가·워크숍·작업

| 테이블                    | 주요 컬럼                                                                                                                                                                                                                      |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `golden_set`              | `name`, `domain_pack`, `ontology_version_id`, `roles`(jsonb, 평가 전용 역할 → 타입)                                                                                                                                                                                 |
| `golden_item`             | `golden_set_id`(FK), `key`(팩 문항 id, 셋 안에서 유일), `question`, `expected_failure`, `absent_values`, `required_conditions`, `context`, `note`, `test_role`, `answerable`, `expected_status`, `expected_grade`, `expected_answer`(워크숍), `required_values`(jsonb), `forbidden_values`(jsonb, 권한 침투용), `fault`(none/executor_error/graph_down/timeout), `source`(manual/workshop)       |
| `eval_run`                | `golden_set_id`, `ontology_version_id`, `llm_config`(jsonb, 역할별 공급자·모델), `status`(running/succeeded/failed), `metrics`(jsonb, 13.2), `gate_passed`, `started_at`, `finished_at`                                         |
| `eval_result`             | `run_id`, `item_id`, `status`, `failure_reason`, `grade`, `answer`, `conditions`, `citations`(jsonb), `verdict`(jsonb: 정답·권한 위반·실패 은폐 판정 근거), `stats`(jsonb: 지연·사실 문장 수)                                                                                   |
| `interview_session`       | `interviewee_id`, `title`, `topic_state`, `status`(open/submitted/published), `published_version_id`                                                                                                                                                          |
| `interview_utterance`     | `session_id`, `speaker`, `text`(마스킹 저장), `seq`                                                                                                                                                                            |
| `draft_element`           | `session_id`, `kind`(object_type/property/link_type/action_candidate/golden_query), `payload`(jsonb), `evidence`(jsonb: `[{utterance_id, quote}]`), `evidence_ok`, `status`(proposed/confirmed/edited/rejected), `reviewed_by` |
| `job`                     | `kind`, `payload`, `status`, `run_after`, `attempts`, `locked_by`, `locked_at` — 기한은 **DB 시계**(`now()`)로만 정하고 비교한다(v0.20: 앱 시계로 넣은 기한이 컨테이너 DB 와 어긋나 방금 넣은 작업을 못 집던 간헐 실패) |
| `graph_outbox`            | `id`(bigserial — **소비 순서**), `event`, `payload`, `created_at`, `processed_at` — 처리 위치는 `processed_at IS NULL`인 가장 작은 `id`다(별도 위치 테이블 없음)                                                                |

의미 캐시는 MVP에 두지 않는다. 도입하면 캐시 키에 **권한 지문**(사용자의 역할 집합 + 게시 스키마 버전의 해시)을 넣어 권한이 다른 사용자끼리 답변을 공유하지 않게 한다(PRD 5.5).

## 7. 질의 파이프라인 (PRD 5.0·5.2)

### 7.1 단계

| 단계              | 모듈                               | 내용                                                                                                                                    |
| ----------------- | ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 1. 맥락 로드      | `chat.context`                     | 대화의 객체 식별자를 읽는다. 값은 4단계에서 다시 조회한다                                                                               |
| 2. 계획           | `query.planner`                    | 권한으로 거른 스키마 요약 + 질문 + 맥락 식별자 → QueryPlan(JSON, 구조화 출력). 질의가 아니면 `not_query`, 조건을 스키마로 표현할 수 없으면 `cannot_answer`(→ `failed(plan_invalid)`) — 표현할 수 없는 조건을 **빼고** 계획을 내지 않는다. 요청할 수 있는 Action 이 있으면 Action 요청 말에 `action`(제안)을 낸다 — 조회하지 않고 17.3 으로 간다(v0.15) |
| 3. 검증           | `query.validator`                  | 타입·속성·링크 실재, 연산자 허용 목록, 값 타입 변환, enum·참조 값 실재 대조(링크 FK 속성의 eq·in 값이 대상 타입 pk 로 있는가 — `query.references`, 링크 끝 조건 포함), 권한, `traverse` 항목 수(3). 질문의 숫자가 계획 어디에도 없으면 오류(`query.coverage`, v0.7). 실패하면 **모든 오류를 한꺼번에** 붙여 1회 재계획. 검증 전 결정론적 재작성 하나: FK 속성의 eq·in 값이 pk 가 아니고 **모두** 그 타입 객체의 제목과 정확히 같으면 같은 뜻의 `traverse` 로 옮긴다(조건을 빼지 않는다, 유사도 없음) |
| 4. 컴파일·실행    | `query.compiler`, `query.executor` | 바인딩 SQL/Cypher 생성 → 실행(타임아웃: SQL 10초, Cypher 5초 기본) → `status = ok / empty / failed`                                     |
| 5. 조회 조건 문장 | `query.conditions`                 | QueryPlan에서 **결정론적으로** 생성("시설 · 지역 = 서울 · 위험등급 ≥ 3 · 소유 조직 = 강남지사"). LLM이 만들지 않는다                    |
| 6. 답변           | `chat.answerer`                    | `ok`일 때만 LLM 호출. `empty`·`failed`·`not_query`는 템플릿(`chat.templates`)                                                           |
| 7. 대조·등급      | `grounding`                        | 8절                                                                                                                                     |
| 8. 기록           | `tracing.recorder`                 | `trace` 한 행                                                                                                                           |

### 7.2 QueryPlan

```json
{
  "target": "Facility",
  "filters": [
    {"property": "region", "op": "eq", "value": "서울"},
    {"property": "risk_grade", "op": "gte", "value": 3}
  ],
  "traverse": [
    {"link": "owned_by", "direction": "out", "to": "Organization",
     "filters": [{"property": "name", "op": "eq", "value": "강남지사"}]}
  ],
  "context_refs": [],
  "aggregate": {"fn": "count", "property": null, "group_by": ["risk_grade"]},
  "sort": [{"property": "risk_grade", "dir": "desc"}],
  "limit": 20
}
```

- `traverse`는 **대상(target)을 좁히는 조건**이다. "링크로 연결된 객체가 이 조건을 만족하는 대상만 남긴다"는 뜻 하나로만 쓴다. 답이 되는 타입은 언제나 `target`이다.
- MVP 의 `traverse` 항목은 **대상에서 링크 하나를 건너간 1홉 조건**이고 항목끼리는 AND 다(최대 3개). 여러 홉을 잇는 경로는 아직 표현하지 않는다. 실행(v0.7): 링크 끝 객체의 조건은 PostgreSQL 에서 pk 집합으로 먼저 구하고(Neo4j 노드에는 속성 값이 없다), Neo4j 는 `EXISTS { MATCH (t)-[:link]->(n:To) WHERE n.pk IN $p }` 로 대상 pk 만 돌려준다(stub 끝점은 연결로 치지 않는다). 대상 조회는 그 pk 집합(`= ANY(:pks)`)으로 좁힌다. 링크 끝 조건에 맞는 객체가 없으면 그래프를 보지 않고 `empty` 다.
- 링크의 양 끝 중 하나라도 읽을 수 없으면 대상 타입과 같은 규칙으로 `failed(forbidden)` 이다(재계획하지 않는다).
- `context_refs` 는 대화 맥락(직전 `ok` 객체 목록 답의 식별자, 최대 20개)에서만 고를 수 있다. 대상 타입이면 대상 pk 를, `traverse` 의 끝 타입이면 그 끝 객체 pk 를 좁힌다. 맥락은 집계·0건·실패 답으로 바뀌지 않는다.
- 연산자는 `eq, ne, gt, gte, lt, lte, in, contains, between, is_null` 고정이다. 표현식은 받지 않는다.
- 값은 언제나 바인드 변수로 나간다.

### 7.3 상태와 등급은 다른 축이다

| 상황                      | status                              | grade | 사용자에게                                                         |
| ------------------------- | ----------------------------------- | ----- | ------------------------------------------------------------------ |
| 질의가 아님(인사 등)      | `not_query`                         | 없음  | 무엇을 물을 수 있는지 안내                                         |
| 계획 해석 실패(재계획 후) | `failed(plan_invalid)`              | 없음  | "질문을 조회 조건으로 바꾸지 못했습니다" + 다시 묻는 예시          |
| 권한 없음                 | `failed(forbidden)`                 | 없음  | "조회 권한이 없는 정보입니다"                                      |
| 실행 오류·타임아웃        | `failed(execution_error / timeout)` | 없음  | "조회에 실패했습니다" + 다시 시도 안내                             |
| 관계 저장소 불가          | `failed(graph_unavailable)`         | 없음  | "관계 정보를 조회할 수 없습니다"                                   |
| 결과 과다                 | `failed(too_broad)`                 | 없음  | 조건을 좁혀 달라는 안내                                            |
| 0건                       | `empty`                             | ①     | 템플릿: "조회 조건(…)에 맞는 객체가 없습니다". LLM을 거치지 않는다 |
| 정상                      | `ok`                                | ①·②·③ | 8절 판정 결과                                                      |
| Action 요청(v0.15)        | `action_preview`                    | 없음  | 미리보기(실행 기록) 또는 확인 사항 + 요청 폼(17.3). 실행은 승인 뒤다 |

`failed`는 어떤 경우에도 `empty`처럼 보이게 하지 않는다(PRD 5.0 "조회 실패는 실패라고 밝힌다").

## 8. 근거 대조와 등급 (PRD 5.0)

### 8.1 인용 표기

답변 생성 LLM에게 조회 결과를 번호 붙은 레코드로 주고, 문장마다 아래 형식으로 인용하게 한다.

- 객체 속성: `[[o:Facility/F-001#risk_grade]]`
- 질의 결과(집계 등): `[[q:1#count]]`

레코드에는 원값과 함께 서버가 만든 **표시용 문자열**(천 단위 구분, 날짜 형식)을 준다. 모델이 숫자를 제멋대로 반올림하면 대조에 실패하도록 하기 위함이다. 평균은 서버가 소수 둘째 자리로 반올림한다. 데이터 블록의 각 줄은 `인용키 | 이름 | 값` 이다 — 인용키를 통째로 보여 주지 않으면 모델이 라벨까지 인용에 베낀다(ADR-0004 실측).

### 8.2 판정 규칙 (`grounding/`, LLM·DB 비의존 순수 함수)

1. 문장 단위로 나눈다.
2. **사실 문장**을 가린다: 숫자·날짜를 담았거나, 조회 결과의 객체 제목·enum 값을 담은 문장. 그 밖의 연결 문장("다음과 같습니다")은 판정에서 뺀다. 판정 전에 **조회 조건에 쓰인 값**(원값·표시 문자열·날짜의 연도)을 문장에서 걷어 낸다 — "2015년 이전에 설치된 철탑은 다음과 같습니다"는 조건의 되풀이이지 결과에 대한 주장이 아니다. 조건 밖의 값이 남으면 여전히 사실 문장이다. 형식이 깨진 인용도 `malformed` 로 지운 기록을 남긴다.
3. 각 인용에 대해 다음을 확인한다.
   - 대상 객체(또는 질의 결과)가 이번 조회 결과에 실제로 있는가
   - 사용자가 그 타입을 읽을 수 있는가
   - 그 속성의 원값 또는 표시용 문자열이 문장 안에 **그대로 포함**되는가
   - 비교 전 정규화는 유니코드 NFC, 공백 접기, 숫자의 천 단위 구분 제거, 날짜의 ISO 변환뿐이다. **유사도 점수는 쓰지 않는다.**
4. 대조에 실패한 인용은 답변에서 지우고 `trace.removed_citations`에 남긴다.
5. 등급을 정한다.
   - ①: 사실 문장이 하나 이상이고 모두 대조를 통과한 인용을 가짐
   - ②: 통과한 인용이 하나 이상이지만, 통과한 인용이 없는 사실 문장이 섞임 → 그 문장에 "추정" 표시
   - ③: 통과한 인용이 없음(사실 문장이 없는 `ok` 답변 포함) → 답변 본문을 버리고 거부 템플릿
6. 등급 ②를 끄는 설정이면 ②를 ③으로 처리한다(PRD 10절 4번).

LLM이 스스로 매긴 등급이나 확신도는 쓰지 않는다.

### 8.3 답변 본문은 검증 뒤에 보낸다

답변을 토큰 단위로 바로 흘려보내면 **나중에 지워질 인용과 문장이 잠깐 화면에 보인다.** 그래서 SSE로는 진행 단계(`stage`)와 조회 조건(`conditions`)을 먼저 보내고, 답변 본문은 대조를 마친 뒤 한 번에 보낸다(14.2). 응답 체감 시간과 맞바꾼 결정이며 ADR 후보로 둔다(21절).

## 9. 보안 (PRD 5.5)

### 9.1 인증·권한

- **인증**: OIDC(Authorization Code + PKCE) → 서버 세션 쿠키(HttpOnly, SameSite=Lax). 폐쇄망에 IdP가 없는 환경을 위해 개발·소규모용 로컬 계정을 둔다(운영에서는 끌 수 있다).
- **권한 적용 3지점**
  1. 플래너 컨텍스트: 읽을 수 있는 타입·링크만 스키마 요약에 싣는다.
  2. 검증기: 계획이 읽을 수 없는 타입을 대상·필터·탐색 경로로 쓰면 `failed(forbidden)`.
  3. 실행기: 컴파일된 SQL/Cypher가 허용 타입만 조회하는지 다시 확인한다.
- 권한 지문(`security.fingerprint`)은 파생 저장소(의미 캐시 등)의 키로 쓴다(6.7 끝 문단).

### 9.2 PII

- 대상 속성은 `property_type.pii_kind`로 선언한다. 인터뷰 발언처럼 스키마가 없는 텍스트에는 패턴 기반 탐지(전화·이메일·주민등록번호 등)를 쓴다.
- **외부 LLM 호출 시**(공급자 설정 `external: true`): 요청마다 가역 토큰(`⟦P1⟧`)으로 바꿔 보내고, 응답을 받은 뒤 원값으로 되돌린다. 토큰 대응표는 요청 메모리에만 둔다(v0.8: `security.pii.Tokenizer`, 게이트웨이 하나 = 요청 하나).
  - 바꾸는 값: 파이프라인이 `LLMGateway.protect` 로 알린 값(`pii_kind` 속성에 건 조건 값, 결과 레코드의 `pii_kind` 속성 원값·표시 문자열) + 패턴(주민번호·전화·이메일). 두 글자 미만 값은 문장을 망가뜨려 제외한다. 값은 정확 일치로만 찾고 긴 값부터 바꾼다.
  - 모델이 토큰을 망가뜨리거나 지어내면 원값이 돌아오지 않아 근거 대조에 실패한다(안전한 쪽).
  - 플래너용 맥락 제목은 제목 속성이 PII 면 싣지 않는다(pk 로 대신).
- **근거 대조는 원값으로 한다**(PRD 5.5). 되돌린 뒤에 대조한다.
- 로그·트레이스·감사 로그에는 언제나 마스킹된 값만 남긴다.

### 9.3 그 밖의 보안 요구

- 비밀값(DB 접속정보, IdP 클라이언트 비밀, LLM API 키)은 환경변수·시크릿 파일로만 받고 DB에는 참조만 저장한다.
- **데이터 값 속 지시문은 데이터로만 취급한다.** 조회 결과는 구분된 데이터 블록으로 LLM에 넣는다. 또 LLM 출력은 검증(QueryPlan 스키마, 근거 대조)을 거쳐야만 효과를 가지므로, 데이터에 숨은 지시가 실행으로 이어질 경로가 없다.
- **감사 이벤트**: 로그인·로그아웃, 역할·권한 변경, 스키마 draft 생성·게시, 데이터 소스·매핑 변경, 동기화 수동 실행, 그래프 재구축, 워크숍 게시, 골든셋 변경, 평가 실행, 감사 로그 조회. v0.8 에 더한 것: 사용자 생성·정지/재사용(`security.user_status_changed`), 트레이스 조회(`security.trace_viewed`), 보존 기간 삭제(`ops.retention_purged`, 건수). 역할·권한 변경은 전후 값을 남긴다. 감사 로그 조회 기록은 결과를 읽은 **뒤에** 넣는다(자기 자신이 결과에 섞이지 않게). 워크숍·골든셋·평가 이벤트는 그 기능의 Phase 에서 연결한다.
- **보존 기간**은 테이블별 설정(`ONTOBOT_RETENTION_TRACE_DAYS` 90 · `_MESSAGE_DAYS` 365 · `_UTTERANCE_DAYS` 180, 0 = 지우지 않음)이고 만료분은 워커의 매일 작업(`retention_purge`)이 지운다. 메시지가 모두 지워진 대화는 맥락과 함께 지운다. 감사 로그는 DB 트리거로 추가 전용이라 이 작업이 지우지 않는다 — 보관·이관은 운영 절차(P1)다.
- **관리 규칙**: 관리자는 자기 계정을 정지하거나 자기 관리자 역할을 뺄 수 없다(마지막 관리자 잠금 방지, 복구는 CLI). 정지·역할 변경은 그 사용자의 세션을 즉시 폐기한다. 역할별 타입 읽기 권한은 집합 교체(PUT)다.
- **로그**: JSON 한 줄(`ts`·`level`·`logger`·`msg`·`request_id`·`exc`), 메시지·인자·예외 문자열 마스킹, `ONTOBOT_LOG_DIR` 가 있으면 크기 회전 파일(`api.log`·`worker.log`). 요청 ID 는 `X-Request-ID`(안전한 형식이면 이어 쓰고 아니면 새로 만듦)로 응답에 돌려준다.

## 10. LLM 게이트웨이

```python
class LLMGateway:
    async def complete(self, role: str, messages: list[Message]) -> Completion: ...
    async def structured(self, role: str, messages: list[Message],
                         schema: type[BaseModel]) -> BaseModel: ...
```

- 역할(`planner` / `answerer` / `interviewer` / `extractor`)마다 공급자·모델·타임아웃을 설정한다. 모델 ID는 코드에 적지 않는다.

  ```yaml
  llm:
    providers:
      local:  {adapter: ollama, base_url: "http://ollama:11434", external: false}
      claude: {adapter: anthropic, api_key_env: ANTHROPIC_API_KEY, external: true}
    roles:
      planner:     {provider: local, model: "<모델명>", timeout_s: 30}
      answerer:    {provider: local, model: "<모델명>", timeout_s: 60}
      interviewer: {provider: local, model: "<모델명>", timeout_s: 30}
      extractor:   {provider: local, model: "<모델명>", timeout_s: 60}
  ```

- **어댑터**(v0.8): `ollama`, `openai_compat`(`{base_url}/chat/completions`, `response_format: json_schema`, 키 선택), `anthropic`(Messages API, system 은 최상위로, 구조화 출력은 도구 하나를 강제 호출). 셋 다 SDK 없이 HTTP 로 부른다(폐쇄망 번들 축소, 외부 호출 경로를 어댑터 파일로 한정). API 키는 `api_key_env` 가 가리키는 환경변수(없으면 `.env`)에서만 읽는다.
- **구조화 출력**: JSON 스키마로 요청 → Pydantic 검증 → 실패하면 오류 내용을 붙여 1회 재시도 → 그래도 실패하면 호출부가 `failed(plan_invalid)` 등으로 처리.
- 모든 호출은 마스킹된 프롬프트·응답·지연을 트레이스에 남긴다.
- **모델이나 공급자를 바꾸면 평가 게이트(13절)를 통과해야 운영 설정에 반영한다.**
- SDK import는 `llm/adapters/` 안에서만 한다(16.3).

## 11. 동기화 (PRD 5.1)

| 모드 | 동작                                                                      |
| ---- | ------------------------------------------------------------------------- |
| 증분 | `watermark_column > 마지막 watermark`인 행만 읽어 upsert                  |
| 전체 | 전 행을 읽고 `row_hash`로 변경 판별, 원천에 없어진 pk는 `deleted_at` 기록 |

- 한 실행은 **스테이징 → 한 트랜잭션 병합**이다. 실패한 실행은 기존 데이터를 지우거나 반쯤 바꾸지 않고 `sync_run.status=failed`와 오류를 남긴다.
- 링크는 `mapping.link_rules`(FK 속성 → Link Type)로 만들고, 바뀐 링크는 같은 트랜잭션에서 `graph_outbox`에 넣는다(5.2).
- 신선도: 객체마다 `synced_at`, 매핑마다 마지막 성공 실행 시각. MVP의 근거 카드에는 마지막 동기화 시각을 보여 준다(PRD 5.0). 값이 바뀌지 않은 행도 `synced_at` 은 갱신한다(마지막으로 **확인한** 시각).
- **행 변환은 속성 자료형을 지킨다.** 한 행이라도 바꾸지 못하면(enum 밖 값·형식 오류·pk 중복) 실행 전체가 `failed` 다 — 건너뛰면 원천에는 있는 객체가 챗봇에는 없는데 아무도 모른다. 오류는 열·속성·pk 를 밝힌다.
- 전체 모드의 삭제 표시는 **그 원천이 넣은 객체**(`source_id`)에만 건다. 삭제된 객체에서 나가거나 들어오는 링크도 함께 삭제 표시하고, 아웃박스에는 `link_delete` 를 `node_delete` 보다 먼저 쓴다.
- 워터마크는 자료형과 함께 저장한다(`datetime:…`·`int:…`) — 다음 실행이 같은 자료형으로 바인드해야 비교가 된다.
- 같은 타입의 병합은 advisory lock 으로 줄을 세운다(매핑이 둘이어도). 매핑은 저장 때와 **실행 때 다시** 게시 버전과 대조한다.
- `secret_ref` 는 `env:PREFIX`(`PREFIX_HOST`·`_PORT`·`_DB`·`_USER`·`_PASSWORD`, 프로세스 환경변수 → 없으면 `.env`) 또는 `file:경로`(YAML/JSON). 접속 URL 한 줄은 받지 않는다(비밀번호의 `@`·`:`가 구분자로 읽힌다).
- **P1 편집 레이어 병합 규칙**(PRD 5.3, 사용자 결정 "편집 레이어 우선"):
  - 동기화는 `object_edit`을 건드리지 않는다.
  - 편집된 속성의 새 원천 값이 `base_source_value`와 다르면 `sync_conflict`(open)를 만든다.
  - 보이는 값은 계속 편집 값이다. 객체 카드는 "편집됨 · 원천 값 ○○ (동기화 시각)"을 함께 보여 준다.
  - 이 자리는 MVP 코드의 `sync/runner.py`에 훅(`on_source_change`)으로 미리 둔다.

## 12. 인터뷰 워크숍 기본형 (PRD 5.8)

- **주제 상태기계**(`workshop.topics`): 역할 → 업무 흐름 → 의사결정 지점 → 사용 데이터 → 예외 상황 → **찾아보는 질문**(골든 질의 초안용, v0.9 추가). 주제마다 필요한 정보가 채워졌는지 보고 다음 질문을 고른다. 모호한 답은 되묻는다.
  - 채워짐 = 면담자 평가(`workshop.interviewer`, 구조화 출력 `sufficient`/`vague`)가 `vague` 가 아님 **AND** 주제의 요구 요소가 세션에 있음(업무 흐름 → `object_type`, 사용 데이터 → `property`, 찾아보는 질문 → `golden_query`). 평가가 실패하면 요구 요소만 본다.
  - 되묻기는 주제당 2회. 넘으면 그 주제를 `incomplete` 로 두고 넘어간다. 질문 문장은 템플릿이 정본이고, LLM 은 `vague` 일 때 되묻는 문장만 쓴다.
- **추출**(`workshop.extractor`): 답 하나마다 요소 후보를 구조화 출력으로 받는다. 입력은 이번 답 하나이고, 모델은 `quote` 만 내며 근거의 `utterance_id` 는 서버가 붙인다. 이름 형식은 추출 때 강제하지 않고 확정 때 검사한다. 골든 질의 후보는 "찾아보는 질문" 주제에서만 받고, 면담자 질문과 서로 포함 관계인 후보는 버린다(에이전트 질문은 평가셋이 아니다 — ADR-0008 실측).
- **같은 요소**: 같은 세션에서 종류와 이름(`api_name`, 속성은 `타입.api_name`, 질문은 문장)이 **정확히** 같으면 새 요소를 만들지 않고 근거만 덧붙인다. `evidence` 항목은 `{utterance_id, quote, ok}`, 요소의 `evidence_ok` 는 하나라도 통과하면 참.
- **근거 확인**(`workshop.evidence`): `quote`가 해당 발언에 **그대로 있는지**(NFC·공백 정규화 후 정확 포함, 2자 이상) 확인한다. 없으면 `evidence_ok=false`로 표시하고, 검토 화면에서 수정 없이 확정할 수 없게 한다.
- **턴 실패**: 공급자에 닿지 못하면 턴 전체가 실패한다(503, 답도 저장하지 않음 — 다시 보낸다). 모델이 스키마를 두 번 어기면 그 단계만 빠지고 응답의 `warnings`(`extraction_failed`·`assessment_failed`)로 밝힌다. 같은 세션의 답은 세션 행 잠금으로 줄을 세운다.
- **검토**(`workshop.review`): 요소 목록(종류·이름·정의·근거 발언)에서 현업 담당자가 확정·수정·거절한다. 확정·수정 때 payload 를 종류별 모델(`workshop.elements`)로 엄격히 검사한다(식별자 형식은 6.1 과 같다). 골든 질의는 답할 수 있는 질문이면 기대 답변이 있어야 확정된다. 게시된 세션의 요소는 바꾸지 않는다. 제출은 검토 전(`proposed`) 요소가 없을 때만.
- **게시**(`workshop.publish`): 모델러가 승인하면 확정 요소로 새 `ontology_version`(draft)을 만든다 — 현재 게시 버전을 부모로 복사한 뒤 더한다. 이름이 정확히 같은 타입은 같은 타입으로, 같은 이름의 속성·링크는 정의(자료형·enum 값 / 양 끝 타입)까지 같으면 이미 있는 것으로 넘어가고 다르면 게시를 막는다(409, 아무것도 만들지 않음). 새 타입의 키·제목 속성은 기본 이름(`id`·`name`)을 두고 draft 의 게시 전 검사가 알려 준다. FK 를 모르는 링크는 `join_mapping`(`{"origin": "workshop"}`)으로 둔다. 이후 5.1의 게시 흐름을 그대로 탄다.
- **골든 질의**: `kind=golden_query` 요소는 `{question, expected_answer, answerable}`을 가진다. 현업 담당자가 확정한 것만 게시 때 `golden_set(name=workshop-session-{id})`의 `golden_item`(source=workshop)으로 복사한다. 기대 답변은 `golden_item.expected_answer` 에 두고, 채점용 `required_values` 는 비워 둔다(Phase 7). `expected_status` 는 가안으로 답할 수 있으면 `ok`, 아니면 `failed`. 인터뷰 에이전트의 질문 자체는 평가셋에 넣지 않는다.
- **Action 후보**는 `draft_element`(kind=action_candidate)로 남겨 v1에서 Action Type 정의에 쓴다.
- 발언은 마스킹해서 저장하고(패턴 기반, 9.2) 이후 추출·평가·근거 확인 모두 저장본을 본다. 보존 기간을 적용한다(9.3).
- 접근: 세션은 만든 현업 담당자 본인과 모델러만 본다(남의 세션은 404). 답은 본인만, 게시는 모델러만. 감사 이벤트 `workshop.session_started`·`element_reviewed`·`submitted`·`published`.

## 13. 평가 (PRD 5.6·7)

### 13.1 실행

- 평가 러너(`evaluation.runner`)는 **운영과 같은 `chat.pipeline`을 인프로세스로** 부르되, 문항의 `test_role` 권한으로 실행한다. `test_role` 이 골든셋의 `roles`(평가 전용 역할 → 읽을 타입 목록)에 있으면 그 목록, 아니면 DB 역할의 권한(`rbac.readable_types_for_role`)으로 돈다 — 평가가 운영 권한 표에 역할을 만들지 않는다. `test_role` 이 없으면 게시 버전의 모든 타입을 읽는다.
- `fault`가 있는 문항은 장애 주입기(`evaluation.faults`)로 실행기 오류·Neo4j 불가·타임아웃을 흉내 낸다. 파이프라인의 주입 지점은 실행기(`executor`) 하나다(운영은 언제나 `execute_plan`). `graph_down` 은 닿을 수 없는 Bolt 주소의 실제 드라이버로 운영 실행기를 돌린다 — 그래서 끝 객체가 실재하는 탐색 질문이어야 한다.
- 문항마다 세션을 따로 열고 롤백한다(파이프라인은 읽기만). 결과는 문항마다 커밋하고, 작업 재시도는 기록된 문항을 건너뛴다. 문항에서 예외가 나면 평가 장치 오류(`harness_errors`)로 남긴다(파이프라인 `failed` 와 다르다).
- 실행 방법: CLI(`scripts/run_eval.py`, `task.py eval·eval-record·eval-replay`)와 API(`POST /api/v1/eval-runs` → 작업 큐 `eval_run` → 워커). 작업이 끝내 실패해 `running` 으로 남은 실행은 다음 실행 요청 때 `failed` 로 닫는다(작업 없이 도는 CLI 실행은 제외).
- 골든셋의 정본은 도메인 팩 `golden.yaml`(`name`·`roles`·`items`)이다. 적재(`evaluation.golden.load_pack`)는 문항을 `key`(팩의 `id`)로 맞추고, 결과가 붙은 문항을 지워야 하면 멈춘다. 워크숍 문항(`source=workshop`)은 적재가 건드리지 않는다.
- **녹화 응답(카세트)**: PR 마다 CI 는 `domain-packs/<팩>/llm_cassette.json` 으로 파이프라인 회귀를 돈다(키 = Ollama 요청 본문의 정규 JSON 해시). 카세트에 없는 요청은 실패(`llm_unavailable`)이고 놓친 요청이 하나라도 있으면 게이트 실패다. 녹화는 실제 모델로 게이트를 통과한 실행에서만 한다. 재생에서는 지연(`p95_ms`)을 보지 않는다. 녹화·재생은 같은 시드의 평가 전용 DB(`ontobot_eval`, `task.py eval-prepare`)에서 한다.
- **게이트**: `evaluation/gate.yaml`. 불변식(`citation_rate` = 1, `fault_cover_ups`·`permission_violations`·`harness_errors`·`action_fabrications` = 0, `unanswerable_graded_rate` = 0)은 확정, `accuracy`·`estimate_ratio`·`action_param_accuracy`·`p95_ms` 는 가안. 불변식은 값이 없으면(분모 0) 통과, 가안은 값이 없으면 실패 — 단 `optional` 가안(`action_param_accuracy`)은 그 종류의 문항이 없는 골든셋에서 통과. 밑돌면 CI가 실패한다. 모델 교체 시에도 필수다(ADR-0009).

### 13.2 PRD 7절 지표 계산식

| PRD 지표                              | 계산                                                                                                                               | 출처          |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------- |
| 골든셋 질의 정답률                    | `answerable=true` 문항 중, 상태가 `expected_status`와 같고 `required_values`를 모두 포함한 비율                                    | eval          |
| 근거 인용률(파이프라인 불변식)        | 사실 문장("추정" 제외) 중 대조를 통과한 인용이 붙은 비율. 8.2 규칙상 언제나 100%여야 하며, 미만이면 등급 판정 결함으로 게이트 실패 | eval·trace    |
| 추정 비율                             | 사실 문장 중 "추정" 표시된 문장의 비율(등급 ②·③을 만든 문장 포함). 모델이 인용을 제대로 다는지 보는 품질 신호                      | eval·trace    |
| 근거 없는 질문에 ①·②로 답한 비율      | `answerable=false` 문항 중 등급이 ①·②인 비율                                                                                       | eval          |
| 대조 실패로 제거된 인용 수            | `trace.removed_citations` 합계(실행별·기간별)                                                                                      | eval·trace    |
| 조회 실패율                           | 질의 응답 중 `status=failed` 비율(장애 주입 문항 제외)                                                                             | trace         |
| 조회 실패를 정상 답변으로 내보낸 건수 | `fault≠none` 문항 중 `status≠failed`인 건수                                                                                        | eval          |
| 권한 위반 응답                        | 답변·근거 제목에 `forbidden_values` 가 나왔거나, 읽을 수 없는 타입의 **객체** 근거(`o:Type/pk`)가 나온 문항 수(집계 근거 `q:N` 은 타입이 없어 제외)       | eval          |
| P95 응답 시간                         | `trace.stage_latency_ms` 합계의 95백분위                                                                                           | trace         |
| 데이터 신선도                         | 매핑별 (현재 − 마지막 성공 `sync_run.finished_at`)의 최댓값                                                                        | sync_run      |
| 동시 사용자 / 데이터 규모 / 가용성    | 부하 테스트 결과 / 객체 수 / 헬스체크 가동률                                                                                       | 별도 측정     |
| 주간 활성 사용자                      | 최근 7일 `message`의 고유 사용자 수                                                                                                | message       |
| 신규 객체 타입 구성 소요 시간         | 타입이 처음 draft에 나타난 시각 → 그 타입을 포함한 버전 게시 시각                                                                  | audit_log     |
| Action 파라미터 정확도 (v1, 가안 90%) | `expected_action.ready=true` 문항 중 제안의 Action·대상 pk·파라미터(문자열 표현)가 모두 기대와 같은 비율                           | eval          |
| Action 지어내기 (v1, 불변식 0)        | 미리보기를 만들면 안 되는 문항(`ready=false`, Action 이 아닌 문항)에서 만들었거나 기대와 다른 대상으로 만든 문항 수                 | eval          |
| Action 성공률 / 승인 거부율 (v1)      | `action_run` 상태 비율                                                                                                             | action_run    |
| 확인되지 않은 편집 충돌 (v1)          | `sync_conflict.status=open` 건수                                                                                                   | sync_conflict |

근거 인용률은 8.2 규칙상 통과한 인용이 없는 사실 문장이 모두 "추정"이 되므로 **구조적으로 100%**다. 그래서 품질 지표가 아니라 **파이프라인 불변식**으로 다루고(PRD v0.4 7절), 모델 품질은 추정 비율로 본다. 게이트는 불변식을 100% 고정으로, 추정 비율은 PRD에서 정한 상한으로 건다.

## 14. API 설계 (MVP)

### 14.1 엔드포인트

모두 `/api/v1` 아래에 있다. MVP의 API는 프론트엔드용 내부 API이고, 외부 공개(키·스코프·호환성 보장)는 P1(PRD 5.11)이다. 오류 응답은 RFC 7807(`application/problem+json`) 형식이다.

| 묶음     | 엔드포인트                                                                                                                                                                                                                                                                                          | 권한                |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- |
| 인증     | `GET /auth/providers` · `GET /auth/login` · `GET /auth/callback` · `POST /auth/local/login` · `POST /auth/logout` · `GET /auth/me`                                                                                                                                                                                                                     | —                   |
| 채팅     | `POST /chat` (SSE) · `GET /conversations` · `GET /conversations/{id}/messages`                                                                                                                                                                                                                      | 사용자              |
| 객체     | `GET /objects/{type}/{pk}` (근거 카드 — 편집·충돌·값 계보 포함) · `GET /sync-conflicts` · `POST /sync-conflicts/{id}/acknowledge` · `DELETE /object-edits/{id}` (v0.13) | 타입 읽기 권한(충돌·편집 해제는 + 모델러·관리자) |
| 온톨로지 | `GET·POST /ontology/versions` · `GET /ontology/versions/{id}` · `POST·PATCH·DELETE /ontology/versions/{id}/object-types[/{tid}]` · 같은 형태의 `/properties`, `/link-types` · `POST /ontology/versions/{id}/import` · `GET /ontology/versions/{id}/export` · `GET /ontology/versions/{id}/diff?base=` · `POST /ontology/versions/{id}/publish` | 모델러              |
| 동기화   | `CRUD /sources` · `POST /sources/{id}/test` · `CRUD /mappings` · `POST /mappings/{id}/sync` · `GET /sync-runs`                                                                                                                                                                                      | 모델러              |
| 그래프   | `GET /graph/status` · `POST /graph/rebuild`                                                                                                                                                                                                                                                         | 관리자              |
| 워크숍   | `GET·POST /workshop/sessions` · `GET /workshop/sessions/{id}` · `POST /workshop/sessions/{id}/utterances` (다음 질문 반환) · `GET /workshop/sessions/{id}/elements` · `PATCH /workshop/elements/{id}` · `POST /workshop/sessions/{id}/submit` · `POST /workshop/sessions/{id}/publish`                                                  | 현업 / 모델러(게시) |
| 평가     | `CRUD /golden-sets` · `CRUD /golden-sets/{id}/items` · `POST /eval-runs`(202, 작업 큐) · `GET /eval-runs[?golden_set_id=]` · `GET /eval-runs/{id}`                                                                                                                                                                                                    | 챗봇 빌더 / 모델러 |
| 관리     | `GET·POST /admin/users` · `PATCH /admin/users/{id}`(상태·역할) · `GET /admin/roles` · `PUT /admin/roles/{id}/permissions` · `GET /admin/object-types` · `GET /admin/metrics?days=`(v1 지표, 17.5) · `GET /audit-logs` · `GET /traces/{id}`                                                                                                  | 관리자              |

### 14.2 채팅 SSE 이벤트

| 이벤트       | 데이터                                                                     | 시점               |
| ------------ | -------------------------------------------------------------------------- | ------------------ |
| `stage`      | `{"stage": "planning" \| "querying" \| "answering" \| "verifying"}`        | 단계 시작          |
| `conditions` | `{"text": "시설 · 지역 = 서울 · …", "graph_as_of": "…" \| null}`           | 조회 직후          |
| `answer`     | `{"text": "…", "estimated_sentences": [2, 5]}`                             | 대조 완료 후 한 번 |
| `citations`  | `[{"ref": "o:Facility/F-001#risk_grade", "title": "…", "synced_at": "…", "values": null}]` — 집계·Function 결과 행(`q:N`)은 `values`(`[{name, label, display}]`, 답을 만들 때 대조한 값)를 함께 싣는다(v0.20, 객체는 카드가 다시 읽는다) | `answer` 직후      |
| `grade`      | `{"status": "ok", "grade": 1}`                                             | `answer` 직후      |
| `action_preview` | `{"text", "action_type", "display_name", "target_type", "target_pk", "target_title", "params", "issues", "candidates", "ready", "run_id"}` (v0.15) | Action 요청일 때 한 번 |
| `error`      | `{"status": "failed", "reason": "timeout", "message": "…"}`                | 실패 시            |
| `done`       | `{"conversation_id": …, "message_id": …, "trace_id": …}`                   | 마지막             |

순서: 진행 이벤트(`stage`·`conditions`) 뒤에 성공·0건·질의 아님은 `answer → citations → grade → done`, Action 요청은 `action_preview → done`(등급 없음), 실패는 `error → done` 이다. `failed` 는 `answer` 로 나가지 않는다. 스트림 본문은 OpenAPI 로 기술할 수 없어, 이벤트별 데이터 모양을 문서 모델 `ChatEvents` 로 싣고 프론트는 그 생성 타입으로 읽는다(계약 테스트가 실제 페이로드를 그 모델로 검증한다). 예기치 못한 서버 오류도 `error(internal_error)` 로 밝힌다.

## 15. 비기능 요구사항

| 구분   | 요구사항                                                                                                                                                                    |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 성능   | 단계별 지연을 `trace.stage_latency_ms`에 남긴다. PRD 7절 P95 목표가 정해지면 13절 게이트에 넣는다. 타임아웃 기본값: SQL 10초, Cypher 5초, LLM은 역할별 설정. 채팅 한 건은 답이 끝날 때까지 DB 연결 하나를 쥐므로 연결 풀(`ONTOBOT_DB_POOL_SIZE`+`MAX_OVERFLOW`, 기본 20+10)이 API 프로세스의 동시 채팅 상한이다. 부하 측정은 `scripts/load_test.py`, 수치는 ADR-0011 |
| 폐쇄망 | 실행 중 외부 네트워크를 호출하지 않는다(외부 LLM 공급자를 켠 SaaS 배포는 예외). 프론트 자산은 빌드에 포함하고 CDN을 쓰지 않는다 — 설치본은 API 가 `ONTOBOT_FRONTEND_DIST` 를 같은 오리진에서 서빙한다(화면 경로는 `index.html` 로 되돌림, `/api/`·`/healthz`·`/readyz` 는 되돌리지 않는다). 오프라인 번들로 설치한다(설치 중 네트워크 없음: 이미지는 `docker load`, 레지스트리 없음 — ADR-0013) |
| 가용성 | MVP는 단일 노드(api 1 + worker 1 + PostgreSQL + Neo4j). 워커는 `SKIP LOCKED`로 수평 확장이 가능한 구조로 만든다. `/healthz`(프로세스 — 재시작 판단은 이것만), `/readyz`(PostgreSQL `SELECT 1`·Neo4j 접속·LLM: Ollama 는 `/api/tags` 로 역할 모델 설치 여부, 외부 공급자는 호출하지 않고 키 설정만. 구성 요소마다 3초 상한, 동시에 본다). `/readyz` 판정: PG 또는 LLM 이 안 되면 `unavailable`(503), Neo4j 만 안 되면 `degraded`(200 — 탐색 질의만 `graph_unavailable`), 모두 되면 `ok`. 인증 없이 열리므로 구성 요소 상태와 오류 종류만 싣는다. 매일 대사(`graph_reconcile`)·보존 기간 정리는 워커가 하루 한 번 발행한다 |
| 로깅   | JSON 한 줄 로그, 요청 ID 전파, PII 마스킹, 회전                                                                                                                             |
| 언어   | UI·안내 문구는 한국어 우선, 문구는 한 파일(`chat/templates.py`, 프론트 `i18n/ko`)에 모은다                                                                                  |

## 16. 프로젝트 구조

### 16.1 디렉터리

```
OntoBot_project/
├── docs/
│   ├── prd/                        # PRD (현재 루트의 PRD 파일들을 옮길 자리)
│   ├── trd/                        # 이 문서
│   └── adr/                        # 21절 결정 기록 (0001-dual-store.md …)
├── backend/
│   ├── pyproject.toml
│   ├── alembic/                    # 마이그레이션
│   ├── src/ontobot/
│   │   ├── main.py                 # 앱 팩토리, 프론트 정적 서빙
│   │   ├── config.py               # 설정 (pydantic-settings)
│   │   ├── api/                    # 라우터 — 검증·위임만 (14절)
│   │   │   ├── deps.py             #   인증·권한 의존성
│   │   │   ├── errors.py           #   RFC 7807
│   │   │   └── auth.py chat.py conversations.py objects.py ontology.py
│   │   │       sources.py graph.py workshop.py eval.py admin.py
│   │   ├── ontology/               # 스키마 레지스트리 (PRD 5.1)
│   │   │   ├── models.py           #   Pydantic 스키마
│   │   │   ├── registry.py         #   게시 버전 캐시
│   │   │   ├── versioning.py       #   draft · 게시 · 버전 비교
│   │   │   ├── importer.py         #   YAML/JSON 임포트·익스포트
│   │   │   └── summary.py          #   플래너용 권한 필터 스키마 요약
│   │   ├── objects/                # 객체·링크 (6.2)
│   │   │   ├── store.py            #   upsert · 삭제 표시
│   │   │   └── reader.py           #   resolve() — 유일한 읽기 경로
│   │   ├── graph/                  # Neo4j 전담 (5절)
│   │   │   ├── client.py           #   드라이버 — Neo4j import는 여기만
│   │   │   ├── projector.py        #   graph_outbox 소비
│   │   │   ├── traversal.py        #   파라미터 바인딩 Cypher 실행
│   │   │   └── reconcile.py        #   대사 · 재구축
│   │   ├── sync/                   # 동기화 (11절)
│   │   │   ├── connectors/         #   base.py postgres.py oracle.py mysql.py csv.py
│   │   │   ├── mapping.py          #   컬럼→속성, 링크 규칙
│   │   │   ├── runner.py           #   스테이징 → 병합, on_source_change 훅
│   │   │   └── freshness.py
│   │   ├── query/                  # 질의 (7절)
│   │   │   ├── plan.py             #   QueryPlan 모델
│   │   │   ├── planner.py          #   LLM → QueryPlan
│   │   │   ├── validator.py        #   실재·권한·깊이 검증
│   │   │   ├── compiler.py         #   QueryPlan → 바인딩 SQL / Cypher
│   │   │   ├── executor.py         #   실행 · 타임아웃 · status
│   │   │   └── conditions.py       #   조회 조건 문장 (결정론적)
│   │   ├── grounding/              # 근거 대조 (8절) — LLM·DB 비의존
│   │   │   ├── citations.py        #   인용 표기 파싱
│   │   │   ├── normalize.py        #   NFC · 공백 · 숫자 · 날짜
│   │   │   ├── facts.py            #   사실 문장 판정
│   │   │   ├── verifier.py         #   인용 대조 · 제거
│   │   │   └── grader.py           #   등급 ①②③
│   │   ├── chat/                   # 오케스트레이션 (PRD 5.2)
│   │   │   ├── pipeline.py         #   7.1 단계 실행
│   │   │   ├── context.py          #   대화 맥락 (식별자만)
│   │   │   ├── answerer.py         #   답변 생성 (LLM)
│   │   │   ├── templates.py        #   empty · failed · not_query 문구
│   │   │   └── stream.py           #   SSE 이벤트 (14.2)
│   │   ├── llm/                    # LLM 게이트웨이 (10절)
│   │   │   ├── gateway.py
│   │   │   ├── structured.py       #   스키마 검증 · 재시도
│   │   │   └── adapters/           #   ollama.py openai_compat.py anthropic.py
│   │   ├── security/               # 보안 (9절)
│   │   │   ├── auth.py             #   OIDC · 세션 · 로컬 계정
│   │   │   ├── rbac.py             #   타입 권한 판정
│   │   │   ├── fingerprint.py      #   권한 지문
│   │   │   ├── pii.py              #   마스킹 · 가역 토큰화 · 패턴 탐지
│   │   │   ├── audit.py
│   │   │   └── secrets.py
│   │   ├── workshop/               # 인터뷰 워크숍 기본형 (12절)
│   │   │   ├── topics.py           #   주제 상태기계
│   │   │   ├── interviewer.py
│   │   │   ├── extractor.py
│   │   │   ├── evidence.py         #   근거 발언 정확 포함 확인
│   │   │   ├── review.py
│   │   │   └── publish.py          #   draft 버전 · 골든 질의 복사
│   │   ├── evaluation/             # 평가 (13절)
│   │   │   ├── golden.py
│   │   │   ├── runner.py
│   │   │   ├── faults.py           #   장애 주입
│   │   │   ├── metrics.py          #   13.2 계산식
│   │   │   ├── gate.py
│   │   │   └── gate.yaml
│   │   ├── tracing/                # recorder.py retention.py
│   │   ├── jobs/                   # queue.py (SKIP LOCKED) worker.py schedules.py
│   │   └── db/                     # session.py, models/ (6절 묶음별 파일)
│   └── tests/
│       ├── unit/                   # grounding/ query/ security/ workshop/ evaluation/
│       ├── integration/            # PostgreSQL + Neo4j
│       ├── contract/               # SSE 이벤트 · OpenAPI 스냅샷
│       ├── eval/                   # 골든셋 게이트
│       └── fixtures/
├── frontend/
│   ├── package.json  vite.config.ts  tsconfig.json
│   └── src/
│       ├── app/                    # 라우터 · 레이아웃 · 인증 가드
│       ├── api/                    # OpenAPI 생성 타입 · fetch 래퍼 · SSE 리더
│       ├── components/             # GradeBadge CitationCard QueryConditions FailureNotice
│       ├── features/
│       │   ├── chat/               # 대화 · 근거 카드 · 추정 표시
│       │   ├── ontology/           # 스키마 편집기 · 버전 비교 · 임포트
│       │   ├── sources/            # 데이터 소스 · 매핑 · 동기화 이력
│       │   ├── workshop/           # 인터뷰 · 요소 검토 목록
│       │   ├── eval/               # 골든셋 · 평가 결과
│       │   └── admin/              # 사용자 · 역할 · 권한 · 감사 로그
│       └── i18n/ko/
├── domain-packs/
│   └── sample/                     # ontology.yaml mappings.yaml golden.yaml seed/(CSV)
├── deployer/                       # 배포 콘솔(ADR-0014) — ontobot 을 import 하지 않는 별도 파이썬 프로젝트
├── devconsole/                     # 개발 콘솔(ADR-0015) — 개발 PC 127.0.0.1 전용, 명령은 scripts/task.py 를 부른다
├── opsjobs/                        # 배포·개발 콘솔 공유 작업 실행기(작업 기록·단계·로그)
├── deploy/
│   ├── docker/                     # app·deployer Dockerfile · compose(운영·gpu·deployer·test·dev) · images.lock · ontobotctl · postgres-init · systemd
│   ├── windows/                    # install-host.ps1(WSL2·Docker 호스트 준비, 최초 1회)
│   ├── INSTALL.md
│   └── env.example
├── scripts/
│   ├── seed_domain_pack.py
│   ├── export_openapi.py           # 프론트 타입 생성 입력
│   ├── run_eval.py
│   ├── graph_rebuild.py
│   ├── build_bundle.py             # 이미지·모델·스택 → 서명 번들(ADR-0013)
│   └── bundle_sign.py              # Ed25519 키·서명·확인
├── Makefile
└── README.md
```

### 16.2 모듈 역할과 PRD 대응

| 모듈         | 역할                                        | PRD           |
| ------------ | ------------------------------------------- | ------------- |
| `ontology`   | 스키마 정의·버전·임포트                     | 5.1           |
| `objects`    | 객체·링크 저장, 단일 읽기 경로              | 5.1, 5.3(P1)  |
| `graph`      | Neo4j 투영·탐색·대사                        | 6절(아키텍처) |
| `sync`       | 커넥터·매핑·증분 동기화·신선도              | 5.1           |
| `query`      | 자연어 → 검증된 질의 → 실행, 조회 조건 문장 | 5.2           |
| `grounding`  | 인용 대조·등급                              | 5.0           |
| `chat`       | 대화 오케스트레이션·맥락·SSE                | 5.2           |
| `llm`        | 모델 게이트웨이                             | 6절(아키텍처) |
| `security`   | 인증·권한·PII·감사                          | 5.5           |
| `workshop`   | 인터뷰 워크숍 기본형                        | 5.8           |
| `evaluation` | 골든셋 회귀 평가·게이트                     | 5.6, 7        |
| `tracing`    | 트레이스 기록·보존                          | 5.0, 5.6      |
| `jobs`       | 작업 큐·워커·스케줄                         | 5.1           |

### 16.3 의존 규칙 (import-linter로 CI에서 강제)

1. `api`는 다른 모듈을 부를 수 있지만, 어떤 모듈도 `api`를 import하지 않는다. 라우터에 업무 로직을 두지 않는다.
2. `grounding`은 `llm`·`db`·`graph`·`sync`를 import하지 않는다(순수 함수 — 8.2).
3. Neo4j 드라이버 import는 `graph/client.py`에서만 한다.
4. LLM SDK import는 `llm/adapters/`에서만 한다.
5. `object` 테이블 읽기는 `objects.reader`에서만 한다. 다른 모듈의 직접 SELECT는 테스트가 소스를 훑어 막는다.
6. `security.rbac`의 판정 함수 외에 권한을 계산하는 코드를 두지 않는다.

### 16.4 그 밖의 규칙

- 프론트의 API 타입은 `scripts/export_openapi.py` → `openapi-typescript`로 생성하고 손으로 고치지 않는다.
- PRD 파일들(`OntoBot_project-prd*.md`)은 `docs/prd/`, 이 문서는 `docs/trd/`, 구현 계획은 `docs/plan/`에 둔다(v0.2에서 이동 완료).

## 17. 확장 지점 (P1~P3)

| PRD          | 기능                                     | 끼워 넣을 자리                                                                                                                                                   |
| ------------ | ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 5.3 (P1)     | Action·편집 레이어                       | `actions/`(registry, runner, approvals) 신설, `objects/edits.py`, `reader.resolve()` 오버레이, `sync/runner.py`의 `on_source_change` 훅에서 `sync_conflict` 기록 |
| 5.0 (P1)     | 값 계보                                  | `objects/lineage.py` — `sync_run`·`object_edit`을 엮어 근거 카드에 표시                                                                                          |
| 5.1 (P1)     | 변경 영향 분석                           | `ontology/impact.py` — Action·Function·프롬프트·골든셋·캐시 참조 검색(요소 단위 참조는 Phase G1 에서 시작)                                                                                            |
| 5.1·5.2 (P1) | 문서 ↔ 객체 연결, 하이브리드 검색        | `document`·`document_chunk`(pgvector) 테이블, `query/retrievers/vector.py`, QueryPlan에 `search` 단계                                                            |
| 5.5 (P1)     | 객체·속성 단위 권한, 감사 로그 변조 방지 | `security/rbac.py`에 속성 투영·행 정책, 컴파일러가 주입 / `audit_log.prev_hash` 해시 체인                                                                        |
| 5.6 (P1)     | 트레이스 뷰·피드백                       | 프론트 `features/traces`, `feedback` 테이블                                                                                                                      |
| 5.8 (P1)     | 워크숍 확장형                            | 프론트 분할 화면, 샘플 데이터용 격리 스키마, `workshop/merge.py`(정확 일치 후보 제안)                                                                            |
| 5.9 (P1)     | 객체 탐색                                | 프론트 `features/explorer`, `graph/traversal.py`의 이웃 조회 API                                                                                                 |
| 5.10 (P1)    | Function                                 | **구현(Phase 12, 17.4)** — `functions/`(binding·evaluator·runner), 정의는 온톨로지 문서(`function_type`), 플래너 `kind: function`, 결과는 `[[q:n]]`으로 인용          |
| 5.11 (P1~P3) | REST API 공개 · 메신저 · SDK             | API 키·스코프 / `integrations/messenger/` / OpenAPI 기반 SDK 생성                                                                                                |
| 5.1 (P2)     | 멀티 소스                                | `sync/identity.py` — 소스 간 같은 객체 식별 규칙                                                                                                                 |
| 5.4 (P2)     | 에이전트 빌더                            | `agents/` — 도구 = query·functions·actions                                                                                                                       |
| 5.7 (P3)     | 자동화                                   | `automations/` + `jobs/schedules.py`                                                                                                                             |
| 5.12 (P3)    | 시나리오 what-if                         | `scenario/` — `reader.resolve(scenario_id)`가 시나리오 사본을 덮고, 탐색 결과에도 시나리오 링크 변경을 메모리에서 덮는다. 실제 객체·아웃박스에는 쓰지 않는다     |

### 17.1 v1 착수 결정 (v0.12)

v1 계획(`docs/plan/OntoBot_project-v1-implementation-plan.md`)의 착수 전 결정이다. ★ 는 사용자 결정.

| # | 항목 | 결정 |
| --- | --- | --- |
| 1 | 편집 레이어와 조회 | 활성 편집은 근거 카드뿐 아니라 **조회 조건에도** 보인다. `objects.reader` 의 `resolve`·`run_query`·`existing_values` 가 `object` 에 활성 `object_edit` 를 덮은 같은 파생 행을 읽는다 |
| 2 | Action·Function 정의 위치 | `OntologyDocument` 에 `action_types`·`functions` 를 더한다. draft 에서 편집·게시, 게시본 불변, 버전 비교·영향 분석 대상 |
| 3 | Action 변경 대상 | 일반 속성만. pk·FK(링크 원천)·**제목** 속성은 게시 전 검사가 거부한다(링크 변경 Action 은 v1.5). 제목은 Neo4j 노드와 참조 값 대조(FK 이름 → `traverse` 재작성)에 쓰여, 편집하면 그래프와 어긋난다(v0.13 추가) |
| 4 | ★ Action 부수효과 | 앱 안 알림 + **메일(사내 SMTP 릴레이) + SMS(사내 게이트웨이: HTTP JSON 또는 발송 테이블 INSERT)**. 실행 트랜잭션에서 `notification_delivery`(발송 아웃박스)에 쓰고 커밋 뒤 워커가 보낸다(재시도, `(action_run, 채널, 수신자)` 중복 방지). 롤백은 정정 발송. 수신자 = 역할의 사용자 연락처(`app_user.email`·`phone`) 또는 대상 객체의 `pii_kind` phone/email 속성, 실행당 상한. 연락처는 로그·감사에서 마스킹. 발송 문구는 템플릿 + 바인드(LLM 이 쓰지 않는다). **실행 중 외부 호출 금지 규칙의 예외는 외부 LLM 공급자와 운영자가 설정한 메일·SMS 게이트웨이뿐이다.** `/readyz` 는 게이트웨이를 부르지 않는다 |
| 5 | Action 파라미터 검증 | 객체 참조는 그 타입에 실재하는 pk·제목과 정확 일치, 값은 질문 원문에 그대로 있거나 enum·자료형 변환 통과. 어긋나면 실행 전에 막고 오류를 한꺼번에 붙여 1회 재계획, 그래도 안 되면 사용자가 고른다 |
| 6 | 멱등성 | `action_run.request_id`(클라이언트 UUID) 유일 인덱스. 재전송은 기존 실행을 돌려준다(발송도 한 번) |
| 7 | Function 형태 | 선언형 식 트리(QueryPlan 조각·집계·산술·비교·조건). 사용자 코드는 받지 않는다. 값은 전부 바인드, 호출자 권한으로 조회 |
| 8 | Function 인용 | `[[q:N#value]]`. 근거 행에 입력 조건(인자·내부 조회 조건)을 싣는다. `grounding` 은 바뀌지 않는다 |
| 9 | 속성·행 단위 권한 | `property_permission(role, type, property, can_read)`·`row_policy(role, type, filters)`(QueryPlan `Filter` 문법). 컴파일러가 주입하고 플래너 요약·데이터 블록·근거 카드·Action 미리보기·Function 모두 같은 판정. 권한 지문에 포함 |
| 10 | 감사 해시 체인 | `audit_log.prev_hash`·`hash`(SHA-256, 직전 해시 + 정규화 JSON), 삽입은 advisory lock 으로 직렬화, 검증은 CLI·매일 작업(끊김은 보고만) |
| 11 | 임베딩 | 로컬 Ollama, `config/llm.yaml` 새 역할 `embedder`. 후보(`bge-m3`·`nomic-embed-text`)를 한국어 골든 문항으로 재서 ADR 로 정한다 |
| 12 | 문서 인용 | `[[d:<doc_id>/<chunk_no>]]` — 값이 청크 원문에 그대로 있는지 정확 일치. 유사도는 검색에만 쓰고 근거 판정에는 쓰지 않는다 |
| 13 | ★ 워크숍 음성 입력 | **폐쇄망에서는 비활성화.** `ONTOBOT_CLOSED_NETWORK=false` 이고 `ONTOBOT_VOICE_INPUT=true` 일 때만 켜진다(화면은 `GET /config/features` 로 받는다). 브라우저 음성 인식이라 음성이 브라우저 공급자 서버로 감을 화면에 알린다. 인식 결과는 텍스트 발언과 같이 마스킹 저장. 오프라인 STT 어댑터는 v1.5 |
| 14 | 공개 REST API | API 키(해시 저장)·스코프(`query`·`objects:read`·`actions:request`)·키마다 서비스 계정(역할). 권한 판정은 `security/rbac.py` 하나 |

### 17.2 Action 실행 (v0.14, Phase 10 구현 — ADR-0012)

| 표 | 주요 컬럼 |
| --- | --- |
| `action_type` | `version_id`, `api_name`, `display_name`, `definition`(jsonb — `ActionTypeDef` 의 나머지) — 버전과 함께 게시, 게시본 불변 |
| `action_run` | `request_id`(유일), `action_type`, `ontology_version_id`, `target_type`·`target_pk`, `params`(저장 표현), `status`(previewed/blocked/rejected/executed/failed/rolled_back), `origin`(ui/chat/api), `preview`(jsonb — 승인자가 보는 그대로), `requested_by`, `decided_by`·`decided_at`, `reason`, `executed_at`, `rolled_back_by`·`_at`, `error` |
| `notification` | `user_id`, `action_run_id`, `kind`(notify/compensation), `body`, `read_at` — 앱 안 알림 |
| `notification_delivery` | `action_run_id`, `channel`(email/sms), `kind`(notify/compensation), `recipient`(원값), `subject`, `body`, `status`(pending/sent/failed/cancelled), `attempts`, `next_attempt_at`, `sent_at`, `error` — 유일 `(실행, 채널, 수신자, 종류)` |
| `app_user` | `email`·`phone` 추가(PII — 응답·감사는 마스킹) |

- **정의**(`ActionTypeDef`): 대상 타입, 요청 역할(비면 대상을 읽는 누구나), 파라미터(자료형·enum·필수), 사전 조건(대상의 **보이는 값**에 거는 고정 연산자), 바꿀 속성(파라미터 또는 상수), 알림(`app`/`email`/`sms`, 받는 역할 또는 대상의 연락처 속성, 템플릿), 승인(역할·`allow_self`), 정정 문구(메일·SMS 가 있으면 필수). 게시 전 검사가 pk·제목·FK 변경, 파라미터↔속성 자료형(enum 값 포함), 선택 파라미터 → 비울 수 없는 속성, 연락처 속성의 `pii_kind`, 모르는 자리표시자를 막는다.
- **흐름**: 미리보기(실행 안 함 — 파라미터 변환 오류는 한꺼번에 422, 사전 조건·채널 미설정·수신자 상한은 `blocked`) → 승인 = 실행(같은 트랜잭션에서 게시 버전 일치·대상·사전 조건 재검증 → 편집 레이어 `apply_edit(action_run_id)` → 앱 알림 → 발송 아웃박스 → `notify_deliver` 작업) → 롤백(이 실행의 활성 편집 해제, 대기 발송 취소, 나간·실패한 발송과 앱 알림 수신자에게 정정 문구). 재검증 실패는 `failed` 기록이다(예외로 되돌리지 않는다). 같은 `request_id` 는 처음 기록을 돌려준다(다른 사용자면 409).
- **권한**(`security/rbac.py`): 요청 = 대상 타입 읽기 + 요청 역할. 승인·거부 = 승인 역할(본인은 `allow_self` 일 때만). 미리보기 단계는 요청자가 거둘 수 있다. 롤백 = 승인 역할 또는 관리자. 읽을 수 없는 타입의 Action·기록은 404.
- **발송**(`notify/`): 설정 `config/notify.yaml`(`ONTOBOT_NOTIFY_CONFIG_PATH`, 설치본은 `shared\notify.yaml`). 채널 어댑터 `smtp`·`sms_http`·`sms_table`·`fake`(개발). 설정하지 않은 채널은 꺼진다. 워커가 기한이 된 행을 집어(임대 5분) 트랜잭션 밖에서 보내고, 실패는 `retry_seconds × 시도` 뒤 재시도, `max_attempts` 를 넘으면 `failed` — 최소 한 번 발송. 주기 발행이 재시도 기한이 된 발송을 다시 넣는다. 문구는 `{이름}` 치환뿐(LLM 이 쓰지 않는다).
- **API**: `GET /action-types`(읽을 수 있는 대상의 게시 Action) · `POST /action-types/{name}/preview` · `GET /action-runs?scope=mine|to_decide|all` · `GET /action-runs/{id}` · `POST /action-runs/{id}/approve|reject|rollback` · `GET /notifications` · `POST /notifications/read` · 온톨로지 `POST·PUT·DELETE /ontology/versions/{id}/action-types[/{aid}]` · `PUT /admin/users/{id}/contacts` · `GET /workshop/action-candidates`(확정한 Action 후보 — 정의를 시작할 때). 감사 이벤트 `action.previewed·executed·rejected·failed·rolled_back`, `security.user_contacts_changed`.

### 17.3 채팅에서 Action (v0.15, Phase 11)

- **계획**: 플래너 출력 `kind` 에 `action` 을 더한다. `ActionProposal{action_type, target, params: [{name, value}]}` — `target` 은 대상을 가리키는 말 그대로(질문의 식별자·이름, 또는 맥락의 pk), 파라미터는 이름·값 목록(사전으로 두면 구조화 출력 스키마가 키를 열어 둔다). 스키마 요약의 `action_types` 는 **이 사용자가 요청할 수 있는** Action(대상 타입 읽기 + 요청 역할) 중 대상을 읽을 수 있는 것만이고, 하나도 없으면 요약에도 프롬프트에도 Action 규칙을 싣지 않는다(Action 이 없는 배포의 프롬프트는 측정한 그대로). 예시 키 순서는 `PlannerOutput`·`ActionProposal` 필드 순서와 같다.
- **대조**(`actions.params`, 17.1-5): 대상을 가리키는 말이 질문에 **그대로** 있거나(근거 대조 정규화) 맥락의 pk 여야 한다 — 실재하는 다른 객체를 골라도 지어낸 것이다. 그다음 pk, 없으면 제목과 정확히 같은 객체(`objects.reader.pks_with_value`), 같은 제목이 여럿이면 후보. 값 파라미터(string·int·decimal·date·datetime)는 질문에 그대로 있어야 하고(수는 값 같음), enum·bool 은 자료형 변환만. 질문에 없는 값은 버리고 알린다. 오류는 한꺼번에 붙여 **1회 재계획**, 그래도 남으면 `action_preview` + `ready=false` + `issues`·`candidates`(같은 제목 객체, 없으면 맥락의 같은 타입 객체)로 사용자에게 보인다. 모르는·요청할 수 없는 Action 은 재계획 뒤 `failed(plan_invalid)`.
- **미리보기**: 파이프라인은 저장하지 않는다. 대조를 통과하면(`ready`) 채팅 API 가 같은 트랜잭션에서 `actions.runner.preview`(`origin=chat`, `request_id = chat-<질문 메시지 id>`)로 실행 기록을 만들고 `run_id` 를 싣는다 — 승인·실행·롤백은 17.2 의 Action API 그대로다. 미리보기 단계에서 막히면(권한 등) `ready=false` 로 돌린다. 화면은 `run_id` 가 있으면 그 기록을 지금 상태로 다시 읽어 보여 주고, 없으면 서버가 확인한 값만 채운 요청 폼(후보는 선택지)을 보여 준다.
- **기록**: `message.status` 에 `action_preview`(마이그레이션 0008), `message.meta.action` 에 이벤트 데이터. 평가 문항은 `golden_item.expected_action{action_type, target_pk, params, ready}`.

### 17.4 Function (v0.19, Phase 12)

| 표 | 주요 컬럼 |
| --- | --- |
| `function_type` | `version_id`, `api_name`, `display_name`, `definition`(jsonb — `FunctionDef` 의 나머지). 유일 `(version_id, api_name)`. 버전과 함께 게시, 게시본 불변(마이그레이션 0010) |

- **정의**(`ontology.models.FunctionDef`, 17.1-7): `params[]`(자료형·enum·필수, 선택이면 `default`), `queries[]`(1~4개 — 각각 `target`·`filters`·`traverse`·`aggregate` 로 QueryPlan 과 같은 모양의 **집계** 조회, 조건 값 자리에 `{param: 이름}`), `body`(식 트리: `query`·`param`·`const` 잎과 `add/sub/mul/div/round/eq/ne/gt/gte/lt/lte/and/or/not/if` — 마디 인자 수 고정, 깊이 8·마디 40 상한), `result_label`·`result_type`(int/decimal/bool)·`decimals`. 사용자 코드는 받지 않는다. Function 이 없는 문서는 정규 표현에서 `functions` 키가 빠져 기존 `content_hash` 가 그대로다.
- **게시 전 검사**(`check_document`): 대상·끝 타입·링크 실재(폐기면 문제), 속성 자료형별 연산자(`ontology.models.OPS_BY_TYPE` — 계획 검증과 같은 표), 파라미터 ↔ 속성 자료형(int → decimal, string → enum 허용, `in`·`between`·`is_null` 에는 파라미터 금지), 집계 함수 ↔ 속성(min·max 도 숫자만), 그룹이 있는 조회는 모두 같은 `group_by`, 조회 이름이 그룹 열·`value`·`inputs` 와 겹치지 않음, 식 자료형(산술은 숫자, 논리는 참거짓, `if` 는 같은 자료형 두 갈래)과 결과 자료형. 경로는 언제나 `functions.<이름>`(영향 분석 경로와 같다), 조회 문제는 메시지 앞에 조회 이름.
- **요약·계획**: 스키마 요약의 `functions` 는 내부에서 조회하는 타입을 **모두** 읽을 수 있고 건너는 링크가 보이는 것만(이름·설명·결과 이름·그룹 열·인자). 하나도 없으면 키도, 플래너 `FUNCTION_RULES` 도 없고 구조화 출력 스키마도 `PlannerOutput` 그대로다(측정한 배포의 녹화 키 유지). 있으면 `FunctionPlannerOutput`(`kind: function`, 필드 순서 `kind·plan·function·action·reason`)과 `FunctionCall{name, args: [{name, value}]}`. 폐기 요소를 조회하는 Function 은 활성 문서(`active()`)에서 빠진다.
- **검증**(`functions.binding.bind`): 없는 Function 은 재계획, 있지만 읽을 수 없는 타입을 조회하면 `forbidden`(재계획 없음 — 이름을 지어 불러도 같다). 인자는 파라미터 자료형으로 변환(`query.validator.coerce`), 값 없는 선택 인자는 기본값, 기본값도 없으면 그 조건을 빼고 조회(조건 문장에 '지정 안 함'). 내부 조회는 `QueryPlan` 으로 바꿔 `validate` 를 그대로 지난다(호출자 권한, 값 바인드). 파이프라인은 내부 조회마다 참조 값 대조(`query.references`)를 하고, 인자 ↔ 질문을 두 방향으로 대조한다: 질문의 숫자가 인자·내부 조건에 없으면 재계획(`query.coverage`), 넣은 인자 값(enum·bool 제외)이 질문에 그대로 없으면 재계획(Action 값 파라미터와 같은 규칙 — 측정에서 플래너가 기본값을 채워 넣으며 질문의 지역 조건을 뺀 채 불렀다). 오류는 한꺼번에 1회 재계획.
- **실행·계산**(`functions.runner`·`evaluator`): 내부 조회를 파이프라인의 실행기로 차례로 돈다(장애 주입도 같은 실행기). 하나라도 `failed` 면 전체가 같은 사유로 `failed`. 값은 그룹 키로 맞추고(그룹이 있는 조회 키의 합집합, 최대 100개 그룹), 빠진 키는 `count` 0·그 밖 값 없음. 계산은 `Decimal`, 값 없음은 번지고 0 으로 나누면 값 없음, 결과는 서버가 `decimals` 로 반올림. 그룹 결과가 하나도 없으면 `empty`.
- **인용**(17.1-8): 결과 행은 `q:N` 근거 — 칸은 그룹 열, **내부 조회 값마다 하나**(이름 = 조회 이름, 라벨 = 조회 표시 이름), `value`(라벨 = `result_label`), 인자가 있으면 `inputs`(입력 조건 문장). 답이 "10곳 중 3곳(30%)"처럼 계산 근거를 말해도 칸마다 인용된다. 근거 대조(`grounding`)는 바뀌지 않았다. 조건 값 가리기는 내부 조회 조건 값과 인자 값. 대화 맥락은 바꾸지 않는다(집계와 같다).
- **조회 조건 문장**: `고위험 시설 비율(기준 위험등급 = 4(기본값)) · 고위험 시설 수: 시설 · 위험등급 ≥ 4 · 시설 유형별 건수 / 전체 시설 수: 시설 · 시설 유형별 건수 · 최대 100개 그룹` — Function 표시 이름·인자(기본값 표시), 내부 조회마다 7.1 5단계 문장(최대 N건 없이).
- **API**: `POST/PUT/DELETE /ontology/versions/{id}/functions[/{function_id}]`(모델러, draft 만, 이름 변경 409), `VersionDetail.functions`, 버전 비교 요소 `function`, 영향 분석이 Function 참조(조회 타입·건너는 링크·쓰는 속성)를 낸다. 감사는 `ontology.draft_edited`(`function.create/update/delete`).
- **화면**: 버전 화면의 Function 섹션 — 요약(인자·내부 조회·그룹·결과)과 JSON 편집(형식은 저장 때 422, 교차 검사는 게시 전 검사). 그래프 삭제 창이 함께 지울 Function 을 고를 수 있고, Function 문제 배지는 첫 내부 조회의 대상 타입 노드에 붙는다. 스키마 그래프 옆 패널에서 고른 타입의 Action(대상)·Function(조회 타입)을 만들고 고치고 지운다(v0.21 — 목록 탭과 같은 양식·API, 노드에 ƒ 칩). 근거 카드는 `q:N` 의 `values`(그룹 열·내부 조회 값·결과·입력 조건)를 보여 준다(v0.20).

### 17.5 v1 운영 지표 (v0.20, Phase 13)

PRD 7절의 v1 지표. `GET /admin/metrics?days=30`(관리자, `actions.metrics`), 관리 화면 맨 위 카드.

- **Action 실행 성공률** = (실행 + 롤백) ÷ (실행 + 롤백 + 실패). 실패 = 승인 때 재검증에 걸림(오작동 신호).
- **승인 거부율** = 거부 ÷ (거부 + 실행 + 롤백 + 실패). 요청자가 거둔 것(`rejected` 이고 결정자 = 요청자)은 `withdrawn` 으로 따로 세고 분모에서 뺀다. `previewed`·`blocked` 도 분모 밖.
- 기간은 요청 시각(`created_at`) 기준·DB 시계. 분모가 0 이면 `null`(화면 '—').
- **확인되지 않은 편집 충돌** = `sync_conflict.status = 'open'` 건수(기간 무관, 지금 값).

**성공 지표 목표**(v0.21, 사용자 결정 — "제안값은 관리자 설정에서 관리"): 표 `ops_target(key 유일, value, updated_by, updated_at)`(마이그레이션 0011), `GET·PUT /admin/targets`(관리자, `evaluation.targets`). 행이 없으면 측정 근거가 있는 제안값(정답률 0.9·추정 비율 0.2·Action 파라미터 정확도 0.9 — 게이트 가안, P95 25초·동시 8명 — ADR-0011), 근거 없는 지표는 미정(`null`). `PUT` 의 `null` 은 설정을 지워 제안값으로, 비율은 0~1, 감사 `ops.targets_changed`. 측정값: 품질은 마지막 **녹화 재생이 아닌** 평가 실행, 조회 실패율·P95·주간 활성 사용자는 트레이스(실패율 분자는 장애성 실패 `execution_error`·`timeout`·`graph_unavailable`·`llm_unavailable`·`too_broad` 만, P95 는 단계별 지연 합의 95 백분위), Action 실행 성공률은 위 지표. **평가 게이트(`gate.yaml`)는 이 값을 읽지 않는다** — CI 에서 DB 없이 같은 기준이어야 하고 불변식을 설정으로 느슨하게 할 수 없어야 한다.

### 17.6 데이터 그룹 권한 (v0.22, Phase 14a)

사용자 결정(2026-10-30, 그래프 편집 제안서): **역할 = 화면·기능, 그룹 = 데이터 권한**. 진행 순서는 14a 그룹 →
14b 속성 숨김·행 정책(읽기·쓰기) → 14c 감사 해시 체인. 속성 권한 기본값은 "타입을 읽으면 모두 보임, 지정한 것만
숨김", 기존 권한은 역할마다 같은 이름 그룹으로 자동 이전.

| 표 | 주요 컬럼 |
| --- | --- |
| `data_group` | `name`(유일, 소문자 식별자), `display_name`, `description`, `external_id`(유일 — SSO 그룹 클레임 값) |
| `group_member` | `(group_id, user_id)` |
| `group_type_permission` | `group_id`, `object_type`, `can_read`·`can_create`·`can_edit`·`can_delete` — 유일 `(group, type)` |
| `group_link_permission` | `group_id`, `link_type`, `can_link` — 유일 `(group, link)` |

- **판정**(`security.rbac`만): 소속 그룹 권한의 **합집합**(허용만, 거부 규칙 없음). `readable_types`(시그니처 그대로)·
  `editable_types`·`readable_types_for_group`(평가 `test_role` 이 팩의 평가 역할이 아니면 같은 이름 그룹). 역할 판정
  (`has_role`)은 API 접근 제어로 그대로.
- **Action**: 요청 = 대상 타입 읽기 + **편집**(+ 요청 역할). 채팅 요약의 요청 가능 Action·`GET /action-types` 의
  `can_request`·`actions.runner.preview` 가 같은 판정. 승인은 Action 의 승인 역할(+ 대상 읽기)이 정한다.
  생성·삭제·연결은 저장만 하고 데이터 그래프 편집(Phase 15 이후)이 쓴다.
- **이전**(마이그레이션 0012): 읽기 권한이 있던 역할마다 같은 이름 그룹, 그 역할 사용자 = 구성원, 읽던 타입 =
  **읽기·편집**(그전에는 읽으면 Action 을 요청할 수 있었으므로 — 업그레이드 뒤 동작 같음). 내림은 그룹 읽기를 같은
  이름 역할로 되돌린다. 평가 DB 에서 올림·내림 왕복 확인.
- **관리**: `GET·POST /admin/groups`, `PATCH·DELETE /admin/groups/{id}`, `PUT /admin/groups/{id}/members`(교체),
  `PUT /admin/groups/{id}/permissions`(교체 — 플래그 없는 행은 남기지 않음, 이름 형식만 검사), `GET /admin/link-types`,
  `POST /admin/users` 의 `groups`, `UserOut.groups`. `GET /admin/roles` 는 역할만(`readable_types` 제거),
  `PUT /admin/roles/{id}/permissions` 는 없앴다. 감사 `security.group_changed`·`group_members_changed`·
  `permission_changed`(target `group:<name>`). 권한 변경은 세션을 폐기하지 않는다(요청마다 다시 읽는다).
- **SSO**: 설정 `oidc_groups_claim`(기본 빈 값 = 꺼짐). 켜면 로그인마다 `external_id` 가 있는 그룹의 소속을 클레임
  값과 **정확히** 맞춘다(없는 값은 빠지고, `external_id` 없는 그룹은 건드리지 않는다, 감사 `via: oidc`).
- **도구**: `security.cli grant-type --group G --type T [--edit]`(그룹이 없으면 만든다, `--role` 은 같은 이름의 그룹),
  `create-user --group G`, `seed_domain_pack.py --grant-read G|--grant-edit G`, `ontobotctl install` 은
  `--grant-edit user --grant-edit admin` 과 관리자 `--group admin`.
- **화면**: 관리 → **데이터 그룹** 탭(그룹 카드: 구성원 체크, 타입 × 읽기·생성·편집·삭제 표, 링크 연결, SSO ID),
  사용자 표의 그룹 칸·계정 추가의 그룹 선택.

### 17.7 속성 숨김·행 정책 (v0.23, Phase 14b)

| 표 | 주요 컬럼 |
| --- | --- |
| `group_property_hide` | `group_id`, `object_type`, `property` — 유일 |
| `group_row_policy` | `group_id`, `object_type`, `applies_to`(read/write), `filters`(jsonb — QueryPlan `Filter` 조건 목록, AND) — 유일 `(group, type, applies_to)` |
| `golden_set.restrictions` | 평가 전용 역할의 `{hidden: {타입: [속성]}, rows: {타입: [조건]}}` |

- **합집합 규칙**(`rbac.merge` — 순수 함수, 평가 역할도 같은 함수): 속성은 그 타입을 **읽게 해 주는 모든 그룹**이 숨길
  때만 숨겨진다(기본은 모두 보임, 사용자 결정). 행은 그 권한을 주는 그룹마다 정책을 OR(각 정책은 조건 AND), 정책
  없는 그룹이 하나라도 있으면 제한 없음. 읽기 정책은 읽기 그룹, 쓰기 정책은 편집 그룹만 본다. `rbac.access(db,
  user)` → `Access{readable, editable, types: {타입: TypeAccess(hidden, read_rows, write_rows)}}`.
- **보이는 문서**(`OntologyDocument.without_properties`): 숨긴 속성을 빼고, 그 속성을 바꾸거나 조건·연락처로 쓰는
  Action 과 쓰는 Function 을 통째로 뺀다(미리보기·계산으로 새지 않게). pk·제목은 숨기지 않는다(저장 때도 거부).
  플래너 요약·계획 검증·데이터 블록·Function 바인드·근거 카드가 이 문서를 본다 — 숨긴 속성은 그 사용자에게 **없는
  속성**(계획에 쓰면 재계획, 권한 없음과 구분하지 않는다).
- **행 정책 주입**: `query.validator.check_scopes`(숨김 전 문서로 검증 — 정책은 숨긴 속성에도 건다)가 정책을
  `CheckedFilter` 로 바꾸고, `ValidatedPlan.scopes`(대상·탐색 끝 타입)에 실어 컴파일러가 `(… AND …) OR (…)` 로 덧붙인다
  — 값은 다른 조건처럼 바인드. 목록·집계·탐색 끝 pk 조회·Function 내부 조회 모두 같다. **잘못된 정책은 그 정책만 빠지고,
  남는 정책이 없으면 `false`(볼 행 없음)** — 정책 오류로 제한이 풀리지 않는다. 저장 때 `invalid_restriction` 으로 먼저 막는다.
- **근거 카드**: 숨긴 속성 칸 없음, 읽기 정책 밖 객체는 404(`query.executor.in_scope`).
- **Action**: 미리보기 때 대상이 요청자의 읽기 정책 밖이면 없는 것(404), 쓰기 정책 밖이면 거부. 승인 때 요청자의 정책으로
  **다시** 확인(밖이면 `failed` 기록).
- **API·화면**: `PUT /admin/groups/{id}/permissions` 의 타입 행에 `hidden`·`read_filters`·`write_filters`(null = 제한 없음),
  `GET /admin/object-types` 에 `properties`(pk·제목 제외). 그룹 카드의 타입 행 **속성·행** 펼치기(숨길 속성 체크, 정책 JSON).
- **평가**: 팩 `restrictions` + 침투 문항(정책 밖 객체 목록 → 0건, 숨긴 속성 질문 → 실패·값 미노출, 집계가 정책 안에서만 셈).
  실제 모델 게이트 통과(권한 위반 0).
- **한계**: 채팅 Action 제안의 같은 제목 후보(`actions.params`)는 읽기 정책을 보지 않는다(실행 기록은 미리보기에서 막힌다 —
  후보 제목이 보일 수 있음). 참조 값 실재 대조(`existing_values`)도 정책을 보지 않는다(존재 여부만).

### 17.8 감사 로그 해시 체인 (v0.24, Phase 14c)

- **매기기**(마이그레이션 0014): `BEFORE INSERT` 트리거 `audit_log_chain` 이 advisory lock(트랜잭션 범위) + 체인 머리 표
  `audit_chain_head`(한 줄: 마지막 `seq`·`hash`)로 순서를 정하고 `seq = 머리+1`, `prev_hash = 머리 해시`,
  `hash = audit_log_hash(prev, seq, ts(UTC, 마이크로초), actor, event, target, payload::text(jsonb 정규 표현), note)`
  (SHA-256 hex). 앱(`audit.record`)은 바뀌지 않는다. 체인 순서는 id 가 아니라 `seq` 다(id 는 트리거 전에 정해져
  커밋 순서와 어긋날 수 있다). 기존 행은 마이그레이션이 id 순서로 채웠다(추가 전용 트리거를 그 트랜잭션에서만 끄고).
- **검증**(`security.audit_chain.verify` — 보고만, 고치지 않는다): 트리거 없이 들어간 행(`unchained`), 같은 함수로 다시 계산한
  해시 불일치(`hash_mismatch`), 직전 해시와 이어지지 않음(`link_broken` — 중간 삭제·끼워 넣기), 순번 빈칸(`gap`), 첫 행
  없음(`head_missing`), 머리와 마지막 행이 다름(`tail_missing` — 꼬리 삭제). 처음 끊긴 곳 하나를 낸다.
- **언제**: 매일 작업 `audit_verify`(끊기면 error 로그 + 작업 결과), `security.cli verify-audit`(끊기면 종료 코드 1),
  관리 → 감사 로그 탭 **해시 체인 검증**(`GET /admin/audit-chain`, 조회도 감사).
- **한계**: DB 소유자가 해시 함수·트리거·머리까지 바꿔 체인을 통째로 다시 쓰는 것은 막지 못한다(외부 보관 백업과 대조가
  맡는다). 감사 기록이 있는 트랜잭션은 커밋까지 체인 잠금을 쥐므로, 감사 기록 뒤에 오래 걸리는 일을 같은 트랜잭션에서
  하지 않는다(지금 경로는 모두 짧다).

### 17.9 문서 등록·색인 (v0.25, Phase 16a)

- **저장**(마이그레이션 0018): `document`(제목·파일 이름·형식·크기·원본 SHA-256·**데이터 그룹(필수)**·추출 글·색인 상태)와
  `document_chunk`(번호·시작 글자·글·`embedding vector`(차원 없음)·`embedding_model`). 원본 파일은 두지 않는다.
  같은 그룹에 같은 파일(SHA-256)은 한 번만. 문서가 있는 그룹은 지울 수 없다.
- **권한**: 문서는 그 그룹 구성원만 본다(`rbac.document_groups` — 관리자도 소속이 아니면 못 본다, 없는 문서와 같은 404).
  등록·삭제·다시 색인은 모델러·관리자 역할 + 그 그룹 구성원.
- **글 추출**(`documents.extract`): `.txt`·`.md`(UTF-8, 안 되면 CP949)·`.pdf` 글자 층(`pypdf`). 스캔 PDF·HWP·암호 PDF 는
  받지 않는다(422). 정규화는 NFC·줄바꿈·줄 끝 공백뿐 — 근거 대조가 청크 원문을 그대로 찾기 때문이다(결정 12).
- **청크**(`documents.chunking`, 순수): 문단을 800자까지 이어 붙이고, 넘치는 문단은 문장 끝 → 글자 수로 자른다. 겹침
  없음, 각 청크는 원문의 연속된 부분 문자열.
- **색인**: 등록 트랜잭션에서 작업 `document_embed` 를 넣고 워커가 16개씩 `LLMGateway.embed("embedder")`(어댑터 `embed` —
  Ollama `/api/embed`, OpenAI 호환 `/embeddings`, Anthropic 은 없음). 모델 호출 동안 DB 트랜잭션을 쥐지 않는다. 지금
  모델과 다른 벡터는 다시 만든다. 실패하면 문서에 오류를 남기고 작업 큐가 다시 시도한다. 임베딩 호출 기록에는 글을 남기지
  않는다(개수·차원·지연).
- **pgvector 설치**: 확장은 슈퍼유저가 DB 초기화 때 별도 스키마 `extensions` 에 만들고(`USAGE` 는 PUBLIC), DB `search_path`
  에 넣는다(`postgres-init`) — 통합 테스트가 `public` 을 지웠다 다시 만들어도 확장이 남는다(앱 계정은 확장을 만들 수 없다,
  pgvector 는 trusted 가 아니다). 마이그레이션은 `CREATE EXTENSION IF NOT EXISTS` 로 있는지만 확인한다. 기존 설치(확장이
  `public`)도 그대로 동작한다.
- **임베딩 모델**(v0.26, Phase 16b — ADR-0016): `config/llm.yaml` 역할 `embedder` = `bge-m3`(1024차원). 합성 한국어 문서
  6건·문항 34개(`scripts/spike_embed.py`)에서 1위 적중 25/28·3위 안 28/28, `nomic-embed-text` 는 2/28. 검색 접두어는 쓰지 않는다.
- **벡터 인덱스**(v0.26, `documents.vector_index`): 모델마다 부분 HNSW — `(embedding::vector(N)) vector_cosine_ops` 식에
  `WHERE embedding_model = '<모델>'`. 모델 이름이 설정에서 오므로 런타임 DDL(`CONCURRENTLY IF NOT EXISTS`)이고 색인 작업이
  청크를 다 만든 뒤 확인한다(모델 이름 형식을 확인한 뒤에만 DDL 에). 검색은 같은 식(`distance_sql`)과 같은 모델 조건을 쓴다.
  임베딩 점수만으로 "답 없음"을 가르지 않는다 — 같은 분야의 답 없는 질문이 답 있는 질문보다 높게 나왔다(16d 는 답변·정확
  일치 근거 대조로 판정, 점수 문턱은 후보 줄이기에만).

### 17.10 문서 ↔ 객체 링크 (v0.27, Phase 16c)

- **저장**(마이그레이션 0019): `document_link(문서, 객체 타입·pk, 맞은 종류 title|pk, 맞은 글, 횟수, 처음 나온 청크, 상태
  candidate|confirmed|rejected, 결정자·시각)`, 문서·객체 쌍마다 하나. 객체는 이름(타입·pk)으로만 가리키고, 지워졌거나
  권한 밖이면 읽을 때 걸러진다.
- **후보 찾기**(`documents.matching`, 순수): 본문(NFC)에 객체 제목·pk 가 **그대로** 있으면 후보 — 유사도·형태소 없음.
  제목 2자 미만, pk 3자 미만·숫자만은 뺀다. 더 긴 다른 이름의 일치 안에 통째로 든 일치는 세지 않는다. 찾는 사람의 권한으로
  (`rbac.access` — 읽을 수 있는 타입·행 정책 안), 제목이 PII 인 타입은 pk 로만. 한 번에 객체 50,000개까지(넘으면
  `truncated`, 그때는 사라진 후보를 지우지 않는다). 동기 요청이다(`POST /documents/{id}/links/scan`, 모델러·관리자 + 그룹 구성원).
- **다시 찾기**: 확정·거부는 그대로, 거부한 것은 되살리지 않고, 찾은 사람이 읽을 수 있는 타입 안에서 더는 맞지 않는 후보만 지운다.
- **결정**: `PUT /documents/{id}/links/{link_id}`(확정·거부·후보로), 감사 `documents.link_decided`(찾기는 `documents.links_scanned`).
- **보기**: `GET /documents/{id}/links` — 보는 사람이 읽을 수 있는 객체만, 모델러·관리자가 아니면 확정만.
  `GET /objects/{type}/{pk}/documents` — 객체를 볼 수 있고(탐색기와 같은 확인) 문서 그룹에 속한 확정 링크만.
  화면: 문서 상세 "연결된 객체", 근거 카드 "관련 문서".

### 17.11 문서 질문·문서 인용 (v0.28, Phase 16d — 결정 12)

- **플래너**: 보는 사람의 문서 그룹(`rbac.document_groups`)에 지금 임베딩 모델로 색인된 문서가 있으면 요약에
  `documents`(제목 최대 30개)를 싣고 `kind: document` 를 연다. 없으면 프롬프트·스키마는 그대로(ADR-0004 Phase 16d).
  문서 질문에는 계획이 없고 검색어는 질문 문장 그대로다.
- **검색**(`query.retrievers.vector`): 보는 사람의 그룹 안, `indexed` 문서의 지금 모델 벡터만, 거리 식은
  `vector_index.distance_sql`, 모델 조건은 확인한 글자(부분 인덱스). 상위 3개, 유사도 0.3 미만 버림. 없으면 `empty`
  (문서 문구). 임베딩 공급자 불가는 `failed(llm_unavailable)`.
- **답변**: 데이터 블록 줄은 `d:<문서>/<청크> | 문서 제목 | 청크 원문`, 답변 프롬프트에 문서 규칙을 덧붙인다.
- **인용·대조**(`grounding.document`, 순수): `[[d:N/M]]`(속성 없음). 통과 = 문장의 숫자가 **모두** 그 청크에
  그대로(숫자 바로 뒤 글자 = 단위까지, 다른 숫자가 붙지 않은 채) AND 문장과 청크가 공백 접은 6자 이상을 그대로
  공유. 유사도 없음. 검색 결과에 없는 청크는 `unknown_ref`. 등급 규칙은 8.2 그대로, ③이면 문서 거부 문구.
- **근거 카드**: 인용에 대조한 청크 원문을 남긴다(`values` — 문서가 지워져도 근거를 보인다). 화면은 원문과 "문서 열기".
- **평가**: `eval-prepare` 가 합성 문서를 `sample_documents` 그룹에 넣고, 평가 실행이 문서를 모두 다시 임베딩한다
  (녹화·재생에 임베딩 응답도 남는다 — 빈 DB 의 CI 재생이 같은 벡터를 받는다). 역할을 정하지 않은 문항만 문서를 읽는다.
  골든 `d01`~`d09`(답 있음 5·객체 질문 1·답 없음 2·그룹 밖 1).

### 17.12 게시 영향 분석 (v0.29, Phase 17a — PRD 5.1 P1)

- `ontology.impact.change_impact(base=게시본, draft)`: `diff` 의 지우기·바꾸기마다 참조처 목록. 참조처 종류 — 정의(Action·
  Function·링크·pk/제목), 동기화 매핑, 골든 문항(대화 맥락 타입·기대 Action·조건 조각에 표시 이름이 **그대로** 든 것),
  행 정책, 속성 숨김, 타입 권한, 워크숍 요소(거부 안 된 것, 이름 정확 일치), 문서 링크 수, 살아 있는 데이터 여부.
  **이름과 개수만** 낸다(설정·본문·값 없음).
- **막는 것**: 정의 안의 깨진 참조(게시 전 검사가 이미 막는다)와 **행 정책이 지우거나 자료형·enum 을 바꾸는 속성을
  쓰는 것** — 게시하면 그 정책이 잘못된 정책이 되어 닫힌 쪽(볼 행 없음)으로 실패해 그 그룹이 조용히 아무것도 못 본다.
  `versioning.publish` 가 게시본이 있으면 `blocking_issues` 를 확인해 `PublishBlocked`(422 `publish_blocked`).
  나머지는 알리기만 한다(표시 이름만 바꾸면 골든 조건 조각이 어긋나는 것도 알림).
- API `GET /ontology/versions/{id}/impact`(게시본이 없거나 그 버전이 게시본이면 빈 목록), 화면: draft 의 "게시 영향" 카드
  (막는 줄은 빨간 표시, 게시 단추를 끈다).

### 17.13 공개 REST API (v0.30, Phase 17b — 결정 14, PRD 5.11 P1)

- **서비스 계정**: `app_user.kind = service`(마이그레이션 0020) — 비밀번호·SSO 없이 로그인하지 않는다. 역할·데이터 그룹은
  사람과 같은 관리(사용자·데이터 그룹)로 받고, 권한 판정도 같은 `rbac`. 감사 `security.service_account_created`.
- **API 키**(`api_key`): 원문 `obk_<prefix 8>_<secret>`, DB 에는 SHA-256 만(원문은 발급 응답에 한 번), 서비스 계정에만 발급,
  스코프 `query`·`objects:read`·`actions:request`(`rbac.has_scope` — 스코프는 데이터 권한을 **좁히기만** 한다), 만료(선택,
  1~3650일)·폐기. 정지 계정·폐기·만료 키는 401 `invalid_api_key`. `last_used_at` 은 1분에 한 번. 감사
  `security.api_key_issued`·`security.api_key_revoked`(prefix 만).
- **인증**: 공개 경로는 `Authorization: Bearer <키>` 만 받는다 — 세션 쿠키로는 부를 수 없다(다른 사이트가 로그인한 브라우저로
  부르는 길을 만들지 않는다). 스코프 없음은 403 `missing_scope`.
- **경로**(`/api/v1/public`): `POST /query`(화면 채팅과 같은 파이프라인, JSON 한 번 — 상태·답·등급·조건·근거·추정 문장·
  `trace_id`, 대화 맥락 없음, 트레이스는 남긴다. Action 요청 말이면 `actions:request` 스코프가 있을 때만 미리보기를 만든다),
  `GET /objects/{type}/{pk}`(근거 카드와 같은 읽기), `POST /actions/{name}/requests`(미리보기 = 요청, `request_id` 로 한 번만,
  `origin=api`), `GET /actions/requests/{run_id}`. **승인·실행은 사람이 화면에서**(채팅 Action 과 같은 규칙).
- **문서**: `GET /api/v1/public/openapi.json`(공개 경로만 붙인 앱으로 만든다 — 내부 스키마가 섞이지 않게, 인증 없음). 폐쇄망이라
  문서 화면(Swagger UI)은 두지 않는다. 호출 빈도 제한은 아직 없다(연결 풀이 동시 질의 상한이다 — 17.1 결정 14 재검토 거리).
- **화면**: 관리 → API 키 탭(서비스 계정 만들기·키 발급(원문 한 번)·목록·폐기).

### 17.14 인터뷰 워크숍 확장형 — 화면 (v0.31, Phase 18a, PRD 5.8 P1)

- **분할 화면**: 대화 | 실시간 미리보기. 미리보기 탭 — 검토(기존 목록) · **요소 그래프**(거절하지 않은 객체·속성·관계 후보로
  그린 "이대로 게시하면 생길 모양", 검토 전은 점선·흐리게, 후보 없이 속성·관계가 가리키는 이름은 점선 상자, 이름은
  정확 일치로만 잇는다 — `features/workshop/previewModel.ts`, 순수) · **Action 정의서**(조치 후보마다 하는 일·근거 발언·정할 것).
  답을 보낼 때마다 요소 목록을 다시 받아 그린다.
- **모바일**(폭 900px 이하): 한 열, 위 탭으로 대화/미리보기 중 하나만.
- **기능 플래그**(결정 13): `GET /config/features` → `{closed_network, voice_input}`. 판정은 `ontobot.features` 하나 —
  `voice_input = ONTOBOT_VOICE_INPUT and not ONTOBOT_CLOSED_NETWORK`(폐쇄망 기본 켜짐, 단위 테스트로 고정). 꺼져 있으면 화면은
  음성 단추를 **그리지 않는다**. 켜져 있으면 브라우저 음성 인식(Web Speech API)으로 입력 칸에 글을 붙이고, 음성이 브라우저
  공급자 서버로 감을 단추 옆에 알린다. 보낸 글은 글 발언과 같이 마스킹 저장된다.
- **미리보기 갱신 시간**(측정, 개발 PC·gemma4:e4b): 답을 보낸 때부터 요소 목록을 다시 받을 때까지 5.7(첫 턴, 모델 적재)·3.5·
  2.9·4.4초. 화면이 턴마다 보여 준다. PRD 수치 제안: **첫 턴 뒤 p50 5초 이하**(가안 5초와 맞다).

### 17.15 인터뷰 병합 (v0.32, Phase 18b, PRD 5.8 P1)

- **후보**(`workshop.merge.candidates`, 순수): 고른 세션들의 확정 요소를 종류별 이름 **정확 일치**로 묶는다(타입 `api_name`,
  속성 `타입.이름`, 링크 `api_name`, Action 후보 이름, 골든 질문 문장). 상태 — `agree`(정의 필드까지 같다), `conflict`(같은
  이름·다른 정의: 속성 `data_type`·`enum_values`, 링크 `from_type`·`to_type` — `publish.merge` 가 막는 것과 같다), `single`.
  이름은 다른데 표시 이름이 정확히 같은 것은 `same_display` 로 알리기만 한다(유사도로 묶지 않는다).
- **병합**(`publish.publish_many`, 모델러): 제출된 세션들(잠금 순서는 id 순)의 확정 요소를 세션 순서대로 이어 한 번의 `merge`
  로 draft 하나를 만든다. 충돌은 `publish_blocked`(409) — 세션 검토에서 고치거나 거절한 뒤 다시. 골든 질의 초안은 같은 질문을
  한 번만(`workshop-merge-<ids>`). 세션 모두 `published`·같은 `published_version_id`, 감사는 세션마다(`merged_sessions`).
  단일 세션 게시(`publish`)는 이것의 한 세션짜리다.
- API `POST /workshop/merge/candidates`·`POST /workshop/merge`(`session_ids` 2~20), 화면: 워크숍 목록에서 제출된 인터뷰를
  골라 병합 후보 표(충돌은 빨간 줄·병합 단추 끔) → 병합.

### 17.16 샘플 데이터 질의 시연 (v0.33, Phase 18c, PRD 5.8 P1)

- **격리 저장소**(`objects.demo_store`): 버전마다 스키마 `demo_v<버전 id>` 에 `object`·`link`·`object_edit`(`LIKE public.…
  INCLUDING DEFAULTS·CONSTRAINTS·INDEXES`, FK 는 베끼지 않음). 요청 트랜잭션의 `search_path` 맨 앞에 그 스키마를 넣는다
  (`set_config(..., true)` — 트랜잭션이 끝나면 풀린다). 객체 SQL 이 스키마를 적지 않으므로 **이것이 유일한 주입 지점**이고
  파이프라인 코드는 운영 그대로다(평가의 장애 주입과 같은 원칙). 스키마 이름은 정수 id 로만 만든다.
- **시연**(`workshop.demo`, 모델러): 게시 전 검사를 통과한 버전만. 타입별 CSV(첫 줄 = 속성 이름, pk 필수, 최대 1000행) —
  정의에 없는 열·자료형 오류·pk 빈칸·겹침은 모두 모아 그 표 전체를 받지 않는다(동기화와 같은 전체 실패). FK 링크 규칙을
  돌린다. 질문은 `pipeline.run` 그대로(읽기 = 그 버전의 모든 타입, 그래프 없음 → 탐색 조건은 `graph_unavailable`, 문서 없음),
  대화·트레이스는 남기지 않는다.
- API `GET /workshop/demo/{version_id}`(준비·검사 결과·타입별 열·행 수)·`POST …/rows`·`POST …/ask`·`DELETE …`(스키마째 지움).
  화면 `/workshop/demo/:versionId`(인터뷰 게시·병합 결과에서 링크).
- 한계: 시연 표는 만든 때의 `public` 표 모양을 베낀다 — 업그레이드로 객체 표가 바뀌면 지우고 다시 넣는다. 스키마는 지우기 전까지
  남는다(백업에 들어간다). 통합 테스트는 세션 시작 때 `demo_v*` 를 함께 지운다(버전 id 가 다시 매겨진다).

## 18. PRD 추적 매트릭스 (P0)

| PRD | 요구사항                                                              | TRD       | 모듈                                      | 검증                            |
| --- | --------------------------------------------------------------------- | --------- | ----------------------------------------- | ------------------------------- |
| 5.0 | 응답 등급 ①②③ 표시                                                    | 8.2, 14.2 | `grounding.grader`, `chat.stream`         | unit, contract                  |
| 5.0 | 규칙 기반 판정(정확 일치, LLM 자기 평가 금지)                         | 8.2       | `grounding.*`                             | unit                            |
| 5.0 | 지어낸 인용 제거·기록                                                 | 8.2       | `grounding.verifier`, `tracing`           | unit, eval                      |
| 5.0 | 조회 실패를 실패로 밝힘                                               | 7.3       | `query.executor`, `chat.templates`        | eval(장애 주입)                 |
| 5.0 | 마지막 동기화 시각 표시                                               | 11        | `sync.freshness`, `objects.reader`        | integration                     |
| 5.1 | 스키마 생성·수정 UI, YAML/JSON 임포트                                 | 6.1, 14.1 | `ontology.*`, 프론트 `ontology`           | unit, integration               |
| 5.1 | 단일 소스 매핑·증분 동기화                                            | 11        | `sync.*`                                  | integration                     |
| 5.1 | 스키마 버전 관리                                                      | 6.1       | `ontology.versioning`                     | unit                            |
| 5.2 | 자연어 → 질의 계획                                                    | 7.1, 7.2  | `query.planner`, `query.validator`        | unit, eval                      |
| 5.2 | 조회 조건 표시                                                        | 7.1       | `query.conditions`                        | unit, contract                  |
| 5.2 | 답변은 5.0 정책을 따름                                                | 7.3, 8    | `chat.pipeline`                           | eval                            |
| 5.2 | 맥락은 식별자만, 값은 매번 재조회                                     | 6.5, 7.1  | `chat.context`, `objects.reader`          | unit                            |
| 5.5 | SSO, RBAC                                                             | 9.1       | `security.auth`, `security.rbac`          | integration                     |
| 5.5 | LLM 컨텍스트 권한 필터                                                | 9.1       | `ontology.summary`, `query.validator`     | unit, eval(권한 침투)           |
| 5.5 | 파생 저장소 권한 격리                                                 | 6.7, 9.1  | `security.fingerprint`                    | unit (캐시 도입 시 integration) |
| 5.5 | PII 마스킹(로그·트레이스·외부 LLM), 대조는 원값                       | 9.2       | `security.pii`, `llm.gateway`             | unit                            |
| 5.5 | 로깅·보존 정책·감사 로그                                              | 9.3, 6.6  | `tracing`, `security.audit`               | integration                     |
| 5.5 | 프라이빗 모델(온프레미스면 P0)                                        | 10        | `llm.adapters`                            | integration                     |
| 5.6 | 골든셋 회귀 평가(정확도·근거 일치율·등급 분포·RBAC 침투·답 없음 문항) | 13        | `evaluation.*`                            | eval                            |
| 5.8 | 인터뷰 에이전트(텍스트)                                               | 12        | `workshop.topics`, `workshop.interviewer` | unit, eval                      |
| 5.8 | 검토 화면(요소 목록 + 근거 발언)                                      | 12        | `workshop.review`, 프론트 `workshop`      | integration                     |
| 5.8 | 산출물(스키마 초안·Action 후보·요약·골든 질의)                        | 12        | `workshop.extractor`, `workshop.publish`  | unit                            |
| 5.8 | 골든 질의는 현업이 확정한 기대 답변만                                 | 12        | `workshop.publish`                        | unit                            |
| 5.8 | 발언 마스킹·보존 기간                                                 | 9.2, 9.3  | `security.pii`, `tracing.retention`       | unit                            |

## 19. 테스트 전략

| 층          | 대상                                                                                                                                       | 비고                                         |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------- |
| 단위        | 근거 대조 규칙(정규화·사실 문장·등급), QueryPlan 검증, 컴파일러(값이 언제나 바인드로 나가는지), RBAC, PII 토큰화 왕복, 근거 발언 정확 포함 | `grounding`은 순수 함수라 픽스처만으로 검증  |
| 통합        | `deploy/docker/compose.test.yaml` 의 PostgreSQL(pgvector, builtin `C.UTF-8` 정렬 — DB `ontobot_test`·`ontobot_source_test`·`ontobot_eval`) + Neo4j(테스트 전용 컨테이너): 동기화 → 아웃박스 → 투영, 그래프 재구축·대사, 탐색 질의 | `task.py test-int` 가 띄운다. CI 는 ubuntu 러너 + 같은 compose, 이미지 빌드·스모크 잡 |
| 배포        | 배포 콘솔(인증·zip 안전 해제·작업 기록·API 규칙, 가짜 ontobotctl), ontobotctl shellcheck, 번들 왕복(서명 → 설치 → 업그레이드 → 롤백) | ADR-0013·0014 |
| 계약        | SSE 이벤트 순서·스키마, OpenAPI 스냅샷                                                                                                     | 프론트 타입 생성과 함께 깨지면 알 수 있게    |
| 권한 침투   | 역할별 문항: 읽을 수 없는 타입을 대상·필터·탐색 경로로 묻기, 데이터 값 속 지시문                                                           | `golden_item.test_role`·`forbidden_values`   |
| 장애        | 실행기 오류·타임아웃·Neo4j 중단 시 `failed`로 나가는지                                                                                     | `golden_item.fault`                          |
| 평가 게이트 | 13.2 지표 임계                                                                                                                             | CI 필수, 모델 교체 시 필수                   |
| 의존 규칙   | 16.3                                                                                                                                       | import-linter + 소스 검사 테스트             |

## 20. MVP 마일스톤 (12주, 가안)

| 주차   | 범위                                                                    | 완료 기준                                                 |
| ------ | ----------------------------------------------------------------------- | --------------------------------------------------------- |
| W1–2   | 저장소 골격, CI, 인증(OIDC·로컬), DB 마이그레이션, 스키마 레지스트리    | 도메인 팩의 `ontology.yaml`을 임포트·게시할 수 있다       |
| W3–4   | 커넥터 1종, 매핑, 증분 동기화, 객체 저장·읽기, 그래프 투영              | 샘플 소스 동기화 후 PostgreSQL·Neo4j 건수가 대사에서 일치 |
| W5–7   | 질의 파이프라인, 근거 대조·등급, 채팅 UI(근거 카드·조회 조건·실패 표시) | 골든셋 초기 문항이 파이프라인을 끝까지 통과               |
| W8–9   | 인터뷰 워크숍 기본형(인터뷰·추출·근거 확인·검토·게시)                   | 인터뷰 1회로 draft 버전과 골든 질의 초안이 만들어진다     |
| W10–11 | 평가 러너·장애 주입·게이트, PII, 감사 로그, 트레이스·보존               | CI 게이트 동작, 권한 침투·장애 문항 통과                  |
| W12    | 안정화, 부하 측정, 오프라인 설치 번들                                   | 폐쇄망 장비에서 번들만으로 설치·기동                      |

## 20.1 v1 출시 확인 (v1.0, Phase 19)

v1(PRD 8절 P1 행) 구현을 마쳤다. 출시 확인은 아래가 전부이고, **실제 폐쇄망 운영 장비 설치는 아직 하지 않았다**.

| 확인 | 결과 |
| --- | --- |
| 정적 검사·테스트 | lint(ruff·format·mypy·import-linter·eslint·tsc)·단위 305·통합 181(compose.test)·계약 12 통과 |
| 평가 게이트(실제 모델, eval_run 19) | 48문항(답할 수 있는 34) — 정답률 0.97(33/34)·근거 인용률 1.0·추정 비율 0.07·답할 수 없는 문항 ①② 0·장애 은폐 0·권한 위반 0·Action 지어내기 0·파라미터 정확도 1.0·p95 9.0초. 놓친 1건 a05 는 없는 식별자 Action 이 재계획에서 `cannot_answer`(지어내기 아님, ADR-0004 Phase 16d). 녹화 재생도 통과(놓침 0) |
| 부하(ADR-0011 Phase 19) | 1·4·8·16명 232건 실패 0, 8명 p95 18.0초, 첫 이벤트 p95 0.29초 |
| 설치·업그레이드·롤백 왕복 | 개발 PC 의 WSL2(Ubuntu) + Docker Desktop 에서 시험 키로 서명한 번들: MVP(2098e81) 전체 번들 설치(18·①, 탐색 질문 정상) → v1 앱 번들 `ontobotctl upgrade`(백업 → 마이그레이션 0008→0020 → 기동 → 준비 확인) → 18·① → `rollback --restore-db`(0008 로 복원, 18·①). 발견을 고친 뒤 모델을 담은 앱 번들로 다시 업그레이드 → 운영자 단계(역할 추가) → 문서 넣기·색인(bge-m3)·문서 질문 ① |
| 업그레이드에서 발견·수정 | 업그레이드는 운영자의 `config/llm.yaml` 을 덮지 않아 v1 의 `embedder` 역할과 bge-m3 모델이 빠졌다 — `/readyz` 는 ok 인데 문서 색인만 실패할 상태. `ontobotctl` 사전 점검·업그레이드가 없는 역할·Ollama 에 없는 모델을 알리고 새 기본값을 `config/llm.yaml.new` 로 둔다(덮지 않는다), 앱 번들에 `--with-models`, 문서 넣기는 역할이 없으면 409 `embedder_not_configured` |

남은 것(v1 밖이거나 운영 장비가 있어야 하는 것):

- Windows Server 2022 의 WSL2 0단계 점검(오프라인 WSL 설치·GPU·재부팅 자동 기동·LAN 포트 전달, ADR-0013).
- 공개 REST API 의 호출 빈도 제한(17.13), 문서 청크 길이 재측정(긴 실제 문서, ADR-0016), 앞 대화를 가리키는 문서 질문.
- 워크숍 시연의 관계 그래프(17.16), 화면 단위 테스트 도구(프론트 테스트 러너 없음).
- PRD 7절 수치 확정(사용자 — 관리 화면 '성공 지표 목표'에서, 제안은 PRD 7절).

## 21. 리스크와 미결 기술 결정 (ADR 후보)

| ADR  | 주제                               | 현재 결정 / 재검토 조건                                                                                              |
| ---- | ---------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| 0001 | 이중 저장소(PostgreSQL + Neo4j)    | 정합성 지연·운영 부담 → 아웃박스·대사·재구축, Neo4j 장애 시 명시적 실패로 대응. 투영 지연이 운영에서 반복되면 재검토 |
| 0002 | Neo4j 에디션·라이선스              | Community 기준(GPLv3, 클러스터링 없음). 권한은 PostgreSQL이 담당해 에디션 영향은 작다. 사내 라이선스 검토 필요       |
| 0003 | 객체 속성 jsonb 단일 테이블        | 타입별 객체가 수백만 건을 넘거나 필터 성능이 목표에 못 미치면 타입별 테이블로 재검토                                 |
| 0004 | 플래너 구조화 출력                 | 로컬 소형 모델의 JSON 실패율을 골든셋으로 측정해, `plan_invalid` 비율이 임계를 넘으면 모델 상향                      |
| 0005 | IdP                                | OIDC 기본. 사내가 LDAP/AD뿐이면 LDAP 어댑터 추가                                                                     |
| 0006 | 답변 본문 비스트리밍(검증 후 전송) | 체감 지연이 문제 되면 "검증 중" 표시와 함께 스트리밍 후 교체 방식 검토                                               |
| 0007 | (대체됨 → 0013) Docker 미사용 | — |
| 0013 | Docker(WSL2) 전환 | Windows Server 2022+ WSL2 + Docker Engine, 서명 이미지 번들, ontobotctl. 대상 OS 0단계 점검(WSL 오프라인·GPU·재부팅·포트)이 실패하면 Linux 서버 또는 Ollama 호스트 설치로 재검토 |
| 0014 | 배포 콘솔 | 앱과 독립된 컨테이너(docker.sock — 관리자 대역·관리자 계정만), 서명 필수, 사전 점검 + 버전 재입력. 이중화·SSO 전용이 되면 재검토 |
| 0015 | 개발 콘솔 | 개발 PC 127.0.0.1 전용(로그인 없음, Host·Origin·헤더 확인), 명령 정본은 task.py, 배포 콘솔과 실행기 공유. 여러 개발자가 한 빌드 서버를 함께 쓰면 재검토 |
| 0008 | 인터뷰 워크숍 면담자·추출기 | 다음 질문은 상태기계, LLM 은 평가·되묻기·추출만. gemma4:e4b(대본 7/8, p50 2.1초). 프롬프트·추출 스키마를 바꾸거나 실제 인터뷰 기록이 생기면 다시 잰다 |
| 0009 | 평가 게이트 | 운영 파이프라인 인프로세스 + 실행기 주입 장애, PR 은 녹화 응답(놓침 = 실패), 실제 모델 게이트는 모델·프롬프트 변경 때 로컬에서 녹화와 함께. 가안 수치는 PRD 7절이 정해지면 고친다 |
| 0016 | 문서 검색 임베딩 | bge-m3(한국어 문항 1위 25/28), 모델별 부분 HNSW, 점수로 답 없음을 가르지 않음. 모델을 바꾸면 `spike_embed.py` 로 다시 재고 모든 청크를 다시 임베딩 |
| 0012 | Action 실행·메일·SMS 발송 | 승인 = 실행(한 트랜잭션 재검증, 실패는 `failed` 기록), 발송은 아웃박스를 커밋 뒤 워커가 최소 한 번, 채널 미설정 = 미리보기에서 막힘. 게이트웨이가 요청 ID 중복 제거를 지원하면 "한 번" 발송으로 |

## 22. 개정 이력

| 버전 | 일자       | 내용 |
| ---- | ---------- | ---- |
| v0.1 | 2026-10-19 | 최초 작성 |
| v0.2 | 2026-10-19 | 착수 전 정정 — `link` 부분 유일키, `golden_item.golden_set_id`, `eval_run`·`eval_result` 컬럼 확정, `graph_outbox` 소비 순서·처리 위치, 식별자 형식(Neo4j 라벨 안전성), 속성 인덱스는 게시 작업이 생성, 권한 지문 참조 절, 문서 위치 |
| v0.3 | 2026-10-19 | Docker 미사용 — Windows Server 네이티브 설치·서비스 등록, 오프라인 번들 구성 변경, 통합 테스트는 로컬 설치 DB의 테스트 전용 DB·인스턴스, CI도 Docker 없이(ADR-0007) |
| v0.4 | 2026-10-20 | Phase 1 구현 반영 — `user_session`·`auth_request` 표, `app_user` 로컬 계정 컬럼, `/auth/providers`·`/auth/local/login`·버전 비교(`/diff`) 엔드포인트, 감사 로그 추가 전용은 DB 트리거로도 강제 |
| v0.5 | 2026-10-20 | Phase 1.5 스파이크 반영(ADR-0004) — `cannot_answer`, 참조 값 실재 대조 범위, 데이터 블록 형식, 평균 반올림, 사실 문장 판정 전 조건 값 제외, 깨진 인용 기록 |
| v0.16 | 2026-10-20 | Docker(WSL2) 전환·배포 콘솔(ADR-0013·0014, ADR-0007·0010 대체) — 2절 설치 수단, 3절 테스트·패키징, 16.1 디렉터리(`deployer/`·`deploy/docker/`), 19절 통합·배포 테스트(컨테이너), 21절 ADR 표. PostgreSQL 정렬을 builtin `C.UTF-8` 로 고정(플랫폼별 한글 정렬 차이로 카세트 키가 바뀌던 문제). `GET /admin/system`(버전·배포 콘솔 링크), 감사 이벤트 `deploy.*` |
| v0.17 | 2026-10-21 | 개발 콘솔(ADR-0015) — 16.1 디렉터리(`devconsole/`·`opsjobs/`), 21절 ADR 표. 배포 콘솔 작업 실행기를 공유 패키지 `opsjobs` 로 옮김(동작 같음, 출력의 색 코드 제거) |
| v0.25 | 2026-10-31 | Phase 16a 문서 등록·색인(17.9) — `document`·`document_chunk`(마이그레이션 0018), 문서 = 데이터 그룹 소유, 글 추출(txt·md·PDF 글자 층, HWP 제외)·겹침 없는 청크·`embedder` 역할·임베딩 작업, pgvector 를 `extensions` 스키마에 |
| v0.26 | 2026-10-31 | Phase 16b 임베딩 측정(ADR-0016) — `embedder` = `bge-m3` 확정, 모델별 부분 HNSW 인덱스(`documents.vector_index`, 런타임 DDL), 임베딩 점수로 답 없음을 가르지 않는다 |
| v0.27 | 2026-10-31 | Phase 16c 문서 ↔ 객체 링크(17.10) — `document_link`(0019), 본문의 제목·pk 정확 일치 후보(찾는 사람 권한, 긴 이름 안의 짧은 이름 제외) → 모델러 확정·거부, 근거 카드 "관련 문서" |
| v0.28 | 2026-10-31 | Phase 16d 문서 질문(17.11) — 플래너 `kind: document`(문서가 있을 때만), 그룹 안 벡터 검색, `[[d:N/M]]` 인용과 청크 원문 정확 일치 대조(숫자·단위·6자 구간), 평가 문서 문항·임베딩 녹화 |
| v0.29 | 2026-10-31 | Phase 17a 게시 영향 분석(17.12) — draft 의 지우기·바꾸기마다 참조처(정의·매핑·골든·행 정책·숨김·권한·워크숍·문서 링크·데이터), 행 정책이 지우는·자료형을 바꾸는 속성을 쓰면 게시를 막는다 |
| v0.30 | 2026-10-31 | Phase 17b 공개 REST API(17.13) — 서비스 계정(`app_user.kind`)·API 키(해시·스코프·만료·폐기, 0020), Bearer 만 받는 `/api/v1/public`(질의 JSON·객체 읽기·Action 요청 → 사람 승인), 공개 OpenAPI |
| v0.31 | 2026-10-31 | Phase 18a 워크숍 확장형 화면(17.14) — 대화 \| 실시간 미리보기(검토·요소 그래프·Action 정의서), 모바일 한 열·탭, 기능 플래그 `GET /config/features`(음성 입력은 폐쇄망이면 그리지 않음), 미리보기 갱신 시간 측정 |
| v0.32 | 2026-10-31 | Phase 18b 인터뷰 병합(17.15) — 세션 간 이름 정확 일치 후보(일치·충돌·한 세션만, 같은 표시 이름 알림), 모델러가 제출 세션 여럿을 draft 하나로(`publish_many`) |
| v0.33 | 2026-10-31 | Phase 18c 샘플 데이터 시연(17.16) — 버전별 격리 스키마 `demo_v<id>`·`search_path` 주입 하나로 운영 파이프라인 그대로 질의, CSV 샘플 행(전체 실패 규칙), 그래프 없음 |
| **v1.0** | 2026-10-31 | **v1(P1) 구현 확정**(Phase 19) — 출시 확인 20.1(게이트·부하·설치 → 업그레이드 → 롤백 왕복), 업그레이드가 새 LLM 역할·모델을 빠뜨리는 문제 수정(`ontobotctl` 설정 차이 알림·`--with-models`·`embedder_not_configured`), ADR-0016 을 21절에, 기반 PRD v0.5 |
| v0.24 | 2026-10-31 | Phase 14c 감사 로그 해시 체인(17.8) — `seq`·`hash`·`audit_chain_head`·트리거·해시 함수(마이그레이션 0014, 기존 행 채움), 검증(끊김 6종, 보고만)·매일 작업·CLI·관리 화면 |
| v0.23 | 2026-10-31 | Phase 14b 속성 숨김·행 정책(17.7) — `group_property_hide`·`group_row_policy`·`golden_set.restrictions`(마이그레이션 0013), 합집합 규칙(`rbac.merge`), 보이는 문서(`without_properties`), 컴파일러 행 정책 주입(닫힌 쪽 실패), 근거 카드·Action 쓰기 정책, 관리 화면, 평가 침투 문항 |
| v0.22 | 2026-10-30 | Phase 14a 데이터 그룹(17.6) — 역할 = 화면·기능, 그룹 = 데이터 권한(사용자 결정). `data_group`·`group_member`·`group_type_permission`·`group_link_permission`(마이그레이션 0012, `type_permission` 이전·제거), 합집합 판정, Action 요청에 편집 권한, 그룹 관리 API·화면, SSO 그룹 클레임 연동(기본 꺼짐), CLI·시드·설치 스크립트 |
| v0.21 | 2026-10-30 | 성공 지표 목표를 관리자 설정으로(17.5 — `ops_target`·`/admin/targets`, 마이그레이션 0011, 측정값 출처: 평가는 재생 제외·운영은 트레이스, 게이트와 분리), 스키마 그래프 옆 패널의 Action·Function 편집 |
| v0.20 | 2026-10-30 | Phase 13 — 17.5 v1 운영 지표(`GET /admin/metrics`, 성공률·거부율 정의, 요청자 거둠 분리), 근거 이벤트 `citations[].values`(집계·Function 행의 대조 값 — 근거 카드), Function 문제 배지 위치, `job` 기한은 DB 시계로만(간헐 실패 원인) |
| v0.19 | 2026-10-30 | Phase 12 Function(17.4) — `FunctionDef`(인자·집계 내부 조회·식 트리·결과)를 정의 문서에, `function_type` 표(마이그레이션 0010), 게시 전 검사(연산자 표 `OPS_BY_TYPE` 를 계획 검증과 공유), 요약에 실을 조건(내부 타입 모두 읽기), 플래너 `kind: function`(`FunctionPlannerOutput` — Function 이 없으면 스키마·프롬프트 그대로), 바인드·권한(`forbidden`)·인자 ↔ 질문 두 방향 대조, 실행기 공유·실패 전파, 그룹 키 맞춤·`Decimal` 계산·서버 반올림, 근거 행(내부 조회 값·결과·입력 조건), 조건 문장, API·화면. 17절 표의 5.10 행 갱신 |
| v0.18 | 2026-10-30 | Phase G1 스키마 그래프 편집기 — 정의 문서에 `deprecated`(객체 타입·링크 타입, 거짓이면 정규 표현·내보내기에서 빠져 기존 `content_hash` 유지). 플래너 요약·계획 검증은 폐기 요소를 뺀 문서(`OntologyDocument.active()`)를 본다(폐기 타입 = 없는 것, 권한 없음 아님). 폐기 타입을 대상으로 하는 Action 은 게시 전 검사 문제. `ontology_layout`(그래프 노드 위치 — 정의 밖, 감사 없음, draft 생성 때 부모 배치 복사), `GET/PUT /ontology/versions/{id}/layout`, 영향 분석 `GET /ontology/versions/{id}/references`(`ontology/impact.py` 첫 조각 — 문서 안 참조·매핑·골든 문항 id·데이터 존재만, 설정·본문·값 없음). `VersionSummary.created_by`·`created_by_name`. 마이그레이션 0009 |
| v0.15 | 2026-10-20 | Phase 11 구현 반영 — 17.3 채팅에서 Action: 플래너 `kind: action`·`ActionProposal`, 요청할 수 있는 Action 만 요약에, 대상·값 대조 규칙(질문·맥락에 그대로, pk → 제목 정확 일치, 후보), 재계획 뒤 확인 사항, 채팅 API 가 미리보기 기록(`origin=chat`). 7.3 상태 `action_preview`, 14.2 SSE `action_preview`, 13.1·13.2 지표 `action_param_accuracy`(가안·optional)·`action_fabrications`(불변식) |
| v0.14 | 2026-10-20 | Phase 10 구현 반영(ADR-0012) — 17.2 Action 실행: `action_type`·`action_run`·`notification`·`notification_delivery` 표, `app_user` 연락처, 정의 모양과 게시 전 검사, 미리보기 → 승인 = 실행(재검증) → 롤백(정정 발송) 흐름, 권한 판정, 발송 아웃박스(최소 한 번·재시도·채널 꺼짐 = 막힘), API·감사 이벤트. 17.1-4 의 아웃박스 표 이름을 `notification_delivery` 로 정정 |
| v0.13 | 2026-10-20 | Phase 9 구현 반영 — 편집 레이어 덮어 읽기(reader 파생 행, 조회 조건·집계·참조 대조 모두), 충돌 판정 규칙·편집당 살아 있는 충돌 하나, 재편집·해제 규칙, 값 계보, 객체·충돌·편집 해제 API, 감사 이벤트 2종, Action 변경 금지에 제목 속성 추가(17.1-3) |
| v0.12 | 2026-10-20 | v1 착수 결정(17.1) — 편집 레이어는 조회 조건에도 적용, Action·Function 은 온톨로지 문서에, Action 부수효과는 앱 알림·메일·SMS(사내 게이트웨이, 아웃박스 발송, 사용자 결정), 파라미터 검증·멱등성, 선언형 Function, 속성·행 권한, 감사 해시 체인, 임베딩·문서 인용, 폐쇄망 설정과 음성 입력 비활성화(사용자 결정), 공개 API 키. 2절 가정 표에 Action 부수효과·폐쇄망 여부 추가 |
| v0.11 | 2026-10-20 | Phase 8 구현 반영(ADR-0010·0011) — `/readyz` 판정 규칙(`ok`/`degraded`/`unavailable`, 외부 LLM 은 호출하지 않음), 설치본 프론트 서빙(`ONTOBOT_FRONTEND_DIST`), DB 연결 풀 설정(동시 채팅 상한), 오프라인 번들 구성·WinSW 서비스·릴리스 폴더 + `current` 정션 업그레이드·롤백(롤백 DB 는 업그레이드 직전 `pg_dump` 복원, 내림 마이그레이션 미사용), 부하 측정 결과 |
| v0.10 | 2026-10-20 | Phase 7 구현 반영(ADR-0009) — 러너 권한(평가 전용 역할 `golden_set.roles`)·장애 주입 지점(실행기)·`graph_down` 은 실제 드라이버, 녹화 응답 카세트(놓침 = 실패), 게이트 판정 규칙(불변식 None 통과·가안 None 실패), 권한 위반은 객체 근거만, 고아 실행 닫기, 골든셋 팩 형식·적재 규칙, `golden_item`·`golden_set`·`eval_result` 컬럼 추가, 평가 API 권한(챗봇 빌더·모델러) |
| v0.9 | 2026-10-20 | Phase 6 구현 반영(ADR-0008) — 주제에 "찾아보는 질문" 추가, 채워짐 판정·되묻기 상한, 추출 입력·근거 id 는 서버, 같은 요소 정확 일치 병합, 턴 실패 규칙, 골든 질의 거르기(주제·면담자 질문), 확정 검증, 게시 합치기 규칙(정확 일치·충돌은 게시 차단), `interview_session.title`·`published_version_id`, `golden_item.expected_answer`, 세션 조회 엔드포인트, 감사 이벤트 |
| v0.8 | 2026-10-20 | Phase 5 구현 반영 — 외부 공급자 PII 가역 토큰화의 대상 값·규칙, 호출 기록 마스킹 저장, 어댑터 셋(SDK 없이 HTTP), 관리 API 형태와 규칙(자기 잠금 방지·세션 폐기·권한 집합 교체), 감사 이벤트 추가, 보존 기간 설정·작업(감사 로그 제외), JSON 로그·요청 ID |
| v0.7 | 2026-10-20 | Phase 3·4 구현 반영 — `traverse` 의미(1홉 조건의 AND, 최대 3)와 실행 방식(PG pk 집합 → Neo4j EXISTS → PG), 링크 권한은 `forbidden`, `context_refs` 범위, FK 이름 → `traverse` 결정론적 재작성, 질문 숫자 반영 검사(`query.coverage`), 재계획 오류 일괄 전달, `message.meta`, `llm_calls` 본문 미저장(Phase 5 까지), SSE `done.conversation_id`·이벤트 순서·`ChatEvents`, 근거 카드 404(권한 없음 = 없음). 측정은 ADR-0004 Phase 4 보강 |
| v0.6 | 2026-10-20 | Phase 2 구현 반영 — 동기화 병합 규칙(행 실패 = 실행 실패, 원천별 삭제 표시, 링크 삭제 순서, 자료형 있는 워터마크), `secret_ref` 형식, stub 노드, 투영 묶음·재시도, 재구축 절차. 세션은 `async with session_scope()` 로만 연다(열린 트랜잭션이 `CREATE INDEX CONCURRENTLY` 를 막은 실측) |
