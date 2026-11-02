"""engagement_seed

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-02 18:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "engagement_seed",
        sa.Column("engagement_id", sa.BigInteger(), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.BigInteger(), nullable=True),
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
            ["engagement_id"],
            ["engagement.id"],
            name=op.f("fk_engagement_seed_engagement_id_engagement"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["app_user.id"], name=op.f("fk_engagement_seed_uploaded_by_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_engagement_seed")),
        sa.UniqueConstraint(
            "engagement_id",
            "object_type",
            name=op.f("uq_engagement_seed_engagement_id_object_type"),
        ),
    )


def downgrade() -> None:
    op.drop_table("engagement_seed")
