"""chat actions

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-20 23:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_OLD = "status IS NULL OR status IN ('ok','empty','failed','not_query')"
_NEW = "status IS NULL OR status IN ('ok','empty','failed','not_query','action_preview')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_message_status"), "message", type_="check")
    op.create_check_constraint(op.f("ck_message_status"), "message", _NEW)
    op.add_column(
        "golden_item",
        sa.Column("expected_action", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("golden_item", "expected_action")
    # 대화 기록은 지우지 않는다 — 본문은 남기고 등급 없는 상태로 낮춘다(실행 기록은 action_run).
    op.execute("UPDATE message SET status = 'not_query' WHERE status = 'action_preview'")
    op.drop_constraint(op.f("ck_message_status"), "message", type_="check")
    op.create_check_constraint(op.f("ck_message_status"), "message", _OLD)
