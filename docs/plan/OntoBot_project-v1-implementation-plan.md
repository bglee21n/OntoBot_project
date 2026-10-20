# OntoBot v1(P1) 구현 계획 — 초안

- 상태: **Phase 11 완료, Phase 12 대기** · 2026-10-20 · 기준 TRD v0.15, PRD v0.4
- 범위: PRD 8절 v1 행(P1) 전부. P2·P3 는 TRD 17절의 자리만 지킨다.
- 앞선 계획: [MVP 구현 계획](OntoBot_project-implementation-plan.md)(Phase 0~8). Phase 번호는 그대로 이어 붙인다.

## Context

MVP(Phase 0~8)는 조회형 챗봇·근거 대조·워크숍 기본형·평가 게이트·오프라인 설치까지 끝났다. Phase 8 의 완료 기준
(폐쇄망 장비 설치)은 운영 장비가 정해지면 확인한다 — v1 작업과 독립이다.

v1 은 P1 항목이 많아 PRD 9절 리스크("v1 범위 과다")대로 **둘로 나눈다.**

- **v1a — 쓰기와 계산**: 편집 레이어·값 계보 → Action → 채팅 Action → Function. 제품 목표("질의 → 추론 → 실행")의
  나머지 절반이고, 서로 의존한다(Action 은 편집 레이어에 쓰고, Function 은 편집 값을 읽는다).
- **v1b — 넓히기**: 객체·속성 권한·감사 해시 체인 → 객체 탐색·트레이스·피드백 → 문서·하이브리드 검색 →
  영향 분석·REST API → 워크숍 확장형. 서로 거의 독립이라 순서를 바꿀 수 있다.

v1a 끝에서 한 번 안정화·평가 게이트·번들을 돌려 **v1a 만으로도 출시할 수 있게** 한다.

원칙은 MVP 와 같다. 정확 일치·결정론적 검증을 늘리고, LLM 이 하는 일은 늘리지 않는다. 새 LLM 경로(Action 파라미터
채우기)는 MVP 의 플래너처럼 **구조화 출력 → 서버 검증 → 실패면 1회 재계획**을 탄다.

## 착수 전 결정 (TRD v0.12 로 반영)

MVP 계획의 "착수 전에 고쳐 둘 것"과 같은 성격이다. ★ 는 사용자가 정한 항목(2026-10-20)이고, 나머지는 권장안이다.
TRD v0.12 17.1절에 같은 내용이 있다.

| # | 항목 | 권장안 | 이유 |
| --- | --- | --- | --- |
| 1 | 편집 레이어가 **조회 조건**에도 보이는가 | 보인다. `reader` 가 `FROM object o` 대신 활성 편집을 덮은 파생 행(`object` LEFT JOIN 활성 `object_edit` 를 jsonb 로 합친 것)을 쓴다. `resolve`·`run_query`·`existing_values` 셋 다 | 근거 카드는 편집 값인데 필터는 원천 값이면 "위험등급 4 이상" 질문에 편집으로 4가 된 시설이 빠진다 — 조용히 틀린다 |
| 2 | Action·Function 정의는 어디에 사는가 | **온톨로지 문서(`OntologyDocument`)에 `action_types`·`functions` 를 더한다.** draft 에서 편집·게시하고 게시본은 불변 | 버전 관리·게시 전 검사·버전 비교·영향 분석을 그대로 쓴다. 따로 두면 "어느 스키마 버전의 Action 인가"가 어긋난다 |
| 3 | Action 이 바꿀 수 있는 속성 | pk·FK(링크 원천)·제목 속성은 **금지**. 일반 속성만. 링크를 바꾸는 Action 은 v1.5 | FK 편집은 링크·아웃박스·Neo4j 까지 번진다. 편집 레이어와 그래프 투영의 정합성 규칙을 새로 세워야 한다 |
| 4 | ★ Action 부수효과 범위 | **앱 안 알림 + 메일 + SMS**(사용자 결정). 메일은 사내 SMTP 릴레이(표준 라이브러리 `smtplib`), SMS 는 사내 발송 게이트웨이 어댑터(일반 HTTP JSON · 발송 테이블 INSERT 두 가지). 원천 시스템 쓰기(write-back)·그 밖의 외부 호출은 하지 않는다. 발송은 실행 트랜잭션 안에서 `notification_delivery` 에 쓰고 **커밋 뒤 워커가** 보낸다(재시도·상태 기록, `(action_run, 채널, 수신자)` 로 중복 발송 방지). 되돌릴 수 없으므로 롤백은 같은 채널로 정정 발송(`compensation` 문구). 수신자는 역할(사용자 연락처) 또는 대상 객체의 `pii_kind` phone/email 속성. 실행당 수신자 상한, 연락처는 로그·감사에서 마스킹. 개발·테스트는 파일로 떨어뜨리는 가짜 게이트웨이 | 사내 게이트웨이는 운영자가 설정하는 내부 주소다 — "실행 중 외부 네트워크 호출 금지" 규칙의 예외는 외부 LLM 공급자에 더해 **운영자가 설정한 메일·SMS 게이트웨이**뿐이다. 트랜잭션 안에서 보내면 롤백돼도 문자는 나간다 |
| 5 | Action 파라미터 검증 | 식별자 파라미터(객체 참조)는 그 타입에 **실재하는 pk/제목과 정확 일치**, 값 파라미터는 질문 원문에 **그대로 있거나** enum·자료형 변환 통과. 어긋나면 실행 전에 막고 사용자에게 고르게 한다 | PRD 5.3 "대조되지 않는 식별자는 차단". `query/references.py`·`coverage.py` 와 같은 결 |
| 6 | 멱등성 | `action_run.request_id`(클라이언트가 만든 UUID) 유일 인덱스. 같은 ID 재전송은 기존 실행을 돌려준다 | PRD 5.3 |
| 7 | ★ Function 의 형태 | **선언형**: 정해진 연산(산술·비교·조건·집계)의 식 트리 + 입력 파라미터 + 내부에서 쓰는 QueryPlan 조각. 사용자 코드(파이썬)는 받지 않는다 | 폐쇄망 서버에서 임의 코드 실행을 피한다. 식 트리는 컴파일러처럼 값을 전부 바인드할 수 있고, 결과를 `q:N` 인용 대상으로 삼기 쉽다. 표현력이 모자라면 도메인 팩 파이썬 플러그인(배포 시 검토)을 v1.5 에 더한다 |
| 8 | Function 결과 인용 | `[[q:N#value]]` 그대로. `q:N` 의 근거 행에 입력 조건(파라미터 값·내부 조회 조건)을 함께 싣는다 | PRD 5.10 "입력 조건과 함께 인용 대상". 근거 대조 코드(`grounding`)는 바뀌지 않는다 |
| 9 | 속성 단위 권한의 모양 | `property_permission(role, type, property, can_read)` + `row_policy(role, type, filters jsonb)`(QueryPlan `Filter` 문법). 둘 다 **컴파일러가 주입**하고 플래너 요약에서도 뺀다 | TRD 17절. 행 정책을 `Filter` 문법으로 두면 바인드·검증 코드를 다시 쓴다 |
| 10 | 감사 해시 체인 | `audit_log.prev_hash`·`hash`(SHA-256, 직전 행 해시 + 정규화 JSON). 삽입을 advisory lock 으로 줄 세움, 검증은 CLI·매일 작업 | PRD 5.5. 체인이 끊긴 위치를 보고만 하고 고치지 않는다(추가 전용) |
| 11 | 임베딩 모델 | 로컬 Ollama, 후보 `bge-m3`(개발 PC 설치됨)·`nomic-embed-text`. 한국어 골든 문항으로 재서 ADR 로 정한다. 모델은 `config/llm.yaml` 의 새 역할 `embedder` | 코드에 모델 ID 금지 규칙 |
| 12 | 문서 근거 대조 | 문서 인용 `[[d:<doc_id>/<chunk_no>]]` — 문장이 말하는 값이 그 청크 원문에 **그대로 있는지**. 유사도로 근거를 판정하지 않는다(검색에만 쓴다) | "근거 대조에 유사도를 넣지 않는다" 불변식 |
| 13 | ★ 워크숍 음성 입력 | **배포 설정으로 켜고 끈다. 폐쇄망이면 꺼진다**(사용자 결정). 새 설정 `ONTOBOT_CLOSED_NETWORK`(기본 `true`)가 `false` 이고 `ONTOBOT_VOICE_INPUT=true` 일 때만 화면에 마이크 버튼이 나온다. 구현은 브라우저 음성 인식(Web Speech API) — 음성이 브라우저 공급자 서버로 가므로 켤 때 화면에 그 사실을 알린다. 인식 결과는 텍스트 발언과 똑같이 마스킹 저장 | 폐쇄망에서 브라우저 음성 인식은 외부 서버를 부른다. 오프라인 STT 모델을 정하면 폐쇄망에서도 켤 수 있게 어댑터를 더한다(v1.5) |
| 14 | 공개 REST API 인증 | API 키(해시 저장, 스코프 `query`·`objects:read`·`actions:request`), 키마다 서비스 계정 = 역할. 기존 `/api/v1` 과 같은 라우터, 공개 경로는 OpenAPI 태그로 구분하고 호환성 보장 | PRD 5.11. 세션 쿠키 경로와 권한 판정은 `security/rbac.py` 하나 |

---

## v1a — 쓰기와 계산

### Phase 9 — 편집 레이어·값 계보 (TRD 6.3, 11절)

- 마이그레이션 0006: `object_edit`·`sync_conflict`(TRD 6.3 그대로 + 활성 편집 부분 유일 `(type, pk, property) WHERE cleared_at IS NULL`).
- `objects/edits.py`(쓰기: 편집·해제, `base_source_value` 기록), `objects/reader.py` 오버레이(결정 1): `resolve`·`run_query`·
  `existing_values` 가 같은 파생 행을 쓴다. `ObjectRow` 에 `edited: dict[property, EditInfo]`(편집자·시각·원천 값).
- `sync/runner.on_source_change` 실제 구현: 편집된 속성의 새 원천 값 ≠ `base_source_value` → `sync_conflict(open)`.
- `objects/lineage.py`(값 계보): 값마다 `원천(data_source·mapping·sync_run, synced_at)` 또는 `편집(action_run·사용자·시각)`.
- 근거 대조: 근거 카드 값 = 보이는 값(편집 값). 데이터 블록·근거 대조가 같은 `ObjectRow` 를 보므로 grounding 은 그대로.
- API: `GET /objects/{type}/{pk}`(편집 표시·계보 포함), `GET /sync-conflicts`, `POST …/acknowledge`, `DELETE /object-edits/{id}`(해제, 감사).
  편집 **생성**은 Phase 10 의 Action 만 한다(직접 편집 API 는 두지 않는다 — 모든 쓰기가 Action 감사를 탄다).
- 화면: 근거 카드에 "편집됨 · 원천 값 ○○ (동기화 시각)", 관리 탭 '편집 충돌'.
- **완료 기준**: 통합 테스트로 편집 → 질의 필터가 편집 값으로 걸림 → 원천 변경 동기화 → 충돌 open · 보이는 값은 편집 값 →
  해제 → 원천 값으로 돌아감. 골든 게이트 회귀 없음(편집 없는 데이터에서 결과 동일).

### Phase 10 — Action Type 정의·실행기 (PRD 5.3)

- 온톨로지 문서 확장(결정 2): `ActionTypeDef{api_name, display_name, target_type, parameters[], preconditions[](Filter 문법),
  changes[](속성 ← 파라미터·상수), notify[]{channel(app/email/sms), to(역할 | 대상 객체 연락처 속성), template},
  approval{roles, allow_self}, compensation(정정 문구 템플릿)}`.
  게시 전 검사: 대상 속성 실재·pk/FK 금지(결정 3)·파라미터 자료형과 속성 자료형 일치.
- 마이그레이션 0007: `action_run(request_id 유일, action_type, ontology_version_id, params, status(previewed/approved/rejected/
  executed/failed/rolled_back), requested_by, approved_by, preview jsonb, error)`, `notification`(앱 안 알림),
  `notification_outbox(action_run_id, channel(email/sms), recipient(원값 — 로그·화면·감사는 마스킹), body, status, attempts, sent_at, error)`,
  `app_user.email`·`phone`(PII).
- `notify/`(신설): `channels/smtp.py`·`channels/sms_http.py`·`channels/sms_table.py`·`channels/fake.py`, 설정은 `config/notify.yaml`
  (+ 비밀값은 `env:`/`file:` 참조), 워커 처리기 `notify_deliver`. 발송 문구는 템플릿 + 파라미터 바인드(LLM 이 쓰지 않는다).
  `/readyz` 에 게이트웨이를 넣지 않는다(외부 LLM 과 같은 이유 — 설정 여부만).
- `actions/registry.py`(게시 버전에서 읽기) · `actions/runner.py`(미리보기 = 사전 조건 평가 + 바뀔 값 diff, 실행 = 한 트랜잭션에서
  재검증 → `object_edit` → 알림 → 감사) · `actions/approvals.py`(승인 규칙, 권한 판정은 `security/rbac.py` 에 함수 추가).
- 롤백: 실행 단위로 그 실행이 만든 편집을 해제. 이미 나간 메일·SMS·알림은 되돌리지 않고 같은 수신자에게 `compensation` 정정
  발송(아직 안 나간 발송은 취소). 미리보기 화면에 "메일 n건·SMS n건이 나가며 되돌릴 수 없음"을 보인다.
- 워크숍: `action_candidate` 요소를 Action Type draft 로 옮기는 버튼(이름·대상 타입·근거 발언을 채워 줌, 나머지는 모델러가).
- API·화면: 온톨로지 편집기에 Action 탭, 실행 목록·승인함·알림.
- **완료 기준**: 미리보기 → 승인 → 실행 → 편집 반영 → 롤백 왕복, 같은 `request_id` 두 번 = 한 번 반영(발송도 한 번), 본인 승인
  금지 규칙, 사전 조건 불만족이면 실행 거부, 실행 트랜잭션 실패면 아무것도 발송되지 않음, 게이트웨이 장애면 재시도 후
  `failed` 로 드러남(실행 자체는 성공으로 남고 발송 실패가 목록에 보임). 모든 단계 감사 기록. 개발 PC 에서는 가짜 게이트웨이와
  로컬 SMTP 시험 서버로 확인 — 실제 사내 게이트웨이 연동은 운영 장비에서.

### Phase 11 — 채팅에서 Action (파라미터 채우기·검증·승인)

- 플래너 출력에 `kind: "action"` + `ActionProposal{action_type, params}` 추가. **`QueryPlan` 필드 순서 규칙**과 같이 예시 키 순서를
  스키마 순서에 맞춘다. 대상 객체 지정은 맥락(`context_refs`)이나 이름 → 결정 5 의 대조.
- `actions/params.py`: 결정 5 검증. 실패 오류는 한꺼번에 붙여 **1회 재계획**, 그래도 안 되면 후보 목록을 보여 주고 사용자가 고르게.
- 파이프라인: Action 은 실행하지 않고 미리보기까지만 — SSE 새 이벤트 `action_preview`(`chat/stream.py` 정본), 승인은 Phase 10 API.
  답변 등급 축과 섞지 않는다(Action 응답은 등급 없음, 상태 `action_preview`).
- 평가: 골든 문항에 `expected_action`(타입·파라미터) — **파라미터 정확도**(PRD 5.6 P1) 지표, 식별자 지어내기 0건을 불변식으로.
  `scripts/spike_plan.py` 로 gemma4:e4b 재측정 → ADR-0004 보강, `eval-record` 로 카세트 갱신.
- **완료 기준**: 골든 Action 문항 파라미터 정확도 기준 통과(가안 90%), 존재하지 않는 대상 지정 문항에서 실행 차단 100%, 기존 조회
  문항 회귀 없음.

### Phase 12 — Function (PRD 5.10)

- 온톨로지 문서 확장: `FunctionDef{api_name, params[], body}` — body 는 결정 7 의 식 트리(`query`(QueryPlan 조각, 파라미터 바인드)·
  `agg`·산술·비교·`if`). 게시 버전에 묶여 버전 관리된다.
- `functions/registry.py`·`functions/evaluator.py`(식 트리 → 컴파일러의 `CompiledQuery` 여러 개 + 파이썬 산술, 값은 전부 바인드,
  호출자 권한으로 조회).
- QueryPlan: 최상위에 `function: {name, args}` 를 대안으로(같은 `kind: query` 안에서 `plan` 대신). 검증기가 인자 자료형 변환.
- 인용: 결과를 `q:N` 근거로, 근거 행에 입력 조건 문장(결정 8). 조회 조건 문장(`query/conditions.py`)에 Function 이름·인자 표시.
- 평가: Function 골든 문항(샘플 팩에 예: "지역별 고위험 시설 비율"). spike 재측정·카세트.
- **완료 기준**: Function 문항 정답·인용률 100%·권한(읽을 수 없는 타입을 내부에서 조회하면 `forbidden`).

### Phase 13 — v1a 안정화

- 평가 게이트 전체(실제 모델) + 카세트, 부하 재측정(Action 미리보기 포함), 번들·설치 스크립트 업그레이드 경로(0006·0007 마이그레이션).
- TRD v0.13·ADR 정리, PRD 7절 v1 지표(Action 성공률·승인 거부율·미확인 충돌) 대시보드 수치 노출.
- **완료 기준**: 게이트 통과, MVP 설치본에서 `-Mode Upgrade` 로 v1a 로 올라가고 롤백 왕복.

---

## v1b — 넓히기 (순서 바꿀 수 있음)

### Phase 14 — 객체·속성 단위 권한, 감사 로그 변조 방지 (PRD 5.5 P1)

- 결정 9: `property_permission`·`row_policy`, 컴파일러 주입(행 정책 = 강제 AND 필터), 플래너 요약·데이터 블록·근거 카드·
  Action 미리보기·Function 내부 조회 모두 같은 판정. 권한 지문(`security/fingerprint.py`)에 속성·행 정책 해시 포함.
- 결정 10: 해시 체인, `security.cli verify-audit`, 매일 검증 작업(끊김은 경보 로그 + 관리 화면).
- 평가: 권한 침투 문항을 속성·행 단위로 확대(PRD 5.6 P1) — 숨긴 속성 값·정책 밖 객체의 값이 답에 나오면 위반.
- **완료 기준**: 침투 문항 위반 0, 체인 검증 통과·변조 시뮬레이션(테스트 DB 에서 트리거 우회) 검출.

### Phase 15 — 객체 탐색·트레이스 뷰·피드백 (PRD 5.9, 5.6 P1)

- `graph/traversal.py` 이웃 조회 API(권한 필터 후), 프론트 `features/explorer`(검색·상세·편집 이력·계보·인스턴스 그래프).
  근거 카드 → 객체 상세 이동.
- `features/traces`(질의 → 계획 → SQL/Cypher → 결과 수 → 근거 판정 → 등급, 관리 화면의 트레이스 상세를 독립 화면으로).
- `feedback(message_id, user_id, rating, comment)` + 관리 화면 목록, 싫어요 → 골든 문항 후보로 옮기기(기대 답변은 사람이).
- **완료 기준**: 브라우저 확인 — 답 → 근거 카드 → 객체 상세 → 이웃 → 다른 객체, 피드백 → 골든 후보.

### Phase 16 — 문서 ↔ 객체 연결, 하이브리드 검색 (PRD 5.1·5.2 P1)

- `document`·`document_chunk`(pgvector), 문서 등록(파일 업로드 — 텍스트·PDF 텍스트 층·HWP 는 범위 확인), 청크·임베딩 작업,
  문서 ↔ 객체 링크(본문에서 객체 제목·pk **정확 일치** 후보 → 모델러 확정).
- 결정 11 측정 ADR, `query/retrievers/vector.py`, QueryPlan `search` 단계(문서 청크 후보 → 결정 12 인용).
- **완료 기준**: 문서 질문 골든 문항(답 없음 포함) 게이트, 문서 인용도 지어내면 제거되는지 단위 테스트.

### Phase 17 — 변경 영향 분석·REST API 공개 (PRD 5.1·5.11 P1)

- `ontology/impact.py`: draft ↔ 게시본 비교 결과마다 참조처(Action·Function·골든 문항의 `required_conditions`·워크숍 요소·
  행 정책) 목록, 게시 전 검사 화면에 표시(막지는 않음, 깨지는 참조는 막음).
- 결정 14: API 키·스코프·서비스 계정, 공개 OpenAPI 문서, 질의(비 SSE JSON 응답)·객체 조회·Action 요청.
- **완료 기준**: 속성 삭제 draft 에서 영향 목록이 그 속성을 쓰는 Action·Function·문항을 모두 보여 줌, API 키로 질의·Action 요청 → 승인 흐름.

### Phase 18 — 인터뷰 워크숍 확장형 (PRD 5.8 P1)

- 분할 화면(대화 | 실시간 미리보기: 요소 그래프·Action 정의서), 모바일 레이아웃. 음성 입력은 결정 13 — 기능 플래그 API
  (`GET /config/features`)로 화면이 켜짐 여부를 받고, 폐쇄망 설정이면 버튼 자체를 그리지 않는다(단위 테스트로 고정).
- 샘플 데이터 질의 시연: 세션 전용 격리 스키마(또는 격리 DB)에 draft 온톨로지 + 샘플 행, 운영 파이프라인을 그 스키마로 돌림
  (평가의 주입 지점처럼 **저장소 주입 하나만** — 다른 주입 지점 금지 규칙과 맞춘다).
- `workshop/merge.py`: 여러 세션 요소를 이름·값 **정확 일치** 후보로만 제안, 병합은 모델러.
- 미리보기 갱신 시간 측정(가안 5초) → PRD 수치 제안.
- **완료 기준**: 인터뷰 2개 병합 → draft → 샘플 데이터로 질문 시연.

### Phase 19 — v1 안정화·출시

- 전체 게이트·부하·번들, TRD v1.0, PRD 7절 v1 지표 수치 제안.

---

## 검증 (전 Phase 공통)

MVP 계획과 같다(`task.py lint`·`test-unit`·`test-int`·`test-contract`·`eval-replay`). 추가로:

- 모델·프롬프트·스키마를 바꾸는 Phase(11·12·16)는 `eval-record` 로 실제 모델 게이트를 통과시키고 카세트를 커밋한다.
- 마이그레이션이 생기는 Phase 는 설치본 업그레이드 경로(`install.ps1 -Mode Upgrade`)를 개발 PC 에서 한 번 돈다.
- 편집 레이어 이후에는 모든 읽기 경로 테스트를 "편집 있음/없음" 두 벌로 둔다(결정 1 이 깨지면 조용히 틀린다).

## 결정 기록

| 일자 | 항목 | 결정 |
| --- | --- | --- |
| 2026-10-20 | 결정 4 Action 부수효과 | 사용자: **SMS·메일 발송까지**(앱 안 알림 포함). 위 표대로 사내 게이트웨이·아웃박스·정정 발송 |
| 2026-10-20 | 결정 13 음성 입력 | 사용자: **폐쇄망에서는 비활성화**. 배포 설정으로 켜고 끈다 |
| 2026-10-20 | 결정 7 Function 형태 | 별도 의견 없음 → 권장안(선언형 식 트리) 유지. 바꾸려면 Phase 12 착수 전에 |
| — | PRD 7절 수치 | 미정. 제안: 동시 8명·P95 25초(ADR-0011), v1 파라미터 정확도 90%(가안) |

## 진행 기록

| 일자 | Phase | 내용 |
| --- | --- | --- |
| 2026-10-20 | — | v1 계획 작성, 결정 4·13 사용자 확정, TRD v0.12(17.1절 v1 착수 결정) |
| 2026-10-20 | 9 | **Phase 9 완료.** 마이그레이션 0006(`object_edit`·`sync_conflict`, 활성 편집·살아 있는 충돌 부분 유일). `objects/reader.py` 가 모든 객체 읽기(`resolve`·`run_query`·`existing_values`)를 활성 편집을 덮은 파생 행에서 하고 `ObjectRow.edited` 를 낸다. `objects/edits.py`(편집·재편집·해제, 충돌 판정 `record_source_changes` 를 `sync.runner.on_source_change` 에 연결, 충돌 목록·확인), `objects/lineage.py`(편집 또는 데이터 소스→매핑→동기화 실행). API: 근거 카드에 편집·충돌·값 출처, `GET /sync-conflicts`·`POST …/acknowledge`·`DELETE /object-edits/{id}`(모델러·관리자, 읽을 수 있는 타입만), 감사 이벤트 2종. 화면: 근거 카드 "편집됨 · 원천 값 ○○"·원천 변경 경고·값 출처, 데이터 소스 화면 '편집 충돌' 패널(확인·편집 해제). 결정: JSON null 과 속성 없음은 같은 NULL 로 비교, 편집이 있는 타입은 속성 인덱스가 덮인 값에 안 쓰임(ADR-0003 재검토 조건), Action 변경 금지에 제목 속성 추가(그래프·FK 이름 재작성과 어긋남). 검증: 통합 테스트 10건 추가(편집이 필터·집계·참조 대조에 보임, 재편집, 원천 변경 → 충돌 open·값 갱신·확인 후 재open·원천 복귀 시 cleared, 다른 속성 변경은 충돌 아님, API 권한·감사), 평가 게이트 재생 통과(카세트 놓침 0). 브라우저(개발 DB): 편집 F-0004 위험등급 1→4 + 원천 2 로 변경 → "위험등급 4 이상 변전소"에 포함·① → 근거 카드에 편집·충돌·동기화 실행 #3, 충돌 패널 표시 — 확인 뒤 편집 해제·원천 복구. 테스트: 단위 189·계약 10·통합 116 |
| 2026-10-20 | 10 | **Phase 10 완료(실제 사내 게이트웨이 연동은 미검증).** 정의: `ActionTypeDef`(대상·요청 역할·파라미터·사전 조건·바꿀 속성·알림·승인·정정 문구)를 온톨로지 문서에 — 표 `action_type`(마이그레이션 0007), draft 편집 API·버전 비교·내보내기, 게시 전 검사(pk·제목·FK 변경, 자료형·enum, 연락처 `pii_kind`, 자리표시자, 정정 문구). 실행: `actions/rules.py`(파라미터 변환·사전 조건·바뀔 값·템플릿 치환 — 순수)·`actions/runner.py`(미리보기 → 승인 = 실행 한 트랜잭션·재검증 실패는 `failed` 기록 → 거부·거둠 → 롤백: 편집 해제·대기 발송 취소·정정 발송), `request_id` 멱등, 권한 판정 4종은 `security/rbac.py`. 발송: `notify/`(설정 `config/notify.yaml` — 채널 꺼짐이 기본, `smtp`·`sms_http`·`sms_table`·`fake`), 아웃박스 `notification_delivery` 를 워커 `notify_deliver` 가 커밋 뒤에(임대·재시도·상한 `failed`, 최소 한 번), 주기 발행이 재시도분을 넣는다(ADR-0012). 앱 알림 `notification`·`app_user.email/phone`(관리 화면에서 입력, 응답은 마스킹). 설치: `shared\notify.yaml` 견본 복사·`ONTOBOT_NOTIFY_CONFIG_PATH`. 화면: 온톨로지 편집기 Action 섹션(구조화 폼, 워크숍 Action 후보로 시작), Action 화면(요청·미리보기·승인 대기·내 요청·전체 기록·롤백·발송 상태), 메뉴의 읽지 않은 알림 수, 관리 화면 연락처. 검증: 단위 31건(정의·검사·규칙·채널·권한)·통합 12건(정상 흐름·발송, 멱등, 권한, 본인 실행, 사전 조건 막힘, 채널 미설정 막힘, 파라미터 오류 묶음, 미리보기 뒤 상태 변화 → `failed`, 롤백, 게이트웨이 실패 재시도 → `failed`, API, 온톨로지 API), 평가 게이트 재생 통과(카세트 놓침 0). 브라우저(개발 DB): Action 하나를 붙인 버전 4 게시 → 요청자 미리보기(F-0004 위험등급 1→4, 사전 조건·앱 알림 3명) → 승인자 승인·실행(근거 카드에 편집됨·요청자) → 알림 배지 → 롤백(원천 값 1 복귀, 정정 알림) — 임시 계정 2개는 정지. 미검증: 실제 SMTP·SMS 게이트웨이, 대량 발송. 테스트: 단위 220·계약 10·통합 128 |
| 2026-10-20 | 11 | **Phase 11 완료.** 플래너 `kind: action` + `ActionProposal{action_type, target, params[{name, value}]}`(요약에 요청할 수 있는 Action 이 있을 때만 프롬프트에 Action 규칙·가상 예시 — 없으면 측정한 프롬프트 그대로). `actions/params.py`(결정 5): 대상을 가리키는 말은 질문에 그대로 있거나 맥락 pk(실재하는 다른 객체도 지어낸 것), pk → 제목 정확 일치(`objects.reader.pks_with_value`), 같은 제목 여럿이면 후보, 값 파라미터는 질문에 그대로(수는 값 같음), enum·bool 은 자료형 변환만 — 오류 한꺼번에 1회 재계획, 남으면 `action_preview`·`ready=false`·확인 사항·후보. 파이프라인은 저장하지 않고, 채팅 API 가 대조를 통과한 제안으로 미리보기 기록(`origin=chat`, `request_id=chat-<질문 id>`)을 만든다. SSE `action_preview`(등급 없음), `message.status`·`golden_item.expected_action`(마이그레이션 0008). 화면: 채팅의 Action 카드(기록을 지금 상태로 다시 읽어 승인 대기·거두기, 대조 실패면 서버가 확인한 값만 채운 요청 폼 + 후보). 평가: `expected_action`, 지표 `action_param_accuracy`(가안 0.9, Action 문항 없는 골든셋은 optional)·불변식 `action_fabrications`=0, 샘플 팩 Action 2개·골든 7문항 + 권한 1문항. 결정: 바뀐 것은 제안 대조뿐 — 미리보기·승인·실행은 Phase 10 그대로. 검증: 실제 모델 게이트(record) 통과 — 정답률 1.0·Action 파라미터 정확도 7/7·지어내기 0·p95 10.4초, 재생 통과(놓침 0), spike 24/25(Action 7/7, 틀린 `u01` 은 Phase 7 과 같음) → ADR-0004 보강. 브라우저(개발 DB, 실제 모델): "F-0004 시설의 위험등급을 3으로 바꿔줘" → 미리보기(1→3, 승인 대기) → 승인자 승인·실행(근거 카드 편집됨) → 롤백(1 복귀), "제주 변전소 99호 …" → 기록 없음·확인 사항·요청 폼(대상 비움, 값 2). 임시 계정 2개는 정지. 곁들여 고친 것: 워크숍 통합 테스트의 감사 이벤트 순서(ORDER BY 없음 — 간헐 실패). 테스트: 단위 236·계약 12·통합 141 |
