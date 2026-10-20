"""workshop publish

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20 21:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("interview_session", sa.Column("title", sa.String(length=200), nullable=True))
    op.add_column(
        "interview_session", sa.Column("published_version_id", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_interview_session_published_version_id_ontology_version"),
        "interview_session",
        "ontology_version",
        ["published_version_id"],
        ["id"],
    )
    op.add_column("golden_item", sa.Column("expected_answer", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("golden_item", "expected_answer")
    op.drop_constraint(
        op.f("fk_interview_session_published_version_id_ontology_version"),
        "interview_session",
        type_="foreignkey",
    )
    op.drop_column("interview_session", "published_version_id")
    op.drop_column("interview_session", "title")
