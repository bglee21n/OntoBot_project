"""작업 목록 — 화면이 고를 수 있는 것은 여기 있는 것뿐이다(임의 명령을 받지 않는다).

``task.py`` 작업은 ``TASKS`` 에 실제로 있는 것만 보인다 — task.py 에서 이름을 바꾸거나 지우면
화면에서도
사라진다. 실행은 언제나 ``uv run --project backend python scripts/task.py <이름>``(문서에 적힌 그
명령)이다.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

# 화면에 보일 task.py 작업 — (분류, 이름, 설명). 개발 서버(dev-api·dev-web·worker)는 services 가
# 맡는다.
TASK_INFO: dict[str, tuple[str, str, str]] = {
    "lint": ("check", "lint", "ruff·format·mypy·import-linter(+콘솔)·eslint·tsc"),
    "test-unit": ("check", "단위 테스트", "백엔드·배포 콘솔 단위 테스트"),
    "test-contract": (
        "check",
        "계약 테스트",
        "OpenAPI 스냅샷·SSE 이벤트 — API 를 바꾸면 openapi 먼저",
    ),
    "test-int": ("check", "통합 테스트", "compose.test 컨테이너(없으면 띄운다)"),
    "openapi": ("check", "OpenAPI 재생성", "API 를 바꾸면 반드시 — 프론트 타입 재생성"),
    "build": ("check", "프론트 빌드", "frontend/dist"),
    "eval-prepare": ("eval", "평가 준비", "평가 DB 마이그레이션·샘플 시드·그래프 재구축"),
    "eval-replay": ("eval", "평가 재생", "녹화 응답으로 게이트(CI 와 같다)"),
    "eval": ("eval", "실제 모델 평가", "Ollama 로 게이트 — 카세트는 바꾸지 않는다"),
    "eval-record": (
        "eval",
        "실제 모델 평가 + 녹화",
        "게이트 통과 시 카세트 갱신 — 결과를 커밋한다",
    ),
    "image": ("build", "이미지 빌드", "ontobot-app:dev·ontobot-deployer:dev"),
    "migrate": ("env", "개발 DB 마이그레이션", ".env 의 DB 에 alembic upgrade head"),
    "dev-up": ("env", "개발 저장소 켜기", "compose.dev(PostgreSQL·Neo4j)"),
    "dev-down": ("env", "개발 저장소 끄기", "compose.dev"),
    "test-up": ("env", "테스트 컨테이너 켜기", "compose.test"),
    "test-down": ("env", "테스트 컨테이너 지우기", "compose.test(데이터도 지운다)"),
    "sync": ("env", "의존성 동기화", "uv sync·npm ci"),
}
# 화면의 "전체 검사" — 위에서 아래로, 실패하면 멈춘다.
CHECK_ALL = ("lint", "test-unit", "test-contract", "test-int")
# 실제 모델(Ollama)이 필요한 작업 — 시작 전에 모델이 있는지 본다.
NEEDS_MODEL = ("eval", "eval-record")

BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")


class CatalogError(Exception):
    pass


def load_task_module(root: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("ontobot_task", root / "scripts" / "task.py")
    if spec is None or spec.loader is None:
        raise CatalogError("scripts/task.py 를 찾지 못했습니다")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # 정의만 있다(실행은 main 에서만)
    return mod


@dataclass(frozen=True)
class TaskEntry:
    name: str
    group: str
    label: str
    description: str
    needs_model: bool


def available_tasks(root: Path) -> list[TaskEntry]:
    names = set(getattr(load_task_module(root), "TASKS", {}))
    return [
        TaskEntry(n, g, label, desc, n in NEEDS_MODEL)
        for n, (g, label, desc) in TASK_INFO.items()
        if n in names
    ]


def uv() -> str:
    found = shutil.which("uv")
    if found is None:
        raise CatalogError("uv 를 찾지 못했습니다(PATH)")
    return found


def task_command(root: Path, name: str) -> list[str]:
    if name not in {t.name for t in available_tasks(root)}:
        raise CatalogError(f"모르는 작업: {name}")
    return [uv(), "run", "--project", str(root / "backend"), "python", "scripts/task.py", name]


def script_command(root: Path, script: str, *args: str) -> list[str]:
    """scripts/ 의 파이썬 스크립트(build_bundle·bundle_sign) — 백엔드 가상환경으로."""
    if script not in ("build_bundle.py", "bundle_sign.py"):
        raise CatalogError(f"모르는 스크립트: {script}")
    return [uv(), "run", "--project", str(root / "backend"), "python", f"scripts/{script}", *args]


def check_branch(name: str) -> str:
    if not BRANCH_RE.match(name) or ".." in name or name.endswith((".lock", "/", ".")):
        raise CatalogError("브랜치 이름은 영문·숫자·._/- 만(예: feature/phase-12)")
    return name


def check_tag(name: str) -> str:
    if not TAG_RE.match(name):
        raise CatalogError("릴리스 태그는 v0.1.0 형식이어야 합니다")
    return name
