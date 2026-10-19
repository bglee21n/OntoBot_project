"""도메인 팩 시드 — 온톨로지를 게시하고 seed/*.csv 를 객체로 적재한다(개발·스파이크·평가 전용).

    uv run --project backend python scripts/seed_domain_pack.py --pack sample

- 게시 버전의 정의가 팩의 ontology.yaml 과 다르면(해시 비교) 새 draft 로 임포트해 게시한다.
  같으면 그대로 둔다 — 시드를 여러 번 돌려도 버전이 늘지 않는다.
- CSV 파일 이름은 타입 api_name 의 소문자다(Facility → facility.csv). 값은 속성 자료형으로
  바꿔 넣는다(int·decimal 은 숫자, 빈 칸은 null).
- 동기화(Phase 2)를 거치지 않고 ``objects.store`` 로 직접 넣는다. 링크는 넣지 않는다.

``.env`` 의 DB 에 쓴다. 운영 DB 를 가리키지 않게 주의할 것 — 이 스크립트는 합성 데이터를 넣는다.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from ontobot.db.session import dispose_engine, session_scope
from ontobot.objects.store import upsert_objects
from ontobot.ontology import importer, registry, versioning
from ontobot.ontology.models import PropertyDef

ROOT = Path(__file__).resolve().parents[1]


def _value(prop: PropertyDef, text: str) -> Any:
    if text == "":
        return None
    if prop.data_type == "int":
        return int(text)
    if prop.data_type == "decimal":
        d = Decimal(text)
        return int(d) if d == d.to_integral_value() else float(d)
    if prop.data_type == "bool":
        return text.lower() in {"true", "1", "y", "예"}
    return text


async def seed(pack: str) -> None:
    pack_dir = ROOT / "domain-packs" / pack
    doc = importer.parse((pack_dir / "ontology.yaml").read_text(encoding="utf-8"), "yaml")
    async for db in session_scope():
        current = await registry.reload(db)
        if current is None or current.content_hash != doc.content_hash():
            draft = await versioning.create_draft(db, actor_id=None, parent_id=None)
            await versioning.replace_document(db, draft.id, doc, actor_id=None)
            await versioning.publish(db, draft.id, actor_id=None)
            await db.commit()
            current = await registry.reload(db)
            print(f"게시: 버전 {current.version_no if current else '?'}")
        else:
            print(f"게시 버전 {current.version_no} 이 팩과 같습니다 — 그대로 둡니다")

        for t in doc.object_types:
            path = pack_dir / "seed" / f"{t.api_name.lower()}.csv"
            if not path.is_file():
                print(f"  {t.api_name}: 시드 파일 없음 ({path.name})")
                continue
            with path.open(encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
            unknown = set(rows[0]) - {p.api_name for p in t.properties} if rows else set()
            if unknown:
                sys.exit(f"{path.name}: 정의에 없는 열 {sorted(unknown)}")
            items = [
                (
                    row[t.pk_property],
                    {p.api_name: _value(p, row.get(p.api_name, "")) for p in t.properties},
                )
                for row in rows
            ]
            changed = await upsert_objects(db, t.api_name, items)
            print(f"  {t.api_name}: {len(items)}건 (바뀐 행 {changed})")
        await db.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", default="sample")
    args = parser.parse_args()

    async def run() -> None:
        try:
            await seed(args.pack)
        finally:
            await dispose_engine()

    asyncio.run(run())


if __name__ == "__main__":
    main()
