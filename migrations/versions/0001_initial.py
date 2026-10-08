"""initial: actions, audit_log, inquiries

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "actions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("inquiry_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_actions_type", "actions", ["type"])
    op.create_index("ix_actions_status", "actions", ["status"])
    op.create_index("ix_actions_inquiry_id", "actions", ["inquiry_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("action_id", sa.String(32), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"])
    op.create_index("ix_audit_log_action_id", "audit_log", ["action_id"])

    op.create_table(
        "inquiries",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("dedupe_key", sa.String(255), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("sender", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_inquiries_dedupe_key", "inquiries", ["dedupe_key"], unique=True)
    op.create_index("ix_inquiries_sender", "inquiries", ["sender"])


def downgrade() -> None:
    op.drop_table("inquiries")
    op.drop_table("audit_log")
    op.drop_table("actions")
