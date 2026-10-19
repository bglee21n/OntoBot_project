"""TRD 16.3 의존 규칙 — import-linter가 못 하는 세밀한 규칙을 소스로 막는다.

| 규칙 | 어디서 막나 |
| --- | --- |
| 1. 어떤 모듈도 api를 import하지 않는다 | import-linter (pyproject) |
| 2. grounding은 llm·db·graph·sync 금지 | import-linter |
| 3. Neo4j 드라이버는 graph/client.py 에서만 | import-linter(패키지 밖) + 여기(패키지 안) |
| 4. LLM SDK는 llm/adapters/ 에서만 | import-linter(패키지 밖) + 여기(패키지 안) |
| 5. object 테이블 읽기는 objects/reader.py 에서만(쓰기·병합 판별은 objects/store.py) | 여기 |
| 6. 권한 판정 함수는 security/rbac.py 에서만 | 여기 |

import-linter 자체가 무력해지지 않았는지도 함께 확인한다(위반 샘플을 실제로 잡는가).
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SRC = BACKEND / "src" / "ontobot"

LLM_SDKS = {"anthropic", "openai", "ollama"}
# object 테이블 ORM 모델의 이름. Phase 2에서 db/models/objects.py 에 이 이름으로 만든다.
OBJECT_MODEL = "ObjectRecord"
# 권한 판정으로 보이는 함수 이름. 이 이름의 함수는 security/rbac.py 에만 있어야 한다.
PERMISSION_FUNCS = re.compile(
    r"^(readable_types|can_read|has_permission|is_allowed|check_permission)$"
)
# object 표를 SQL 문자열로 직접 다루는 모양(읽기·쓰기 모두). objects 패키지 밖에서는 금지다.
RAW_OBJECT_SQL = re.compile(r'\b(?:FROM|JOIN|UPDATE|INTO)\s+"?object"?(?=\s|$|;|\))', re.I)


def _modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_source_tree_is_scanned() -> None:
    # 경로가 어긋나 아무것도 훑지 않으면 아래 테스트가 전부 거짓 초록이 된다.
    assert len(_modules()) > 10


def test_neo4j_driver_only_in_graph_client() -> None:
    offenders = [
        _rel(p)
        for p in _modules()
        if "neo4j" in _imported_roots(ast.parse(p.read_text(encoding="utf-8")))
        and _rel(p) != "graph/client.py"
    ]
    assert offenders == [], f"neo4j는 graph/client.py 에서만 import한다: {offenders}"


def test_llm_sdk_only_in_adapters() -> None:
    offenders = [
        _rel(p)
        for p in _modules()
        if LLM_SDKS & _imported_roots(ast.parse(p.read_text(encoding="utf-8")))
        and not _rel(p).startswith("llm/adapters/")
    ]
    assert offenders == [], f"LLM SDK는 llm/adapters/ 에서만 import한다: {offenders}"


def test_object_table_read_only_in_reader() -> None:
    # 모델 정의(db/models)와 쓰기(objects/store.py)는 이름을 알아야 한다. 읽기(select)는 reader만.
    allowed_name = {"objects/reader.py", "objects/store.py"}
    offenders = []
    for p in _modules():
        rel = _rel(p)
        text = p.read_text(encoding="utf-8")
        if rel.startswith("db/models/"):
            continue
        uses_model = re.search(rf"\b{OBJECT_MODEL}\b", text) is not None
        if uses_model and rel not in allowed_name:
            offenders.append(f"{rel}: {OBJECT_MODEL} 사용")
        if rel == "objects/store.py" and re.search(r"\bselect\s*\(", text):
            offenders.append(f"{rel}: store는 쓰기만 한다 — 읽기는 reader.resolve()")
        if RAW_OBJECT_SQL.search(text) and not rel.startswith("objects/"):
            offenders.append(
                f"{rel}: object 표를 SQL 로 직접 다룸 — objects.reader/store 를 거칠 것"
            )
    assert offenders == [], offenders


def test_permission_functions_only_in_rbac() -> None:
    offenders = []
    for p in _modules():
        if _rel(p) == "security/rbac.py":
            continue
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and PERMISSION_FUNCS.match(
                node.name
            ):
                offenders.append(f"{_rel(p)}:{node.lineno} {node.name}")
    assert offenders == [], f"권한 판정은 security/rbac.py 하나에만 둔다: {offenders}"


# ---------------------------------------------------------------- import-linter


def _lint_imports() -> str:
    exe = shutil.which("lint-imports", path=str(Path(sys.executable).parent))
    if exe is None:
        pytest.skip("import-linter 미설치")
    return exe


def test_import_contracts_hold() -> None:
    res = subprocess.run(
        [_lint_imports()],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**_env(), "PYTHONPATH": str(BACKEND / "src")},
    )
    assert res.returncode == 0, res.stdout + res.stderr


def test_import_linter_catches_a_violation(tmp_path: Path) -> None:
    # 계약이 조용히 무력해지면(이름 오타, 패키지 경로 변경) 위 테스트는 영원히 초록이다.
    # 같은 모양의 위반 샘플을 만들어 실제로 실패하는지 본다.
    pkg = tmp_path / "fakebot"
    (pkg / "grounding").mkdir(parents=True)
    (pkg / "llm").mkdir()
    # encoding을 생략하면 Windows 기본 인코딩(cp949)으로 써져 import-linter가 디코딩에 실패하고,
    # 계약 위반이 아닌 '도구 오류'로 0이 아닌 코드를 내 이 테스트가 거짓 초록이 된다.
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "llm" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "grounding" / "__init__.py").write_text("from fakebot import llm\n", encoding="utf-8")
    cfg = tmp_path / "contracts.toml"
    cfg.write_text(
        textwrap.dedent("""
        [tool.importlinter]
        root_package = "fakebot"

        [[tool.importlinter.contracts]]
        name = "grounding must not import llm"
        type = "forbidden"
        source_modules = ["fakebot.grounding"]
        forbidden_modules = ["fakebot.llm"]
    """)
    )
    res = subprocess.run(
        [_lint_imports(), "--config", str(cfg), "--no-cache"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**_env(), "PYTHONPATH": str(tmp_path)},
    )
    assert res.returncode != 0, res.stdout + res.stderr
    # 도구 오류가 아니라 계약 위반으로 실패했는지 확인한다.
    assert "1 broken" in res.stdout, res.stdout + res.stderr
    assert "fakebot.grounding -> fakebot.llm" in res.stdout


def _env() -> dict[str, str]:
    import os

    # 한국어 Windows 콘솔 코드페이지(cp949)로 출력하면 utf-8 디코딩이 깨진다.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env
