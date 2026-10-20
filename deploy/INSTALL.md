# OntoBot 설치 안내 (Windows Server + WSL2 + Docker, 폐쇄망)

컨테이너로 돈다(ADR-0013). 서버에는 WSL2 배포판 `ontobot` 하나를 두고 그 안의 Docker Engine 이 PostgreSQL·
Neo4j·Ollama·앱·배포 콘솔을 돌린다. 설치 중에도 실행 중에도 인터넷을 쓰지 않는다. 최초 설치만 PowerShell 로 하고,
이후 업그레이드·롤백·백업은 **배포 콘솔**(브라우저, ADR-0014)로 한다.

> **먼저 확인:** Windows Server **2022 이상**이어야 한다(2019 는 WSL2 미지원). 운영 장비에서 ADR-0013 의
> "0단계 점검"(WSL 오프라인 설치·GPU·재부팅 자동 기동·포트 전달)을 한 번 해 본다 — 개발 PC 에서는 확인하지 못했다.

## 0. 서명 키 (배포 담당자, 한 번)

번들은 Ed25519 로 서명한다. 개인키는 배포 담당자만 오프라인으로 보관하고, 공개키를 서버에 등록한다.

```powershell
$env:PYTHONUTF8 = "1"
uv run --project backend python scripts/bundle_sign.py keygen --out D:\keys\ontobot-release
# → ontobot-release.key (개인키 — 외부 유출 금지) · ontobot-release.pub (서버에 등록)
```

## 1. 번들 만들기 (인터넷이 되는 빌드 장비)

준비물: Git, uv, Docker(빌드용), `ollama pull gemma4:e4b`(config/llm.yaml 의 모델). **커밋한 상태**에서 만든다
(커밋하지 않은 변경이 있으면 버전에 `-dirty` 가 붙고 서버가 거부한다).

```powershell
$env:PYTHONUTF8 = "1"
# 최초 설치용 전체 번들(이미지 전부 + 모델, 약 14GB)
uv run --project backend python scripts/build_bundle.py --signing-key D:\keys\ontobot-release.key
# 업그레이드용 앱 번들(앱·배포 콘솔 이미지만, 약 220MB) — 배포 콘솔에 올릴 zip 도 만든다
uv run --project backend python scripts/build_bundle.py --app-only --zip --signing-key D:\keys\ontobot-release.key
```

결과: `dist\bundle\ontobot-<버전>\`(+ `.zip`). 제3자 이미지는 `deploy/docker/images.lock` 의 다이제스트로 받는다.

**호스트 자산**(최초 설치에만, 한 폴더 — 예: `E:\ontobot-host-assets`). 공식 배포처에서 받아 체크섬을 확인한다.

| 파일 | 받는 곳 | 비고 |
| --- | --- | --- |
| `wsl*.msi` | Microsoft WSL GitHub Releases | WSL 2.x 패키지 |
| `*ubuntu*.tar*` | Ubuntu 24.04 WSL rootfs(cloud-images 의 `ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz`) | `wsl --import` 용 |
| `debs\*.deb` | download.docker.com(Ubuntu noble): `containerd.io`·`docker-ce`·`docker-ce-cli`·`docker-buildx-plugin`·`docker-compose-plugin`, NVIDIA Container Toolkit(`nvidia-container-toolkit`·`-base`·`libnvidia-container1`·`libnvidia-container-tools`) | 의존 패키지가 빠지면 설치가 멈추고 알려 준다 |

GPU 를 쓰려면 Windows 쪽에 NVIDIA 드라이버(WSL CUDA 지원)를 먼저 설치한다.

## 2. 설치 (폐쇄망 서버)

요구: Windows Server 2022+, 관리자 PowerShell, 메모리 16GB 이상(WSL 에 기본 12GB), 디스크 여유 60GB 이상
(번들 14GB + 이미지 적재 + 데이터). 서비스 전용 로컬 계정을 만들어 그 계정으로 실행하길 권한다 — WSL 배포판은
**실행한 계정의 것**이고, 부팅 예약 작업도 그 계정으로 돈다.

```powershell
cd E:\ontobot-0.1.0-abc1234
powershell -ExecutionPolicy Bypass -File .\host\install-host.ps1 `
    -HostAssets E:\ontobot-host-assets -TrustedKey E:\keys\ontobot-release.pub `
    -AdminUsername admin -SeedSample -AllowCidrs 10.10.0.0/24
```

| 매개변수 | 뜻 |
| --- | --- |
| `-HostAssets` | 1절의 호스트 자산 폴더 |
| `-TrustedKey` | 신뢰 공개키(여러 개 가능 — 키 교체 대비) |
| `-AdminUsername` | 첫 OntoBot 관리자 계정(비밀번호를 묻는다) |
| `-SeedSample` | 시험용 샘플 데이터(합성) — 운영 데이터를 바로 넣을 거면 뺀다 |
| `-AllowCidrs` | 배포 콘솔(8100)에 접속할 관리자 대역. 비우면 같은 서브넷만 |
| `-MemoryGB`·`-Processors` | WSL 자원 상한(기본 12GB·제한 없음) |

순서: OS 확인 → WSL 기능(재부팅이 필요하면 종료 코드 3 — 재부팅 뒤 같은 명령) → WSL·배포판 → systemd →
Docker·NVIDIA → 번들·공개키 복사 → `ontobotctl install`(서명 확인 → 이미지·모델 적재 → 비밀값 생성 → 저장소 기동 →
마이그레이션 → 샘플·관리자 → 앱·배포 콘솔 기동 → 매일 백업 타이머 → `/readyz` 대기) → 부팅 예약 작업
(계정 비밀번호를 묻는다) → 방화벽.

### 설치 확인 (완료 기준)

1. 다른 PC 브라우저에서 `http://<서버>:8000` → 관리자 로그인 → 채팅 `위험등급이 3 이상인 시설은 몇 개야?` → 18·①.
2. 관리자 PC 에서 `http://<서버>:8100`(배포 콘솔) → 같은 관리자 계정 → 현황이 모두 정상.
3. 서버를 재부팅하고 1을 다시(예약 작업·포트 전달).

## 3. 업그레이드·롤백·백업 — 배포 콘솔

1. 앱 번들 zip 을 **배포 콘솔 → 번들 → 올리기**, 또는 서버의 `C:\OntoBot-bundles` 에 복사해 **가져오기**.
2. 서명·해시를 확인한 사전 점검 결과가 **통과**면 **업그레이드** → 대상 버전을 입력 → 실행. 진행 로그가 보인다.
3. 문제가 있으면 **롤백**: 프로그램만(기본) / DB 까지 업그레이드 직전 백업으로(`RESTORE` 입력 — 그 뒤 데이터 사라짐).
4. **현황 → 지금 백업**. 매일 02:30 자동 백업(14개 보관, `C:\OntoBot\backups` 에 복사본).

배포 콘솔이 안 되면 같은 일을 WSL 안에서 직접 한다(비상 경로):

```powershell
wsl -d ontobot -u root -- ontobotctl status
wsl -d ontobot -u root -- ontobotctl upgrade /opt/ontobot/bundles/<번들>
wsl -d ontobot -u root -- ontobotctl rollback            # --restore-db 는 RESTORE 를 묻는다
wsl -d ontobot -u root -- ontobotctl backup
```

## 4. 운영 메모

| 항목 | 위치·방법 |
| --- | --- |
| 설정 | WSL 의 `/opt/ontobot/app.env`(앱 설정 — OIDC·세션·보존 기간·원천 DB·SMTP 비밀값), `/opt/ontobot/config/`(llm.yaml·notify.yaml). 바꾼 뒤 `wsl -d ontobot -u root -- bash -c "cd /opt/ontobot && docker compose up -d"` |
| 인프라 비밀값 | `/opt/ontobot/.env`(DB·Neo4j·postgres 비밀번호 — ontobotctl 이 만들고 관리, 600) |
| 로그 | `/opt/ontobot/logs/`(앱 JSON 로그), `docker compose logs api` |
| 준비 확인 | `/readyz` — `ok` / `degraded`(Neo4j 만) / `unavailable`. 컨테이너 재시작 판단은 `/healthz` 만 |
| 메일·SMS | `/opt/ontobot/config/notify.yaml` + 비밀값은 `app.env`(ADR-0012) |
| HTTPS | 앞단 역방향 프록시에서 종료, `app.env` 의 `ONTOBOT_COOKIE_SECURE=true`·`ONTOBOT_ALLOWED_ORIGINS` |
| 방화벽 | 8000(모두)·8100(관리자 대역). PG·Neo4j·Ollama 는 컨테이너 네트워크 밖으로 열지 않는다 |

## 5. 개발 PC

Docker Desktop(WSL2) 또는 WSL 안의 Docker Engine. 통합 테스트·평가 재생은 `task.py test-int`·`eval-prepare`·
`eval-replay` 가 `deploy/docker/compose.test.yaml` 을 띄운다. 개발 저장소를 컨테이너로 쓰려면 `task.py dev-up`.
설치 절차 시험(서버 대신): 번들을 Docker Desktop 의 `/opt/ontobot-trial` 로 복사하고 배포 콘솔 이미지 안에서
`ontobotctl install` 을 돌린다(`ONTOBOT_HOME=/opt/ontobot-trial`, `-dirty` 번들이면 `ONTOBOT_ALLOW_DIRTY=1`).
