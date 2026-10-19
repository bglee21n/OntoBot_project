"""OpenAPI 스키마를 frontend/src/api/openapi.json 으로 내보낸다(TRD 16.4).

프론트 타입(schema.d.ts)은 이 파일에서 openapi-typescript 로 만든다 — 손으로 고치지 않는다.
tests/contract/test_openapi_snapshot.py 가 커밋된 파일이 현재 API와 같은지 확인한다.

    uv run --project backend python scripts/task.py openapi
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend" / "src" / "api" / "openapi.json"


def render() -> str:
    from ontobot.main import create_app

    spec = create_app().openapi()
    return json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    OUT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
