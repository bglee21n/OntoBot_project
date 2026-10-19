# OntoBot (가칭) — 온톨로지 기반 챗봇 서비스

| 문서 | 위치 |
| --- | --- |
| PRD v0.4 · 검토 의견 | [docs/prd/](docs/prd/) |
| TRD v0.2 | [docs/trd/OntoBot_project-trd.md](docs/trd/OntoBot_project-trd.md) |
| 구현 계획 (MVP P0) | [docs/plan/OntoBot_project-implementation-plan.md](docs/plan/OntoBot_project-implementation-plan.md) |
| 결정 기록 | [docs/adr/](docs/adr/) |

현재 단계: **Phase 0 — 저장소 골격·CI** (코드는 `/healthz`와 의존 규칙 검사까지).

## 개발 환경

- Python 3.12 — 가상환경은 **uv**가 만든다(`backend/.venv`). 전체 고정은 `backend/uv.lock`.
- Node 22 — 프론트 의존성은 `frontend/package-lock.json`에 고정.
- PostgreSQL — 개발 PC의 로컬 인스턴스(`ontobot_db` / 계정 `ontobot`) 또는 `deploy/docker-compose.yml`.
- Neo4j — Phase 2부터 필요하다(`deploy/docker-compose.yml`).

```bash
cp deploy/env.example .env           # 비밀번호를 채운다. .env 는 커밋하지 않는다
cd backend && uv sync                # backend/.venv 생성
cd ../frontend && npm ci
```

## 자주 쓰는 작업

명령의 정본은 [scripts/task.py](scripts/task.py)다(Windows에는 make가 없으므로 직접 부른다).

```bash
uv run --project backend python scripts/task.py lint        # ruff·mypy·import-linter·eslint·tsc
uv run --project backend python scripts/task.py test-unit
uv run --project backend python scripts/task.py test-int    # .env 의 PostgreSQL 이 필요
uv run --project backend python scripts/task.py dev-api     # http://localhost:8000/healthz
uv run --project backend python scripts/task.py dev-web     # http://localhost:5173
```

## 의존 규칙 (TRD 16.3)

`import-linter`(패키지 단위)와 `backend/tests/unit/test_dependency_rules.py`(파일 단위)가 함께 막는다.
어떤 모듈도 `api`를 import하지 않고, `grounding`은 LLM·DB를 모르며, Neo4j 드라이버는 `graph/client.py`,
LLM SDK는 `llm/adapters/`, object 테이블 읽기는 `objects/reader.py`, 권한 판정은 `security/rbac.py`에서만 한다.
