"""OntoBot 배포 콘솔(ADR-0014).

앱(``ontobot``)을 import 하지 않는다 — 업그레이드가 실패해 앱이 깨졌을 때(롤백이 가장 필요할 때)도
돌아야
하기 때문이다. 절차는 직접 하지 않고 ``ontobotctl``(절차의 정본)을 부른다.

- ``settings``: 환경변수
- ``auth``: OntoBot 관리자 계정으로 로그인(앱 DB 를 읽기만), 세션·실패 제한
- ``bundles``: 업로드 조각·반입 폴더·안전한 압축 해제
- ``jobs``: ontobotctl 작업 실행·기록(한 번에 하나)
- ``api``: HTTP API 와 화면
"""
