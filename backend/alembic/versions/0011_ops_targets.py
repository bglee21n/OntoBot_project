"""ops targets

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-30 20:41:36.090066
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ops_target",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["app_user.id"], name=op.f("fk_ops_target_updated_by_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_target")),
        sa.UniqueConstraint("key", name=op.f("uq_ops_target_key")),
    )


def downgrade() -> None:
    op.drop_table("ops_target")
