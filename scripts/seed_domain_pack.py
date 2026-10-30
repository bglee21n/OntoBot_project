"""도메인 팩 시드 — 온톨로지를 게시하고 seed/*.csv 를 객체로 적재한다(개발·스파이크·평가 전용).

    uv run --project backend python scripts/seed_domain_pack.py --pack sample

- 게시 버전의 정의가 팩의 ontology.yaml 과 다르면(해시 비교) 새 draft 로 임포트해 게시한다.
  같으면 그대로 둔다 — 시드를 여러 번 돌려도 버전이 늘지 않는다.
- CSV 파일 이름은 타입 api_name 의 소문자다(Facility → facility.csv). 값은 속성 자료형으로
  바꿔 넣는다(int·decimal 은 숫자, 빈 칸은 null).
- 동기화(Phase 2)를 거치지 않고 ``objects.store`` 로 직접 넣는다. ``fk_property`` 링크는 객체의
  FK 값으로 만든다(Neo4j 에는 ``scripts/graph_rebuild.py`` 로 투영한다 — 평가의 탐색 문항이 쓴다).

- ``--grant-read user`` 는 데이터 그룹 ``user`` 의 읽기 권한에 팩의 타입을 **더한다**(그룹이 없으면
  만든다 — 설치 확인용, 새로 설치한 DB 에는 아무 그룹도 읽기 권한이 없어 질의가 모두 ``forbidden``
  이다). 감사 로그에 남는다. Phase 14 부터 데이터 권한은 역할이 아니라 그룹이 준다.

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
from ontobot.objects.store import seed_fk_links, upsert_objects
from ontobot.ontology import importer, registry, versioning
from ontobot.ontology.models import PropertyDef
from ontobot.security import admin, rbac

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


async def grant_read(group_name: str, types: list[str], *, edit: bool = False) -> None:
    async with session_scope() as db:
        for t in types:
            await admin.grant_read(db, group_name, t, via="seed", edit=edit)
        readable = await rbac.readable_types_for_group(db, group_name)
        await db.commit()
    print(f"  그룹 {group_name} 읽기 권한: {', '.join(sorted(readable))}")


async def seed(pack: str, grant: list[str], grant_edit: list[str]) -> None:
    pack_dir = ROOT / "domain-packs" / pack
    doc = importer.parse(
        (pack_dir / "ontology.yaml").read_text(encoding="utf-8"), "yaml"
    )
    async with session_scope() as db:
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
            unknown = (
                set(rows[0]) - {p.api_name for p in t.properties} if rows else set()
            )
            if unknown:
                sys.exit(f"{path.name}: 정의에 없는 열 {sorted(unknown)}")
            items = [
                (
                    row[t.pk_property],
                    {
                        p.api_name: _value(p, row.get(p.api_name, ""))
                        for p in t.properties
                    },
                )
                for row in rows
            ]
            changed = await upsert_objects(db, t.api_name, items)
            print(f"  {t.api_name}: {len(items)}건 (바뀐 행 {changed})")
        for lt in doc.link_types:
            if lt.source.kind == "fk_property" and lt.source.property:
                made = await seed_fk_links(
                    db, lt.api_name, lt.from_type, lt.source.property
                )
                print(f"  링크 {lt.api_name}: 새로 {made}건")
        await db.commit()
    types = [t.api_name for t in doc.object_types]
    for name in grant:
        await grant_read(name, types)
    for name in grant_edit:
        # 편집 = Action 요청(Phase 14). 읽기도 함께 준다.
        await grant_read(name, types, edit=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", default="sample")
    parser.add_argument(
        "--grant-read",
        action="append",
        default=[],
        metavar="GROUP",
        help="이 데이터 그룹의 읽기 권한에 팩의 타입을 더한다(없으면 만든다, 여러 번)",
    )
    parser.add_argument(
        "--grant-edit",
        action="append",
        default=[],
        metavar="GROUP",
        help="이 데이터 그룹에 팩의 타입 읽기·편집(= Action 요청)을 준다(없으면 만든다, 여러 번)",
    )
    args = parser.parse_args()

    async def run() -> None:
        try:
            await seed(args.pack, args.grant_read, args.grant_edit)
        finally:
            await dispose_engine()

    asyncio.run(run())


if __name__ == "__main__":
    main()
