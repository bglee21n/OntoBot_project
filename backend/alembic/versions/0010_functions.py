"""functions

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-30 21:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "function_type",
        sa.Column("version_id", sa.BigInteger(), nullable=False),
        sa.Column("api_name", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["ontology_version.id"],
            name=op.f("fk_function_type_version_id_ontology_version"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_function_type")),
    )
    op.create_index(
        "uq_function_type_version_api", "function_type", ["version_id", "api_name"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_function_type_version_api", table_name="function_type")
    op.drop_table("function_type")
