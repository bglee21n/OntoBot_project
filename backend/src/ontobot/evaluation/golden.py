"""골든셋 — 도메인 팩 ``golden.yaml`` ↔ DB(TRD 13절).

팩 파일이 정본이다. 적재(``load_pack``)는 이름이 같은 골든셋을 찾아 문항을 ``key``(팩의 ``id``)로
맞춘다: 있으면 고치고, 없으면 만들고, 팩에서 사라진 문항은 지운다. 이미 평가 결과가 붙은 문항을
지우면 지난 실행 기록이 깨지므로 그때는 적재를 멈춘다(``GoldenConflict`` — 새 이름으로 적재한다).

워크숍이 만든 문항(``source=workshop``)은 팩과 무관하다 — 적재가 건드리지 않는다.
"""

from __future__ import annotations

from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.evaluation import EvalResult, EvalRun, GoldenItem, GoldenSet
from ontobot.security import audit

Status = Literal["ok", "empty", "failed", "not_query"]


class PackItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=1000)
    expected_status: Status
    expected_failure: str | None = None
    expected_grade: Literal[1, 2, 3] | None = None
    # 데이터로 답할 수 있는 질문인가. false 면 ①·② 로 답하면 안 된다(PRD 7절 불변식).
    answerable: bool = True
    required_values: list[str] = Field(default_factory=list)
    absent_values: list[str] = Field(default_factory=list)
    required_conditions: list[str] = Field(default_factory=list)
    forbidden_values: list[str] = Field(default_factory=list)
    context: list[dict[str, str]] = Field(default_factory=list)
    test_role: str | None = None
    fault: Literal["none", "executor_error", "graph_down", "timeout"] = "none"
    note: str | None = None

    @field_validator("required_values", "absent_values", "forbidden_values", mode="before")
    @classmethod
    def _as_text(cls, v: Any) -> Any:
        # YAML 이 "18" 을 숫자로 읽어도 비교는 문자열로 한다.
        return [str(x) for x in v] if isinstance(v, list) else v


class Pack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    # 평가 전용 역할 → 읽을 수 있는 타입. 문항의 test_role 이 여기 있으면 이 권한으로 돈다.
    roles: dict[str, list[str]] = Field(default_factory=dict)
    items: list[PackItem]

    @field_validator("items")
    @classmethod
    def _unique_ids(cls, items: list[PackItem]) -> list[PackItem]:
        ids = [i.id for i in items]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"문항 id 가 중복됩니다: {', '.join(dup)}")
        return items


class GoldenConflict(Exception):
    pass


def parse(text: str) -> Pack:
    return Pack.model_validate(yaml.safe_load(text))


def _apply(row: GoldenItem, it: PackItem) -> None:
    row.question = it.question
    row.expected_status = it.expected_status
    row.expected_failure = it.expected_failure
    row.expected_grade = it.expected_grade
    row.answerable = it.answerable
    row.required_values = it.required_values
    row.absent_values = it.absent_values
    row.required_conditions = it.required_conditions
    row.forbidden_values = it.forbidden_values
    row.context = it.context
    row.test_role = it.test_role
    row.fault = it.fault
    row.note = it.note


async def load_pack(
    db: AsyncSession, pack: Pack, *, domain_pack: str | None, ontology_version_id: int | None
) -> tuple[GoldenSet, dict[str, int]]:
    """팩을 적재한다. (골든셋, {added, updated, removed}). 호출부가 커밋한다."""
    gs = await db.scalar(select(GoldenSet).where(GoldenSet.name == pack.name))
    if gs is None:
        gs = GoldenSet(name=pack.name, domain_pack=domain_pack, roles=pack.roles)
        db.add(gs)
        await db.flush()
    gs.roles = pack.roles
    gs.domain_pack = domain_pack
    gs.ontology_version_id = ontology_version_id

    rows = {
        r.key: r
        for r in await db.scalars(
            select(GoldenItem).where(
                GoldenItem.golden_set_id == gs.id, GoldenItem.source == "manual"
            )
        )
    }
    wanted = {it.id for it in pack.items}
    gone = [r for k, r in rows.items() if k not in wanted]
    for r in gone:
        used = await db.scalar(select(exists().where(EvalResult.item_id == r.id)))
        if used:
            raise GoldenConflict(
                f"문항 '{r.key}' 은 평가 결과가 있어 지울 수 없습니다 — 새 이름으로 적재하세요"
            )
        await db.delete(r)
    counts = {"added": 0, "updated": 0, "removed": len(gone)}
    for it in pack.items:
        row = rows.get(it.id)
        if row is None:
            row = GoldenItem(golden_set_id=gs.id, key=it.id, source="manual")
            db.add(row)
            counts["added"] += 1
        else:
            counts["updated"] += 1
        _apply(row, it)
    await db.flush()
    audit.record(
        db,
        audit.GOLDEN_CHANGED,
        actor_id=None,
        target=f"golden_set:{gs.id}",
        payload={"op": "load_pack", "domain_pack": domain_pack, **counts},
    )
    return gs, counts


async def items(db: AsyncSession, golden_set_id: int) -> list[GoldenItem]:
    return list(
        await db.scalars(
            select(GoldenItem)
            .where(GoldenItem.golden_set_id == golden_set_id)
            .order_by(GoldenItem.key.nulls_last(), GoldenItem.id)
        )
    )


# ---------------------------------------------------------------- 화면·API 편집(TRD 14.1 평가 묶음)


class SetNotFound(Exception):
    pass


class ItemNotFound(Exception):
    pass


class InUse(Exception):
    """평가 결과가 붙어 있어 지울 수 없다(지난 실행 기록이 깨진다)."""


class NameTaken(Exception):
    pass


ITEM_FIELDS = (
    "question",
    "expected_status",
    "expected_failure",
    "expected_grade",
    "answerable",
    "required_values",
    "absent_values",
    "required_conditions",
    "forbidden_values",
    "context",
    "test_role",
    "fault",
    "note",
)


async def get_set(db: AsyncSession, set_id: int) -> GoldenSet:
    gs = await db.get(GoldenSet, set_id)
    if gs is None:
        raise SetNotFound(set_id)
    return gs


async def create_set(
    db: AsyncSession, name: str, roles: dict[str, list[str]], *, actor_id: int
) -> GoldenSet:
    if await db.scalar(select(exists().where(GoldenSet.name == name))):
        raise NameTaken(name)
    gs = GoldenSet(name=name, roles=roles)
    db.add(gs)
    await db.flush()
    audit.record(
        db,
        audit.GOLDEN_CHANGED,
        actor_id=actor_id,
        target=f"golden_set:{gs.id}",
        payload={"op": "create_set", "name": name},
    )
    return gs


async def delete_set(db: AsyncSession, gs: GoldenSet, *, actor_id: int) -> None:
    if await db.scalar(select(exists().where(EvalRun.golden_set_id == gs.id))):
        raise InUse(gs.id)
    audit.record(
        db,
        audit.GOLDEN_CHANGED,
        actor_id=actor_id,
        target=f"golden_set:{gs.id}",
        payload={"op": "delete_set", "name": gs.name},
    )
    await db.delete(gs)


async def get_item(db: AsyncSession, gs: GoldenSet, item_id: int) -> GoldenItem:
    item = await db.get(GoldenItem, item_id)
    if item is None or item.golden_set_id != gs.id:
        raise ItemNotFound(item_id)
    return item


async def save_item(
    db: AsyncSession,
    gs: GoldenSet,
    fields: dict[str, Any],
    *,
    actor_id: int,
    item: GoldenItem | None = None,
    key: str | None = None,
) -> GoldenItem:
    """새 문항(``item`` 없음) 또는 일부 필드 고치기. 값 검사는 호출부(API 모델)가 했다."""
    if item is None:
        item = GoldenItem(golden_set_id=gs.id, key=key, source="manual")
        db.add(item)
    for name, value in fields.items():
        if name not in ITEM_FIELDS:
            raise ValueError(name)
        setattr(item, name, value)
    await db.flush()
    audit.record(
        db,
        audit.GOLDEN_CHANGED,
        actor_id=actor_id,
        target=f"golden_item:{item.id}",
        payload={"op": "save_item", "golden_set_id": gs.id, "fields": sorted(fields)},
    )
    return item


async def delete_item(db: AsyncSession, item: GoldenItem, *, actor_id: int) -> None:
    if await db.scalar(select(exists().where(EvalResult.item_id == item.id))):
        raise InUse(item.id)
    audit.record(
        db,
        audit.GOLDEN_CHANGED,
        actor_id=actor_id,
        target=f"golden_item:{item.id}",
        payload={"op": "delete_item", "golden_set_id": item.golden_set_id},
    )
    await db.delete(item)
