"""메일·SMS 발송(TRD 17.1-4) — 운영자가 설정한 사내 게이트웨이로만 보낸다.

- ``config``: ``config/notify.yaml`` 읽기. 설정하지 않은 채널은 꺼진다(그 채널을 쓰는
  Action 은 미리보기에서 막힌다 — 보낸 척하지 않는다).
- ``channels``: 게이트웨이 어댑터(SMTP·SMS HTTP·SMS 발송 테이블·개발용 파일).
  네트워크 호출은 여기만 한다.
- ``delivery``: ``notification_delivery`` 아웃박스를 워커가 커밋 뒤에 보낸다
  (재시도·상태 기록).
"""
