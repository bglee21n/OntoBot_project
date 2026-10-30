"""편집 레이어 쓰기와 충돌 기록(TRD 6.3, 11절, 17.1-1).

- 보이는 값 = 활성 편집 값, 없으면 원천 값. 덮어 읽기는 ``reader`` 가 한다 — 여기는 편집을 만들고
  지우고, 편집의 자세한 정보(편집자·편집 당시 원천 값·충돌)를 돌려준다.
- **동기화는 편집을 건드리지 않는다**(편집 레이어 우선, PRD 5.3). 편집된 속성의 새 원천 값이
  편집 당시 원천 값(``base_source_value``)과 다르면 ``sync_conflict`` 를 남긴다 — 조용히 넘기지
  않는다.
- 편집은 Action 실행기(Phase 10)만 만든다. 해제는 권한자가 한다(감사는 호출자가 같은 트랜잭션에).

값은 ``object.properties`` 와 같은 JSON 표현으로 넘긴다(속성 자료형 변환은 호출자 몫).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.objects import ObjectEdit


class EditError(ValueError):
    """편집할 수 없다(이미 해제됨·확인할 수 없는 상태 등). API 가 문제 응답으로 바꾼다."""


class NotFound(EditError):
    """대상(객체·편집·충돌)이 없다."""


@dataclass(frozen=True)
class ConflictInfo:
    id: int
    new_source_value: Any
    source_synced_at: datetime | None
    status: str


@dataclass(frozen=True)
class EditInfo:
    id: int
    property: str
    value: Any
    base_source_value: Any
    edited_by: int | None
    edited_by_name: str | None
    edited_at: datetime
    action_run_id: int | None
    conflict: ConflictInfo | None


async def apply_edit(
    db: AsyncSession,
    *,
    object_type: str,
    pk: str,
    prop: str,
    value: Any,
    edited_by: int | None,
    action_run_id: int | None = None,
    now: datetime | None = None,
) -> int:
    """속성 하나를 편집한다. 같은 속성의 활성 편집이 있으면 해제하고 새로 만든다.

    편집 당시 원천 값을 함께 적는다 — 이후 동기화가 이것과 비교해 충돌을 판정한다.
    삭제 표시된 객체는 편집할 수 없다.
    """
    now = now or datetime.now(UTC)
    found = (
        await db.execute(
            text(
                "SELECT NULLIF(properties -> CAST(:p AS text), CAST('null' AS jsonb)) AS v "
                "FROM object "
                "WHERE type = :t AND pk = :pk AND deleted_at IS NULL FOR UPDATE"
            ),
            {"t": object_type, "pk": pk, "p": prop},
        )
    ).first()
    if found is None:
        raise NotFound(f"객체가 없습니다: {object_type}/{pk}")
    previous = await db.scalar(
        text(
            "SELECT id FROM object_edit WHERE type = :t AND pk = :pk AND property = :p "
            "AND cleared_at IS NULL"
        ),
        {"t": object_type, "pk": pk, "p": prop},
    )
    if previous is not None:
        await _clear(db, previous, edited_by, now)
    edit = ObjectEdit(
        type=object_type,
        pk=pk,
        property=prop,
        value=value,
        base_source_value=found.v,
        action_run_id=action_run_id,
        edited_by=edited_by,
        edited_at=now,
    )
    db.add(edit)
    await db.flush()
    return edit.id


async def clear_edit(
    db: AsyncSession, edit_id: int, user_id: int | None, now: datetime | None = None
) -> ObjectEdit:
    """편집을 해제한다 — 보이는 값이 원천 값으로 돌아간다. 그 편집의 살아 있는 충돌도 닫는다."""
    edit = await db.get(ObjectEdit, edit_id, with_for_update=True)
    if edit is None:
        raise NotFound("편집을 찾을 수 없습니다")
    if edit.cleared_at is not None:
        raise EditError("이미 해제된 편집입니다")
    await _clear(db, edit_id, user_id, now or datetime.now(UTC))
    await db.refresh(edit)
    return edit


async def _clear(db: AsyncSession, edit_id: int, user_id: int | None, now: datetime) -> None:
    await db.execute(
        text("UPDATE object_edit SET cleared_at = :now, cleared_by = :u WHERE id = :id"),
        {"now": now, "u": user_id, "id": edit_id},
    )
    await db.execute(
        text(
            "UPDATE sync_conflict SET status = 'cleared', resolved_by = :u, resolved_at = :now "
            "WHERE edit_id = :id AND status <> 'cleared'"
        ),
        {"now": now, "u": user_id, "id": edit_id},
    )


async def active_edits(
    db: AsyncSession, object_type: str, pks: Sequence[str]
) -> dict[str, dict[str, EditInfo]]:
    """객체마다 속성 → 활성 편집(살아 있는 충돌 포함)."""
    if not pks:
        return {}
    rows = await db.execute(
        text(
            "SELECT e.id, e.pk, e.property, e.value, e.base_source_value, e.edited_by, "
            "u.display_name AS edited_by_name, e.edited_at, e.action_run_id, c.id AS c_id, "
            "c.new_source_value, c.source_synced_at, c.status FROM object_edit e "
            "LEFT JOIN sync_conflict c ON c.edit_id = e.id AND c.status <> 'cleared' "
            "LEFT JOIN app_user u ON u.id = e.edited_by "
            "WHERE e.type = :t AND e.pk = ANY(:pks) AND e.cleared_at IS NULL"
        ),
        {"t": object_type, "pks": list(pks)},
    )
    out: dict[str, dict[str, EditInfo]] = {}
    for r in rows:
        conflict = (
            ConflictInfo(r.c_id, r.new_source_value, r.source_synced_at, r.status)
            if r.c_id is not None
            else None
        )
        out.setdefault(r.pk, {})[r.property] = EditInfo(
            r.id,
            r.property,
            r.value,
            r.base_source_value,
            r.edited_by,
            r.edited_by_name,
            r.edited_at,
            r.action_run_id,
            conflict,
        )
    return out


async def record_source_changes(db: AsyncSession, object_type: str, pks: list[str]) -> None:
    """동기화 병합 트랜잭션 안에서, 원천 값이 바뀐 객체의 편집을 대조한다(``sync.runner`` 훅).

    편집된 속성마다(지금 원천 값 = ``object.properties``):

    - 편집 당시 원천 값과 **같아졌으면** 살아 있는 충돌을 ``cleared`` 로 닫는다.
    - **다르고** 살아 있는 충돌이 있으면, 기록한 새 원천 값과 다를 때만 값·시각을 고치고
      ``open`` 으로 되돌린다(확인한 뒤 원천이 또 바뀌면 다시 봐야 한다).
    - **다르고** 살아 있는 충돌이 없으면 ``open`` 으로 새로 남긴다.

    값 비교는 jsonb 동등(숫자는 수로 비교)이고, 속성이 없으면 NULL 로 본다.
    """
    if not pks:
        return
    params = {"t": object_type, "pks": pks}
    # JSON null 과 속성 없음을 같은 NULL 로 — 편집 당시 값도 그렇게 저장된다(none_as_null).
    src = "NULLIF(o.properties -> e.property, CAST('null' AS jsonb))"
    scope = (
        "FROM object_edit e JOIN object o ON o.type = e.type AND o.pk = e.pk "
        "WHERE e.type = :t AND e.pk = ANY(:pks) AND e.cleared_at IS NULL"
    )
    await db.execute(
        text(
            "UPDATE sync_conflict c SET status = 'cleared', resolved_by = NULL, "  # noqa: S608 — 고정 틀
            f"resolved_at = now() {scope} AND c.edit_id = e.id AND c.status <> 'cleared' "
            f"AND {src} IS NOT DISTINCT FROM e.base_source_value"
        ),
        params,
    )
    await db.execute(
        text(
            f"UPDATE sync_conflict c SET new_source_value = {src}, "  # noqa: S608 — 고정 틀
            "source_synced_at = o.synced_at, status = 'open', resolved_by = NULL, "
            f"resolved_at = NULL {scope} AND c.edit_id = e.id AND c.status <> 'cleared' "
            f"AND {src} IS DISTINCT FROM e.base_source_value "
            f"AND {src} IS DISTINCT FROM c.new_source_value"
        ),
        params,
    )
    await db.execute(
        text(
            "INSERT INTO sync_conflict (edit_id, new_source_value, source_synced_at, status) "  # noqa: S608 — 고정 틀
            f"SELECT e.id, {src}, o.synced_at, 'open' {scope} "
            f"AND {src} IS DISTINCT FROM e.base_source_value "
            "AND NOT EXISTS (SELECT 1 FROM sync_conflict c "
            "WHERE c.edit_id = e.id AND c.status <> 'cleared')"
        ),
        params,
    )


# ---------------------------------------------------------------- 충돌 목록(관리 화면)


@dataclass(frozen=True)
class ConflictRow:
    id: int
    status: str
    edit_id: int
    type: str
    pk: str
    property: str
    edit_value: Any
    base_source_value: Any
    new_source_value: Any
    source_synced_at: datetime | None
    edited_by: str | None
    edited_at: datetime
    resolved_by: str | None
    resolved_at: datetime | None


async def list_conflicts(
    db: AsyncSession, types: Sequence[str], status: str | None = None, limit: int = 200
) -> list[ConflictRow]:
    """읽을 수 있는 타입의 충돌. ``status`` 가 없으면 살아 있는 것(open·acknowledged)."""
    status_sql = "c.status <> 'cleared'" if status is None else "c.status = :status"
    rows = await db.execute(
        text(
            "SELECT c.id, c.status, e.id AS edit_id, e.type, e.pk, e.property, "  # noqa: S608 — 고정 틀
            "e.value AS edit_value, e.base_source_value, c.new_source_value, "
            "c.source_synced_at, eu.display_name AS edited_by, e.edited_at, "
            "ru.display_name AS resolved_by, c.resolved_at "
            "FROM sync_conflict c JOIN object_edit e ON e.id = c.edit_id "
            "LEFT JOIN app_user eu ON eu.id = e.edited_by "
            "LEFT JOIN app_user ru ON ru.id = c.resolved_by "
            f"WHERE e.type = ANY(:types) AND {status_sql} "
            "ORDER BY c.id DESC LIMIT :n"
        ),
        {"types": list(types), "status": status, "n": limit},
    )
    return [ConflictRow(**r._mapping) for r in rows]


async def acknowledge_conflict(
    db: AsyncSession, conflict_id: int, user_id: int, now: datetime | None = None
) -> tuple[str, str, str]:
    """``open`` → ``acknowledged`` 하고 (type, pk, 속성)을 돌려준다.

    편집 값은 그대로 이긴다 — 낡았으면 편집을 해제한다.
    """
    row = (
        await db.execute(
            text(
                "SELECT c.status, e.type, e.pk, e.property FROM sync_conflict c "
                "JOIN object_edit e ON e.id = c.edit_id WHERE c.id = :id FOR UPDATE OF c"
            ),
            {"id": conflict_id},
        )
    ).first()
    if row is None:
        raise NotFound("충돌을 찾을 수 없습니다")
    if row.status != "open":
        raise EditError("확인할 수 있는 충돌이 아닙니다")
    await db.execute(
        text(
            "UPDATE sync_conflict SET status = 'acknowledged', resolved_by = :u, "
            "resolved_at = :now WHERE id = :id"
        ),
        {"u": user_id, "now": now or datetime.now(UTC), "id": conflict_id},
    )
    return row.type, row.pk, row.property


async def edit_target(db: AsyncSession, edit_id: int) -> tuple[str, str, str] | None:
    """편집의 (type, pk, 속성) — 해제 전에 권한(타입 읽기)을 판정하려고 쓴다."""
    row = (
        await db.execute(
            text("SELECT type, pk, property FROM object_edit WHERE id = :id"), {"id": edit_id}
        )
    ).first()
    return (row.type, row.pk, row.property) if row else None


async def open_conflict_count(db: AsyncSession) -> int:
    """확인되지 않은 편집 충돌 건수(PRD 7절 v1 지표)."""
    return int(
        await db.scalar(text("SELECT count(*) FROM sync_conflict WHERE status = 'open'")) or 0
    )


@dataclass(frozen=True)
class EditHistory:
    id: int
    property: str
    value: Any
    base_source_value: Any
    edited_by_name: str | None
    edited_at: datetime
    action_run_id: int | None
    cleared_at: datetime | None


async def history(
    db: AsyncSession, object_type: str, pk: str, properties: Sequence[str], *, limit: int = 100
) -> list[EditHistory]:
    """객체 하나의 편집 이력(해제된 것 포함, 최근 먼저). ``properties`` 밖(숨긴 속성)은 빼고."""
    if not properties:
        return []
    rows = await db.execute(
        text(
            "SELECT e.id, e.property, e.value, e.base_source_value, u.display_name AS name, "
            "e.edited_at, e.action_run_id, e.cleared_at FROM object_edit e "
            "LEFT JOIN app_user u ON u.id = e.edited_by "
            "WHERE e.type = :t AND e.pk = :pk AND e.property = ANY(:props) "
            "ORDER BY e.edited_at DESC, e.id DESC LIMIT :n"
        ),
        {"t": object_type, "pk": pk, "props": list(properties), "n": limit},
    )
    return [
        EditHistory(
            r.id,
            r.property,
            r.value,
            r.base_source_value,
            r.name,
            r.edited_at,
            r.action_run_id,
            r.cleared_at,
        )
        for r in rows
    ]
