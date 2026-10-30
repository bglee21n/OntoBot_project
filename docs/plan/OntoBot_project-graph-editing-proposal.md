# 제안: 그래프에서 온톨로지 추가·편집·삭제·생성 (Palantir Foundry 참조)

## Context
지금 온톨로지 편집은 목록·폼 화면(`frontend/src/features/ontology/VersionDetailPage.tsx`, `forms.tsx`)뿐이다.
타입 사이 관계가 한눈에 보이지 않고, 객체 인스턴스를 그래프로 보거나 고칠 방법도 없다. Foundry 의
**Ontology Manager 그래프 뷰**(스키마)와 **Vertex / Object Explorer 그래프**(인스턴스)를 참조해 두 단계로 제안한다.
이번 산출물은 제안서다. 구현 계획은 승인 뒤에 따로 쓴다.

### 확정한 결정 (사용자 답변, 2026-10-30)
| 항목 | 결정 |
|---|---|
| 범위 | 스키마 그래프(1단계) → 인스턴스 그래프(2단계), 이번 산출물은 제안서 |
| 역할·그룹 | **역할 = 화면·기능, 그룹 = 데이터 권한**(읽기·생성·편집·삭제·연결) |
| 그룹 소속 | 관리 화면에서 지정. OIDC `groups` 클레임 연동은 설정으로 켜고 끈다 |
| 원천 객체 삭제 | **숨김(묘비) 허용**. 원천이 바뀌어도 숨김을 유지하고 동기화 충돌로 기록 → 담당자가 확인·해제 |
| 소유 그룹 | 생성자가 자기 소속 그룹 중에서 고른다. 소속이 하나면 자동, **필수** |
| draft 협업 | 소유자 표시 + 다른 사람의 draft 를 편집하면 경고. 게시는 기존 advisory lock |
| 확장 한도 | 최대 깊이·노드 수를 **관리자 설정 화면**에서 관리(DB 설정, 감사 기록). 기본값은 깊이 3·노드 200 을 제안 |
| 순서 | 1단계 스키마 그래프 먼저(Phase 12 와 별개) → Phase 14 를 그룹·쓰기 권한으로 확장 → Phase 15 위에 2단계 |

현재 자산(재사용)
- draft 편집 API 전부: `api/ontology.py` — 타입·속성·링크·Action CRUD, `diff?base=`, `issues`, `publish`
- 불변식: draft 만 수정, `api_name` 불변, 게시 시 `check_document` 가 깨진 참조를 막음(`ontology/versioning.py`, `models.py`)
- 인스턴스: 편집 레이어 `objects/edits.py`(속성 오버레이, Action 만 씀), `actions/runner.py`(미리보기 → 승인 = 실행 → 롤백)
- 없는 것: 그래프 라이브러리, 이웃 조회 API(`graph/traversal.py`, Phase 15 예정), 객체·링크 생성·삭제(TRD: 링크 변경은 v1.5)

---

## 1단계 — 스키마 그래프 편집기 (Ontology Manager 그래프 뷰)
백엔드 변경은 거의 없다. 기존 draft API 위의 새 보기다.

| Foundry | OntoBot 대응 |
|---|---|
| Object type = 노드 | `ObjectTypeDef` 노드. 제목·pk·속성 수·PII 표시 |
| Link type = 엣지 | `LinkTypeDef` 엣지. cardinality(one/many) 화살표, 출처(FK/조인) 선 모양 |
| Branch / Proposal | draft = 브랜치, `diff?base=게시본` = 제안 검토 |
| Status (experimental/active/deprecated) | 새 개념 **deprecated**(아래) |

**조작**
- **생성**: 빈 곳 더블클릭 → `ObjectTypeForm`(옆 패널). 노드 A → B 로 끌기 → `LinkTypeForm`(from·to 미리 채움, FK 속성은 A 의 속성 중에서 고름)
- **편집**: 노드·엣지 클릭 → 옆 패널에 기존 `ObjectTypeCard`·`PropertyForm`·`LinkTypeForm` 그대로. `api_name` 은 게시본에 있던 요소면 읽기 전용
- **삭제**: 확인 창에 **영향 목록**(이 타입을 쓰는 링크·Action·동기화 매핑·골든 문항)을 먼저 보여 준다. 지금은 삭제가 참조 검사 없이 통과하고 게시 때 막히므로, 편집 시점에 미리 알려 주는 것이 핵심이다. 함께 지울지 고르게 한다(연쇄 삭제는 사용자가 고른 것만)
- **변경 보기 겹치기**: diff 결과로 노드·엣지에 색을 입힌다(추가 초록·변경 노랑·삭제 점선). `issues` 는 해당 노드 배지로 보여 준다. 그래프 위에서 바로 게시
- **Action 타입**: 대상 타입 노드에 칩으로 붙인다(클릭하면 기존 `ActionForm`)

**설계 결정(제안)**
1. 라이브러리: `@xyflow/react`(React Flow, MIT) + `elkjs` 자동 배치. npm 번들이라 폐쇄망 문제가 없다(CDN 호출 없음)
2. 노드 위치는 **정의 문서 밖**에 둔다(`content_hash` 와 게시 불변성을 건드리지 않게). `ontology_layout(version_id, element, x, y)` 표를 두고, draft 를 만들 때 부모 버전의 배치를 복사한다. 처음에는 자동 배치 + 사용자가 옮긴 것만 저장
3. 영향 분석: Phase 17 `ontology/impact.py` 의 일부를 앞당긴다. 우선 `GET /versions/{id}/elements/{kind}/{name}/references`(문서 안 참조 + 동기화 매핑·골든 참조)
4. **삭제 대신 폐기(deprecated)**: 게시본에 이미 있던 타입을 지우면 `object` 표 데이터·채팅 맥락·감사 기록이 고아가 된다. Foundry 처럼 `status: deprecated` 로 두면 플래너 요약에서는 빠지지만 읽기는 유지한다. 실제 삭제는 draft 에서 새로 만든 요소에만 허용한다. 플래너 요약이 바뀌므로 `spike_plan.py` 재측정과 TRD 개정이 필요하다
5. 권한: 편집은 modeler, 보기는 modeler·admin(기존 `rbac.py` 그대로)

---

## 2단계 — 인스턴스 그래프 편집 (Vertex / Object Explorer)
**전제**: Phase 15 의 `graph/traversal.py` 이웃 조회 API와 `features/explorer`

**보기**: 객체에서 시작해 이웃을 펼친다(1홉씩, 링크 타입으로 거름, 노드 수 상한). Neo4j 에는 pk·title·stub 만 있다는 규칙을 지킨다. 속성 값은 `objects.reader` 에서 권한을 거쳐 읽는다. Neo4j 를 쓸 수 없으면 PG `link` 로 대체하지 않고 "그래프 불가"를 표시한다(기존 규칙과 같다).

**편집은 전부 Action 을 거친다**(Foundry 도 Ontology 쓰기는 Action 으로만 한다. 기존 규칙 "편집은 Action 만"을 유지)
- 노드 우클릭 → 이 객체에 쓸 수 있는 Action 목록 → 기존 미리보기 → 승인 = 실행 → 롤백
- Foundry Action 규칙 종류에 맞춰 `ActionTypeDef.changes` 를 확장한다:

| 규칙 | 의미 | 저장 |
|---|---|---|
| `modify`(기존) | 속성 값 변경 | `object_edit` 오버레이 |
| `create_object` | 새 객체 | 편집 레이어 "생성 객체"(origin=edit) |
| `delete_object` | 객체 삭제 | 편집 레이어 묘비(소프트 삭제, 롤백 가능) |
| `add_link` / `remove_link` | 링크 연결·해제 | 새 `link_edit` 표 |

- 링크 편집은 **새 링크 출처 `manual`** 에만 허용한다. FK·조인 링크는 원천 데이터에서 파생되므로 건드리지 않는다. 이렇게 하면 "Action 은 FK 속성을 바꾸지 못한다"는 게시 검사를 유지할 수 있다
- 그래프 조작 ↔ Action: 노드 A → B 끌기 = 그 링크 타입의 `add_link` Action 미리보기, 빈 곳 = `create_object` Action 폼

**지켜야 할 불변식(구현 시 위험 지점)**
- `objects/reader.py` 의 파생 행(`_OBJECTS`)이 생성 객체·묘비까지 덮어 읽어야 한다. 그러지 않으면 카드와 필터·집계가 조용히 어긋난다
- 투영: `link_edit`·생성·삭제도 같은 트랜잭션에서 `graph_outbox` 에 넣는다. 재구축(`graph_rebuild.py`)도 편집 레이어를 포함해야 한다
- 동기화 충돌: 원천에 같은 pk 의 객체가 나타나거나 묘비 객체가 원천에서 바뀌면 `record_source_changes` 가 충돌로 남긴다
- 승인 트랜잭션 안에서 재검증, 감사는 같은 트랜잭션, 알림은 아웃박스(기존 Action 규칙 그대로)
- 채팅 Action(미리보기까지만)·`action_fabrications` = 0 불변식은 바꾸지 않는다. 생성·삭제 Action 을 채팅에서 제안하게 하려면 별도 측정이 필요하다

---

## 깊이에 따른 확장·축소 (보강 4 — 두 그래프 공통)
초안에는 2단계에 "1홉씩 펼친다, 노드 수 상한"만 있었다. 이를 두 그래프 공통 기능으로 명시한다.

**조작**
- **노드 확장**: 노드의 `+` 또는 더블클릭으로 1홉을 펼친다. 링크 타입·방향(나가는/들어오는/양쪽)을 골라 펼칠 수 있고, 노드의 "+N" 배지는 아직 펼치지 않은 이웃 수다
- **노드 축소**: `−` 를 누르면 **그 노드를 통해서만 들어온** 하위 노드를 접는다. 다른 경로로도 연결된 노드는 남긴다. 화면이 "누가 펼쳐서 들어왔는가"(확장 출처)를 기억해 계산한다
- **깊이 지정**: 도구막대에서 깊이 슬라이더(1~3)를 쓴다. 선택한 노드에서 N홉까지 한 번에 펼치거나, N홉보다 먼 노드를 접는다. 기본 깊이는 1 이다. 최대 깊이와 노드 수 상한은 관리자 설정 화면에서 정한다
- **모두 접기 / 선택 노드만 보기(포커스)**, 확장 단계 되돌리기(뒤로)
- **상한**: 화면 노드 수 상한(관리자 설정, 기본 200)을 둔다. 서버도 `neighbors` 의 `limit` 을 같은 설정으로 자른다(화면만 믿지 않는다). 넘으면 그 홉은 펼치지 않는다. 대신 "이웃 N개 — 목록으로 보기"를 띄워 목록에서 골라 넣게 한다

**1단계(스키마 그래프)**: 타입 수가 적으면 전체를 보여 주고, 많아지면 선택한 타입 기준 깊이 N 포커스 모드를 쓴다. 정의 문서 전체가 이미 화면에 있으므로 서버 호출은 없다.

**2단계(인스턴스 그래프)**
- 서버 API 는 **1홉만** 제공한다: `GET /objects/{type}/{pk}/neighbors?link_type=&direction=&limit=&cursor=` — 결과는 이웃 목록 + 링크 타입별 남은 개수다
- 깊이 N 은 화면이 1홉 호출을 너비 우선으로 반복해서 만든다. 서버에서 가변 길이 경로(`*1..N`)를 쓰지 않는 이유는 다음과 같다
  - 경로 **중간 노드**까지 권한을 검사하지 않으면, 읽을 수 없는 객체를 지나는 연결이 드러난다
  - 응답 크기와 부하가 깊이에 따라 폭증한다
  - 홉마다 같은 권한 경로(`readable_types`·그룹 권한·행 정책)를 다시 지난다
- "+N" 개수도 읽을 수 있는 이웃만 센다(보강 1의 3번)
- 확장 상태(펼친 노드·깊이)는 URL 에 pk 를 넣지 않는다(개인정보 규칙). 화면 상태로만 두고, 저장이 필요하면 서버의 "저장된 보기"로 둔다(선택 사항)

## 데이터 접근 권한 검토 (보강)
초안은 권한을 "modeler 편집, 기존 rbac 그대로" 한 줄로만 다뤘다. 이 방식으로는 인스턴스 그래프에서 데이터가 샌다.
현재 코드의 원칙은 두 겹이다. 하나는 **역할 관문**(`require_role`)이고, 다른 하나는 **타입 읽기 권한**(`rbac.readable_types` = `type_permission.can_read`)이다.
읽을 수 없는 타입은 403 이 아니라 **404 "없는 것과 같다"**로 답한다(`api/objects.py` 의 `card`, `acknowledge_conflict`).
그래프는 이 두 겹을 **홉마다, 노드마다** 지켜야 한다.

**1단계(스키마 그래프)** — 다루는 것이 정의(메타데이터)라 위험은 낮다. 다만 다음을 지킨다.
- 보기: `Reader`(modeler·admin), 편집·배치 저장·게시: `Modeler`. `api/ontology.py` 의 기존 의존성을 그대로 쓴다
- 영향 분석 `references` 는 **개수와 이름만** 돌려준다. 골든 문항 본문·기대값·동기화 매핑 설정(원천 테이블·컬럼, 접속 정보)은 담지 않는다. 매핑 상세는 기존 `api/sources.py` 권한(modeler·admin)으로만 본다
- 노드에 객체 수 같은 **데이터 통계를 싣지 않는다**. 싣는다면 그 타입의 `readable_types` 를 가진 사람에게만 보인다

**2단계(인스턴스 그래프)** — 실제 데이터라 다음을 필수 규칙으로 둔다.
1. **역할만으로는 데이터를 못 본다.** modeler·admin 이라도 `type_permission` 이 없으면 그 타입의 노드는 보이지 않는다. 역할 관문과 `readable_types` 를 **둘 다** 통과해야 한다(Action 쪽 주석과 같은 원칙)
2. **이웃 조회에 읽기 가능 타입을 먼저 건다.** `traversal.py` 는 Cypher 를 만들기 전에 `readable_types` 로 끝점 라벨을 거른다. 라벨은 바인드할 수 없으므로 `TYPE_NAME` 정규식으로 조립 직전에 다시 확인한다. 응답을 받은 뒤에 거르지 않는다
3. **존재도 새지 않게 한다.** 읽을 수 없는 이웃은 노드·stub·엣지·"N개 더" 개수 어디에도 나오지 않는다. 시작 객체를 읽을 수 없으면 404 다
4. 속성 값과 제목은 `objects.reader` 로만 읽는다. Neo4j 의 `title` 을 화면 제목으로 쓰지 않는다(PII 제목은 null 이다). 표시는 `display()`·PII 마스킹 규칙을 따른다
5. 속성 단위 권한은 아직 없다. 그래서 Phase 14(`property_permission`·`row_policy`) 전에는 **타입을 읽을 수 있으면 그 타입의 속성 전체**가 보인다. 2단계를 Phase 14 **뒤**에 두든지, 앞에 둔다면 그래프 노드에는 제목과 pk 만 싣고 상세는 기존 `card` 엔드포인트로 연다(같은 권한 경로를 쓴다)
6. **Action 권한**
   - `add_link`·`remove_link`: 양 끝 타입을 **모두** 읽을 수 있어야 한다
   - 링크 대상 고르기 검색: 읽을 수 있는 타입 안에서만 찾는다
   - `create_object`: pk 가 이미 있다는 충돌 메시지가 읽을 수 없는 객체의 존재를 알려 주면 안 된다. 같은 타입이라 읽기 권한이 있겠지만, 행 정책(Phase 14) 이후에는 일반 메시지로 낸다
   - 판정은 `can_request_action`·`can_decide_action` 에 `readable_types` 를 더한다. 새 판정 함수는 `security/rbac.py` 에만 둔다
7. 조회·편집은 모두 `audit.record` 로 남긴다. 이웃 조회는 대량으로 훑는 경로가 되므로 노드 수 상한과 호출 기록을 둔다
8. 평가·테스트: 권한 없는 역할로 이웃 조회와 링크 Action 을 했을 때 404 가 나는지, stub·개수가 새지 않는지 통합 테스트로 고정한다

## 데이터 쓰기 권한·소유 (보강 2 — 현재 구조의 빈 곳)
**확인한 사실**
- 권한은 **읽기뿐**이다. `type_permission(role_id, object_type, can_read)`(`db/models/security.py:48`) 하나이고, 주석에도 "MVP는 타입 단위 읽기 권한만"이라고 적혀 있다. Phase 14 계획(결정 9)의 `property_permission`·`row_policy` 도 읽기 판정만 다룬다
- 데이터 쓰기를 거르는 것은 **Action 정의의 `requester_roles`·승인 역할**뿐이다(`rbac.can_request_action`). "이 역할이 이 타입을 만들거나 고치거나 지울 수 있는가"를 데이터 쪽에서 정하는 상한이 없다. 그래서 modeler 가 Action 을 하나 정의하면 그 Action 이 곧 쓰기 권한이 된다
- **데이터에 소유·생성자 기록이 없다.** `object`·`link` 표에는 `source_id`·`synced_at`·`deleted_at` 뿐이다(`db/models/objects.py`). `edited_by` 는 속성 편집(`object_edit`)에만 있다. "내가 만든 것만 고친다" 같은 사용자별 규칙을 걸 근거가 없다

그래프에서 생성·삭제를 열면 이 빈 곳이 곧바로 드러난다. 2단계의 **선행 조건**으로 다음을 제안한다.

**① 데이터 쓰기 권한 = 데이터 쪽 상한 ∩ Action 규칙**
- `type_permission` 에 `can_create`·`can_edit`·`can_delete` 를 더한다. 기본값은 false 이고, 기존 행은 마이그레이션에서 false 로 둔다
- 링크는 `link_permission(role_id, link_type, can_link)` 로 둔다. 양 끝 타입의 읽기 권한도 함께 필요하다
- Action 실행 권한은 **둘의 교집합**이다. Action 의 `requester_roles` 를 통과하고, 요청자 역할이 그 규칙 종류의 데이터 권한(`modify`→can_edit, `create_object`→can_create, `delete_object`→can_delete, `add/remove_link`→can_link)을 가져야 한다. 승인자도 같은 상한을 통과해야 한다
- Foundry 와 같은 구조다. Action 의 제출 조건만으로는 부족하고, 대상 데이터에 대한 편집 권한도 있어야 한다
- 판정 함수(`can_write_type`, `can_link`)는 `security/rbac.py` 에만 둔다. 미리보기 단계에서 검사해 `blocked` 로 보여 주고, 승인 트랜잭션 안에서 다시 검사한다(게시 버전 재검증과 같은 자리)
- 관리 화면: 기존 역할별 읽기 타입 설정(`api/admin.py` 의 `set_readable_types`)을 타입 × {읽기·생성·편집·삭제} 표로 넓힌다. 변경은 감사 기록에 남기고, 자기 잠금 방지 규칙은 `security/admin.py` 에 둔다

**② 데이터의 출처·소유 기록**
- 편집 레이어로 만든 객체·링크에는 `created_by`·`created_at`·`action_run_id` 를 저장한다. 묘비(삭제)에는 `deleted_by`·`action_run_id` 를 저장한다
- 원천 동기화로 들어온 객체는 소유 그룹이 없다(원천 시스템이 권위를 가진다)
- `delete_object` 의 동작은 대상에 따라 다르다
  - 편집 레이어로 만든 객체: 삭제(묘비)
  - 원천 객체: **숨김 묘비**. 동기화는 묘비를 건드리지 않는다
  - 원천 행이 바뀌면 `record_source_changes` 가 숨김 충돌로 남기고, 담당자가 확인하거나 숨김을 해제한다(편집 레이어 우선 원칙)
  - 원천에서 실제로 지워지면 묘비는 그대로 둔다
  - 숨김은 `can_delete` 권한으로 판정하고, 해제는 기존 편집 해제와 같은 권한 경로를 쓴다
- 카드·그래프 노드에 "출처: 원천 ○○ / 사용자 생성(○○○, 실행 #N)"을 보여 준다. 기존 계보(`object_lineage`)를 넓힌다

**③ 그룹 기반 권한 — 개인이 아니라 그룹에 준다** (위 ①의 "역할"은 아래 그룹으로 읽는다)
지금은 역할(`user`·`modeler`·`bot_builder`·`domain_expert`·`admin`)이 화면·기능 권한과 데이터 읽기 권한(`type_permission.role_id`)을 함께 맡는다. 그래서 "영업팀은 고객·계약 읽기·편집, 물류팀은 출하만" 같은 조직 단위 규칙을 표현하려면 역할을 계속 늘려야 한다. Foundry 도 데이터 권한은 그룹(과 마킹)에 주고, 사용자는 그룹 구성원으로 권한을 얻는다. 다음처럼 나눌 것을 제안한다.

| 축 | 정하는 것 | 주체 |
|---|---|---|
| **역할**(기존) | 어떤 화면·기능을 쓰는가 — 모델링, 평가, 관리 | 사용자 ↔ 역할 |
| **그룹**(신규) | 어떤 데이터를 읽고·만들고·고치고·지우는가 | 사용자 ↔ 그룹 |

- **표**
  - `user_group(id, name, display_name, external_id, description)`
  - `group_member(group_id, user_id)` — 평면 그룹에서 시작하고, 중첩은 넣지 않는다
  - `group_type_permission(group_id, object_type, can_read, can_create, can_edit, can_delete)`
  - `group_link_permission(group_id, link_type, can_link)`
  - Phase 14 의 `property_permission`·`row_policy` 도 `group_id` 를 주체로 둔다
- **판정**: 사용자의 유효 권한은 소속 그룹 권한의 **합집합**이다(허용만 있고 거부 규칙은 없다 — 판정이 단순하고 설명하기 쉽다). `rbac.readable_types` 는 그룹을 거쳐 계산하도록 바꾸고, 호출부 시그니처는 유지한다
- **기존 데이터 이전**: 마이그레이션에서 `type_permission` 을 가진 역할마다 같은 이름의 그룹을 만든다. 그 역할의 사용자를 구성원으로 넣고 읽기 권한을 옮긴다. 이전 뒤 결과가 같은지 대조 테스트로 고정한 다음 `type_permission` 을 제거한다
- **SSO 연동**: OIDC 로그인 때(`security/oidc.py` `_find_or_create_user`) 설정한 클레임(예: `groups`)을 읽어 `external_id` 가 일치하는 그룹으로 소속을 맞춘다(정확 일치만). 로컬 계정은 관리 화면에서 지정한다. 클레임 이름은 설정에 두고, 기본값은 꺼짐이다
- **Action**: `requester_roles`·승인 역할 옆에 `requester_groups`·`approval_groups` 를 둔다. 실행 권한 = Action 규칙(역할 또는 그룹) ∩ ①의 그룹 데이터 권한이다
- **소유 = 소유 그룹**: 편집 레이어로 만든 객체에는 `created_by`(감사용 개인)와 함께 **`owner_group`** 을 저장한다. 생성자는 자기가 속한 그룹 중에서 고른다. 행 정책 Filter 문법에 `$current_groups` 를 더해 `owner_group IN $current_groups` 로 "우리 팀이 만든 것만 고친다/본다"를 표현한다. 정책은 `applies_to: read|write` 로 구분하고, 컴파일러 주입(읽기)과 Action 재검증(쓰기)이 같은 판정을 쓴다
- **권한 지문·평가**: 채팅·카세트의 권한 지문을 역할 집합에서 **그룹 권한 집합**으로 바꾼다(CLAUDE.md: 카세트 키가 바뀌므로 `eval-record` 를 다시 해야 한다). 골든의 `test_role` 옆에 `test_group` 을 둔다
- **관리 화면**: 그룹 목록·구성원, 그룹 × 타입 {읽기·생성·편집·삭제} 표, 그룹 × 링크 타입 {연결}. 변경은 감사 기록에 남긴다. 마지막 관리 그룹을 비우는 것 같은 자기 잠금은 `security/admin.py` 에서 막는다
- 객체마다 사용자나 그룹을 붙이는 ACL(`object_grant`)은 여전히 권하지 않는다. 행 정책의 `owner_group` 으로 충분하고, 컴파일러 주입·평가 불변식을 단순하게 유지할 수 있다

**④ 스키마(정의) 쪽**: 버전에는 `created_by` 가 있지만 modeler 는 누구나 어느 draft 든 고칠 수 있다. 여러 modeler 가 함께 쓴다면 draft 소유자·잠금(편집 중 표시)을 1단계에 함께 넣을지 정해야 한다(권장: 소유자 표시 + 다른 사람의 draft 편집 시 경고, 게시는 기존 advisory lock)

**영향**: Phase 14 범위를 "읽기"에서 "읽기·쓰기·소유"로 넓힌다. TRD 17절 결정 9 개정, 평가 불변식(권한 위반 = 0)에 쓰기 경로 문항 추가, 마이그레이션 + `test_schema` 대조가 필요하다.

## 단계 배치(제안)
- **1단계**: Phase 12(Function)와 독립적이다. 바로 끼우거나 Phase 13(v1a 안정화) 직후에 둔다. 영향 분석 일부는 Phase 17 에서 앞당긴다
- **2단계**: Phase 15(explorer·traversal) 위에 얹는다. 생성·삭제·링크 Action 은 TRD 의 "링크 변경 Action = v1.5" 결정을 고치는 일이라 TRD 개정(22절)과 ADR 이 필요하다
- 화면을 바꾸므로 운영자·사용자 매뉴얼도 함께 고친다

## 확인 방법(구현 시)
- 1단계: 기존 `api/ontology.py` 통합 테스트에 references·layout 엔드포인트를 더한다. 그래프에서 생성·끌기·삭제·게시를 브라우저 미리보기로 확인한다. `content_hash` 가 배치 변경에 바뀌지 않는지 단위 테스트로 확인한다
- 2단계: reader 덮어 읽기·투영·대사·롤백 통합 테스트, `eval-replay` 회귀, 평가 불변식 유지
