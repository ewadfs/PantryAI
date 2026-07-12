"""add ai_cost_events

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-07-12

Prompt 27: per-call AI usage + cost ledger for /stats/ai-costs.
"""

from alembic import op
import sqlalchemy as sa


revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_cost_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("model", sa.String(length=50), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("batch_api", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column("batch_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("circular_fetch_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_ai_cost_events_created", "ai_cost_events", ["created_at"])
    op.create_index(
        "ix_ai_cost_events_category_created",
        "ai_cost_events",
        ["category", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_cost_events_category_created", table_name="ai_cost_events")
    op.drop_index("ix_ai_cost_events_created", table_name="ai_cost_events")
    op.drop_table("ai_cost_events")
