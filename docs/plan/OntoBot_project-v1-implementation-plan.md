# OntoBot v1(P1) 구현 계획 — 초안

- 상태: **Phase 14 완료 · Phase 15 진행 중(a 피드백·b 객체 탐색 완료 — 다음 트레이스 화면) · Phase 13 업그레이드 왕복 남음** · 2026-10-31 · 기준 TRD v0.24, PRD v0.4
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

### Phase G1 — 스키마 그래프 편집기 (Ontology Manager 그래프 뷰, 제안서 1단계)

[그래프 편집 제안서](OntoBot_project-graph-editing-proposal.md)의 1단계다. Phase 12 와 독립적이어서 먼저 진행한다.
기존 draft API(`api/ontology.py`) 위에 **새 보기**를 더하는 일이고, 목록 화면(`VersionDetailPage`)은 그대로 둔다(탭 전환: 목록 | 그래프).
인스턴스 그래프(2단계)는 Phase 14(그룹·쓰기 권한)·15(탐색) 위에 따로 계획한다.

- **G1-1 의존성·골격**
  - `frontend` 에 `@xyflow/react`·`elkjs` 를 추가한다. 버전은 정확히 고정하고, npm 번들이라 폐쇄망에서도 외부 호출이 없다. 라이선스는 MIT·EPL-2.0 이므로 번들 제3자 고지에 추가한다
  - `features/ontology/graph/` 를 새로 둔다
    - `SchemaGraph.tsx`(캔버스)
    - `toGraph.ts`(VersionDetail → 노드·엣지, 순수 함수)
    - `layout.ts`(elk 자동 배치 + 저장 좌표 우선)
    - `SidePanel.tsx`(선택한 요소 편집)
  - 편집 폼은 기존 `forms.tsx`(`ObjectTypeForm`·`PropertyForm`·`LinkTypeForm`)와 `ActionTypes.tsx` 의 `ActionForm` 을 그대로 쓴다. `VersionDetailPage` 의 `ObjectTypeCard` 는 옆 패널에서도 쓸 수 있게 따로 꺼낸다(동작 변경 없음)
  - 화면 문구는 `i18n/ko/index.ts` 의 `ko.ontology.graph.*` 에 둔다
- **G1-2 보기**
  - 노드: 타입(표시 이름·`api_name`·pk·제목·속성 수, PII 속성이 있으면 표시, Action 칩)
  - 엣지: 링크 타입(cardinality 화살표, `fk_property` 실선·`join_mapping` 점선, 라벨은 표시 이름)
  - 게시 전 검사 `issues` 는 `path` 앞부분으로 해당 노드·엣지에 배지를 붙인다. 문서 전체에 걸린 문제는 상단에 둔다
  - 변경 겹치기: 기존 `useDiff(v.id, base)` 결과로 추가·변경·삭제를 색으로 표시한다. 삭제된 요소는 base 문서에서 가져와 유령 노드로 그린다. base 는 기본이 `parent_id` 이고, 기존 `DiffPanel` 과 같은 선택 상자를 쓴다
  - 깊이 포커스: 노드를 고르고 깊이 1~3 을 정하면 그 범위 밖을 흐리게 하거나 숨긴다. 노드별 `+`/`−` 로 펼치고 접는다. 서버 호출 없이 문서 안에서 너비 우선으로 계산한다(`toGraph.ts` 의 `neighborhood(doc, start, depth)`, 단위 테스트 대상)
- **G1-3 편집 조작**(draft 일 때만, 게시·보관본은 읽기 전용)
  - 빈 곳 더블클릭 → 옆 패널 `ObjectTypeForm` → `POST …/object-types`
  - 노드 A 핸들 → B 로 끌기 → `LinkTypeForm` 이 from=A·to=B 로 미리 채워지고, FK 속성 후보는 A 의 속성으로 좁혀진다 → `POST …/link-types`
  - 노드·엣지 클릭 → 옆 패널 편집(`PATCH`). 게시본(parent)에 이미 있는 요소면 `api_name` 을 읽기 전용으로 둔다. 서버는 원래 이름을 바꾸지 않는다
  - 삭제(Delete 키·메뉴) → G1-4 영향 확인 창 → 고른 요소만 차례로 `DELETE`. 연쇄 삭제는 자동으로 하지 않는다
  - 편집이 성공하면 기존 `useVersionMutation` 이 버전 쿼리를 무효화해 그래프가 다시 그려진다
- **G1-4 영향 분석 API**(Phase 17 `ontology/impact.py` 의 첫 조각)
  - `ontology/impact.py`(순수 함수) `references(doc, kind, name) -> list[Ref]`
    - 문서 안의 참조: 링크 양 끝, FK 속성, Action 대상·바꿀 속성·사전 조건·연락처 속성, pk·제목 속성
  - DB 참조
    - `mapping.object_type`(동기화 매핑)
    - 그 타입의 게시 데이터 존재 여부(객체가 있는지만 — 개수·값은 싣지 않는다, `objects/` 안의 함수로)
    - 골든 문항의 타입·속성 이름(**정확 일치**)
  - `GET /ontology/versions/{id}/references?kind=object_type|property|link_type&name=…[&type=…]` → `{in_document: [...], mappings: [{id, name}], golden_items: [{id}], has_objects: bool}`
    - 이름과 개수만 낸다. 매핑 설정·골든 본문은 싣지 않는다
    - 권한은 `Reader`(modeler·admin), 기존 의존성 그대로
- **G1-5 폐기(deprecated)**
  - 정의 모델: `ObjectTypeDef`·`LinkTypeDef` 에 `deprecated: bool = False` 를 더한다. 마이그레이션 0009 로 `object_type`·`link_type` 에 열을 추가하고 기본값은 false
  - 가져오기·내보내기·`diff`·`to_document` 에도 반영한다
  - 게시본에 이미 있는 타입·링크를 삭제하려 하면 확인 창이 "폐기"를 기본으로 권한다. 실제 삭제는 draft 에서 새로 만든 요소에만 바로 허용하고, 그 밖에는 경고를 한 번 더 띄운다(서버는 막지 않는다 — 게시 검사가 깨진 참조를 막는다)
  - 효과
    - `ontology/summary.py` 의 `schema_summary` 가 폐기된 타입과 그 타입에 닿는 링크를 싣지 않는다. 플래너가 새 질문에 쓰지 않게 하려는 것이다
    - 근거 카드(`GET /objects/{type}/{pk}`)·기존 대화 맥락은 계속 읽는다
    - `check_document`: 폐기되지 않은 Action 이 폐기된 타입을 대상으로 삼으면 문제로 보고한다
  - 측정: 폐기 요소가 없으면 요약이 바이트 단위로 같아서 카세트 키가 그대로다(단위 테스트로 고정). 그러므로 `spike_plan` 재측정은 하지 않는다. 폐기를 쓰는 골든 문항을 더할 때만 `eval-record` 를 한다
- **G1-6 배치 저장**
  - 마이그레이션 0009 에 함께 넣는다: `ontology_layout(version_id FK cascade, element_kind, element_name, x, y, updated_by, updated_at)`, 유일 키는 `(version_id, element_kind, element_name)`
  - 정의 문서 밖이므로 `content_hash`·게시 불변성과 무관하다. 게시본의 배치도 고칠 수 있다(정의가 아니라 화면 상태다)
  - `GET`·`PUT /ontology/versions/{id}/layout`(`PUT` 은 `Modeler`, 통째 교체, 항목 상한 2000). 감사 기록은 남기지 않는다(정의 변경이 아니다 — 결정 기록에 남긴다)
  - `POST /versions`(parent 에서 draft 만들기)가 부모 배치를 복사한다(`versioning.create_draft` 안, 같은 트랜잭션)
  - 화면: 저장된 좌표 → 없으면 elk 자동 배치. 끌기가 끝나면 0.5초 디바운스 뒤 저장. "자동 정렬" 단추는 저장 좌표를 지우고 다시 배치한다
- **G1-7 draft 소유자 표시**
  - `VersionSummary`·`VersionDetail` 에 `created_by_name` 을 더한다(이미 있는 `ontology_version.created_by`)
  - 내가 만들지 않은 draft 를 열면 상단에 "○○○ 님의 draft 입니다 — 함께 편집하면 서로의 변경을 덮을 수 있습니다" 경고를 띄운다. 편집은 막지 않는다. 게시는 기존 advisory lock 이 줄을 세운다
- **API 변경 후**: `task.py openapi` 로 프론트 타입을 다시 만든다. 계약 스냅샷이 갱신된다
- **매뉴얼**: 사용자·운영자 매뉴얼의 온톨로지 편집 장에 그래프 보기를 추가한다(화면 캡처는 `docs/manual/tools/capture_app.py`)
- **완료 기준**
  - 단위 테스트
    - `toGraph`·`neighborhood`(깊이 1~3, 순환)·issue 경로 → 노드 매핑
    - `impact.references`(링크 끝·FK·Action 참조)
    - 폐기 타입이 요약에서 빠짐 + 폐기 없으면 요약이 같음
    - `deprecated` 왕복(가져오기·내보내기·diff)
  - 통합 테스트
    - `references` 가 매핑·골든·객체 존재를 이름·불리언으로만 냄
    - layout `PUT`·`GET` 과 draft 생성 시 복사
    - layout 변경 후 `content_hash` 불변
    - 권한: admin 은 읽기만, user 는 404/403
  - 브라우저(개발 DB): 그래프에서 타입 추가 → 끌어서 링크 → 속성 편집 → 이슈 배지가 사라지는지 → 게시본 대비 색 → 깊이 포커스 → 삭제 영향 창 → 폐기 → 게시 → 채팅 질문에서 폐기 타입이 쓰이지 않는지
  - `lint`·`test-contract`·`eval-replay` 통과(카세트 놓침 0)

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
  - **15b 완료(2026-10-31)**: `GET /objects/search`(pk·제목 부분 일치, 행 정책 주입), `GET /objects/{t}/{pk}/neighbors`
    (1홉만 — 읽을 수 있는 끝 타입의 링크만 Cypher 로 만들고 stub 제외, 제목은 reader, 행 정책 밖은 개수에도 없음,
    Neo4j 불가면 503 — PG link 대체 없음, 호출마다 감사 `objects.explored`), `GET /objects/{t}/{pk}/edits`(해제 포함,
    숨긴 속성 제외). 확장 한도는 `app_setting`(0016) 관리자 설정(깊이 3·노드 200 기본, 서버도 노드 수로 자름).
    화면 `/explore` — 깊이 N 은 화면이 1홉을 너비 우선으로 되풀이, 접기는 확장 출처 기록으로. 근거 카드 → 탐색기는
    라우터 상태로 넘긴다(URL 에 pk 없음). 그래프에서의 편집(Action)은 제안서 2단계 편집으로 남긴다.
- `features/traces`(질의 → 계획 → SQL/Cypher → 결과 수 → 근거 판정 → 등급, 관리 화면의 트레이스 상세를 독립 화면으로).
- `feedback(message_id, user_id, rating, comment)` + 관리 화면 목록, 싫어요 → 골든 문항 후보로 옮기기(기대 답변은 사람이).
  - **15a 완료(2026-10-31)**: `feedback`(0015, 사용자·답마다 하나, 다시 누르면 고침, 의견 마스킹 저장) — 자기 대화의
    답에만(`PUT /messages/{id}/feedback`, 남의 답은 404). 목록·옮기기·넘기기는 평가 화면(`bot_builder`·`modeler`,
    `GET /feedback`·`POST /feedback/{id}/promote|dismiss`, 감사 `eval.feedback_handled`). 옮긴 문항은
    `source=feedback` — 팩 적재가 건드리지 않는다. 질문만 미리 채우고 기대값은 사람이 적는다.
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
| 2026-10-20 | 배포 방식 | 사용자: **화면으로 배포** → 이어서 **Docker 전환**(Windows Server + WSL2 + Docker Engine, 개발·CI 테스트도 컨테이너, Ollama 컨테이너 + GPU, 별도 배포 콘솔·브라우저 업로드 + 반입 폴더·번들 서명). ADR-0013·0014, ADR-0007·0010 대체 |
| 2026-10-21 | 개발 콘솔 | 사용자: 개발자가 화면으로 소스·검사·평가·빌드·번들을 — **개발 PC 전용(localhost)**, 소스는 **PR·CI 까지**, 기능은 검사·테스트·평가·카세트·이미지·번들·서명·개발 환경 모두. ADR-0015 |
| 2026-10-30 | 그래프 편집 | 사용자: 스키마 그래프(Phase G1)를 먼저 한다. 인스턴스 그래프는 Phase 14 를 **그룹 기반 데이터 권한**(역할 = 기능, 그룹 = 읽기·생성·편집·삭제·연결, 관리 화면 + SSO 선택 연동, 소유 그룹 필수)으로 넓힌 뒤 Phase 15 위에 둔다. 원천 객체 삭제 = 숨김 묘비 + 충돌 기록, draft 는 소유자 표시 + 경고, 확장 한도는 관리자 설정. 배치(layout)는 정의가 아니므로 감사 기록 없이 둔다. 상세는 [제안서](OntoBot_project-graph-editing-proposal.md) |
| 2026-10-30 | PRD 7절 수치 | 사용자: **관리자 설정에서 관리**(`ops_target`, 비우면 제안값). 게이트 기준(gate.yaml)과 분리 |
| 2026-10-30 | Phase 14 진행 | 사용자: 그룹 → 속성·행 → 감사 체인 순. 속성 권한 기본 = 타입을 읽으면 모두 보임(지정한 것만 숨김). 기존 역할 권한은 같은 이름 그룹으로 자동 이전. 행 정책은 읽기·쓰기 모두 |
| 2026-10-30 | 그래프 편집 | 사용자: "그래프에서 모든 편집" — 스키마 그래프 옆 패널에서 Action·Function 편집 |
| — | PRD 7절 수치 | 미정. 제안: 동시 8명·P95 25초(ADR-0011), v1 파라미터 정확도 90%(가안) |

## 진행 기록

| 일자 | Phase | 내용 |
| --- | --- | --- |
| 2026-10-20 | — | v1 계획 작성, 결정 4·13 사용자 확정, TRD v0.12(17.1절 v1 착수 결정) |
| 2026-10-20 | 9 | **Phase 9 완료.** 마이그레이션 0006(`object_edit`·`sync_conflict`, 활성 편집·살아 있는 충돌 부분 유일). `objects/reader.py` 가 모든 객체 읽기(`resolve`·`run_query`·`existing_values`)를 활성 편집을 덮은 파생 행에서 하고 `ObjectRow.edited` 를 낸다. `objects/edits.py`(편집·재편집·해제, 충돌 판정 `record_source_changes` 를 `sync.runner.on_source_change` 에 연결, 충돌 목록·확인), `objects/lineage.py`(편집 또는 데이터 소스→매핑→동기화 실행). API: 근거 카드에 편집·충돌·값 출처, `GET /sync-conflicts`·`POST …/acknowledge`·`DELETE /object-edits/{id}`(모델러·관리자, 읽을 수 있는 타입만), 감사 이벤트 2종. 화면: 근거 카드 "편집됨 · 원천 값 ○○"·원천 변경 경고·값 출처, 데이터 소스 화면 '편집 충돌' 패널(확인·편집 해제). 결정: JSON null 과 속성 없음은 같은 NULL 로 비교, 편집이 있는 타입은 속성 인덱스가 덮인 값에 안 쓰임(ADR-0003 재검토 조건), Action 변경 금지에 제목 속성 추가(그래프·FK 이름 재작성과 어긋남). 검증: 통합 테스트 10건 추가(편집이 필터·집계·참조 대조에 보임, 재편집, 원천 변경 → 충돌 open·값 갱신·확인 후 재open·원천 복귀 시 cleared, 다른 속성 변경은 충돌 아님, API 권한·감사), 평가 게이트 재생 통과(카세트 놓침 0). 브라우저(개발 DB): 편집 F-0004 위험등급 1→4 + 원천 2 로 변경 → "위험등급 4 이상 변전소"에 포함·① → 근거 카드에 편집·충돌·동기화 실행 #3, 충돌 패널 표시 — 확인 뒤 편집 해제·원천 복구. 테스트: 단위 189·계약 10·통합 116 |
| 2026-10-20 | 10 | **Phase 10 완료(실제 사내 게이트웨이 연동은 미검증).** 정의: `ActionTypeDef`(대상·요청 역할·파라미터·사전 조건·바꿀 속성·알림·승인·정정 문구)를 온톨로지 문서에 — 표 `action_type`(마이그레이션 0007), draft 편집 API·버전 비교·내보내기, 게시 전 검사(pk·제목·FK 변경, 자료형·enum, 연락처 `pii_kind`, 자리표시자, 정정 문구). 실행: `actions/rules.py`(파라미터 변환·사전 조건·바뀔 값·템플릿 치환 — 순수)·`actions/runner.py`(미리보기 → 승인 = 실행 한 트랜잭션·재검증 실패는 `failed` 기록 → 거부·거둠 → 롤백: 편집 해제·대기 발송 취소·정정 발송), `request_id` 멱등, 권한 판정 4종은 `security/rbac.py`. 발송: `notify/`(설정 `config/notify.yaml` — 채널 꺼짐이 기본, `smtp`·`sms_http`·`sms_table`·`fake`), 아웃박스 `notification_delivery` 를 워커 `notify_deliver` 가 커밋 뒤에(임대·재시도·상한 `failed`, 최소 한 번), 주기 발행이 재시도분을 넣는다(ADR-0012). 앱 알림 `notification`·`app_user.email/phone`(관리 화면에서 입력, 응답은 마스킹). 설치: `shared\notify.yaml` 견본 복사·`ONTOBOT_NOTIFY_CONFIG_PATH`. 화면: 온톨로지 편집기 Action 섹션(구조화 폼, 워크숍 Action 후보로 시작), Action 화면(요청·미리보기·승인 대기·내 요청·전체 기록·롤백·발송 상태), 메뉴의 읽지 않은 알림 수, 관리 화면 연락처. 검증: 단위 31건(정의·검사·규칙·채널·권한)·통합 12건(정상 흐름·발송, 멱등, 권한, 본인 실행, 사전 조건 막힘, 채널 미설정 막힘, 파라미터 오류 묶음, 미리보기 뒤 상태 변화 → `failed`, 롤백, 게이트웨이 실패 재시도 → `failed`, API, 온톨로지 API), 평가 게이트 재생 통과(카세트 놓침 0). 브라우저(개발 DB): Action 하나를 붙인 버전 4 게시 → 요청자 미리보기(F-0004 위험등급 1→4, 사전 조건·앱 알림 3명) → 승인자 승인·실행(근거 카드에 편집됨·요청자) → 알림 배지 → 롤백(원천 값 1 복귀, 정정 알림) — 임시 계정 2개는 정지. 미검증: 실제 SMTP·SMS 게이트웨이, 대량 발송. 테스트: 단위 220·계약 10·통합 128 |
| 2026-10-20 | 11 | **Phase 11 완료.** 플래너 `kind: action` + `ActionProposal{action_type, target, params[{name, value}]}`(요약에 요청할 수 있는 Action 이 있을 때만 프롬프트에 Action 규칙·가상 예시 — 없으면 측정한 프롬프트 그대로). `actions/params.py`(결정 5): 대상을 가리키는 말은 질문에 그대로 있거나 맥락 pk(실재하는 다른 객체도 지어낸 것), pk → 제목 정확 일치(`objects.reader.pks_with_value`), 같은 제목 여럿이면 후보, 값 파라미터는 질문에 그대로(수는 값 같음), enum·bool 은 자료형 변환만 — 오류 한꺼번에 1회 재계획, 남으면 `action_preview`·`ready=false`·확인 사항·후보. 파이프라인은 저장하지 않고, 채팅 API 가 대조를 통과한 제안으로 미리보기 기록(`origin=chat`, `request_id=chat-<질문 id>`)을 만든다. SSE `action_preview`(등급 없음), `message.status`·`golden_item.expected_action`(마이그레이션 0008). 화면: 채팅의 Action 카드(기록을 지금 상태로 다시 읽어 승인 대기·거두기, 대조 실패면 서버가 확인한 값만 채운 요청 폼 + 후보). 평가: `expected_action`, 지표 `action_param_accuracy`(가안 0.9, Action 문항 없는 골든셋은 optional)·불변식 `action_fabrications`=0, 샘플 팩 Action 2개·골든 7문항 + 권한 1문항. 결정: 바뀐 것은 제안 대조뿐 — 미리보기·승인·실행은 Phase 10 그대로. 검증: 실제 모델 게이트(record) 통과 — 정답률 1.0·Action 파라미터 정확도 7/7·지어내기 0·p95 10.4초, 재생 통과(놓침 0), spike 24/25(Action 7/7, 틀린 `u01` 은 Phase 7 과 같음) → ADR-0004 보강. 브라우저(개발 DB, 실제 모델): "F-0004 시설의 위험등급을 3으로 바꿔줘" → 미리보기(1→3, 승인 대기) → 승인자 승인·실행(근거 카드 편집됨) → 롤백(1 복귀), "제주 변전소 99호 …" → 기록 없음·확인 사항·요청 폼(대상 비움, 값 2). 임시 계정 2개는 정지. 곁들여 고친 것: 워크숍 통합 테스트의 감사 이벤트 순서(ORDER BY 없음 — 간헐 실패). 테스트: 단위 236·계약 12·통합 141 |
| 2026-10-20 | 배포 | **Docker(WSL2) 전환 + 배포 콘솔(Phase 번호 밖 — 사용자 요청).** 앱 이미지(api·worker·migrate)·배포 콘솔 이미지·compose(운영·gpu·deployer·test·dev)·`images.lock`(제3자 다이제스트), 절차의 정본 `ontobotctl`(verify·preflight·install·upgrade·rollback·backup·status, `--json`·`--non-interactive`, 잠금, 실패 시 앱 재기동), `scripts/build_bundle.py`·`bundle_sign.py`(Ed25519 — 서버는 openssl 로 확인), `install-host.ps1`(WSL·Docker·GPU·예약 작업·portproxy·방화벽 — **대상 OS 미검증**), 배포 콘솔 `deployer/`(관리자 로그인·업로드·반입·사전 점검·버전 재입력 업그레이드·RESTORE 롤백·백업·자기 갱신·작업 기록·감사 `deploy.*`), 매일 백업 타이머, 관리 화면 버전 카드(`GET /admin/system`). 통합 테스트·평가·CI 를 compose.test 로. 발견·수정: 컨테이너 PG 의 glibc 정렬이 한글 순서를 바꿔 카세트를 놓쳐 builtin `C.UTF-8` 로 고정, 서명 파일 CRLF, DB 복원 시 `COMMENT ON EXTENSION` 실패로 앱이 멈춘 채 남던 문제. 검증: 단위 236 + 콘솔 19·계약 12·통합 141(컨테이너)·평가 재생 통과(놓침 0), 개발 PC(Docker Desktop WSL2)에서 전체 번들 서명 설치 → 채팅 18·① → 콘솔에서 반입 → 사전 점검(디스크 부족으로 막힘 확인 후 통과) → 업그레이드(13초, 백업) → 콘솔 자기 갱신 → DB 복원 롤백. 미검증: Windows Server 2022 의 WSL2(0단계 점검)·재부팅 자동 기동·LAN 포트 전달, 화면의 파일 선택 단추(업로드 API 는 같은 순서의 조각 클라이언트로 218MB 왕복·잘못된 오프셋 거부·가져오기·사전 점검까지 확인) |
| 2026-10-21 | 개발 콘솔 | **개발 콘솔(Phase 번호 밖 — 사용자 요청).** `devconsole/`(127.0.0.1:8200, Host·Origin·헤더 확인, 명령은 `task.py` 의 `TASKS` 를 읽어 부름): 대시보드(검사별 마지막 결과·그때 커밋, dirty·OpenAPI·카세트 경고, 전체 검사·출시 준비), 소스(상태·diff·커밋·브랜치·태그·푸시), GitHub(`gh` — PR·CI), 검사·테스트, 평가·카세트(게이트 표·틀린 문항·놓침), 빌드·번들(앱만/전체·zip·서명·검증·반출·릴리스 노트), 개발 환경(dev-api·worker·dev-web 서비스, 컨테이너 작업, Ollama 모델, 디스크, `.env` 키), 작업 기록. 배포 콘솔의 작업 실행기를 `opsjobs/` 로 빼 공유, 화면 부품 `components/ops/`. CI 에 devconsole 잡. 발견·수정: 작업 출력의 ANSI 색 코드로 pytest 요약이 안 잡힘(실행기에서 제거), 시작 직후 개발 서버를 끄면 uv → 런처 사이 자식이 남음(자손 목록을 떠서 하나씩 종료). 검증: lint·devconsole 19·배포 콘솔 19, 브라우저에서 대시보드 → 단위 테스트 작업(274 통과 요약) → diff → GitHub 안내(`gh` 미설치) → dev-api 켜기·로그·끄기(트리 정리) → 이미지 빌드(app·deployer, 배포 콘솔 이미지에서 `opsjobs` import·`/healthz` 확인) → 앱 번들(zip·서명, 218MB) → 서명 확인(파일 14개) → 릴리스 노트 → 반출(해시 대조 0 불일치). 변조한 복사본·다른 공개키는 검증이 거부. 미검증: 실제 커밋·푸시·PR(시험 브랜치 안 만듦), 평가 작업, 깨끗한 트리의 번들을 서버(ontobotctl)가 받는지(이번 번들은 -dirty) |
| 2026-10-30 | G1 | **Phase G1 완료(스키마 그래프 편집기).** 정의 문서 `deprecated`(객체·링크 타입 — 거짓이면 정규 표현·내보내기에서 빠져 기존 해시 유지), 플래너 요약·검증·참조 대조·FK 이름 재작성은 `active()` 문서, 폐기 대상 Action 은 게시 전 검사. 마이그레이션 0009(`deprecated` 열, `ontology_layout`). API: `GET /references`(`ontology/impact.py` + `objects.reader.has_objects/has_links` — 이름·개수·존재만), `GET/PUT /layout`(정의 밖, draft 생성 때 부모 배치 복사), 버전에 `created_by_name`. 화면: 버전 화면 목록|그래프 탭(`@xyflow/react` 12.12.0·`elkjs` 0.12.0 자동 배치), 노드·엣지(변경 색·이슈 배지·폐기·PII·Action 칩), 빈 곳 두 번 눌러 타입·핸들 끌어 링크, 옆 패널(타입 정보·속성 표·링크 양식), 포커스 깊이 1~3·+n 펼치기·− 접기·범위 밖 숨기기, 삭제 영향 창(함께 지울 링크·Action 선택, 게시본 요소는 폐기 권장), 끌기 위치 저장·자동 정렬, 남의 draft 경고. 결정: 서버는 삭제를 막지 않음(게시 검사가 막음), 배치는 감사 없음. 검증: 단위 244(폐기 해시·왕복·활성 문서·요약 불변, 영향 참조), 통합 4건 추가(폐기 왕복·영향은 이름만·배치 해시 불변·복사·권한) — 네이티브 테스트 DB 로 137 통과(실패 1 = 테스트 Neo4j 꺼짐, Docker Desktop 미기동으로 compose.test 미사용), 브라우저(Playwright, 개발 DB 버전 5 draft): 타입 생성·이슈 배지·끌어 링크(FK 고르기)·포커스/펼치기/접기·삭제 영향 창·폐기. 미검증: compose.test 컨테이너 통합 테스트·`eval-replay`, 폐기 타입이 든 버전을 게시한 뒤 실제 채팅(단위 테스트로만), 프론트 단위 테스트(테스트 도구 없음) |
| 2026-10-30 | 12 | **Phase 12 완료(Function).** 정의: `FunctionDef`(인자 — 자료형·enum·필수·기본값, 내부 조회 1~4개 — QueryPlan 모양의 집계·조건 값 자리에 `{param}`, 식 트리 — 산술·비교·논리·`if`·`round`, 결과 이름·자료형·소수 자리)를 온톨로지 문서에 — 표 `function_type`(마이그레이션 0010), draft 편집 API·버전 비교·영향 분석(조회 타입·링크·속성), 게시 전 검사(연산자 표를 계획 검증과 공유, 파라미터 ↔ 속성 자료형, 그룹 열 일치, 칸 이름 충돌, 식 자료형). 실행: `functions/binding.py`(인자 변환·기본값·내부 계획 `validate` — 호출자 권한, 읽을 수 없는 타입이면 `forbidden`)·`evaluator.py`(그룹 키 맞춤, `Decimal`, 값 없음 전파, 서버 반올림, 근거 행 = 그룹 열·내부 조회 값·결과·입력 조건, 조건 문장)·`runner.py`(파이프라인 실행기 공유, 내부 실패 = 전체 실패). 플래너: 요약에 Function 이 있을 때만 `FUNCTION_RULES`·`FunctionPlannerOutput`(`kind: function`) — 없으면 스키마·프롬프트 그대로. 파이프라인: 내부 조회 참조 값 대조, 인자 ↔ 질문 두 방향 대조(질문 숫자 → 인자, 인자 값 → 질문). 화면: 버전 화면 Function 섹션(요약·JSON 편집), 그래프 삭제 창에서 함께 지우기. 샘플 팩 Function 2개·골든 4문항. 발견·수정: 지역을 못 읽는 역할에서 플래너가 다른 Function 을 기본값을 채워 부르며 지역 조건을 뺐다(① 로 나감) — 프롬프트로는 안 바뀌어 '인자 값은 질문에 그대로' 결정론적 대조를 더함(ADR-0004 보강). 검증: 실제 모델 게이트(record) 통과 — 정답률 1.0·인용률 1.0·답할 수 없는 문항 ①② 0·p95 8.8초, 재생 통과(놓침 0), spike 28/28(Function 3/3). 브라우저(평가 DB, 실제 모델, 임시 계정·권한은 확인 뒤 정지·삭제): Function 목록·JSON 편집 → 저장 → 게시 전 검사에 `functions.high_risk_ratio` 문제 → 잘못된 JSON 오류 → 복구, 채팅 "시설 유형별 고위험 시설 비율" → 30/40/70%·①, Facility 만 읽는 역할의 "대구 지역의 고위험 시설 비율은?" → 실패(질문 해석 실패), 지역 권한을 더하면 57.1%·①. 미검증: 그래프 화면에서 Function 이슈 배지(경로가 노드에 붙지 않음 — 게시 전 검사 목록에만), 근거 카드는 집계와 같이 '카드 없음' 안내(입력 조건은 조건 문장·데이터 블록에). 테스트: 단위 271(Function 27)·계약 12·통합 152(Function 7, compose.test). `test_claim_skips_locked_jobs_and_retries_with_backoff` 가 전체 실행에서 간헐 실패한다 — 변경 전 코드에서도 같았고 단독 실행·재실행은 통과(Phase 13 에서 원인 확인) |
| 2026-10-30 | 13 | **Phase 13 진행(업그레이드·롤백 왕복 미실시).** Phase 12 마무리: 그래프에서 Function 문제 배지(첫 내부 조회 대상 타입), 근거 카드가 `q:N`(집계·Function) 행의 대조 값을 보여 줌(SSE `citations[].values`). 작업 큐 기한을 DB 시계로만(앱 시계로 넣은 `run_after` 가 컨테이너 DB 시계와 어긋나 방금 넣은 작업을 못 집던 `test_claim_skips_locked_jobs_and_retries_with_backoff` 간헐 실패의 원인). v1 운영 지표(PRD 7절): `actions/metrics.py`·`GET /admin/metrics`(Action 실행 성공률·승인 거부율 — 요청자가 거둔 것은 따로, 미확인 편집 충돌), 관리 화면 카드. 부하 재측정(`load_test.py --with-actions`, ADR-0011 보강): 1·4·8·16명 232건 실패 0, 8명 p95 18.8초, Action 미리보기 p95 1.1~10.2초 — 첫 측정은 통합 테스트가 테스트 Neo4j 를 덮어 탐색 질문이 `empty` 였고 재구축 뒤 다시 잼(측정 함정 기록). 매뉴얼 5·23·26·28·33장 집계 카드 설명. 검증: lint·단위 271·계약 12·통합 154·평가 재생 통과(놓침 0, 프롬프트 변경 없음 — 실제 모델 녹화는 Phase 12 것). 남은 것: 설치본 업그레이드(0009·0010)·롤백 왕복(서명 키·시험 설치 필요) |
| 2026-10-30 | 13 | **사용자 결정 반영.** 성공 지표 목표는 관리자 설정에서(PRD 7절 수치를 문서에 고정하지 않음): `ops_target`(마이그레이션 0011)·`evaluation/targets.py`·`GET·PUT /admin/targets`, 관리 화면 '성공 지표 목표' 표(목표·측정·판정·근거, 비우면 제안값). 측정 출처를 바로잡음 — 처음엔 평가의 `failure_rate`(실패가 정답인 문항 포함, 21%)·재생 실행의 p95(0)를 썼다가, 실패율·P95·주간 활성은 트레이스(장애성 실패만)로, 품질은 재생이 아닌 평가 실행으로. 게이트(`gate.yaml`)는 그대로. 스키마 그래프 옆 패널에서 Action·Function 만들기·고치기·지우기(노드 ƒ 칩) — "그래프에서 모든 편집". 남은 파일(매뉴얼 Word·PDF·`capture_app.py`) 커밋, 브랜치 푸시. 검증: lint·단위 271·계약 12·통합 155, 브라우저(평가 DB, 임시 관리자 계정은 정지): 목표 표(측정 정답률 100%·P95 23.5초·주간 활성 2) → 조회 실패율 목표 5% 저장 → 판정 충족 → 제안값 복귀, 그래프에서 Facility 패널 Action 2·Function 2 → Function 추가(대상 Facility 미리 채움) → 저장 → 삭제. 업그레이드 왕복은 Docker Desktop VM `/opt` 공간(16GB) 부족으로 실패 — 위치를 바꿔 다시 할 것 |
| 2026-10-30 | 14a | **Phase 14a 완료(데이터 그룹).** 마이그레이션 0012(`data_group`·`group_member`·`group_type_permission`{읽기·생성·편집·삭제}·`group_link_permission`{연결}, 역할 읽기 권한 → 같은 이름 그룹으로 이전 — 읽던 타입은 편집도, `type_permission` 제거). `rbac.readable_types`(시그니처 그대로)·`editable_types`·`readable_types_for_group`, Action 요청에 대상 편집 권한(채팅 요약·`can_request`·`runner.preview`). 그룹 관리 API·화면(관리 → 데이터 그룹, 사용자 표·계정 추가의 그룹), SSO 그룹 클레임 연동(`oidc_groups_claim`, 기본 꺼짐, `external_id` 정확 일치), CLI `grant-type --group [--edit]`·`create-user --group`, 시드 `--grant-edit`, `ontobotctl` 설치 인자, 매뉴얼 촬영 환경. 검증: lint·단위 271·계약 12·통합 156(그룹 API·감사·SSO 동기화·편집 권한 없는 요청 거부), 평가 재생 통과(놓침 0 — 평가는 팩 역할이라 영향 없음), 평가 DB 에서 0011→0012 올림(역할 user·admin → 그룹, 구성원·읽기·편집)·내림·다시 올림, 브라우저(평가 DB, 임시 관리자 정지): 데이터 그룹 탭에 이전된 권한 표시 → 생성 켜면 읽기 자동 → 저장 → API 확인 → 원복. 미검증: 실제 SSO 클레임 로그인(단위·통합 함수로만), 매뉴얼 사진 |
| 2026-10-31 | 14b | **Phase 14b 완료(속성 숨김·행 정책).** 마이그레이션 0013(`group_property_hide`·`group_row_policy`{read/write, Filter 조건 AND}·`golden_set.restrictions`). `rbac.merge`(합집합 — 숨김은 모든 읽기 그룹이 숨길 때만, 행은 그룹별 OR·정책 없는 그룹이 있으면 제한 없음)·`rbac.access`, 보이는 문서 `without_properties`(숨긴 속성을 쓰는 Action·Function 제외, pk·제목 숨김 불가), `check_scopes`(잘못된 정책은 닫힌 쪽)·`ValidatedPlan.scopes`·컴파일러 주입(대상·탐색 끝·집계·Function 내부), 근거 카드(숨김·정책 밖 404), Action 쓰기 정책(미리보기·승인 재확인), 관리 화면 타입 행 '속성·행'. 평가: 팩 `restrictions` + 침투 3문항(r01 정책 밖 변전소 → 0건, r02 숨긴 설비 용량 → 실패, r03 정책 안에서만 집계 7). 검증: 단위 276(합집합·보이는 문서·정책 컴파일·닫힌 실패)·계약 12·통합 161(정책이 목록·집계를 줄임, 숨긴 속성은 계획 불가·데이터 블록에 없음, 깨진 정책 = 0건, 카드 숨김·404, 쓰기 정책 거부, 저장 검사·두 그룹 합집합), 실제 모델 게이트(record) 통과 — 정답률 1.0·권한 위반 0·p95 8.9초, 재생 통과(놓침 0), 브라우저(평가 DB): 그룹 카드에서 숨김·정책 저장 → API 반영, pk 숨김 400. 한계: 채팅 Action 제안 후보·참조 값 대조는 정책을 보지 않음(TRD 17.7) |
| 2026-10-31 | 14c | **Phase 14c 완료(감사 해시 체인) → Phase 14 완료.** 마이그레이션 0014(`audit_log.seq`·`hash`, `audit_chain_head`, 해시 함수 `audit_log_hash`, `BEFORE INSERT` 트리거 — advisory lock 으로 줄 세움, 기존 행 id 순서로 채움). `security/audit_chain.py`(끊김 6종: unchained·hash_mismatch·link_broken·gap·head_missing·tail_missing, 보고만), 매일 작업 `audit_verify`(끊기면 error 로그), CLI `verify-audit`(종료 코드 1), `GET /admin/audit-chain`·감사 로그 탭 '해시 체인 검증'. 검증: 평가 DB 에 0014 적용 — 기존 160행 체인 정상 → 새 행 161 정상, 통합 테스트 변조 시뮬레이션(트리거를 끄고 내용 변경 → hash_mismatch, 중간 삭제 → link_broken, 꼬리 삭제 → tail_missing, 트리거 없이 삽입 → unchained — 모두 트랜잭션 안에서 되돌림), lint·단위 276·계약 12·통합 162. 완료 기준(침투 위반 0 — 14b 게이트, 체인 검증·변조 검출) 충족. 미검증: 대량 감사 행에서 검증 시간, 브라우저에서 검증 단추(API 로만 확인) |
