"""OpenAPI 스냅샷 — 커밋된 frontend/src/api/openapi.json 이 현재 API와 같은지(TRD 19절 계약).

다르면 프론트 타입(schema.d.ts)이 옛 API를 가리킨다. 화면은 컴파일되는데 실행하면 필드가
없는, 오류 없이 틀어지는 상태가 된다. 고치는 법: ``scripts/task.py openapi``.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _render() -> str:
    spec = importlib.util.spec_from_file_location(
        "export_openapi", ROOT / "scripts" / "export_openapi.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rendered: str = module.render()
    return rendered


def test_committed_openapi_is_current() -> None:
    committed = (ROOT / "frontend" / "src" / "api" / "openapi.json").read_text(encoding="utf-8")
    assert committed == _render(), (
        "API가 바뀌었습니다 — `scripts/task.py openapi` 로 다시 생성하세요"
    )


def test_generated_ts_types_exist() -> None:
    assert (ROOT / "frontend" / "src" / "api" / "schema.d.ts").is_file()
