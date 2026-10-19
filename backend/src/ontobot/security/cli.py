"""사용자·권한 관리 CLI — 관리 화면(Phase 5) 전까지의 유일한 관리 수단.

    uv run python -m ontobot.security.cli create-user --username alice --display-name 앨리스 \\
        --role modeler --role user
    uv run python -m ontobot.security.cli grant-type --role user --type Facility

비밀번호는 인자로 받지 않는다(셸 기록·프로세스 목록에 남는다). 대화형 입력을 쓰고,
자동화에는 ``ONTOBOT_NEW_PASSWORD`` 환경변수를 쓴다.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.security import AppUser, Role, TypePermission, UserRole
from ontobot.db.session import dispose_engine, session_scope
from ontobot.security import audit
from ontobot.security.auth import MIN_PASSWORD_LENGTH, hash_password
from ontobot.security.rbac import KNOWN_ROLES


def _read_password() -> str:
    pw = os.environ.get("ONTOBOT_NEW_PASSWORD")
    if pw:
        return pw
    first = getpass.getpass(f"비밀번호({MIN_PASSWORD_LENGTH}자 이상): ")
    if first != getpass.getpass("비밀번호 확인: "):
        sys.exit("비밀번호가 서로 다릅니다")
    return first


async def _role(db: AsyncSession, name: str) -> Role:
    if name not in KNOWN_ROLES:
        sys.exit(f"알 수 없는 역할: {name} (가능: {', '.join(sorted(KNOWN_ROLES))})")
    role: Role | None = await db.scalar(select(Role).where(Role.name == name))
    if role is None:
        sys.exit(f"역할 '{name}' 이 DB에 없습니다 — 마이그레이션을 먼저 적용하세요")
    return role


async def create_user(username: str, display_name: str, roles: list[str]) -> None:
    try:
        password_hash = hash_password(_read_password())
    except ValueError as e:
        sys.exit(str(e))
    async for db in session_scope():
        if await db.scalar(select(AppUser.id).where(AppUser.username == username)):
            sys.exit(f"이미 있는 사용자입니다: {username}")
        user = AppUser(username=username, display_name=display_name, password_hash=password_hash)
        db.add(user)
        await db.flush()
        for name in roles:
            db.add(UserRole(user_id=user.id, role_id=(await _role(db, name)).id))
        audit.record(
            db,
            audit.USER_CREATED,
            actor_id=None,
            target=f"app_user:{user.id}",
            payload={"username": username, "roles": roles, "via": "cli"},
        )
        await db.commit()
        print(f"사용자 생성: {username} (id={user.id}, 역할={', '.join(roles) or '없음'})")


async def grant_type(role_name: str, object_type: str) -> None:
    async for db in session_scope():
        role = await _role(db, role_name)
        existing = await db.scalar(
            select(TypePermission).where(
                TypePermission.role_id == role.id, TypePermission.object_type == object_type
            )
        )
        if existing:
            existing.can_read = True
        else:
            db.add(TypePermission(role_id=role.id, object_type=object_type, can_read=True))
        audit.record(
            db,
            audit.PERMISSION_CHANGED,
            actor_id=None,
            target=f"role:{role_name}",
            payload={"object_type": object_type, "can_read": True, "via": "cli"},
        )
        await db.commit()
        print(f"읽기 권한 부여: {role_name} → {object_type}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ontobot.security.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user", help="로컬 계정 생성")
    cu.add_argument("--username", required=True)
    cu.add_argument("--display-name", required=True)
    cu.add_argument("--role", action="append", default=[], dest="roles")
    gt = sub.add_parser("grant-type", help="역할에 객체 타입 읽기 권한 부여")
    gt.add_argument("--role", required=True)
    gt.add_argument("--type", required=True, dest="object_type")
    args = parser.parse_args(argv)

    async def run() -> None:
        try:
            if args.cmd == "create-user":
                await create_user(args.username, args.display_name, args.roles)
            else:
                await grant_type(args.role, args.object_type)
        finally:
            await dispose_engine()

    asyncio.run(run())


if __name__ == "__main__":
    main()
