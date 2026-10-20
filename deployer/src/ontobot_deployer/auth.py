"""로그인 — OntoBot 관리자 계정(admin 역할·active)만. 앱 DB 를 **읽기만** 한다.

앱 프로세스가 멈춰 있어도(업그레이드·장애) PostgreSQL 만 살아 있으면 로그인된다. 비밀번호 해시는
앱과
같은 argon2 형식(``ontobot.security.auth``)이다 — 앱 코드를 import 하지 않고 같은 라이브러리로
확인한다.
세션은 메모리에만 둔다(콘솔을 다시 시작하면 다시 로그인). 실패가 이어지면 계정·주소별로 잠시 막는다.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MAX_FAILURES = 5
LOCK_SECONDS = 300
_hasher = PasswordHasher()
# 없는 계정도 같은 시간이 걸리게(계정 존재 여부가 응답 시간으로 새지 않게).
_DUMMY = _hasher.hash(secrets.token_urlsafe(16))


@dataclass(frozen=True)
class Admin:
    id: int
    username: str
    display_name: str


@dataclass(frozen=True)
class UserRecord:
    id: int
    username: str
    display_name: str
    password_hash: str | None
    status: str
    roles: frozenset[str]


Lookup = Callable[[str], Awaitable[UserRecord | None]]


class Locked(Exception):
    pass


class Auth:
    def __init__(self, lookup: Lookup, session_minutes: int = 60) -> None:
        self._lookup = lookup
        self._ttl = session_minutes * 60
        self._sessions: dict[str, tuple[Admin, float]] = {}
        self._failures: dict[str, list[float]] = {}

    def _locked(self, key: str, now: float) -> bool:
        recent = [t for t in self._failures.get(key, []) if now - t < LOCK_SECONDS]
        self._failures[key] = recent
        return len(recent) >= MAX_FAILURES

    async def login(self, username: str, password: str, client: str) -> Admin | None:
        now = time.monotonic()
        keys = (f"u:{username.lower()}", f"c:{client}")
        if any(self._locked(k, now) for k in keys):
            raise Locked("로그인 실패가 이어져 잠시 막았습니다. 5분 뒤 다시 시도하세요")
        user = await self._lookup(username)
        ok = False
        try:
            _hasher.verify(user.password_hash if user and user.password_hash else _DUMMY, password)
            ok = user is not None and user.password_hash is not None
        except (VerificationError, InvalidHashError):
            ok = False
        if not ok or user is None or user.status != "active" or "admin" not in user.roles:
            for k in keys:
                self._failures.setdefault(k, []).append(now)
            return None
        for k in keys:
            self._failures.pop(k, None)
        return Admin(user.id, user.username, user.display_name)

    def open_session(self, admin: Admin) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = (admin, time.monotonic() + self._ttl)
        return token

    def session(self, token: str | None) -> Admin | None:
        if not token:
            return None
        hit = self._sessions.get(token)
        if hit is None:
            return None
        admin, expires = hit
        if time.monotonic() > expires:
            self._sessions.pop(token, None)
            return None
        # 쓰는 동안은 연장한다(긴 업그레이드를 지켜보다 끊기지 않게).
        self._sessions[token] = (admin, time.monotonic() + self._ttl)
        return admin

    def close(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)


def pg_lookup(host: str, port: int, db: str, user: str, password: str) -> Lookup:
    """앱 DB 의 app_user·역할을 읽는다. 연결은 요청마다 연다(로그인은 드물다)."""

    async def lookup(username: str) -> UserRecord | None:
        import asyncpg

        conn = await asyncpg.connect(
            host=host, port=port, database=db, user=user, password=password, timeout=5
        )
        try:
            row = await conn.fetchrow(
                "SELECT u.id, u.username, u.display_name, u.password_hash, u.status, "
                "COALESCE(array_agg(r.name) FILTER (WHERE r.name IS NOT NULL), '{}') AS roles "
                "FROM app_user u LEFT JOIN user_role ur ON ur.user_id = u.id "
                "LEFT JOIN role r ON r.id = ur.role_id WHERE u.username = $1 GROUP BY u.id",
                username,
            )
        finally:
            await conn.close()
        if row is None:
            return None
        return UserRecord(
            id=row["id"],
            username=row["username"],
            display_name=row["display_name"],
            password_hash=row["password_hash"],
            status=row["status"],
            roles=frozenset(row["roles"]),
        )

    return lookup
