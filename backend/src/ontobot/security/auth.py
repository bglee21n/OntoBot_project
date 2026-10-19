"""인증 — 로컬 계정과 서버 세션(TRD 9.1).

- 쿠키에는 무작위 토큰만 싣고 DB에는 그 SHA-256 만 둔다. DB가 새어도 세션을 가로챌 수 없고,
  로그아웃·계정 정지가 즉시 효력을 갖는다(서명 토큰이었다면 만료 전까지 살아 있다).
- OIDC 로그인도 같은 ``create_session`` 으로 합류한다(로그인 방식만 ``auth_method`` 로 남는다).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.security import AppUser, UserSession

_hasher = PasswordHasher()
# 없는 사용자로 로그인할 때도 같은 시간을 쓰게 하는 미끼 해시.
# 응답 시간으로 계정이 있는지 알 수 없게 한다.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

MIN_PASSWORD_LENGTH = 12


@dataclass(frozen=True)
class IssuedSession:
    token: str  # 쿠키에 싣는 원문. 다시 꺼낼 수 없다.
    expires_at: datetime


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다")
    return _hasher.hash(password)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def authenticate_local(db: AsyncSession, username: str, password: str) -> AppUser | None:
    user = await db.scalar(select(AppUser).where(AppUser.username == username))
    stored = user.password_hash if user and user.password_hash else _DUMMY_HASH
    try:
        _hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return None
    if user is None or user.password_hash is None or user.status != "active":
        return None
    return user


async def create_session(
    db: AsyncSession, user: AppUser, *, method: str, ttl: timedelta
) -> IssuedSession:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + ttl
    db.add(
        UserSession(
            token_hash=_token_hash(token),
            user_id=user.id,
            expires_at=expires_at,
            auth_method=method,
        )
    )
    await db.flush()
    return IssuedSession(token=token, expires_at=expires_at)


async def resolve_session(db: AsyncSession, token: str) -> AppUser | None:
    """유효한 세션이면 사용자를, 아니면 None. 만료·폐기·정지 계정은 모두 None."""
    now = datetime.now(UTC)
    row = await db.execute(
        select(AppUser)
        .join(UserSession, UserSession.user_id == AppUser.id)
        .where(
            UserSession.token_hash == _token_hash(token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
            AppUser.status == "active",
        )
    )
    return row.scalar_one_or_none()


async def revoke_session(db: AsyncSession, token: str) -> int | None:
    """세션을 폐기하고 그 사용자 id 를 돌려준다(없으면 None)."""
    result = await db.execute(
        update(UserSession)
        .where(UserSession.token_hash == _token_hash(token), UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
        .returning(UserSession.user_id)
    )
    return result.scalar_one_or_none()
