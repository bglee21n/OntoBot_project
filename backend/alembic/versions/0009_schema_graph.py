"""schema graph — deprecated flags and layout

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-30 17:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "object_type",
        sa.Column("deprecated", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "link_type",
        sa.Column("deprecated", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_table(
        "ontology_layout",
        sa.Column("version_id", sa.BigInteger(), nullable=False),
        sa.Column("element_kind", sa.String(length=16), nullable=False),
        sa.Column("element_name", sa.String(length=64), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
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
        sa.CheckConstraint(
            "element_kind IN ('object_type')", name=op.f("ck_ontology_layout_element_kind")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["app_user.id"], name=op.f("fk_ontology_layout_updated_by_app_user")
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["ontology_version.id"],
            name=op.f("fk_ontology_layout_version_id_ontology_version"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_layout")),
    )
    op.create_index(
        "uq_ontology_layout_element",
        "ontology_layout",
        ["version_id", "element_kind", "element_name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_ontology_layout_element", table_name="ontology_layout")
    op.drop_table("ontology_layout")
    op.drop_column("link_type", "deprecated")
    op.drop_column("object_type", "deprecated")
