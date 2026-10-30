"""사용자·권한 관리 CLI — 관리 화면(Phase 5) 전까지의 유일한 관리 수단.

    uv run python -m ontobot.security.cli create-user --username alice --display-name 앨리스 \\
        --role modeler --role user --group user
    uv run python -m ontobot.security.cli grant-type --group user --type Facility
    python -m ontobot.security.cli audit-deploy --event deploy.upgrade --actor-id 3 --payload '{}'

역할은 화면·기능, **그룹은 데이터 권한**이다(Phase 14). ``grant-type`` 은 데이터 그룹에 읽기
권한을 준다(그룹이 없으면 만든다). ``--role`` 은 예전 이름으로, 같은 이름의 그룹을 뜻한다.

비밀번호는 인자로 받지 않는다(셸 기록·프로세스 목록에 남는다). 대화형 입력을 쓰고,
자동화에는 ``ONTOBOT_NEW_PASSWORD`` 환경변수를 쓴다.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.security import AppUser, DataGroup, GroupMember, Role, UserRole
from ontobot.db.session import dispose_engine, session_scope
from ontobot.security import admin, audit
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


async def create_user(
    username: str, display_name: str, roles: list[str], groups: list[str]
) -> None:
    try:
        password_hash = hash_password(_read_password())
    except ValueError as e:
        sys.exit(str(e))
    async with session_scope() as db:
        if await db.scalar(select(AppUser.id).where(AppUser.username == username)):
            sys.exit(f"이미 있는 사용자입니다: {username}")
        user = AppUser(username=username, display_name=display_name, password_hash=password_hash)
        db.add(user)
        await db.flush()
        for name in roles:
            db.add(UserRole(user_id=user.id, role_id=(await _role(db, name)).id))
        for name in groups:
            gid = await db.scalar(select(DataGroup.id).where(DataGroup.name == name))
            if gid is None:
                sys.exit(f"그룹이 없습니다: {name} — grant-type 으로 먼저 만드세요")
            db.add(GroupMember(group_id=gid, user_id=user.id))
        audit.record(
            db,
            audit.USER_CREATED,
            actor_id=None,
            target=f"app_user:{user.id}",
            payload={"username": username, "roles": roles, "groups": groups, "via": "cli"},
        )
        await db.commit()
        print(
            f"사용자 생성: {username} (id={user.id}, 역할={', '.join(roles) or '없음'}, "
            f"그룹={', '.join(groups) or '없음'})"
        )


async def grant_type(group_name: str, object_type: str, *, edit: bool = False) -> None:
    async with session_scope() as db:
        await admin.grant_read(db, group_name, object_type, via="cli", edit=edit)
        await db.commit()
        print(f"{'읽기·편집' if edit else '읽기'} 권한 부여: 그룹 {group_name} → {object_type}")


async def audit_deploy(event: str, actor_id: int | None, payload: str) -> None:
    """배포 콘솔의 작업 결과를 감사 로그에(ADR-0014). 이벤트 이름은 정해진 것만."""
    if event not in audit.DEPLOY_EVENTS:
        sys.exit(f"알 수 없는 배포 이벤트: {event}")
    data = json.loads(payload) if payload else {}
    if not isinstance(data, dict):
        sys.exit("payload 는 JSON 객체여야 합니다")
    async with session_scope() as db:
        audit.record(db, event, actor_id=actor_id, target="deployment", payload=data)
        await db.commit()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ontobot.security.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user", help="로컬 계정 생성")
    cu.add_argument("--username", required=True)
    cu.add_argument("--display-name", required=True)
    cu.add_argument("--role", action="append", default=[], dest="roles")
    cu.add_argument("--group", action="append", default=[], dest="groups")
    gt = sub.add_parser("grant-type", help="데이터 그룹에 객체 타입 읽기 권한 부여(없으면 만든다)")
    gt.add_argument("--group", "--role", required=True, dest="group")
    gt.add_argument("--type", required=True, dest="object_type")
    gt.add_argument("--edit", action="store_true", help="편집(= Action 요청)도 준다")
    ad = sub.add_parser("audit-deploy", help="배포 콘솔 작업을 감사 로그에 남김")
    ad.add_argument("--event", required=True)
    ad.add_argument("--actor-id", type=int, default=None)
    ad.add_argument("--payload", default="{}")
    args = parser.parse_args(argv)

    async def run() -> None:
        try:
            if args.cmd == "create-user":
                await create_user(args.username, args.display_name, args.roles, args.groups)
            elif args.cmd == "audit-deploy":
                await audit_deploy(args.event, args.actor_id, args.payload)
            else:
                await grant_type(args.group, args.object_type, edit=args.edit)
        finally:
            await dispose_engine()

    asyncio.run(run())


if __name__ == "__main__":
    main()
