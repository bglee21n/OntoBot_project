"""Neo4j 재구축(TRD 5.5 복구) — Neo4j 를 비우고 PostgreSQL 에서 다시 투영한 뒤 대사한다.

    uv run --project backend python scripts/graph_rebuild.py           # 재구축 + 대사
    uv run --project backend python scripts/graph_rebuild.py --check   # 대사만

화면의 '재구축'(POST /api/v1/graph/rebuild)과 같은 함수를 부른다. 워커가 없어도 돈다 — 장애 복구 때
API·워커가 모두 내려가 있을 수 있기 때문이다. ``.env`` 의 ``ONTOBOT_NEO4J_*`` 를 쓴다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from ontobot.db.session import dispose_engine
from ontobot.jobs import handlers


async def main(check_only: bool) -> int:
    try:
        if not check_only:
            counts = await handlers.graph_rebuild({})
            print(f"재구축: 노드 {counts['nodes']} · 링크 {counts['links']}")
        report = await handlers.graph_reconcile({})
    finally:
        await dispose_engine()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="재구축하지 않고 대사만")
    sys.exit(asyncio.run(main(parser.parse_args().check)))
