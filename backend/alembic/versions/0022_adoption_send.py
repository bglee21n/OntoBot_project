"""adoption send: golden_item source adoption, interview_session.engagement_id

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-02 14:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_golden_item_source"), "golden_item", type_="check")
    op.create_check_constraint(
        op.f("ck_golden_item_source"),
        "golden_item",
        "source IN ('manual','workshop','feedback','adoption')",
    )
    op.add_column("interview_session", sa.Column("engagement_id", sa.BigInteger(), nullable=True))
    op.create_index(
        op.f("ix_interview_session_engagement_id"),
        "interview_session",
        ["engagement_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_interview_session_engagement_id_engagement"),
        "interview_session",
        "engagement",
        ["engagement_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_interview_session_engagement_id_engagement"),
        "interview_session",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_interview_session_engagement_id"), table_name="interview_session")
    op.drop_column("interview_session", "engagement_id")
    op.drop_constraint(op.f("ck_golden_item_source"), "golden_item", type_="check")
    op.create_check_constraint(
        op.f("ck_golden_item_source"),
        "golden_item",
        "source IN ('manual','workshop','feedback')",
    )
