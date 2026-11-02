"""engagement_decision

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-02 16:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "engagement_decision",
        sa.Column("engagement_id", sa.BigInteger(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("eval_run_id", sa.BigInteger(), nullable=True),
        sa.Column("gate_passed", sa.Boolean(), nullable=True),
        sa.Column("invariant_violations", sa.Integer(), nullable=False),
        sa.Column("decided_by", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('go','go_after_fix','no_go')",
            name=op.f("ck_engagement_decision_decision"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["app_user.id"], name=op.f("fk_engagement_decision_decided_by_app_user")
        ),
        sa.ForeignKeyConstraint(
            ["engagement_id"],
            ["engagement.id"],
            name=op.f("fk_engagement_decision_engagement_id_engagement"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["eval_run_id"],
            ["eval_run.id"],
            name=op.f("fk_engagement_decision_eval_run_id_eval_run"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_engagement_decision")),
    )
    op.create_index(
        op.f("ix_engagement_decision_engagement_id"),
        "engagement_decision",
        ["engagement_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_engagement_decision_engagement_id"), table_name="engagement_decision")
    op.drop_table("engagement_decision")
