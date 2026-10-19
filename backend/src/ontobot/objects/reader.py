"""객체 읽기 — 유일한 읽기 경로(TRD 6.2, 16.3-5).

여기가 지키는 것 둘:

1. **삭제 표시된 객체는 보이지 않는다.** ``deleted_at IS NULL`` 을 호출부마다 적게 두면 언젠가
   한 곳이 빠진다. 그래서 컴파일러는 조건 조각만 만들고 ``FROM object`` 와 타입·삭제 조건은
   이 파일만 붙인다.
2. **게시 버전에 선언된 속성만 내보낸다.** 버전에서 빠진 속성의 값이 jsonb 에 남아 있어도
   보이지 않는다(TRD 6.1). 호출부가 선언된 속성 목록을 넘긴다.

MVP 는 원천 값을 그대로 돌려준다. P1 에서 편집 레이어, P3 에서 시나리오를 여기서 덮는다 —
호출부는 바뀌지 않는다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ObjectRow:
    type: str
    pk: str
    properties: dict[str, Any]
    synced_at: datetime | None


@dataclass(frozen=True)
class CompiledQuery:
    """``query.compiler`` 가 만드는 조회 명세. SQL 조각은 별칭 ``o``(object) 위의 식이다.

    조각에는 값이 들어가지 않는다 — 값과 속성 키는 전부 ``params`` 의 바인드다.
    """

    object_type: str
    declared_properties: Sequence[str]
    where: Sequence[str] = ()
    # 집계 질의면 (별칭, 식) 목록. 비면 객체 행 조회다.
    aggregate_select: Sequence[tuple[str, str]] = ()
    group_by: Sequence[str] = ()
    order_by: Sequence[str] = ()
    limit: int | None = None
    params: dict[str, Any] = field(default_factory=dict)


def _project(props: dict[str, Any], declared: Sequence[str]) -> dict[str, Any]:
    return {k: props[k] for k in declared if k in props}


async def resolve(
    db: AsyncSession, object_type: str, pks: Sequence[str], declared: Sequence[str]
) -> dict[str, ObjectRow]:
    """식별자로 객체를 읽는다(근거 카드·대화 맥락 재조회)."""
    if not pks:
        return {}
    rows = await db.execute(
        text(
            "SELECT pk, properties, synced_at FROM object "
            "WHERE type = :t AND pk = ANY(:pks) AND deleted_at IS NULL"
        ),
        {"t": object_type, "pks": list(pks)},
    )
    return {
        r.pk: ObjectRow(object_type, r.pk, _project(r.properties, declared), r.synced_at)
        for r in rows
    }


def render_sql(q: CompiledQuery) -> str:
    """실행할 SQL 전문. 트레이스(마스킹 전 값은 바인드라 SQL 에 없다)와 테스트가 함께 쓴다."""
    where = " AND ".join(["o.type = :__type", "o.deleted_at IS NULL", *q.where])
    if q.aggregate_select:
        select = ", ".join(f"{expr} AS {alias}" for alias, expr in q.aggregate_select)
    else:
        select = "o.pk, o.properties, o.synced_at"
    sql = f"SELECT {select} FROM object o WHERE {where}"  # noqa: S608 — 조각은 컴파일러의 고정 틀
    if q.group_by:
        sql += " GROUP BY " + ", ".join(q.group_by)
    if q.order_by:
        sql += " ORDER BY " + ", ".join(q.order_by)
    if q.limit is not None:
        sql += " LIMIT :__limit"
    return sql


async def run_query(db: AsyncSession, q: CompiledQuery) -> list[ObjectRow] | list[dict[str, Any]]:
    params = {**q.params, "__type": q.object_type}
    if q.limit is not None:
        params["__limit"] = q.limit
    result = await db.execute(text(render_sql(q)), params)
    if q.aggregate_select:
        return [dict(r._mapping) for r in result]
    return [
        ObjectRow(q.object_type, r.pk, _project(r.properties, q.declared_properties), r.synced_at)
        for r in result
    ]


# ---------------------------------------------------------------- 그래프 대사·재구축용 (TRD 5.5)


async def object_keys(db: AsyncSession, object_type: str) -> list[str]:
    """살아 있는 객체의 pk 를 바이트 순서로. Neo4j 쪽과 같은 순서로 이어 붙여 해시를 비교한다
    (COLLATE "C" = UTF-8 바이트 순서 = 파이썬 문자열 정렬 순서)."""
    return list(
        await db.scalars(
            text(
                "SELECT pk FROM object WHERE type = :t AND deleted_at IS NULL "
                'ORDER BY pk COLLATE "C"'
            ),
            {"t": object_type},
        )
    )


async def link_keys(db: AsyncSession, link_type: str) -> list[str]:
    return list(
        await db.scalars(
            text(
                "SELECT from_pk || chr(31) || to_pk AS k FROM link "
                "WHERE link_type = :l AND deleted_at IS NULL "
                'ORDER BY (from_pk || chr(31) || to_pk) COLLATE "C"'
            ),
            {"l": link_type},
        )
    )


async def iter_titles(
    db: AsyncSession, object_type: str, title_property: str | None, *, chunk: int = 2000
) -> AsyncIterator[list[tuple[str, str | None]]]:
    """재구축용 (pk, 제목) 묶음. 제목 속성이 PII 면 호출부가 None 을 넘긴다."""
    last = ""
    while True:
        rows = (
            await db.execute(
                text(
                    "SELECT pk, CASE WHEN CAST(:tp AS text) IS NULL THEN NULL "
                    "ELSE properties ->> CAST(:tp AS text) END AS title FROM object "
                    'WHERE type = :t AND deleted_at IS NULL AND pk COLLATE "C" > :last '
                    'ORDER BY pk COLLATE "C" LIMIT :n'
                ),
                {"t": object_type, "tp": title_property, "last": last, "n": chunk},
            )
        ).all()
        if not rows:
            return
        yield [(r.pk, r.title) for r in rows]
        last = rows[-1].pk


async def iter_links(
    db: AsyncSession, link_type: str, *, chunk: int = 5000
) -> AsyncIterator[list[tuple[str, str]]]:
    last_id = 0
    while True:
        rows = (
            await db.execute(
                text(
                    "SELECT id, from_pk, to_pk FROM link "
                    "WHERE link_type = :l AND deleted_at IS NULL AND id > :last "
                    "ORDER BY id LIMIT :n"
                ),
                {"l": link_type, "last": last_id, "n": chunk},
            )
        ).all()
        if not rows:
            return
        yield [(r.from_pk, r.to_pk) for r in rows]
        last_id = rows[-1].id
