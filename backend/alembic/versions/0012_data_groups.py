"""data groups — 역할 읽기 권한(type_permission)을 데이터 그룹으로 옮긴다

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-30 22:00:00.000000

역할은 화면·기능, 그룹은 데이터 권한이다(Phase 14). 읽기 권한이 있던 역할마다 같은 이름의
그룹을 만들고 그 역할의 사용자를 구성원으로, 읽기 권한을 옮긴다. 읽을 수 있던 타입에는
**편집**도 준다 — 그전에는 대상을 읽을 수 있으면 Action 을 요청할 수 있었고, 이제는 편집 권한이
필요하다. 업그레이드 뒤에도 누가 무엇을 읽고 요청하는지 같다(관리자가 그 뒤 좁힌다).
내림은 그룹 이름과 같은 역할로 읽기 권한을 되돌린다(그룹에서 더한 쓰기 권한·구성원 변경은
잃는다).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _created_at() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _group_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["group_id"],
        ["data_group.id"],
        name=op.f(f"fk_{table}_group_id_data_group"),
        ondelete="CASCADE",
    )


def upgrade() -> None:
    op.create_table(
        "data_group",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_group")),
        sa.UniqueConstraint("external_id", name=op.f("uq_data_group_external_id")),
        sa.UniqueConstraint("name", name=op.f("uq_data_group_name")),
    )
    op.create_table(
        "group_member",
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        _group_fk("group_member"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_group_member_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id", "user_id", name=op.f("pk_group_member")),
    )
    op.create_table(
        "group_type_permission",
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        *(
            sa.Column(c, sa.Boolean(), server_default="false", nullable=False)
            for c in ("can_read", "can_create", "can_edit", "can_delete")
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        _created_at(),
        _group_fk("group_type_permission"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_type_permission")),
    )
    op.create_index(
        "uq_group_type_permission",
        "group_type_permission",
        ["group_id", "object_type"],
        unique=True,
    )
    op.create_table(
        "group_link_permission",
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("link_type", sa.String(length=64), nullable=False),
        sa.Column("can_link", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        _created_at(),
        _group_fk("group_link_permission"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_link_permission")),
    )
    op.create_index(
        "uq_group_link_permission", "group_link_permission", ["group_id", "link_type"], unique=True
    )
    # 이전: 읽기 권한이 있던 역할 → 같은 이름의 그룹(구성원·읽기 권한 그대로).
    op.execute(
        "INSERT INTO data_group (name, display_name, description) "
        "SELECT r.name, r.name, 'migrated from role ' || r.name FROM role r "
        "WHERE EXISTS (SELECT 1 FROM type_permission p WHERE p.role_id = r.id AND p.can_read)"
    )
    op.execute(
        "INSERT INTO group_member (group_id, user_id) "
        "SELECT g.id, ur.user_id FROM data_group g "
        "JOIN role r ON r.name = g.name JOIN user_role ur ON ur.role_id = r.id"
    )
    op.execute(
        "INSERT INTO group_type_permission (group_id, object_type, can_read, can_edit) "
        "SELECT g.id, p.object_type, true, true FROM data_group g "
        "JOIN role r ON r.name = g.name JOIN type_permission p ON p.role_id = r.id "
        "WHERE p.can_read"
    )
    op.drop_index("uq_type_permission_role_type", table_name="type_permission")
    op.drop_table("type_permission")


def downgrade() -> None:
    op.create_table(
        "type_permission",
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("can_read", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["role.id"],
            name=op.f("fk_type_permission_role_id_role"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_type_permission")),
    )
    op.create_index(
        "uq_type_permission_role_type", "type_permission", ["role_id", "object_type"], unique=True
    )
    op.execute(
        "INSERT INTO type_permission (role_id, object_type, can_read) "
        "SELECT DISTINCT r.id, p.object_type, true FROM group_type_permission p "
        "JOIN data_group g ON g.id = p.group_id JOIN role r ON r.name = g.name WHERE p.can_read"
    )
    op.drop_index("uq_group_link_permission", table_name="group_link_permission")
    op.drop_table("group_link_permission")
    op.drop_index("uq_group_type_permission", table_name="group_type_permission")
    op.drop_table("group_type_permission")
    op.drop_table("group_member")
    op.drop_table("data_group")
