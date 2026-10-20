# ADR-0014: 배포 콘솔 — 화면으로 하는 업그레이드·롤백·백업

- 상태: 채택 (2026-10-20, 사용자 결정)
- 근거: 사용자 결정(범위·위치·번들 반입·서명), ADR-0013(Docker)

## 결정

| 항목 | 결정 |
| --- | --- |
| 범위 | 업그레이드·롤백(프로그램만 / DB 복원)·상태·백업·번들 가져오기·배포 콘솔 갱신. **최초 설치는 PowerShell 한 번**(`install-host.ps1` — 이때 콘솔도 설치) |
| 위치 | **별도 컨테이너 `ontobot-deployer`**(compose 프로젝트도 따로, 포트 8100). 앱 API 안에 두지 않는다 — 업그레이드가 실패해 앱이 안 뜰 때가 롤백이 가장 필요할 때다. 앱 관리 화면에는 지금 버전과 콘솔 링크만(`GET /admin/system`, `ONTOBOT_DEPLOYER_URL`) |
| 코드 | `deployer/`(별도 파이썬 프로젝트, `ontobot` import 금지 — 테스트가 막는다), 화면은 `frontend/src/deploy`(Vite 두 번째 입력, 콘솔 이미지에 포함) |
| 절차 | 콘솔은 절차를 직접 하지 않고 **`ontobotctl --json --non-interactive`** 를 자식 프로세스로 부른다. 진행 줄 `==> ` 을 단계로, 마지막 JSON 줄을 결과로 읽는다. 한 번에 한 작업(콘솔 잠금 + ontobotctl `flock`) |
| 로그인 | OntoBot 관리자 계정(admin·active)만 — 앱 DB 의 `app_user` 를 읽기만 하고 argon2 로 확인(앱이 멈춰도 PG 만 살면 된다). 세션은 메모리, 실패 5회면 5분 잠금 |
| 번들 반입 | **브라우저 업로드**(8MB 조각, 오프셋 확인·이어 올리기) + **반입 폴더**(`C:\OntoBot-bundles` → 콘솔의 `/drop`). 둘 다 `$ONTOBOT_HOME/bundles/` 로 옮긴 뒤 쓴다(데몬이 보는 경로). zip 은 안전 해제(절대 경로·`..`·심볼릭 링크·최상위 둘 이상 거부) |
| 진위 | **Ed25519 서명.** 빌드 담당이 `SHA256SUMS` 에 서명(`scripts/bundle_sign.py`, 개인키는 오프라인 보관). 서버는 `trusted-keys/*.pub` 로 `openssl pkeyutl -verify` → `sha256sum -c` → 목록 밖 파일 없음. 신뢰 키가 하나라도 있으면 서명 필수(없으면 설치 거부 — 개발용 `ONTOBOT_ALLOW_UNSIGNED=1`). `-dirty` 번들 거부 |
| 실수 방지 | 업그레이드는 사전 점검을 통과한 번들만, **대상 버전을 사람이 다시 입력**해야 실행. DB 복원 롤백은 `RESTORE` 입력 |
| 기록 | 작업마다 `job.json`·`output.log`, 이력 `history.jsonl`(추가만). 끝난 뒤 `ontobotctl audit` → `ontobot.security.cli audit-deploy` 로 앱 감사 로그(`deploy.*`)에도 남긴다(DB 가 안 되면 이력에만) |
| 자기 갱신 | 업그레이드가 새 콘솔 이미지를 `.env` 의 `ONTOBOT_DEPLOYER_IMAGE_NEXT` 로 남긴다. '배포 콘솔 갱신' → 새 이미지로 띄운 일회성 컨테이너(`ontobotctl deployer-apply`)가 교체, 건강 확인 실패면 이전 이미지로 되돌린다 |

## 보안

콘솔은 `/var/run/docker.sock` 을 마운트한다 — **WSL 배포판 root 와 같은 권한**이다. 그래서:

- 8100 은 관리자 대역에서만(`ONTOBOT_DEPLOYER_ALLOW_CIDRS` + Windows 방화벽 원격 주소 제한). HTTPS 는 앞단 프록시.
- 상태를 바꾸는 요청은 `X-Ontobot-Deploy: 1` 헤더 필수(CSRF), 쿠키 HttpOnly·SameSite=Strict, `X-Frame-Options: DENY`.
- 실행하는 코드는 서명된 번들의 것뿐이다. 콘솔 화면은 임의 명령·임의 경로를 받지 않는다(번들 이름은 정규식, 명령은 고정).

## 결과

- 운영자 매뉴얼의 업그레이드·롤백·백업이 화면 절차가 되고, PowerShell·`ontobotctl` 은 비상 경로가 된다.
- 콘솔 자체가 고장 나면 WSL 에서 `ontobotctl` 을 직접 쓴다(같은 절차).

## 재검토 조건

여러 서버(이중화)로 늘 때, 사내 SSO 만 쓰고 로컬 관리자 계정을 없앨 때(그때는 1회용 접속 코드 경로를 더한다).
