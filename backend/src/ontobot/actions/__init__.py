"""Action 실행(PRD 5.3, TRD 17.1) — 미리보기 → 승인 → 실행(편집 레이어 + 알림·발송) → 롤백.

- ``rules``: 파라미터 변환·사전 조건 평가·문구 치환 — 순수 함수.
- ``runner``: 실행 기록(``action_run``)의 상태 전이. 권한 판정은 ``security.rbac`` 에만 있다.

정의는 게시된 온톨로지 문서에 있다(``ontology.models.ActionTypeDef``). Action 은 원천을 바꾸지 않고
편집 레이어에 쓴다(write-back 없음, TRD 2절 8번).
"""
