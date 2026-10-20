"""개발 작업 실행기 — Makefile 과 Windows(make 없음) 양쪽의 단일 정본.

    uv run --project backend python scripts/task.py <작업>
    make <작업>            # Makefile 은 이 스크립트를 부르기만 한다

작업 목록은 TASKS 하나다. 명령을 Makefile 과 여기 두 곳에 적으면 조용히 갈라진다.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
NPM = shutil.which("npm") or "npm"
UV = ["uv", "run", "--project", str(BACKEND)]

TASKS: dict[str, list[tuple[Path, list[str]]]] = {
    "sync": [(BACKEND, ["uv", "sync"]), (FRONTEND, [NPM, "ci"])],
    "lint": [
        (BACKEND, [*UV, "ruff", "check", "."]),
        (ROOT, [*UV, "ruff", "check", "--config", "backend/pyproject.toml", "scripts"]),
        (BACKEND, [*UV, "ruff", "format", "--check", "."]),
        (BACKEND, [*UV, "mypy"]),
        (BACKEND, [*UV, "lint-imports"]),
        (FRONTEND, [NPM, "run", "lint"]),
        (FRONTEND, [NPM, "run", "typecheck"]),
    ],
    "test-unit": [(BACKEND, [*UV, "pytest", "tests/unit", "-q"])],
    "test-int": [(BACKEND, [*UV, "pytest", "tests/integration", "-q", "-m", "integration"])],
    "test-contract": [(BACKEND, [*UV, "pytest", "tests/contract", "-q"])],
    # 평가(TRD 13절). 환경변수의 DB·Neo4j 를 쓴다 — 개발 데이터와 섞이지 않게 평가 전용 DB 를
    # 가리킨다(ONTOBOT_DB_NAME=ontobot_eval, ONTOBOT_NEO4J_URI=테스트 인스턴스).
    # eval-prepare 는 그래프를 다시 만든다.
    "eval-prepare": [
        (BACKEND, [*UV, "alembic", "upgrade", "head"]),
        (ROOT, [*UV, "python", "scripts/seed_domain_pack.py", "--pack", "sample"]),
        (ROOT, [*UV, "python", "scripts/graph_rebuild.py"]),
    ],
    "eval": [(ROOT, [*UV, "python", "scripts/run_eval.py", "--pack", "sample"])],
    "eval-record": [
        (ROOT, [*UV, "python", "scripts/run_eval.py", "--pack", "sample", "--mode", "record"])
    ],
    "eval-replay": [
        (ROOT, [*UV, "python", "scripts/run_eval.py", "--pack", "sample", "--mode", "replay"])
    ],
    "build": [(FRONTEND, [NPM, "run", "build"])],
    "openapi": [
        (ROOT, [*UV, "python", "scripts/export_openapi.py"]),
        (FRONTEND, [NPM, "run", "gen:api"]),
    ],
    "migrate": [(BACKEND, [*UV, "alembic", "upgrade", "head"])],
    "dev-api": [(BACKEND, [*UV, "uvicorn", "ontobot.main:app", "--reload", "--port", "8000"])],
    "dev-web": [(FRONTEND, [NPM, "run", "dev"])],
    "worker": [(BACKEND, [*UV, "python", "-m", "ontobot.jobs.worker"])],
}

# pytest 는 수집된 테스트가 없으면 5를 돌려준다.
# 아직 비어 있는 층을 실패로 보지 않는다.
NO_TESTS_COLLECTED = 5


def main(argv: list[str]) -> int:
    # 한국어 Windows 콘솔(cp949)에서 출력할 수 없는 글자로 작업 전체가 죽지 않게 한다.
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    if len(argv) != 1 or argv[0] not in TASKS:
        print("사용법: task.py <" + "|".join(TASKS) + ">")
        return 2
    for cwd, cmd in TASKS[argv[0]]:
        print(f"$ ({cwd.relative_to(ROOT) or '.'}) {' '.join(cmd)}", flush=True)
        code = subprocess.call(cmd, cwd=cwd)  # noqa: S603 — TASKS 의 고정 명령만 실행
        if code == NO_TESTS_COLLECTED and "pytest" in cmd:
            print("  (수집된 테스트 없음, 건너뜀)")
            continue
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
