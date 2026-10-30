"""property hide and row policy per data group

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-31 09:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def _common(table: str) -> list[sa.SchemaItem]:
    return [
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["data_group.id"],
            name=op.f(f"fk_{table}_group_id_data_group"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def upgrade() -> None:
    op.create_table(
        "group_property_hide",
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("property", sa.String(length=64), nullable=False),
        *_common("group_property_hide"),
    )
    op.create_index(
        "uq_group_property_hide",
        "group_property_hide",
        ["group_id", "object_type", "property"],
        unique=True,
    )
    op.create_table(
        "group_row_policy",
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("applies_to", sa.String(length=8), nullable=False),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_common("group_row_policy"),
        sa.CheckConstraint(
            "applies_to IN ('read','write')", name=op.f("ck_group_row_policy_applies_to")
        ),
    )
    op.create_index(
        "uq_group_row_policy",
        "group_row_policy",
        ["group_id", "object_type", "applies_to"],
        unique=True,
    )
    op.add_column(
        "golden_set",
        sa.Column(
            "restrictions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("golden_set", "restrictions")
    op.drop_index("uq_group_row_policy", table_name="group_row_policy")
    op.drop_table("group_row_policy")
    op.drop_index("uq_group_property_hide", table_name="group_property_hide")
    op.drop_table("group_property_hide")
