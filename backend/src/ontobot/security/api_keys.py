"""공개 REST API 키(Phase 17b, TRD 17.1 결정 14).

- 키 원문 = ``obk_<prefix>_<secret>``. ``prefix``(8자, 비밀 아님)로 화면·감사에서 키를 가리키고,
  DB 에는 원문의 SHA-256 만 둔다. 원문은 발급 응답에 **한 번만** 나간다 — 잃어버리면 새로 발급한다.
- 키는 **서비스 계정**(``app_user.kind = service``) 하나에 매인다. 무엇을 읽고 바꿀 수 있는지는 그
  계정의 역할·데이터 그룹이 정하고(사람과 같은 ``rbac``), 스코프는 그 위에서 **좁히기만** 한다.
- 정지된 계정·폐기·만료된 키는 모두 없는 키와 같다(401). 쓴 시각(``last_used_at``)은 1분에 한 번만
  고친다.
- 관리(발급·폐기·서비스 계정 만들기)의 규칙은 ``security.admin`` 과 같은 자리 — 감사는 호출자
  트랜잭션에.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.security import ApiKey, AppUser
from ontobot.security import audit
from ontobot.security.admin import AdminError
from ontobot.security.rbac import SCOPES

KEY_RE = re.compile(r"^obk_([0-9a-f]{8})_([A-Za-z0-9_-]{32,64})$")
NAME_RE = re.compile(r"^[A-Za-z0-9가-힣 ._-]{1,64}$")
MAX_DAYS = 3650
TOUCH_EVERY = timedelta(minutes=1)


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _new_key() -> tuple[str, str]:
    prefix = secrets.token_hex(4)
    return prefix, f"obk_{prefix}_{secrets.token_urlsafe(32)}"


@dataclass(frozen=True)
class Caller:
    """API 키로 들어온 호출자 — 서비스 계정과 키의 스코프."""

    user: AppUser
    key_id: int
    prefix: str
    scopes: frozenset[str]


@dataclass(frozen=True)
class KeyView:
    id: int
    name: str
    prefix: str
    user_id: int
    username: str | None
    scopes: list[str]
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None


async def resolve(db: AsyncSession, presented: str) -> Caller | None:
    """유효한 키면 호출자를, 아니면 None. 형식이 틀린 값은 DB 를 보지 않는다."""
    if not KEY_RE.match(presented):
        return None
    now = datetime.now(UTC)
    row = (
        await db.execute(
            select(ApiKey, AppUser)
            .join(AppUser, AppUser.id == ApiKey.user_id)
            .where(
                ApiKey.key_hash == _hash(presented),
                ApiKey.revoked_at.is_(None),
                AppUser.status == "active",
                AppUser.kind == "service",
            )
        )
    ).first()
    if row is None:
        return None
    key, user = row
    if key.expires_at is not None and key.expires_at <= now:
        return None
    if key.last_used_at is None or now - key.last_used_at > TOUCH_EVERY:
        key.last_used_at = now
    return Caller(user, key.id, key.prefix, frozenset(key.scopes))


def _check_scopes(scopes: Sequence[str]) -> list[str]:
    bad = sorted(set(scopes) - set(SCOPES))
    if bad or not scopes:
        raise AdminError(
            f"스코프는 {', '.join(SCOPES)} 중 하나 이상입니다"
            + (f" — 모르는 것: {bad}" if bad else ""),
            code="invalid_scope",
            status=422,
        )
    return sorted(set(scopes))


async def create_service_account(
    db: AsyncSession, *, actor_id: int, username: str, display_name: str
) -> AppUser:
    """로그인하지 않는 계정(비밀번호·SSO 없음). 역할·데이터 그룹은 사용자 관리에서 준다."""
    if await db.scalar(select(AppUser.id).where(AppUser.username == username)):
        raise AdminError("이미 있는 사용자 이름입니다", code="username_taken", status=409)
    user = AppUser(username=username, display_name=display_name, kind="service")
    db.add(user)
    await db.flush()
    audit.record(
        db,
        audit.SERVICE_ACCOUNT_CREATED,
        actor_id=actor_id,
        target=f"app_user:{user.id}",
        payload={"username": username},
    )
    await db.flush()
    return user


async def issue(
    db: AsyncSession,
    *,
    actor_id: int,
    user_id: int,
    name: str,
    scopes: Sequence[str],
    expires_days: int | None,
) -> tuple[ApiKey, str]:
    """키 발급 — (기록, 원문). 원문은 이 응답 말고는 어디에도 남지 않는다(감사에도 prefix 만)."""
    user = await db.get(AppUser, user_id)
    if user is None or user.kind != "service":
        raise AdminError(
            "API 키는 서비스 계정에만 발급합니다", code="not_service_account", status=422
        )
    if not NAME_RE.match(name):
        raise AdminError(
            "키 이름은 1~64자(글자·숫자·공백·._-)입니다", code="invalid_name", status=422
        )
    if expires_days is not None and not 1 <= expires_days <= MAX_DAYS:
        raise AdminError(f"만료는 1~{MAX_DAYS}일입니다", code="invalid_expiry", status=422)
    granted = _check_scopes(scopes)
    prefix, plain = _new_key()
    while await db.scalar(select(ApiKey.id).where(ApiKey.prefix == prefix)):
        prefix, plain = _new_key()
    key = ApiKey(
        name=name,
        prefix=prefix,
        key_hash=_hash(plain),
        user_id=user.id,
        scopes=granted,
        created_by=actor_id,
        expires_at=(
            datetime.now(UTC) + timedelta(days=expires_days) if expires_days is not None else None
        ),
    )
    db.add(key)
    await db.flush()
    audit.record(
        db,
        audit.API_KEY_ISSUED,
        actor_id=actor_id,
        target=f"api_key:{key.id}",
        payload={"prefix": prefix, "user_id": user.id, "scopes": granted, "days": expires_days},
    )
    await db.flush()
    return key, plain


async def revoke(db: AsyncSession, *, actor_id: int, key_id: int) -> None:
    key = await db.get(ApiKey, key_id)
    if key is None:
        raise AdminError("API 키를 찾을 수 없습니다", code="api_key_not_found", status=404)
    if key.revoked_at is not None:
        return
    key.revoked_at = datetime.now(UTC)
    audit.record(
        db,
        audit.API_KEY_REVOKED,
        actor_id=actor_id,
        target=f"api_key:{key.id}",
        payload={"prefix": key.prefix},
    )
    await db.flush()


async def listing(db: AsyncSession) -> list[KeyView]:
    rows = await db.execute(
        select(ApiKey, AppUser.username)
        .join(AppUser, AppUser.id == ApiKey.user_id)
        .order_by(ApiKey.id.desc())
    )
    return [
        KeyView(
            id=k.id,
            name=k.name,
            prefix=k.prefix,
            user_id=k.user_id,
            username=u,
            scopes=list(k.scopes),
            created_at=k.created_at,
            expires_at=k.expires_at,
            revoked_at=k.revoked_at,
            last_used_at=k.last_used_at,
        )
        for k, u in rows
    ]
