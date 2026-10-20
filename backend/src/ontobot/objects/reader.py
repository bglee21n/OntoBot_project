"""객체 읽기 — 유일한 읽기 경로(TRD 6.2, 16.3-5).

여기가 지키는 것 셋:

1. **삭제 표시된 객체는 보이지 않는다.** ``deleted_at IS NULL`` 을 호출부마다 적게 두면 언젠가
   한 곳이 빠진다. 그래서 컴파일러는 조건 조각만 만들고 ``FROM`` 과 타입·삭제 조건은
   이 파일만 붙인다.
2. **게시 버전에 선언된 속성만 내보낸다.** 버전에서 빠진 속성의 값이 jsonb 에 남아 있어도
   보이지 않는다(TRD 6.1). 호출부가 선언된 속성 목록을 넘긴다.
3. **보이는 값 = 활성 편집 값, 없으면 원천 값**(편집 레이어, TRD 6.3·17.1-1). 근거 카드만이
   아니라 조회 조건·집계·참조 값 대조도 같은 값을 본다 — 그래서 객체를 읽는 SQL 은 모두 ``object``
   가 아니라 편집을 덮은 파생 행(``_OBJECTS``, 별칭 ``o``)에서 읽는다. 카드는 편집 값인데 필터는
   원천 값이면 "위험등급 4 이상" 질문에서 편집으로 4가 된 시설이 조용히 빠진다.

P3 의 시나리오도 여기서 덮는다 — 호출부는 바뀌지 않는다. 그래프 대사·재구축용 읽기(아래 묶음)는
원천 값을 본다: Neo4j 에는 pk·제목만 있고, Action 은 pk·FK·제목 속성을 바꾸지 못한다(TRD 17.1-3).
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
    # 보이는 값이 편집 값인 속성(선언된 것만). 편집자·원천 값 같은 자세한 것은 ``objects.edits``.
    edited: frozenset[str] = frozenset()


# 편집을 덮은 객체 행. ``properties`` 는 보이는 값, ``edited`` 는 활성 편집 묶음(없으면 NULL).
# 타입 조건을 안쪽에도 걸어 편집 대조를 그 타입으로 좁힌다. 편집이 있는 타입에서는 속성 표현식
# 인덱스가 덮인 값에는 쓰이지 않는다 — 규모가 커져 문제가 되면 ADR-0003 과 함께 다시 본다.
_OBJECTS = (
    "(SELECT ob.id, ob.type, ob.pk, ob.source_id, ob.synced_at, ob.deleted_at, "
    "ob.properties || COALESCE(ed.patch, CAST('{}' AS jsonb)) AS properties, ed.patch AS edited "
    "FROM object ob LEFT JOIN LATERAL ("
    "SELECT jsonb_object_agg(e.property, e.value) AS patch FROM object_edit e "
    "WHERE e.type = ob.type AND e.pk = ob.pk AND e.cleared_at IS NULL) ed ON true "
    "WHERE ob.type = :__type) o"
)


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


def _row(object_type: str, r: Any, declared: Sequence[str]) -> ObjectRow:
    edited = frozenset(k for k in (r.edited or {}) if k in declared)
    return ObjectRow(object_type, r.pk, _project(r.properties, declared), r.synced_at, edited)


async def resolve(
    db: AsyncSession, object_type: str, pks: Sequence[str], declared: Sequence[str]
) -> dict[str, ObjectRow]:
    """식별자로 객체를 읽는다(근거 카드·대화 맥락 재조회)."""
    if not pks:
        return {}
    rows = await db.execute(
        text(
            f"SELECT o.pk, o.properties, o.synced_at, o.edited FROM {_OBJECTS} "  # noqa: S608 — 고정 틀
            "WHERE o.pk = ANY(:pks) AND o.deleted_at IS NULL"
        ),
        {"__type": object_type, "pks": list(pks)},
    )
    return {r.pk: _row(object_type, r, declared) for r in rows}


async def existing_values(
    db: AsyncSession, object_type: str, prop: str, values: Sequence[str]
) -> set[str]:
    """``values`` 중 그 타입의 어떤 살아 있는 객체의 ``prop`` 값과 **정확히** 같은 것."""
    if not values:
        return set()
    rows = await db.scalars(
        text(
            f"SELECT DISTINCT o.properties ->> CAST(:p AS text) FROM {_OBJECTS} "  # noqa: S608
            "WHERE o.deleted_at IS NULL AND o.properties ->> CAST(:p AS text) = ANY(:vs)"
        ),
        {"__type": object_type, "p": prop, "vs": list(values)},
    )
    return set(rows)


async def pks_with_value(
    db: AsyncSession, object_type: str, prop: str, value: str, limit: int
) -> list[str]:
    """``prop`` 값이 ``value`` 와 **정확히** 같은 살아 있는 객체의 pk(최대 ``limit``개, pk 순)."""
    rows = await db.scalars(
        text(
            f"SELECT o.pk FROM {_OBJECTS} "  # noqa: S608 — 고정 틀
            "WHERE o.deleted_at IS NULL AND o.properties ->> CAST(:p AS text) = :v "
            "ORDER BY o.pk LIMIT :n"
        ),
        {"__type": object_type, "p": prop, "v": value, "n": limit},
    )
    return list(rows)


def render_sql(q: CompiledQuery) -> str:
    """실행할 SQL 전문. 트레이스(마스킹 전 값은 바인드라 SQL 에 없다)와 테스트가 함께 쓴다."""
    where = " AND ".join(["o.type = :__type", "o.deleted_at IS NULL", *q.where])
    if q.aggregate_select:
        select = ", ".join(f"{expr} AS {alias}" for alias, expr in q.aggregate_select)
    else:
        select = "o.pk, o.properties, o.synced_at, o.edited"
    sql = f"SELECT {select} FROM {_OBJECTS} WHERE {where}"  # noqa: S608 — 조각은 컴파일러의 고정 틀
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
    return [_row(q.object_type, r, q.declared_properties) for r in result]


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
