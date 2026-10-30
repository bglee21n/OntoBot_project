"""탐색기 확장 한도 — 관리자 설정(``app_setting``), 행이 없으면 기본값.

화면이 깊이·노드 수를 지키지만 서버도 같은 값으로 자른다(화면만 믿지 않는다). 최대 깊이는 화면이
1홉 호출을 되풀이하는 횟수라 서버가 직접 쓰지 않고 화면에 알려 준다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.settings import AppSetting
from ontobot.security import audit

KEY = "explorer.limits"
DEPTH_RANGE = (1, 5)
NODES_RANGE = (10, 1000)


@dataclass(frozen=True)
class Limits:
    max_depth: int = 3
    max_nodes: int = 200


DEFAULT = Limits()


async def get(db: AsyncSession) -> Limits:
    value = await db.scalar(select(AppSetting.value).where(AppSetting.key == KEY))
    if not isinstance(value, dict):
        return DEFAULT
    try:
        return Limits(
            max_depth=_clamp(int(value["max_depth"]), DEPTH_RANGE),
            max_nodes=_clamp(int(value["max_nodes"]), NODES_RANGE),
        )
    except (KeyError, TypeError, ValueError):
        return DEFAULT


def _clamp(v: int, bounds: tuple[int, int]) -> int:
    return max(bounds[0], min(bounds[1], v))


async def put(db: AsyncSession, limits: Limits, *, actor_id: int) -> Limits:
    value = {"max_depth": limits.max_depth, "max_nodes": limits.max_nodes}
    await db.execute(
        insert(AppSetting)
        .values(key=KEY, value=value, updated_by=actor_id)
        .on_conflict_do_update(
            index_elements=[AppSetting.key], set_={"value": value, "updated_by": actor_id}
        )
    )
    audit.record(db, audit.EXPLORER_LIMITS_CHANGED, actor_id=actor_id, target=KEY, payload=value)
    return limits
