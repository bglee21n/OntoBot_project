"""engagement

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-02 10:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def _id_created() -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "engagement",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        *_id_created(),
        sa.ForeignKeyConstraint(
            ["created_by"], ["app_user.id"], name=op.f("fk_engagement_created_by_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_engagement")),
    )
    op.create_table(
        "engagement_member",
        sa.Column("engagement_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        *_id_created(),
        sa.CheckConstraint(
            "role IN ('owner','editor','viewer')", name=op.f("ck_engagement_member_role")
        ),
        sa.ForeignKeyConstraint(
            ["engagement_id"],
            ["engagement.id"],
            name=op.f("fk_engagement_member_engagement_id_engagement"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_engagement_member_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_engagement_member")),
        sa.UniqueConstraint(
            "engagement_id", "user_id", name=op.f("uq_engagement_member_engagement_id_user_id")
        ),
    )
    op.create_index(
        op.f("ix_engagement_member_engagement_id"),
        "engagement_member",
        ["engagement_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_engagement_member_user_id"), "engagement_member", ["user_id"], unique=False
    )
    op.create_table(
        "engagement_sheet",
        sa.Column("engagement_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        *_id_created(),
        sa.ForeignKeyConstraint(
            ["engagement_id"],
            ["engagement.id"],
            name=op.f("fk_engagement_sheet_engagement_id_engagement"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["app_user.id"], name=op.f("fk_engagement_sheet_updated_by_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_engagement_sheet")),
        sa.UniqueConstraint(
            "engagement_id", "kind", name=op.f("uq_engagement_sheet_engagement_id_kind")
        ),
    )


def downgrade() -> None:
    op.drop_table("engagement_sheet")
    op.drop_index(op.f("ix_engagement_member_user_id"), table_name="engagement_member")
    op.drop_index(op.f("ix_engagement_member_engagement_id"), table_name="engagement_member")
    op.drop_table("engagement_member")
    op.drop_table("engagement")
