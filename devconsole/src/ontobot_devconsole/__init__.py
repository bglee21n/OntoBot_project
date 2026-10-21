"""OntoBot 개발 콘솔(ADR-0015) — 개발 PC 의 127.0.0.1 에서만 돈다.

소스(git·GitHub)·검사·평가·이미지·번들·개발 서버를 한 화면에서. 명령의 정본은
``scripts/task.py`` 다 — 콘솔은 그 ``TASKS`` 를 읽어 작업 목록을 만들고 ``task.py <작업>`` 을
그대로 부른다(명령을 두 곳에 적지 않는다).
작업 실행·기록은 배포 콘솔과 같은 ``opsjobs``.

- ``settings``: 저장소 위치·포트·서명 키 경로
- ``catalog``: 실행할 수 있는 작업(고정 목록)과 인자 검증
- ``git``·``github``: 소스 상태 읽기·쓰기, gh CLI
- ``services``: 오래 사는 개발 서버(dev-api·worker·dev-web)
- ``results``: 테스트·평가 출력에서 결과 뽑기
- ``overview``: 대시보드 신호등
- ``api``: HTTP API 와 화면
"""
