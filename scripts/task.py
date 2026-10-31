"""개발 작업 실행기 — Makefile 과 Windows(make 없음) 양쪽의 단일 정본.

    uv run --project backend python scripts/task.py <작업>
    make <작업>            # Makefile 은 이 스크립트를 부르기만 한다

작업 목록은 TASKS 하나다. 명령을 Makefile 과 여기 두 곳에 적으면 조용히 갈라진다.

통합 테스트·평가는 컨테이너 저장소(deploy/docker/compose.test.yaml)를 쓴다(ADR-0013). 접속값은 그
compose 의 고정 테스트 값이고 환경변수로 넘긴다 — 루트 .env(개발 DB)를 가리키지 않는다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
DEPLOYER = ROOT / "deployer"
DEVCONSOLE = ROOT / "devconsole"
NPM = shutil.which("npm") or "npm"
UV = ["uv", "run", "--project", str(BACKEND)]
UVD = ["uv", "run", "--project", str(DEPLOYER)]
UVC = ["uv", "run", "--project", str(DEVCONSOLE)]
TEST_COMPOSE = [
    "docker",
    "compose",
    "-f",
    str(ROOT / "deploy" / "docker" / "compose.test.yaml"),
]
DEV_COMPOSE = [
    "docker",
    "compose",
    "-f",
    str(ROOT / "deploy" / "docker" / "compose.dev.yaml"),
    "--env-file",
    str(ROOT / ".env"),
]

# compose.test.yaml 의 고정 테스트 값(비밀이 아니다). 환경변수가 .env 보다 앞선다.
TEST_ENV = {
    "ONTOBOT_DB_HOST": "127.0.0.1",
    "ONTOBOT_DB_PORT": os.environ.get("ONTOBOT_TEST_PG_PORT", "55432"),
    "ONTOBOT_DB_USER": "ontobot",
    "ONTOBOT_DB_PASSWORD": "ontobot-test",
    "ONTOBOT_TEST_NEO4J_URI": "bolt://127.0.0.1:"
    + os.environ.get("ONTOBOT_TEST_NEO4J_PORT", "57687"),
    "ONTOBOT_TEST_NEO4J_USER": "neo4j",
    "ONTOBOT_TEST_NEO4J_PASSWORD": "ontobot-test-graph",
    "PYTHONUTF8": "1",
}
# 평가 전용 DB 와 테스트 Neo4j — 개발 데이터와 섞이지 않고 카세트 키가 CI 와 같다.
EVAL_ENV = {
    **TEST_ENV,
    "ONTOBOT_DB_NAME": "ontobot_eval",
    "ONTOBOT_NEO4J_URI": TEST_ENV["ONTOBOT_TEST_NEO4J_URI"],
    "ONTOBOT_NEO4J_USER": "neo4j",
    "ONTOBOT_NEO4J_PASSWORD": TEST_ENV["ONTOBOT_TEST_NEO4J_PASSWORD"],
}
TEST_UP = (ROOT, [*TEST_COMPOSE, "up", "-d", "--wait"])

Step = tuple[Path, list[str]] | tuple[Path, list[str], dict[str, str]]

TASKS: dict[str, list[Step]] = {
    "sync": [
        (BACKEND, ["uv", "sync"]),
        (DEPLOYER, ["uv", "sync"]),
        (DEVCONSOLE, ["uv", "sync"]),
        (FRONTEND, [NPM, "ci"]),
    ],
    "lint": [
        (BACKEND, [*UV, "ruff", "check", "."]),
        (ROOT, [*UV, "ruff", "check", "--config", "backend/pyproject.toml", "scripts"]),
        (BACKEND, [*UV, "ruff", "format", "--check", "."]),
        (BACKEND, [*UV, "mypy"]),
        (BACKEND, [*UV, "lint-imports"]),
        (DEPLOYER, [*UVD, "ruff", "check", "."]),
        (DEPLOYER, [*UVD, "ruff", "format", "--check", "."]),
        (DEPLOYER, [*UVD, "mypy"]),
        (DEVCONSOLE, [*UVC, "ruff", "check", ".", "../opsjobs"]),
        (DEVCONSOLE, [*UVC, "ruff", "format", "--check", ".", "../opsjobs"]),
        (DEVCONSOLE, [*UVC, "mypy"]),
        (FRONTEND, [NPM, "run", "lint"]),
        (FRONTEND, [NPM, "run", "typecheck"]),
    ],
    "test-unit": [
        (BACKEND, [*UV, "pytest", "tests/unit", "-q"]),
        (DEPLOYER, [*UVD, "pytest", "-q"]),
        (DEVCONSOLE, [*UVC, "pytest", "-q"]),
    ],
    # 컨테이너가 이미 떠 있으면 그대로 쓴다. 다 쓴 뒤 지우려면 test-down.
    "test-int": [
        TEST_UP,
        (
            BACKEND,
            [*UV, "pytest", "tests/integration", "-q", "-m", "integration"],
            TEST_ENV,
        ),
    ],
    "test-up": [TEST_UP],
    "test-down": [(ROOT, [*TEST_COMPOSE, "down", "-v"])],
    "test-contract": [(BACKEND, [*UV, "pytest", "tests/contract", "-q"])],
    # 평가(TRD 13절). 컨테이너의 평가 전용 DB(ontobot_eval)와 테스트 Neo4j 를 쓴다(EVAL_ENV).
    # eval-prepare 는 그래프를 다시 만든다. 모델은 config/llm.yaml 의 Ollama 를 부른다.
    "eval-prepare": [
        TEST_UP,
        (BACKEND, [*UV, "alembic", "upgrade", "head"], EVAL_ENV),
        (
            ROOT,
            [
                *UV,
                "python",
                "scripts/seed_domain_pack.py",
                "--pack",
                "sample",
                "--documents",
                "sample_documents",
            ],
            EVAL_ENV,
        ),
        (ROOT, [*UV, "python", "scripts/graph_rebuild.py"], EVAL_ENV),
    ],
    "eval": [
        (ROOT, [*UV, "python", "scripts/run_eval.py", "--pack", "sample"], EVAL_ENV)
    ],
    "eval-record": [
        (
            ROOT,
            [
                *UV,
                "python",
                "scripts/run_eval.py",
                "--pack",
                "sample",
                "--mode",
                "record",
            ],
            EVAL_ENV,
        )
    ],
    "eval-replay": [
        (
            ROOT,
            [
                *UV,
                "python",
                "scripts/run_eval.py",
                "--pack",
                "sample",
                "--mode",
                "replay",
            ],
            EVAL_ENV,
        )
    ],
    "build": [(FRONTEND, [NPM, "run", "build"])],
    "openapi": [
        (ROOT, [*UV, "python", "scripts/export_openapi.py"]),
        (FRONTEND, [NPM, "run", "gen:api"]),
    ],
    "migrate": [(BACKEND, [*UV, "alembic", "upgrade", "head"])],
    # 개발 저장소(PostgreSQL·Neo4j)를 컨테이너로. 루트 .env 의 비밀번호·포트를 쓴다.
    "dev-up": [(ROOT, [*DEV_COMPOSE, "up", "-d", "--wait"])],
    "dev-down": [(ROOT, [*DEV_COMPOSE, "down"])],
    "image": [
        (
            ROOT,
            [
                "docker",
                "build",
                "-f",
                "deploy/docker/app.Dockerfile",
                "-t",
                "ontobot-app:dev",
                ".",
            ],
        ),
        (
            ROOT,
            [
                "docker",
                "build",
                "-f",
                "deploy/docker/deployer.Dockerfile",
                "-t",
                "ontobot-deployer:dev",
                ".",
            ],
        ),
    ],
    # 개발 콘솔(ADR-0015) — 127.0.0.1:8200. 화면 빌드 후 띄운다.
    "devconsole": [
        (FRONTEND, [NPM, "run", "build:devconsole"]),
        (ROOT, [*UVC, "python", "-m", "ontobot_devconsole"]),
    ],
    "dev-api": [
        (BACKEND, [*UV, "uvicorn", "ontobot.main:app", "--reload", "--port", "8000"])
    ],
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
    for step in TASKS[argv[0]]:
        cwd, cmd = step[0], step[1]
        extra = step[2] if len(step) == 3 else {}  # type: ignore[misc]
        print(f"$ ({cwd.relative_to(ROOT) or '.'}) {' '.join(cmd)}", flush=True)
        # uv run 은 --project 의 가상환경을 고른다 — 부모의 VIRTUAL_ENV 를 넘기면 경고만 낸다.
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"} | extra
        code = subprocess.call(cmd, cwd=cwd, env=env)  # noqa: S603 — 고정 명령
        if code == NO_TESTS_COLLECTED and "pytest" in cmd:
            print("  (수집된 테스트 없음, 건너뜀)")
            continue
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
