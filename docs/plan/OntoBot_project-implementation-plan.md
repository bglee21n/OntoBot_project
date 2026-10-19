# OntoBot MVP(P0) 구현 계획 — `OntoBot_project-trd.md` v0.1 기준

## Context

`D:\OntoBot_project`에는 아직 문서만 있습니다(PRD v0.4, PRD 검토 의견, TRD v0.1, 커밋 1개). 코드는 하나도 없습니다.
TRD는 MVP(P0)를 구현할 수 있는 수준으로 정해 두었고, 20절에 12주 마일스톤(가안)이 있습니다.
이 계획은 그 마일스톤을 **실행 순서·산출 파일·완료 기준·검증 방법**으로 풀어 놓은 것입니다.
범위는 P0만입니다. P1 이상은 TRD 17절에 적힌 "끼워 넣을 자리"만 코드에 남깁니다.

원칙은 하나입니다. **얇은 수직 슬라이스를 먼저 끝까지 관통시키고, 그다음 넓힙니다.**
질문 → QueryPlan → SQL → 근거 대조 → 등급 → SSE 경로가 W5~7에 처음 붙으면 그때 설계 결함이 한꺼번에 드러납니다.
그래서 Phase 1이 끝나면 하드코딩된 스키마로 이 경로를 한 번 관통시켜 봅니다(Phase 1.5).

## 착수 전 가정 (계획에 반영됨, 바뀌면 해당 Phase만 수정)

| 항목 | 가정 | 근거 |
| --- | --- | --- |
| 첫 커넥터 | **PostgreSQL**(원천도 PG). Oracle·MySQL·CSV는 `connectors/base.py` 인터페이스만 둔다 | TRD 20절 "커넥터 1종". 통합 테스트는 로컬 PG의 원천 역할 테스트 DB(`ontobot_source_test`)를 쓴다 |
| 샘플 도메인 | `domain-packs/sample`: Facility / Organization / Region 3타입, 링크 2종(`owned_by`, `located_in`) | TRD 7.2 예시와 같은 모양. 실제 도메인(PRD 10절 1번)이 정해지면 팩만 추가 |
| 평가 게이트 수치 | PRD 7절 공란 → `gate.yaml`에 **임시값**을 넣고 "가안" 주석. 불변식(인용률 100%, 실패 은폐 0건, 권한 위반 0건, 답 없음 문항 ①② 0%)만 확정값 | PRD 7절 |
| 등급 ② | 허용(설정 `GRADE2_ENABLED=true`) | TRD 2절 4번 |
| 로컬 LLM | Ollama 어댑터 우선 구현. OpenAI 호환·Anthropic 어댑터는 Phase 5까지 | 폐쇄망 기본 |
| 인증 | 로컬 계정을 W1에 먼저, OIDC는 같은 세션 계층 위에 붙인다 | 폐쇄망 IdP 부재 대비(TRD 9.1) |
| 개발 DB | 로컬 PostgreSQL 18 `ontobot_db`, 계정 `ontobot`(DB 소유자). 비밀번호는 루트 `.env`에만 둔다(커밋 금지) | 사용자 결정 |
| 가상환경 | **uv** — `backend/.venv`, 전체 고정은 `backend/uv.lock` | 사용자 결정 |
| 설치 수단 | **Docker 미사용** — 개발·CI·운영 모두 네이티브. 운영은 Windows Server(ADR-0007) | 사용자 결정 |

## TRD에서 착수 전에 고쳐 둘 것 (구현 중 판단을 막는 빈칸)

1. **`link` 테이블 유일키가 없다** → `(link_type, from_pk, to_pk)` 부분 유일 인덱스(`deleted_at IS NULL`). 없으면 증분 동기화가 링크를 중복 생성하고 아웃박스도 중복 이벤트를 낸다.
2. **`golden_item`에 `golden_set_id`가 없다** → FK 추가.
3. **`eval_result`·`eval_run` 컬럼이 비어 있다** → `eval_run(golden_set_id, ontology_version_id, llm_config jsonb, status, metrics jsonb, gate_passed)`, `eval_result(run_id, item_id, status, failure_reason, grade, answer, citations jsonb, verdict jsonb)`로 정한다.
4. **`graph_outbox`에 처리 위치를 기록할 자리가 없다**(5.2 "처리한 위치를 기록") → `processed_at`으로 충분한지 확정. 순서 보장을 위해 `id` bigserial 순 소비로 고정.
5. 16.4의 PRD 파일명(`ontology-chatbot-prd*.md`)이 실제(`OntoBot_project-prd*.md`)와 다르다 → 문서만 정정.
6. 9.1 "권한 지문은 파생 저장소의 키로 쓴다(6.7)" → 6.7 끝 문단을 가리키는 것이므로 참조 절을 명확히.
7. 동적 Neo4j 라벨: 타입 `api_name`을 라벨로 쓰므로 **식별자 정규식(`^[A-Z][A-Za-z0-9_]{0,63}$`)을 스키마 검증에서 강제**해야 Cypher 조립이 안전하다(값은 바인드 가능하지만 라벨은 불가).
8. `indexed=true` 속성의 표현식 인덱스를 "게시 시 마이그레이션 작업이 생성" → Alembic이 아니라 **런타임 DDL 작업**(job kind `create_property_index`)으로 처리하고 `CREATE INDEX CONCURRENTLY IF NOT EXISTS`로 멱등하게.

이 8개는 Phase 0에서 TRD v0.2로 반영하고 진행합니다.

---

## Phase 0 — 저장소 골격·CI (W1 전반)

**만드는 것**
- 16.1 디렉터리 그대로 생성. 루트 PRD/TRD는 `docs/prd/`, `docs/trd/`로 이동. `docs/adr/0001~0006` 템플릿(21절).
- `backend/pyproject.toml`(pinned, uv 관리 — 개발 의존성은 `[dependency-groups]`), ruff·mypy(strict 아님, `grounding/`·`query/`만 strict), pytest-asyncio.
- **import-linter 계약을 첫날 넣는다**(16.3의 1~4번). 5번(object 직접 SELECT 금지)·6번(권한 계산 금지)은 소스를 훑는 테스트 `tests/unit/test_dependency_rules.py`로.
- `frontend/` Vite+React+TS, eslint, tsc. `src/i18n/ko`.
- ~~compose~~ → **Docker 미사용**(ADR-0007). 구성 요소는 로컬에 직접 설치하고 `deploy/windows/`에 설치 절차를 둔다.
- 작업 실행기 `scripts/task.py`가 명령의 정본(Windows에 make가 없다). `Makefile`은 그것을 부르기만 한다.
- CI 파이프라인: lint → unit → contract → integration(Windows 러너, 설치된 PostgreSQL) → eval gate.

**완료 기준**: 빈 앱이 `/healthz` 200, CI 녹색, import-linter가 위반 샘플을 실제로 잡는지 확인하는 테스트 1개.

## Phase 1 — 기반: DB·인증·스키마 레지스트리 (W1~2)

**백엔드**
- `config.py`(pydantic-settings), `db/session.py`(async SQLAlchemy), `db/models/`를 6절 묶음별 파일로: `ontology.py objects.py sync.py chat.py security.py eval.py workshop.py jobs.py`.
- Alembic 초기 마이그레이션: P0 테이블 전부 + `object_edit`·`sync_conflict`는 **만들지 않는다**(P1). `audit_log`는 앱 계정에 INSERT·SELECT만 GRANT하는 별도 마이그레이션(운영 DB 역할 분리 스크립트 포함).
- `security/auth.py`: 로컬 계정(argon2) + 서버 세션 테이블(또는 서명 쿠키 + 서버측 세션 저장) → HttpOnly·SameSite=Lax. OIDC(Authorization Code + PKCE)는 같은 세션 발급 함수로 합류. `api/deps.py`의 `current_user`, `require_role`.
- `security/rbac.py`: `readable_types(user) -> frozenset[str]` 단 하나의 판정 함수. 이후 모든 권한 판단은 이것만 부른다.
- `security/audit.py`: `record(event, actor, target, payload)` — payload는 `security.pii.mask` 경유.
- `ontology/models.py`(Pydantic, 식별자 정규식 포함), `versioning.py`(draft 생성·게시·게시본 불변·버전 diff), `importer.py`(YAML/JSON ↔ 버전), `registry.py`(현재 게시 버전 인메모리 캐시, 게시 시 원자적 교체), `summary.py`(권한 필터된 플래너용 요약).
- `api/errors.py`(RFC 7807), `api/auth.py`, `api/ontology.py`(14.1 온톨로지 묶음).

**프론트**
- 로그인, 인증 가드, 레이아웃. `features/ontology`: 버전 목록 → 타입/속성/링크 편집 → 임포트 → 게시. OpenAPI 타입 생성 파이프라인(`scripts/export_openapi.py` → `openapi-typescript`)을 이 시점에 연결.

**완료 기준**(TRD 20절 W1–2): `domain-packs/sample/ontology.yaml`을 화면과 API 양쪽으로 임포트·게시할 수 있다. 게시본 수정 시도 → 409. 감사 로그에 draft 생성·게시가 남는다.

**테스트**: unit — 게시본 불변, 버전 diff, 식별자 검증, `readable_types`. integration — 임포트→게시→registry 갱신.

## Phase 1.5 — 수직 슬라이스 스파이크 (W2 말, 3일 이내)

목적: Phase 4의 위험(소형 모델 구조화 출력 실패율, 근거 대조 규칙의 실효성)을 일찍 측정. ADR-0004 판단 근거를 미리 얻는다.

- 샘플 팩 seed CSV를 `object` 테이블에 직접 적재(동기화 없이).
- `llm/gateway.py` + Ollama 어댑터 + `structured.py`(스키마 검증·1회 재시도)만 먼저.
- `query/plan.py`·`validator.py`(실재·연산자만)·`compiler.py`(단일 타입 SQL만)·`grounding/` 최소판.
- 골든 질문 10개로 `plan_invalid` 비율과 등급 분포를 기록 → `docs/adr/0004`에 수치로 남김.

스파이크 코드는 버리지 않고 Phase 4의 출발점으로 다듬습니다. 단, traverse·권한·SSE는 여기서 만들지 않습니다.

## Phase 2 — 동기화·객체 저장·그래프 투영 (W3~4)

**백엔드**
- `sync/connectors/base.py`(인터페이스: `test()`, `iter_rows(query|table, watermark)`), `postgres.py`. 나머지 셋은 `NotImplementedError` 스텁.
- `security/secrets.py`: `secret_ref` → 환경변수·시크릿 파일 조회. DB에는 참조만.
- `sync/mapping.py`: `column_map`·`pk_column`·`link_rules` 검증(대상 속성·링크 타입이 **현재 게시 버전에 실재**해야 저장).
- `sync/runner.py`: 스테이징 테이블 → **한 트랜잭션 병합**(object upsert by `row_hash`, full 모드 `deleted_at`, link upsert/delete, **같은 트랜잭션에서 `graph_outbox` 기록**). 실패 시 `sync_run.failed` + 기존 데이터 무변경. `on_source_change` 훅 자리(MVP는 no-op).
- `objects/store.py`(upsert·삭제 표시), `objects/reader.py`의 `resolve(type, pks, *, user)`: **게시 버전에 선언된 속성만** 투영, MVP는 원천 값 그대로. 16.3-5 소스 검사 테스트 추가.
- `sync/freshness.py`: 매핑별 마지막 성공 시각, 객체별 `synced_at`.
- `jobs/queue.py`(`FOR UPDATE SKIP LOCKED`, `run_after`, `attempts` 상한), `jobs/worker.py`, `jobs/schedules.py`(매핑 `schedule` → job 생성, 대사·보존 작업).
- `graph/client.py`(드라이버는 여기만), `graph/projector.py`(outbox id 순 소비, 노드 `(type, pk, title)`만 — 속성·PII 금지), `graph/reconcile.py`(타입·링크 타입별 건수+해시 대사, 재구축), `scripts/graph_rebuild.py`.
- `api/sources.py`(sources·mappings·sync-runs), `api/graph.py`(`/graph/status`의 `projection_lag`, `/graph/rebuild`).
- 게시 시 `indexed=true` 속성 → `create_property_index` 작업 발행.

**프론트**: `features/sources` — 소스 등록·접속 테스트·매핑 편집·수동 동기화·실행 이력.

**완료 기준**(W3–4): 샘플 원천 PG 동기화 후 PostgreSQL·Neo4j 건수가 대사에서 일치. 동기화 중간 실패를 주입하면 기존 데이터가 그대로이고 `sync_run.failed`가 남는다. 증분 두 번 실행 시 링크·아웃박스 중복 없음.

**테스트**: integration — 동기화→아웃박스→투영, 증분 watermark, full 삭제 표시, 재구축 후 대사 일치, Neo4j에 속성이 없음을 단언.

## Phase 3 — 근거 대조·등급 (W5, Phase 4와 병행 가능)

`grounding/`은 LLM·DB 비의존 순수 함수라 **픽스처만으로 먼저 완성**합니다. 파이프라인의 계약을 이 모듈이 정합니다.

- `citations.py`: `[[o:Type/pk#prop]]`, `[[q:n#field]]` 파싱.
- `normalize.py`: NFC, 공백 접기, 천 단위 구분 제거, 날짜 ISO. **유사도 없음.**
- `facts.py`: 사실 문장 판정(숫자·날짜, 결과 객체 제목, enum 값 포함).
- `verifier.py`: 인용 대상이 결과에 있는가 / 읽을 수 있는 타입인가(호출부가 `readable_types` 결과를 인자로 넘김 — grounding이 security를 import하지 않게) / 원값 또는 표시 문자열이 문장에 **그대로** 있는가. 실패 인용 제거 + 제거 목록 반환.
- `grader.py`: ①②③ 규칙, `grade2_enabled=False`면 ②→③. 반환값에 `fact_sentences`, `verified_sentences`, `estimated_sentences`.
- 표시용 문자열은 `query` 쪽 레코드 직렬화(`query/records.py`)가 만들고 grounding은 받기만 한다.

**완료 기준**: 단위 테스트로 등급 표의 모든 칸과 "근거 인용률 = 100% 불변식"을 단언. 반올림된 숫자·다른 객체 인용·권한 밖 인용이 각각 제거되는 케이스.

## Phase 4 — 질의 파이프라인·채팅 (W5~7)

**백엔드**
- `query/plan.py`(QueryPlan Pydantic, 연산자 고정 집합), `planner.py`(권한 필터 스키마 요약 + 맥락 식별자 → 구조화 출력, `not_query` 판정 포함).
- `query/validator.py`: 타입·속성·링크 실재, 연산자·값 타입 변환, enum·참조 값 실재, **권한(대상·필터·탐색 경로 모두)**, 깊이 ≤3. 실패 시 오류를 붙여 1회 재계획.
- `query/compiler.py`: traverse → 바인딩 Cypher(허용 라벨 조건 이중 방어, 결과는 대상 pk 집합만), 나머지 → 바인딩 SQL(jsonb 표현식, pk 집합은 `= ANY(:pks)`). **값이 언제나 바인드로 나간다는 것을 테스트로 고정.**
- `query/executor.py`: SQL 10초·Cypher 5초 타임아웃, pk 상한 10,000 → `too_broad`, Neo4j 불가 → `graph_unavailable`(**PG `link`로 대체 금지**), status = ok/empty/failed.
- `query/conditions.py`: 결정론적 조회 조건 문장, 투영 지연 10분 초과 시 "관계 정보는 HH:MM 기준".
- `chat/context.py`(식별자만 저장·로드), `chat/answerer.py`(번호 붙은 레코드를 구분된 데이터 블록으로), `chat/templates.py`(empty·failed·not_query·③ 거부 문구 전부), `chat/pipeline.py`(7.1 단계, 단계별 지연 측정), `chat/stream.py`(14.2 이벤트 순서: stage → conditions → answer → citations → grade → done / error).
- `tracing/recorder.py`: trace 한 행(plan, 마스킹된 SQL/Cypher, removed_citations, stage_latency_ms, llm_calls).
- `api/chat.py`, `api/conversations.py`, `api/objects.py`(근거 카드: 값 + 마지막 동기화 시각, 권한 검사).

**프론트**: `features/chat` — SSE 리더(`api/`), `QueryConditions`, `GradeBadge`, `CitationCard`(클릭 → `/objects/{type}/{pk}`), "추정" 표시, `FailureNotice`(failed를 empty와 다른 모양으로). 답변 본문은 `answer` 이벤트에서 한 번에 그린다(ADR-0006).

**완료 기준**(W5–7): 골든셋 초기 문항(샘플 팩 `golden.yaml` 20~30문항)이 파이프라인을 끝까지 통과. 상태 표 7.3의 모든 행을 재현하는 문항이 최소 1개씩 있다.

**테스트**: unit — validator 거부 사례(권한 밖 경로 포함), compiler 바인드, conditions 결정성. contract — SSE 순서·스키마 스냅샷, OpenAPI 스냅샷. integration — traverse 질의 PG+Neo4j 왕복.

## Phase 5 — 보안 마감·LLM 게이트웨이 확장 (W8~10 중 병행)

- `security/pii.py`: `pii_kind` 기반 마스킹 + 패턴 탐지(전화·이메일·주민번호) + **요청 단위 가역 토큰화**(`⟦P1⟧`, 대응표는 메모리만). 왕복 테스트.
- `llm/gateway.py`: 공급자 `external: true`면 토큰화 → 호출 → 복원. **근거 대조는 복원 후 원값으로.** 모든 호출을 마스킹해 trace에.
- 어댑터 추가: `openai_compat.py`, `anthropic.py`(SDK import는 `llm/adapters/`만). 모델 ID는 설정 파일에만.
- `security/fingerprint.py`(역할 집합 + 게시 버전 해시) — MVP는 캐시가 없으므로 함수와 단위 테스트만.
- 감사 이벤트 9.3 목록 전부 연결, `api/admin.py`(사용자·역할·권한·감사 로그 조회·trace 조회). 감사 로그 조회 자체도 감사.
- `tracing/retention.py`: 테이블별 보존 기간, 워커 작업으로 만료 삭제.
- 로깅: JSON 한 줄, 요청 ID 전파, 마스킹 필터, 회전.
- 프론트 `features/admin`.

## Phase 6 — 인터뷰 워크숍 기본형 (W8~9)

- `workshop/topics.py`(역할 → 업무 흐름 → 의사결정 → 데이터 → 예외, 충족 판정·되묻기), `interviewer.py`, `extractor.py`(구조화 출력: 요소 + `{utterance_id, quote}`), `evidence.py`(NFC·공백 정규화 후 **정확 포함**만), `review.py`(`evidence_ok=false`는 수정 없이 확정 불가), `publish.py`(확정 요소 → 새 draft 버전, 확정된 `golden_query`만 `golden_item(source=workshop)`으로 복사, Action 후보는 draft_element로 보존).
- 발언은 마스킹 저장, 보존 기간 적용.
- `api/workshop.py`, 프론트 `features/workshop`(텍스트 인터뷰 + 요소 검토 목록).

**완료 기준**(W8–9): 인터뷰 1회로 draft 버전과 골든 질의 초안이 만들어지고, 근거 없는 요소는 그대로 확정되지 않는다.

## Phase 7 — 평가·게이트 (W10~11)

- `evaluation/golden.py`(도메인 팩 `golden.yaml` ↔ DB), `runner.py`(**운영과 같은 `chat.pipeline` 인프로세스**, 문항의 `test_role`로 실행), `faults.py`(executor 오류·Neo4j 불가·타임아웃 주입 — 파이프라인에 주입 지점을 의존성으로 둔다), `metrics.py`(13.2 계산식 전부), `gate.py` + `gate.yaml`.
- `scripts/run_eval.py`, `api/eval.py`(golden-sets CRUD, eval-runs), 프론트 `features/eval`.
- CI에 eval 게이트 단계 연결(로컬 Ollama 필요 → CI 러너에 Ollama 또는 녹화된 LLM 응답 픽스처 모드 중 택1. **권장: 게이트는 실제 모델로, PR마다는 픽스처 모드로 파이프라인 회귀만**).

**완료 기준**(W10–11): 게이트 동작, 권한 침투·장애 주입 문항이 전부 통과(실패 은폐 0, 권한 위반 0).

## Phase 8 — 안정화·부하·오프라인 번들 (W12)

- 부하 측정(동시 사용자·P95) → PRD 7절 공란 수치 확정 제안.
- `/readyz`(PG·Neo4j·LLM), 매일 대사 스케줄 확인.
- `deploy/offline/build_bundle.ps1`(uv·Python, wheelhouse, 프론트 빌드 산출물, PostgreSQL·pgvector·Neo4j·JRE·Ollama 설치본), `deploy/windows/install.ps1`(설치·서비스 등록·업그레이드·롤백), `INSTALL.md`. 서비스 래퍼(WinSW 또는 NSSM)를 이 Phase에서 확정한다. 프론트 자산은 빌드에 포함, CDN 없음.
- **완료 기준**: 인터넷 없는 장비에서 번들만으로 설치·기동, 샘플 팩 시드 → 질의까지.

---

## 재사용 메모 (SafeBot에서 가져올 수 있는 검증된 설계)

`D:\disaster_chatbot_project`(SafeBot)를 확장하지 않고 신규 구축하지만(TRD 2절 7번), 같은 함정을 이미 겪은 설계가 있어 **패턴만** 옮깁니다(코드 복사 아님).
- 작업 큐 `FOR UPDATE SKIP LOCKED` + `RETURNING` 한 문장 claim, `attempts` 상한 → `app/libs/indexing_queue.py`
- 기동 시 고아 `running` 정리 + stale 판정 → `SystemTaskStatus.recover_orphan_running`
- 인메모리 캐시 원자적 교체(`registry.py`) → `app/libs/graph_store.py`, `golden_datasets.py`
- RFC 6266 다운로드 헤더, CORS `expose_headers` 한 곳 → `app/libs/download_headers.py`
- PII 컬럼 목록 + 값 패턴 마스킹, 디버그 덤프 마스킹 → `app/libs/pii_mask.py`, `debug_dump.py`

## 검증 (전 Phase 공통)

```
make lint            # ruff·mypy·import-linter·eslint·tsc
make test-unit       # grounding·query·security·workshop·evaluation
make test-int        # 로컬 PG 테스트 DB(ontobot_test·ontobot_source_test) + Neo4j 테스트 인스턴스
make test-contract   # SSE 순서·OpenAPI 스냅샷
make eval            # 골든셋 게이트(gate.yaml)
```

엔드투엔드: 로컬 서비스(PostgreSQL·Neo4j·Ollama) 기동 + `task.py dev-api`·`dev-web` → 샘플 팩 시드(`scripts/seed_domain_pack.py`) → 브라우저에서 로그인 → 온톨로지 게시 → 동기화 → 채팅으로 TRD 7.3 상태 표의 각 행(정상·0건·권한 없음·Neo4j 중단·과다)을 재현하고, 근거 카드·조회 조건·등급 배지·실패 표시가 서로 다른 모양으로 나오는지 화면에서 확인.

## 첫 착수 범위 제안

승인되면 **Phase 0 + TRD v0.2 정정(위 8개)**부터 진행합니다. 결과물: 디렉터리 골격, CI, import-linter 계약, `/healthz`, 문서 이동.

## 진행 기록

| 일자 | Phase | 내용 |
| --- | --- | --- |
| 2026-10-19 | 0 | 문서 이동(`docs/prd`·`docs/trd`·`docs/plan`), TRD v0.2 정정 8건, ADR 0001~0006, 백엔드 골격(uv·`/healthz`·설정·DB 세션), 의존 규칙(import-linter 4계약 + 소스 검사), 프론트 골격(Vite·React·TS — TypeScript는 `openapi-typescript`·`typescript-eslint` 호환을 위해 **5.9.3** 고정), compose·CI. ⚠ pgvector 확장은 `ontobot_db`에 아직 설치되지 않았다(`CREATE EXTENSION vector`는 슈퍼유저 필요 — 사용은 P1) |
| 2026-10-19 | 0 | Docker 제거(Dockerfile·compose), TRD v0.3, ADR-0007, CI 통합 잡을 Windows 러너 + 설치된 PostgreSQL로 전환. Neo4j 네이티브 설치는 Phase 2 착수 시 |
| 2026-10-20 | 0 | 개발 PC 준비: `ontobot` 슈퍼유저 권한(사용자), 테스트 DB 2개·pgvector 확장, Neo4j Community 5.26.32 LTS 개발(7687)·테스트(7688) 인스턴스를 Windows 서비스로 설치, `graph/client.py` 최소판과 접속 스모크 테스트 |
| 2026-10-20 | 1 | **Phase 1 완료.** ORM 28+1표·Alembic 0001/0002(감사 로그 추가 전용 트리거, 기본 역할 5종), 로컬 계정(argon2)·서버 세션(토큰 해시만 저장)·RBAC·감사·RFC 7807·Origin 검사, OIDC(Authorization Code + PKCE, 가짜 IdP 테스트), 온톨로지 정의 모델·임포트/익스포트·버전 비교·게시(advisory lock, 단일 게시 부분 유일 인덱스)·레지스트리·플래너 요약, 사용자 CLI, 샘플 도메인 팩, 프론트(로그인·온톨로지 편집기·OpenAPI 타입 생성 + 계약 테스트). 테스트: 단위 52·계약 2·통합 43. 브라우저 확인: 로그인→draft→임포트→게시→복사 draft→속성 추가→버전 비교 |
| 2026-10-20 | 1.5 | **스파이크 완료**(ADR-0004). 단일 타입 질의가 계획→검증→참조 대조→실행→조회 조건→답변→근거 대조→등급까지 관통. 측정: 구조화 JSON 실패 전 모델 0건, 결함 수정 후 gemma4:e4b 12/12·p50 1.3초로 운영 기본 모델 채택. 발견·수정: 문장 나누기 인용 분리, 데이터 블록 라벨 베끼기·깨진 인용 무기록, 평균 반올림, 코드 속성에 이름(참조 값 대조), 조건 누락(`cannot_answer`). TRD v0.5. 테스트: 단위 90·계약 2·통합 57 |
| 2026-10-20 | 2 | **Phase 2 완료.** PostgreSQL 커넥터(읽기 전용 트랜잭션), 시크릿 참조(env·file, `.env` 폴백), 매핑 검증(게시 버전 + 실제 원천 열), 동기화 실행기(스테이징 → 한 트랜잭션 병합, 행 실패 = 실행 실패, 증분 워터마크), 링크 규칙, 아웃박스 → Neo4j 투영(stub 노드, 묶음 단위 재시도), 대사(건수+키 해시)·재구축, 작업 큐(SKIP LOCKED·백오프·고아 회수)·워커·주기 발행, 속성 인덱스 작업(CONCURRENTLY), API·화면. 발견·수정: 세션 제너레이터가 트랜잭션을 열어 둬 인덱스 작업이 자기 자신을 기다림(→ `async with session_scope()`), Neo4j 장애 시 드라이버 재시도 47초(→ 5초), `env:` 참조가 `.env` 를 못 봄. 브라우저 확인: 소스 등록·접속 시험·매핑(열 자동 대응)·동기화·재구축·대사 일치·원천 변경이 Neo4j 관계까지 반영. 테스트: 단위 91·계약 2·통합 74 |
