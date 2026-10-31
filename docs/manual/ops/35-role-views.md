# 35장 역할별로 보이는 화면

사용자에게서 "메뉴가 없어요", "화면이 비어 있어요" 같은 문의가 오면, 그 사람의 **역할**에 따라 원래 그렇게 보이는
것인지 확인해야 합니다. 이 장은 이 책에서 만든 계정마다 로그인해 각 메뉴를 실제로 열어 본 화면입니다.

| 계정 | 역할 | 보이는 메뉴 |
| --- | --- | --- |
| `kim.user` | user | 채팅 · 온톨로지 · 데이터 소스 · Action |
| `lee.modeler` | modeler, user, domain_expert | 채팅 · **인터뷰** · 온톨로지 · **평가** · 데이터 소스 · Action |
| `choi.expert` | domain_expert | 채팅 · **인터뷰** · 온톨로지 · 데이터 소스 · Action |
| `jung.builder` | bot_builder | 채팅 · 온톨로지 · **평가** · 데이터 소스 · Action |
| `park.admin` | admin, user | 채팅 · 온톨로지 · 데이터 소스 · Action · **관리** |

> **알아 두기** 메뉴가 보여도 그 화면의 기능을 쓸 수 있는 것은 아닙니다. 온톨로지·데이터 소스 메뉴는 모두에게
> 보이지만, 내용을 보고 고치는 일은 모델러만 할 수 있습니다. 서버가 요청을 막기 때문에 화면이 비거나 "불러오는 중…"에
> 머뭅니다. 고장이 아닙니다.

## 35.1 일반 사용자 — `kim.user`

![일반 사용자의 상단 막대](images/35-roles/kim.user-topbar.png)

### 채팅

![일반 사용자 — 채팅](images/35-roles/kim.user-chat.png)

질문과 답은 속한 데이터 그룹의 권한(8장)에 따라 달라집니다. `user` 그룹은 이 책에서 세 타입을 모두 읽을 수 있습니다.

### 온톨로지

![일반 사용자 — 온톨로지(목록이 뜨지 않음)](images/35-roles/kim.user-ontology.png)

일반 사용자에게는 버전 목록이 뜨지 않고 **불러오는 중…** 에 머뭅니다. **빈 draft 만들기** 단추가 보여도 누르면
권한 오류입니다. 사용자가 "온톨로지가 안 열려요"라고 하면 정상이라고 안내하세요.

### 데이터 소스

![일반 사용자 — 데이터 소스(내용이 비어 있음)](images/35-roles/kim.user-sources.png)

틀만 보이고 그래프 상태·데이터 소스·매핑 내용은 비어 있습니다(서버가 막음).

### Action

![일반 사용자 — Action](images/35-roles/kim.user-actions.png)

요청과 **내 요청** 은 쓸 수 있습니다. 승인할 권한이 없으면 승인 대기 탭이 비어 있습니다.

## 35.2 모델러 — `lee.modeler`

![모델러의 상단 막대](images/35-roles/lee.modeler-topbar.png)

모델러는 메뉴가 가장 많습니다(관리 제외).

![모델러 — 채팅](images/35-roles/lee.modeler-chat.png)

![모델러 — 인터뷰 워크숍](images/35-roles/lee.modeler-workshop.png)

인터뷰 워크숍은 현업 전문가의 업무 설명을 듣고 온톨로지 후보를 뽑는 화면입니다(사용자 매뉴얼 14~15장). 워크숍은 AI
모델을 쓰므로, 많이 쓰면 채팅 응답이 느려질 수 있습니다.

![모델러 — 온톨로지](images/35-roles/lee.modeler-ontology.png)

![모델러 — 평가](images/35-roles/lee.modeler-eval.png)

평가 화면은 골든셋을 실제 모델로 돌려 정확도를 잽니다(사용자 매뉴얼 20장). 시험 서버에는 골든셋이 없어 "골든셋이
없습니다."가 보입니다. 평가를 돌리면 worker 와 AI 모델이 몇 분간 바빠지므로, 운영 서버에서는 업무 시간을 피하도록
챗봇 빌더와 협의하세요.

![모델러 — 데이터 소스(전부 보임)](images/35-roles/lee.modeler-sources.png)

![모델러 — Action](images/35-roles/lee.modeler-actions.png)

## 35.3 현업 전문가 — `choi.expert`

![현업 전문가의 상단 막대](images/35-roles/choi.expert-topbar.png)

![현업 전문가 — 채팅](images/35-roles/choi.expert-chat.png)

> **알아 두기** 이 책에서는 domain_expert 역할에 읽기 권한을 주지 않았습니다(8.5). 그래서 현업 전문가가 채팅으로
> 질문하면 "질문 해석 실패"가 나옵니다. 현업 전문가에게 질문도 시키려면 읽기 권한을 주거나 user 역할을 더하세요.

![현업 전문가 — 인터뷰 워크숍](images/35-roles/choi.expert-workshop.png)

![현업 전문가 — 온톨로지](images/35-roles/choi.expert-ontology.png)

![현업 전문가 — 데이터 소스](images/35-roles/choi.expert-sources.png)

![현업 전문가 — Action](images/35-roles/choi.expert-actions.png)

## 35.4 챗봇 빌더 — `jung.builder`

![챗봇 빌더의 상단 막대](images/35-roles/jung.builder-topbar.png)

![챗봇 빌더 — 채팅](images/35-roles/jung.builder-chat.png)

![챗봇 빌더 — 평가](images/35-roles/jung.builder-eval.png)

![챗봇 빌더 — 온톨로지](images/35-roles/jung.builder-ontology.png)

![챗봇 빌더 — 데이터 소스](images/35-roles/jung.builder-sources.png)

![챗봇 빌더 — Action](images/35-roles/jung.builder-actions.png)

## 35.5 관리자 — `park.admin`

![관리자의 상단 막대](images/35-roles/park.admin-topbar.png)

![관리자 — 채팅](images/35-roles/park.admin-chat.png)

![관리자 — 온톨로지](images/35-roles/park.admin-ontology.png)

관리자(admin)는 온톨로지를 볼 수 있지만, 고치는 일은 모델러의 일입니다.

![관리자 — 데이터 소스](images/35-roles/park.admin-sources.png)

관리자는 데이터 소스 화면의 그래프 상태·편집 충돌을 보고 처리(재구축, 충돌 확인·해제)할 수 있습니다.

![관리자 — Action](images/35-roles/park.admin-actions.png)

**Action** 메뉴 옆 빨간 숫자 3과 알림함 — 14장에서 실행·롤백한 Action 의 앱 알림입니다.

![관리자 — 관리](images/35-roles/park.admin-admin.png)

## 35.6 역할 문의에 답하기

| 문의 | 확인 | 답 |
| --- | --- | --- |
| "관리 메뉴가 없어요" | admin 역할? | 관리자만 보임. 필요하면 7.6 |
| "인터뷰 메뉴가 없어요" | domain_expert·modeler? | 역할 추가 |
| "평가 메뉴가 없어요" | bot_builder·modeler? | 역할 추가 |
| "온톨로지가 계속 불러오는 중이에요" | modeler? | 모델러가 아니면 정상 |
| "데이터 소스 화면이 비어 있어요" | modeler·admin? | 정상 |
| "질문하면 해석 실패만 나와요" | 역할의 읽기 권한 | 8.2 |
| "승인 단추가 없어요" | Action 의 승인 역할 | 14.9 |
| "배포 콘솔에 못 들어가요" | admin 역할, 로컬 계정 | 17.2 |
