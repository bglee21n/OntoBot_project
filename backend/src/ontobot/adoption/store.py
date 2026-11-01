"""도입·구성원·양식 저장(TRD 17.17). 권한 판정은 호출부가 ``rbac.engagement_access`` 로 한다.

감사는 호출자 트랜잭션에 넣는다. payload 에 양식 값을 넣지 않는다(결정 A4 — 사람 정보 칸이 있다).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.adoption import sheets
from ontobot.db.models.adoption import Engagement, EngagementMember, EngagementSheet
from ontobot.db.models.security import AppUser
from ontobot.security import audit
from ontobot.security.rbac import ENGAGEMENT_OWNER, ENGAGEMENT_ROLES


class NotFound(Exception):
    pass


class UserNotFound(Exception):
    pass


class LastOwner(Exception):
    """마지막 owner 를 빼거나 낮추면 아무도 그 도입을 관리하지 못한다."""


class RevisionConflict(Exception):
    def __init__(self, current: int) -> None:
        super().__init__("다른 사람이 먼저 저장했습니다")
        self.current = current


def _target(engagement_id: int) -> str:
    return f"engagement:{engagement_id}"


# ---------------------------------------------------------------- 도입


async def create(
    db: AsyncSession, *, actor_id: int, name: str, description: str | None
) -> Engagement:
    e = Engagement(name=name, description=description, created_by=actor_id)
    db.add(e)
    await db.flush()
    db.add(EngagementMember(engagement_id=e.id, user_id=actor_id, role=ENGAGEMENT_OWNER))
    audit.record(
        db,
        audit.ENGAGEMENT_CREATED,
        actor_id=actor_id,
        target=_target(e.id),
        payload={"name": name},
    )
    await db.flush()
    return e


async def get(db: AsyncSession, engagement_id: int) -> Engagement:
    e = await db.get(Engagement, engagement_id)
    if e is None:
        raise NotFound
    return e


async def update_info(
    db: AsyncSession, e: Engagement, *, actor_id: int, name: str | None, description: str | None
) -> None:
    changed = []
    if name is not None and name != e.name:
        e.name = name
        changed.append("name")
    if description is not None and description != (e.description or ""):
        e.description = description or None
        changed.append("description")
    if changed:
        audit.record(
            db,
            audit.ENGAGEMENT_UPDATED,
            actor_id=actor_id,
            target=_target(e.id),
            payload={"fields": changed},
        )
        await db.flush()


async def member_role(db: AsyncSession, engagement_id: int, user_id: int) -> str | None:
    role: str | None = await db.scalar(
        select(EngagementMember.role).where(
            EngagementMember.engagement_id == engagement_id, EngagementMember.user_id == user_id
        )
    )
    return role


async def list_for_user(db: AsyncSession, user_id: int) -> list[tuple[Engagement, str]]:
    rows = await db.execute(
        select(Engagement, EngagementMember.role)
        .join(EngagementMember, EngagementMember.engagement_id == Engagement.id)
        .where(EngagementMember.user_id == user_id)
        .order_by(Engagement.id.desc())
    )
    return [(e, role) for e, role in rows.all()]


# ---------------------------------------------------------------- 구성원


@dataclass(frozen=True)
class Member:
    user_id: int
    username: str | None
    display_name: str
    role: str


async def members(db: AsyncSession, engagement_id: int) -> list[Member]:
    rows = await db.execute(
        select(
            EngagementMember.user_id, AppUser.username, AppUser.display_name, EngagementMember.role
        )
        .join(AppUser, AppUser.id == EngagementMember.user_id)
        .where(EngagementMember.engagement_id == engagement_id)
        .order_by(EngagementMember.id)
    )
    return [Member(uid, username, name, role) for uid, username, name, role in rows.all()]


async def _owners(db: AsyncSession, engagement_id: int) -> int:
    n: int = (
        await db.scalar(
            select(func.count()).where(
                EngagementMember.engagement_id == engagement_id,
                EngagementMember.role == ENGAGEMENT_OWNER,
            )
        )
        or 0
    )
    return n


async def set_member(
    db: AsyncSession, engagement_id: int, *, actor_id: int, username: str, role: str
) -> Member:
    """아이디 정확 일치로 사람 계정을 찾아 구성원으로 넣거나 역할을 바꾼다."""
    if role not in ENGAGEMENT_ROLES:
        raise ValueError(role)
    user = await db.scalar(
        select(AppUser).where(
            AppUser.username == username, AppUser.kind == "person", AppUser.status == "active"
        )
    )
    if user is None:
        raise UserNotFound
    m = await db.scalar(
        select(EngagementMember).where(
            EngagementMember.engagement_id == engagement_id, EngagementMember.user_id == user.id
        )
    )
    if m is None:
        db.add(EngagementMember(engagement_id=engagement_id, user_id=user.id, role=role))
    elif m.role != role:
        if m.role == ENGAGEMENT_OWNER and await _owners(db, engagement_id) <= 1:
            raise LastOwner
        m.role = role
    audit.record(
        db,
        audit.ENGAGEMENT_MEMBERS_CHANGED,
        actor_id=actor_id,
        target=_target(engagement_id),
        payload={"user_id": user.id, "role": role},
    )
    await db.flush()
    return Member(user.id, user.username, user.display_name, role)


async def remove_member(
    db: AsyncSession, engagement_id: int, *, actor_id: int, user_id: int
) -> None:
    m = await db.scalar(
        select(EngagementMember).where(
            EngagementMember.engagement_id == engagement_id, EngagementMember.user_id == user_id
        )
    )
    if m is None:
        raise UserNotFound
    if m.role == ENGAGEMENT_OWNER and await _owners(db, engagement_id) <= 1:
        raise LastOwner
    await db.delete(m)
    audit.record(
        db,
        audit.ENGAGEMENT_MEMBERS_CHANGED,
        actor_id=actor_id,
        target=_target(engagement_id),
        payload={"user_id": user_id, "role": None},
    )
    await db.flush()


# ---------------------------------------------------------------- 양식


@dataclass(frozen=True)
class SheetState:
    kind: str
    payload: dict[str, Any]
    revision: int  # 0 = 아직 저장한 적 없음
    updated_at: datetime | None
    updated_by: int | None


async def read_sheet(db: AsyncSession, engagement_id: int, kind: str) -> SheetState:
    row = await db.scalar(
        select(EngagementSheet).where(
            EngagementSheet.engagement_id == engagement_id, EngagementSheet.kind == kind
        )
    )
    if row is None:
        return SheetState(kind, {}, 0, None, None)
    return SheetState(kind, row.payload, row.revision, row.updated_at, row.updated_by)


async def sheet_states(db: AsyncSession, engagement_id: int) -> list[SheetState]:
    rows = await db.scalars(
        select(EngagementSheet).where(EngagementSheet.engagement_id == engagement_id)
    )
    return [SheetState(r.kind, r.payload, r.revision, r.updated_at, r.updated_by) for r in rows]


async def save_sheet(
    db: AsyncSession,
    engagement_id: int,
    kind: str,
    *,
    actor_id: int,
    revision: int,
    payload: dict[str, Any],
) -> SheetState:
    """``revision`` = 읽을 때 받은 판. 지금 판과 다르면 ``RevisionConflict``(덮어쓰지 않는다).

    검증은 ``sheets.validate`` 하나 — 실패하면 ``sheets.SheetInvalid``.
    """
    clean = sheets.validate(kind, payload)
    before = await read_sheet(db, engagement_id, kind)
    if revision == 0:
        stmt: Any = (
            insert(EngagementSheet)
            .values(
                engagement_id=engagement_id,
                kind=kind,
                payload=clean,
                revision=1,
                updated_by=actor_id,
            )
            .on_conflict_do_nothing(index_elements=["engagement_id", "kind"])
            .returning(EngagementSheet.revision, EngagementSheet.updated_at)
        )
    else:
        stmt = (
            update(EngagementSheet)
            .where(
                EngagementSheet.engagement_id == engagement_id,
                EngagementSheet.kind == kind,
                EngagementSheet.revision == revision,
            )
            .values(
                payload=clean, revision=revision + 1, updated_by=actor_id, updated_at=func.now()
            )
            .returning(EngagementSheet.revision, EngagementSheet.updated_at)
        )
    saved = (await db.execute(stmt)).first()
    if saved is None:
        raise RevisionConflict((await read_sheet(db, engagement_id, kind)).revision)
    new_revision, updated_at = saved
    changed = sorted(k for k in {*before.payload, *clean} if before.payload.get(k) != clean.get(k))
    audit.record(
        db,
        audit.ENGAGEMENT_SHEET_SAVED,
        actor_id=actor_id,
        target=_target(engagement_id),
        payload={"kind": kind, "revision": new_revision, "sections": changed},
    )
    await db.flush()
    return SheetState(kind, clean, new_revision, updated_at, actor_id)
