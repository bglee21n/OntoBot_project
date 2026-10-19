# TRD: 온톨로지 기반 챗봇 서비스 (OntoBot, 가칭)

| 항목      | 내용                                                             |
| --------- | ---------------------------------------------------------------- |
| 문서      | 기술 요구사항 문서(TRD) v0.5                                     |
| 작성일    | 2026-10-19 (v0.3: 2026-10-19 — 22절 개정 이력)                  |
| 기반 PRD  | `OntoBot_project-prd.md` v0.4                                    |
| 상세 범위 | MVP(P0)는 구현 가능한 수준으로, P1 이상은 끼워 넣을 자리(17절)만 |

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
| (추가) 설치 수단       | **Docker 미사용 — Windows Server 네이티브 설치** (사용자 결정, ADR-0007) | Linux 지원이 필요하면 설치 스크립트 추가 |

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
| 테스트           | pytest, pytest-asyncio, 로컬 설치 PostgreSQL·Neo4j의 테스트 전용 DB·인스턴스       | 19절                                   |
| 관측성           | JSON 구조화 로그 + DB `trace` 테이블                                               | 외부 APM 의존 없음(폐쇄망)             |
| 패키징           | Windows Server 네이티브 설치 + 오프라인 번들(uv·Python, wheelhouse, 프론트 빌드 산출물, PostgreSQL·pgvector·Neo4j·JRE 설치본, 설치 스크립트). api·worker는 Windows 서비스로 등록. **Docker는 쓰지 않는다**(ADR-0007) | 폐쇄망 설치, 운영 대상 최소화 |

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
| 복구                             | `scripts/graph_rebuild.py`가 Neo4j를 비우고 PostgreSQL에서 다시 투영                                                                                                                                           |

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
| `object_edit`   | `type`, `pk`, `property`, `value`, `base_source_value`(편집 당시 원천 값), `action_run_id`, `edited_by`, `edited_at`, `cleared_at` |
| `sync_conflict` | `edit_id`, `new_source_value`, `source_synced_at`, `status`(open/acknowledged/cleared)                                             |

규칙(PRD 5.3, 11절): 보이는 값 = 활성 편집 값이 있으면 그것, 없으면 원천 값. 충돌은 기록하되 편집 값이 이긴다.

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
| `message`              | `conversation_id`, `role`, `content`, `status`(ok/empty/failed/not_query), `grade`(1/2/3/null), `trace_id`                                                                                                                                            |
| `conversation_context` | `conversation_id`, `refs`(jsonb: `[{type, pk}]`) — **식별자만**, 값은 담지 않는다(PRD 5.2)                                                                                                                                                            |
| `trace`                | `user_id`, `message_id`, `plan`(jsonb), `sql_masked`, `cypher_masked`, `status`, `failure_reason`, `grade`, `fact_sentences`, `verified_sentences`, `removed_citations`(jsonb), `stage_latency_ms`(jsonb), `llm_calls`(jsonb, 마스킹된 프롬프트·응답) |

### 6.6 보안

| 테이블              | 주요 컬럼                                                                                                                   |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `app_user`          | `oidc_sub`(`iss|sub`), `username`·`password_hash`(로컬 계정, argon2), `display_name`, `status`                                                                                        |
| `user_session`      | `token_hash`(SHA-256 — 원문은 쿠키에만), `user_id`, `expires_at`, `revoked_at`, `auth_method`(local/oidc) |
| `auth_request`      | 진행 중인 OIDC 로그인: `state_hash`, `nonce`, `code_verifier`, `return_to`(같은 사이트 상대 경로만), `expires_at` — 콜백에서 한 번만 꺼내 지운다 |
| `role`, `user_role` |                                                                                                                             |
| `type_permission`   | `role_id`, `object_type`, `can_read` — MVP는 타입 단위(PRD 5.5). 속성 단위는 P1                                             |
| `audit_log`         | `ts`, `actor_id`, `event`, `target`, `payload`(마스킹) — 앱 DB 계정에 **INSERT·SELECT만** 허용. 해시 체인(`prev_hash`)은 P1 |

### 6.7 평가·워크숍·작업

| 테이블                    | 주요 컬럼                                                                                                                                                                                                                      |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `golden_set`              | `name`, `domain_pack`, `ontology_version_id`                                                                                                                                                                                   |
| `golden_item`             | `golden_set_id`(FK), `question`, `test_role`, `answerable`, `expected_status`, `expected_grade`, `required_values`(jsonb), `forbidden_values`(jsonb, 권한 침투용), `fault`(none/executor_error/graph_down/timeout), `source`(manual/workshop)       |
| `eval_run`                | `golden_set_id`, `ontology_version_id`, `llm_config`(jsonb, 역할별 공급자·모델), `status`(running/succeeded/failed), `metrics`(jsonb, 13.2), `gate_passed`, `started_at`, `finished_at`                                         |
| `eval_result`             | `run_id`, `item_id`, `status`, `failure_reason`, `grade`, `answer`, `citations`(jsonb), `verdict`(jsonb: 정답·권한 위반·실패 은폐 판정 근거)                                                                                   |
| `interview_session`       | `interviewee_id`, `topic_state`, `status`(open/submitted/published)                                                                                                                                                            |
| `interview_utterance`     | `session_id`, `speaker`, `text`(마스킹 저장), `seq`                                                                                                                                                                            |
| `draft_element`           | `session_id`, `kind`(object_type/property/link_type/action_candidate/golden_query), `payload`(jsonb), `evidence`(jsonb: `[{utterance_id, quote}]`), `evidence_ok`, `status`(proposed/confirmed/edited/rejected), `reviewed_by` |
| `job`                     | `kind`, `payload`, `status`, `run_after`, `attempts`, `locked_by`, `locked_at`                                                                                                                                                 |
| `graph_outbox`            | `id`(bigserial — **소비 순서**), `event`, `payload`, `created_at`, `processed_at` — 처리 위치는 `processed_at IS NULL`인 가장 작은 `id`다(별도 위치 테이블 없음)                                                                |

의미 캐시는 MVP에 두지 않는다. 도입하면 캐시 키에 **권한 지문**(사용자의 역할 집합 + 게시 스키마 버전의 해시)을 넣어 권한이 다른 사용자끼리 답변을 공유하지 않게 한다(PRD 5.5).

## 7. 질의 파이프라인 (PRD 5.0·5.2)

### 7.1 단계

| 단계              | 모듈                               | 내용                                                                                                                                    |
| ----------------- | ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 1. 맥락 로드      | `chat.context`                     | 대화의 객체 식별자를 읽는다. 값은 4단계에서 다시 조회한다                                                                               |
| 2. 계획           | `query.planner`                    | 권한으로 거른 스키마 요약 + 질문 + 맥락 식별자 → QueryPlan(JSON, 구조화 출력). 질의가 아니면 `not_query`, 조건을 스키마로 표현할 수 없으면 `cannot_answer`(→ `failed(plan_invalid)`) — 표현할 수 없는 조건을 **빼고** 계획을 내지 않는다 |
| 3. 검증           | `query.validator`                  | 타입·속성·링크 실재, 연산자 허용 목록, 값 타입 변환, enum·참조 값 실재 대조(링크 FK 속성의 eq·in 값이 대상 타입 pk 로 있는가 — `query.references`), 권한, 탐색 깊이(기본 3홉). 실패하면 오류를 붙여 1회 재계획 |
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
- **외부 LLM 호출 시**(공급자 설정 `external: true`): 요청마다 가역 토큰(`⟦P1⟧`)으로 바꿔 보내고, 응답을 받은 뒤 원값으로 되돌린다. 토큰 대응표는 요청 메모리에만 둔다.
- **근거 대조는 원값으로 한다**(PRD 5.5). 되돌린 뒤에 대조한다.
- 로그·트레이스·감사 로그에는 언제나 마스킹된 값만 남긴다.

### 9.3 그 밖의 보안 요구

- 비밀값(DB 접속정보, IdP 클라이언트 비밀, LLM API 키)은 환경변수·시크릿 파일로만 받고 DB에는 참조만 저장한다.
- **데이터 값 속 지시문은 데이터로만 취급한다.** 조회 결과는 구분된 데이터 블록으로 LLM에 넣는다. 또 LLM 출력은 검증(QueryPlan 스키마, 근거 대조)을 거쳐야만 효과를 가지므로, 데이터에 숨은 지시가 실행으로 이어질 경로가 없다.
- **감사 이벤트**: 로그인·로그아웃, 역할·권한 변경, 스키마 draft 생성·게시, 데이터 소스·매핑 변경, 동기화 수동 실행, 그래프 재구축, 워크숍 게시, 골든셋 변경, 평가 실행, 감사 로그 조회.
- **보존 기간**은 테이블별 설정(`trace`, `interview_utterance`, `message`)이고 만료분은 워커가 지운다. 감사 로그는 별도 설정이다.

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
- 신선도: 객체마다 `synced_at`, 매핑마다 마지막 성공 실행 시각. MVP의 근거 카드에는 마지막 동기화 시각을 보여 준다(PRD 5.0).
- **P1 편집 레이어 병합 규칙**(PRD 5.3, 사용자 결정 "편집 레이어 우선"):
  - 동기화는 `object_edit`을 건드리지 않는다.
  - 편집된 속성의 새 원천 값이 `base_source_value`와 다르면 `sync_conflict`(open)를 만든다.
  - 보이는 값은 계속 편집 값이다. 객체 카드는 "편집됨 · 원천 값 ○○ (동기화 시각)"을 함께 보여 준다.
  - 이 자리는 MVP 코드의 `sync/runner.py`에 훅(`on_source_change`)으로 미리 둔다.

## 12. 인터뷰 워크숍 기본형 (PRD 5.8)

- **주제 상태기계**(`workshop.topics`): 역할 → 업무 흐름 → 의사결정 지점 → 사용 데이터 → 예외 상황. 주제마다 필요한 정보가 채워졌는지 보고 다음 질문을 고른다. 모호한 답은 되묻는다.
- **추출**(`workshop.extractor`): 발언마다 요소 후보와 근거(`{utterance_id, quote}`)를 구조화 출력으로 받는다.
- **근거 확인**(`workshop.evidence`): `quote`가 해당 발언에 **그대로 있는지**(NFC·공백 정규화 후 정확 포함) 확인한다. 없으면 `evidence_ok=false`로 표시하고, 검토 화면에서 수정 없이 확정할 수 없게 한다.
- **검토**(`workshop.review`): 요소 목록(종류·이름·정의·근거 발언)에서 현업 담당자가 확정·수정·거절한다.
- **게시**(`workshop.publish`): 모델러가 승인하면 확정된 요소로 새 `ontology_version`(draft)을 만든다. 이후 5.1의 게시 흐름을 그대로 탄다.
- **골든 질의**: `kind=golden_query` 요소는 `{question, expected_answer, answerable}`을 가진다. 현업 담당자가 기대 답변을 확정한 것만 게시 때 `golden_item`(source=workshop)으로 복사한다. 인터뷰 에이전트의 질문 자체는 평가셋에 넣지 않는다.
- **Action 후보**는 `draft_element`(kind=action_candidate)로 남겨 v1에서 Action Type 정의에 쓴다.
- 발언은 마스킹해서 저장하고 보존 기간을 적용한다(9.2, 9.3).

## 13. 평가 (PRD 5.6·7)

### 13.1 실행

- 평가 러너는 **운영과 같은 `chat.pipeline`을 인프로세스로** 부르되, 문항의 `test_role` 권한으로 실행한다.
- `fault`가 있는 문항은 장애 주입기(`evaluation.faults`)로 실행기 오류·Neo4j 불가·타임아웃을 흉내 낸다.
- 실행 방법: CLI(`scripts/run_eval.py`)와 API(`POST /api/v1/eval-runs`).
- **게이트**: `evaluation/gate.yaml`의 임계값을 밑돌면 CI가 실패한다. 모델 교체 시에도 필수다.

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
| 권한 위반 응답                        | `test_role` 문항 중 답변·근거에 `forbidden_values` 또는 읽을 수 없는 타입의 객체가 나온 건수                                       | eval          |
| P95 응답 시간                         | `trace.stage_latency_ms` 합계의 95백분위                                                                                           | trace         |
| 데이터 신선도                         | 매핑별 (현재 − 마지막 성공 `sync_run.finished_at`)의 최댓값                                                                        | sync_run      |
| 동시 사용자 / 데이터 규모 / 가용성    | 부하 테스트 결과 / 객체 수 / 헬스체크 가동률                                                                                       | 별도 측정     |
| 주간 활성 사용자                      | 최근 7일 `message`의 고유 사용자 수                                                                                                | message       |
| 신규 객체 타입 구성 소요 시간         | 타입이 처음 draft에 나타난 시각 → 그 타입을 포함한 버전 게시 시각                                                                  | audit_log     |
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
| 객체     | `GET /objects/{type}/{pk}` (근거 카드)                                                                                                                                                                                                                                                              | 타입 읽기 권한      |
| 온톨로지 | `GET·POST /ontology/versions` · `GET /ontology/versions/{id}` · `POST·PATCH·DELETE /ontology/versions/{id}/object-types[/{tid}]` · 같은 형태의 `/properties`, `/link-types` · `POST /ontology/versions/{id}/import` · `GET /ontology/versions/{id}/export` · `GET /ontology/versions/{id}/diff?base=` · `POST /ontology/versions/{id}/publish` | 모델러              |
| 동기화   | `CRUD /sources` · `POST /sources/{id}/test` · `CRUD /mappings` · `POST /mappings/{id}/sync` · `GET /sync-runs`                                                                                                                                                                                      | 모델러              |
| 그래프   | `GET /graph/status` · `POST /graph/rebuild`                                                                                                                                                                                                                                                         | 관리자              |
| 워크숍   | `POST /workshop/sessions` · `POST /workshop/sessions/{id}/utterances` (다음 질문 반환) · `GET /workshop/sessions/{id}/elements` · `PATCH /workshop/elements/{id}` · `POST /workshop/sessions/{id}/submit` · `POST /workshop/sessions/{id}/publish`                                                  | 현업 / 모델러(게시) |
| 평가     | `CRUD /golden-sets` · `CRUD /golden-sets/{id}/items` · `POST /eval-runs` · `GET /eval-runs/{id}`                                                                                                                                                                                                    | 챗봇 빌더           |
| 관리     | `CRUD /admin/users` · `/admin/roles` · `/admin/permissions` · `GET /audit-logs` · `GET /traces/{id}`                                                                                                                                                                                                | 관리자              |

### 14.2 채팅 SSE 이벤트

| 이벤트       | 데이터                                                                     | 시점               |
| ------------ | -------------------------------------------------------------------------- | ------------------ |
| `stage`      | `{"stage": "planning" \| "querying" \| "answering" \| "verifying"}`        | 단계 시작          |
| `conditions` | `{"text": "시설 · 지역 = 서울 · …", "graph_as_of": "…" \| null}`           | 조회 직후          |
| `answer`     | `{"text": "…", "estimated_sentences": [2, 5]}`                             | 대조 완료 후 한 번 |
| `citations`  | `[{"ref": "o:Facility/F-001#risk_grade", "title": "…", "synced_at": "…"}]` | `answer` 직후      |
| `grade`      | `{"status": "ok", "grade": 1}`                                             | `answer` 직후      |
| `error`      | `{"status": "failed", "reason": "timeout", "message": "…"}`                | 실패 시            |
| `done`       | `{"message_id": "…", "trace_id": "…"}`                                     | 마지막             |

## 15. 비기능 요구사항

| 구분   | 요구사항                                                                                                                                                                    |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 성능   | 단계별 지연을 `trace.stage_latency_ms`에 남긴다. PRD 7절 P95 목표가 정해지면 13절 게이트에 넣는다. 타임아웃 기본값: SQL 10초, Cypher 5초, LLM은 역할별 설정                 |
| 폐쇄망 | 실행 중 외부 네트워크를 호출하지 않는다(외부 LLM 공급자를 켠 SaaS 배포는 예외). 프론트 자산은 빌드에 포함하고 CDN을 쓰지 않는다. 오프라인 번들로 설치한다                   |
| 가용성 | MVP는 단일 노드(api 1 + worker 1 + PostgreSQL + Neo4j). 워커는 `SKIP LOCKED`로 수평 확장이 가능한 구조로 만든다. `/healthz`(프로세스), `/readyz`(PostgreSQL·Neo4j·LLM 연결) |
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
├── deploy/
│   ├── windows/                    # 설치 스크립트(PowerShell) · 서비스 등록 · INSTALL.md
│   ├── offline/                    # build_bundle.ps1 (uv·Python · wheelhouse · 프론트 빌드 · DB 설치본)
│   └── env.example
├── scripts/
│   ├── seed_domain_pack.py
│   ├── export_openapi.py           # 프론트 타입 생성 입력
│   ├── run_eval.py
│   └── graph_rebuild.py
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
| 5.1 (P1)     | 변경 영향 분석                           | `ontology/impact.py` — Action·Function·프롬프트·골든셋·캐시 참조 검색                                                                                            |
| 5.1·5.2 (P1) | 문서 ↔ 객체 연결, 하이브리드 검색        | `document`·`document_chunk`(pgvector) 테이블, `query/retrievers/vector.py`, QueryPlan에 `search` 단계                                                            |
| 5.5 (P1)     | 객체·속성 단위 권한, 감사 로그 변조 방지 | `security/rbac.py`에 속성 투영·행 정책, 컴파일러가 주입 / `audit_log.prev_hash` 해시 체인                                                                        |
| 5.6 (P1)     | 트레이스 뷰·피드백                       | 프론트 `features/traces`, `feedback` 테이블                                                                                                                      |
| 5.8 (P1)     | 워크숍 확장형                            | 프론트 분할 화면, 샘플 데이터용 격리 스키마, `workshop/merge.py`(정확 일치 후보 제안)                                                                            |
| 5.9 (P1)     | 객체 탐색                                | 프론트 `features/explorer`, `graph/traversal.py`의 이웃 조회 API                                                                                                 |
| 5.10 (P1)    | Function                                 | `functions/`(registry, 버전), QueryPlan에 `function` 단계, 결과는 `[[q:n]]`으로 인용                                                                             |
| 5.11 (P1~P3) | REST API 공개 · 메신저 · SDK             | API 키·스코프 / `integrations/messenger/` / OpenAPI 기반 SDK 생성                                                                                                |
| 5.1 (P2)     | 멀티 소스                                | `sync/identity.py` — 소스 간 같은 객체 식별 규칙                                                                                                                 |
| 5.4 (P2)     | 에이전트 빌더                            | `agents/` — 도구 = query·functions·actions                                                                                                                       |
| 5.7 (P3)     | 자동화                                   | `automations/` + `jobs/schedules.py`                                                                                                                             |
| 5.12 (P3)    | 시나리오 what-if                         | `scenario/` — `reader.resolve(scenario_id)`가 시나리오 사본을 덮고, 탐색 결과에도 시나리오 링크 변경을 메모리에서 덮는다. 실제 객체·아웃박스에는 쓰지 않는다     |

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
| 통합        | 로컬 설치 PostgreSQL(테스트 전용 DB `ontobot_test`, 원천 역할 `ontobot_source_test`) + Neo4j(테스트 전용 인스턴스 — Community는 사용자 DB가 하나뿐이라 개발 그래프와 분리한다): 동기화 → 아웃박스 → 투영, 그래프 재구축·대사, 탐색 질의 | CI도 Docker 없이 러너에 직접 설치한다 |
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

## 21. 리스크와 미결 기술 결정 (ADR 후보)

| ADR  | 주제                               | 현재 결정 / 재검토 조건                                                                                              |
| ---- | ---------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| 0001 | 이중 저장소(PostgreSQL + Neo4j)    | 정합성 지연·운영 부담 → 아웃박스·대사·재구축, Neo4j 장애 시 명시적 실패로 대응. 투영 지연이 운영에서 반복되면 재검토 |
| 0002 | Neo4j 에디션·라이선스              | Community 기준(GPLv3, 클러스터링 없음). 권한은 PostgreSQL이 담당해 에디션 영향은 작다. 사내 라이선스 검토 필요       |
| 0003 | 객체 속성 jsonb 단일 테이블        | 타입별 객체가 수백만 건을 넘거나 필터 성능이 목표에 못 미치면 타입별 테이블로 재검토                                 |
| 0004 | 플래너 구조화 출력                 | 로컬 소형 모델의 JSON 실패율을 골든셋으로 측정해, `plan_invalid` 비율이 임계를 넘으면 모델 상향                      |
| 0005 | IdP                                | OIDC 기본. 사내가 LDAP/AD뿐이면 LDAP 어댑터 추가                                                                     |
| 0006 | 답변 본문 비스트리밍(검증 후 전송) | 체감 지연이 문제 되면 "검증 중" 표시와 함께 스트리밍 후 교체 방식 검토                                               |
| 0007 | Docker 미사용, Windows Server 네이티브 | 서비스 등록·업그레이드·롤백을 설치 스크립트가 맡는다. Linux 운영이 필요해지면 systemd 설치 스크립트를 추가한다 |

## 22. 개정 이력

| 버전 | 일자       | 내용 |
| ---- | ---------- | ---- |
| v0.1 | 2026-10-19 | 최초 작성 |
| v0.2 | 2026-10-19 | 착수 전 정정 — `link` 부분 유일키, `golden_item.golden_set_id`, `eval_run`·`eval_result` 컬럼 확정, `graph_outbox` 소비 순서·처리 위치, 식별자 형식(Neo4j 라벨 안전성), 속성 인덱스는 게시 작업이 생성, 권한 지문 참조 절, 문서 위치 |
| v0.3 | 2026-10-19 | Docker 미사용 — Windows Server 네이티브 설치·서비스 등록, 오프라인 번들 구성 변경, 통합 테스트는 로컬 설치 DB의 테스트 전용 DB·인스턴스, CI도 Docker 없이(ADR-0007) |
| v0.4 | 2026-10-20 | Phase 1 구현 반영 — `user_session`·`auth_request` 표, `app_user` 로컬 계정 컬럼, `/auth/providers`·`/auth/local/login`·버전 비교(`/diff`) 엔드포인트, 감사 로그 추가 전용은 DB 트리거로도 강제 |
| v0.5 | 2026-10-20 | Phase 1.5 스파이크 반영(ADR-0004) — `cannot_answer`, 참조 값 실재 대조 범위, 데이터 블록 형식, 평균 반올림, 사실 문장 판정 전 조건 값 제외, 깨진 인용 기록 |
