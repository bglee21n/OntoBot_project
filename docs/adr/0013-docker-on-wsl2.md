# ADR-0013: Docker(WSL2) 로 전환 — 컨테이너 이미지 번들과 ontobotctl

- 상태: 채택 (2026-10-20, 사용자 결정) — **ADR-0007(Docker 미사용)·ADR-0010(WinSW·릴리스 폴더)을 대체**
- 근거: 사용자 결정(운영 OS·적용 범위·Ollama·배포 화면), TRD 3절·15절·19절

## 맥락

배포를 화면으로 하기로 하면서(ADR-0014) 사용자가 Docker 전환을 결정했다. 네이티브 설치는 구성 요소마다 설치본·
서비스 래퍼·버전 고정 방식이 달라 스크립트가 길고, 업그레이드가 "새 가상환경 + 정션 + 서비스 재등록"이었다.
컨테이너로 바꾸면 설치본은 **서명된 이미지 묶음**, 업그레이드는 **이미지 교체 + 마이그레이션**이 된다.

## 결정

| 항목 | 결정 |
| --- | --- |
| 운영 OS | **Windows Server 2022 이상 + WSL2 + Docker Engine(CE)**. WSL2 배포판 `ontobot`(Ubuntu rootfs 오프라인 import, systemd) 안에서 돈다. Docker Desktop 은 쓰지 않는다(서버 용도·라이선스) |
| 구성 | `deploy/docker/compose.yaml` 하나(프로젝트 `ontobot`): postgres(pgvector)·neo4j·ollama·migrate(일회성)·api·worker. 앱 이미지 하나(`ontobot-app:<버전>`)를 명령만 달리해 쓴다 |
| 제3자 이미지 | `deploy/docker/images.lock` 에 태그 + 다이제스트. 빌드 장비가 다이제스트로 받아 태그를 붙여 `docker save`. `docker load` 는 다이제스트를 보존하지 않으므로 운영은 태그로 쓰고 무결성은 서명된 번들 해시가 지킨다 |
| GPU | Ollama 컨테이너 + NVIDIA Container Toolkit. nvidia 런타임이 있으면 `compose.gpu.yaml` 을 겹친다(`.env` 의 `COMPOSE_FILE`) |
| 네트워크 | 바깥 포트는 api(8000)·배포 콘솔(8100)뿐. PG·Neo4j·Ollama 는 컨테이너 네트워크 안에서만. WSL2 는 NAT 라 부팅 시 `netsh portproxy` 를 WSL 주소로 다시 건다(예약 작업) |
| 데이터 | 이름 있는 볼륨(pgdata·neo4jdata·ontobot_ollama-models) — WSL 의 ext4 가상 디스크 안. Windows 경로 마운트는 느려서 데이터에 쓰지 않는다 |
| 정렬 | PostgreSQL 클러스터를 **builtin `C.UTF-8`**(코드 포인트 순서)로 만든다. glibc `en_US.utf8` 은 한글 순서가 Windows `Korean_Korea.949` 와 달라 그룹 순서·데이터 블록·카세트 키가 바뀌었다(실측: 평가 재생에서 q04 카세트 놓침) |
| 비밀값 | `/opt/ontobot/.env`(compose 치환·인프라 비밀값, 600)와 `app.env`(앱 설정, 600)를 나눈다. 앱 컨테이너에 postgres 슈퍼유저 비밀번호를 넣지 않는다 |
| 절차의 정본 | `deploy/docker/ontobotctl`(bash) — verify·preflight·install·upgrade·rollback·backup·status. docker CLI·coreutils·openssl 만 쓴다. 호스트 준비는 `deploy/windows/install-host.ps1`(최초 1회) |
| 백업 | `ontobotctl backup`(pg_dump -Fc). WSL systemd 타이머로 매일, Windows 폴더(`C:\OntoBot\backups`)로 복사, 14개 보관. Neo4j 는 백업하지 않는다(PG 에서 재구축) |
| 개발·CI | 통합 테스트·평가 재생은 `compose.test.yaml`(운영과 같은 이미지, 테스트 전용 포트 55432·57687)을 `task.py` 가 띄운다. CI 통합 잡은 `ubuntu-latest` + compose, 이미지 빌드·스모크 잡 추가. 개발 저장소는 `compose.dev.yaml`(선택) |

### 업그레이드·롤백 (ontobotctl)

- **upgrade**: 서명·해시 확인 → `docker load`(서비스가 도는 동안) → 백업 → api·worker 중지 → 새 이미지로 migrate
  → 새 버전 기동 → `/readyz` 대기(종료 코드 2 = 떴지만 준비 안 됨). 마이그레이션이 실패하면 이전 태그로 다시 띄운다.
- **rollback**: 이전 버전 태그로 `compose up`. `--restore-db` 는 업그레이드 직전 백업을 DROP/CREATE 후 복원
  (감사 로그 트리거 때문에 `--clean` 을 쓰지 않는다 — ADR-0010 과 같은 이유).

## 0단계 점검 — 대상 OS 에서 먼저 확인할 것 (미검증)

개발 PC(Windows 11, Docker Desktop WSL2)에서 이미지·compose·ontobotctl·배포 콘솔은 확인했다. **Windows Server
2022 위의 WSL2 는 확인하지 못했다.** 운영 장비에서 다음을 먼저 본다. 하나라도 안 되면 결정을 다시 한다(대안:
Linux 서버에 Docker, 또는 Ollama 만 호스트 설치).

1. 오프라인 WSL 설치(msi)와 `wsl --import`, `wsl.conf` systemd
2. Docker CE `.deb` 오프라인 설치, `docker run --gpus all … nvidia-smi`
3. 재부팅 뒤 예약 작업으로 배포판 기동·컨테이너 자동 시작(`restart: unless-stopped`)·`vmIdleTimeout=-1`
4. `netsh portproxy` 로 다른 PC 에서 8000 접속, 방화벽
5. PG 볼륨 성능(pgbench 짧게), 장시간 유휴 뒤 WSL 이 내려가지 않는지

## 결과

- 오프라인 번들: `scripts/build_bundle.py` — `images/ontobot.tar`·`third-party.tar`·`ollama-models.tar`·`stack/`·
  `bin/ontobotctl`·`host/install-host.ps1`·`bundle.env`·`manifest.json`·`SHA256SUMS(.sig)`. 업그레이드용 `--app-only`.
- 기존 `deploy/windows/install.ps1`·WinSW 서비스 정의·`deploy/offline/build_bundle.ps1` 은 지운다(이력은 git).
  네이티브 설치본은 운영에 설치된 적이 없어(MVP 미검증) 옮기는 도구를 두지 않는다 — 필요하면 `pg_dump` → `pg_restore`.
- 개발 PC 의 네이티브 PostgreSQL·Neo4j 는 개발 DB 로 계속 써도 된다(통합 테스트·평가는 더 이상 쓰지 않는다).

## 재검토 조건

0단계 점검 실패, WSL 장시간 안정성 문제, 운영이 Linux 서버로 정해질 때(그때는 install-host 만 바뀐다).
