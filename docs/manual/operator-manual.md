# OntoBot 시스템 운영자 매뉴얼

시스템 운영자 매뉴얼은 장별 원본(`docs/manual/ops/*.md`)과 실제 설치본에서 찍은 화면·명령 출력 사진
(`docs/manual/ops/images/`)으로 되어 있고, Word·PDF 로 묶어 배포합니다. 화면 사용법은 [사용자 매뉴얼](user-manual.md),
설치 절차의 정본은 [deploy/INSTALL.md](../../deploy/INSTALL.md)입니다.

## 차례

| 부 | 원본 | 대상 |
| --- | --- | --- |
| 들어가며 | [00-intro.md](ops/00-intro.md) | 모두 |
| 제1부 이해하기(1~3장) | [01](ops/01-overview.md) · [02](ops/02-architecture.md) · [03](ops/03-terms.md) | 처음 맡은 운영자 |
| 제2부 설치하기(4~5장) | [04](ops/04-prepare.md) · [05](ops/05-install.md) | 설치 담당자 |
| 제3부 사람과 권한(6~10장) | [06](ops/06-web.md) · [07](ops/07-users.md) · [08](ops/08-permissions.md) · [09](ops/09-audit.md) · [10](ops/10-trace.md) | OntoBot 관리자 |
| 제4부 데이터와 알림(11~16장) | [11](ops/11-sources.md) · [12](ops/12-mapping.md) · [13](ops/13-graph.md) · [14](ops/14-actions.md) · [15](ops/15-notify.md) · [16](ops/16-conflicts.md) | 데이터 연결 담당자 |
| 제5부 배포 콘솔(17~24장) | [17](ops/17-console.md) · [18](ops/18-status.md) · [19](ops/19-backup.md) · [20](ops/20-bundles.md) · [21](ops/21-upgrade.md) · [22](ops/22-rollback.md) · [23](ops/23-deployer-update.md) · [24](ops/24-jobs.md) | 배포 담당자 |
| 제6부 지키기(25~28장) | [25](ops/25-checks.md) · [26](ops/26-logs.md) · [27](ops/27-incidents.md) · [28](ops/28-symptoms.md) | 장애 대응 담당자 |
| 제7부 참고(29~33장) | [29](ops/29-cli.md) · [30](ops/30-config.md) · [31](ops/31-security.md) · [32](ops/32-drills.md) · [33](ops/33-faq.md) | 필요할 때 |
| 제8부 깊이 보기(34~42장) | [34](ops/34-question-gallery.md) · [35](ops/35-role-views.md) · [36](ops/36-ontology-check.md) · [37](ops/37-capacity.md) · [38](ops/38-evaluation.md) · [39](ops/39-resources.md) · [40](ops/40-database.md) · [41](ops/41-messages.md) · [42](ops/42-support-cases.md) | 필요할 때 |
| 부록(43~45장) | [43](ops/43-forms.md) · [44](ops/44-glossary.md) · [45](ops/45-index.md) | 모두 |

## 고치고 다시 만들기

설치·배포 절차(`ontobotctl`·배포 콘솔)나 관리 화면을 바꾸면 해당 장을 고치고 사진을 다시 찍습니다. 도구는
`docs/manual/tools/` 에 있습니다.

| 순서 | 명령 | 하는 일 |
| --- | --- | --- |
| 1 | `uv run --project backend python scripts/build_bundle.py --signing-key <키>` | 촬영할 설치본(전체 번들). 업그레이드 장은 `--app-only` 번들이 하나 더 필요 |
| 2 | (Docker Desktop 가상 머신의 시험 폴더에 `ontobotctl install`) | 실제 설치본 — 매뉴얼은 이 설치본을 찍음(00장 "촬영 환경") |
| 3 | `cd docs/manual/tools && PYTHONUTF8=1 uv run --project ../../../backend --with playwright python capture_ops.py [장 …]` | 웹 화면(설치된 Edge)과 명령 출력 사진. 많은 장이 시험 설치본의 상태를 바꾸므로 순서대로, 다시 찍을 장만 골라서 |
| 4 | `PYTHONUTF8=1 python docs/manual/tools/index_md.py --book ops` | 45장 찾아보기 |
| 5 | `PYTHONUTF8=1 uv run --with python-docx --with markdown --with playwright --with pillow python docs/manual/tools/build_manual.py --book ops` | `dist/manual/` 에 Word(.docx)·PDF |

명령 출력 사진은 시험 설치본 안에서 실제로 실행한 결과이고, 사진 속 명령은 운영 서버의 꼴
(`wsl -d ontobot -u root -- …`)로 보입니다. 계정 비밀번호는 루트 `.env` 의 `ONTOBOT_MANUAL_PASSWORD` 로만 넘기고, 사진의
비밀값은 촬영 도구(`term.py`)가 가립니다. AI 가 쓴 답 문장은 다시 찍으면 조금 달라질 수 있으므로, 다시 찍은 뒤에는
본문이 인용한 숫자·조회 조건이 사진과 맞는지 확인하세요.
