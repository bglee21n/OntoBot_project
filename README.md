# OntoBot (가칭) — 온톨로지 기반 챗봇 서비스

| 문서 | 위치 |
| --- | --- |
| PRD v0.4 · 검토 의견 | [docs/prd/](docs/prd/) |
| TRD v0.2 | [docs/trd/OntoBot_project-trd.md](docs/trd/OntoBot_project-trd.md) |
| 구현 계획 (MVP P0) | [docs/plan/OntoBot_project-implementation-plan.md](docs/plan/OntoBot_project-implementation-plan.md) |
| 결정 기록 | [docs/adr/](docs/adr/) |

현재 단계: **Phase 2 완료 — 동기화·객체 저장·그래프 투영**. 원천 PostgreSQL → 스테이징 병합 → 아웃박스 → Neo4j, 대사·재구축. 다음은 Phase 3·4(근거 대조 완성·질의 파이프라인·채팅). 플래너 측정은 [ADR-0004](docs/adr/0004-planner-structured-output.md).

## 개발 환경

- Python 3.12 — 가상환경은 **uv**가 만든다(`backend/.venv`). 전체 고정은 `backend/uv.lock`.
- Node 22 — 프론트 의존성은 `frontend/package-lock.json`에 고정.
- PostgreSQL — 개발 PC에 설치한 인스턴스(`ontobot_db` / 계정 `ontobot`).
- Neo4j Community — Phase 2부터 필요하다. 설치 절차는 [deploy/windows/README.md](deploy/windows/README.md).
- **Docker는 쓰지 않는다**(개발·CI·운영 모두 네이티브, [ADR-0007](docs/adr/0007-no-docker-windows-native.md)).

```bash
cp deploy/env.example .env           # 비밀번호를 채운다. .env 는 커밋하지 않는다
cd backend && uv sync                # backend/.venv 생성
cd ../frontend && npm ci
```

## 처음 한 번

```bash
uv run --project backend python scripts/task.py migrate        # DB 마이그레이션(.env 의 DB)
cd backend
uv run python -m ontobot.security.cli create-user --username alice --display-name 앨리스 --role modeler --role user
uv run python -m ontobot.security.cli grant-type --role user --type Facility
```

비밀번호는 대화형으로 입력한다(인자로 받지 않는다).

샘플 데이터와 스파이크(로컬 Ollama 필요, 모델은 [config/llm.yaml](config/llm.yaml)):

```bash
uv run --project backend python scripts/seed_domain_pack.py --pack sample
uv run --project backend python scripts/spike_plan.py --models qwen3:8b
```
 샘플 온톨로지는
[domain-packs/sample/ontology.yaml](domain-packs/sample/ontology.yaml) — 화면의 '임포트'로 넣고 게시한다.

## 자주 쓰는 작업

명령의 정본은 [scripts/task.py](scripts/task.py)다(Windows에는 make가 없으므로 직접 부른다).

```bash
uv run --project backend python scripts/task.py lint        # ruff·mypy·import-linter·eslint·tsc
uv run --project backend python scripts/task.py test-unit
uv run --project backend python scripts/task.py test-int    # .env 의 PostgreSQL 이 필요
uv run --project backend python scripts/task.py dev-api     # http://localhost:8000/healthz
uv run --project backend python scripts/task.py dev-web     # http://localhost:5173
uv run --project backend python scripts/task.py worker      # 동기화·그래프 투영·주기 작업(따로 띄운다)
uv run --project backend python scripts/graph_rebuild.py    # Neo4j 재구축 + 대사(--check 는 대사만)
uv run --project backend python scripts/task.py openapi     # API 가 바뀌면 프론트 타입 재생성(계약 테스트가 확인)
```

## 의존 규칙 (TRD 16.3)

`import-linter`(패키지 단위)와 `backend/tests/unit/test_dependency_rules.py`(파일 단위)가 함께 막는다.
어떤 모듈도 `api`를 import하지 않고, `grounding`은 LLM·DB를 모르며, Neo4j 드라이버는 `graph/client.py`,
LLM SDK는 `llm/adapters/`, object 테이블 읽기는 `objects/reader.py`, 권한 판정은 `security/rbac.py`에서만 한다.
