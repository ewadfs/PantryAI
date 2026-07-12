"""Prompt 32: recipes.quality_flags_json (honesty chips) + ai_cost_events.stage
(per-stage model attribution for the cost ledger).

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "e8f9a0b1c2d3"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recipes", sa.Column("quality_flags_json", JSONB(), nullable=True))
    op.add_column(
        "ai_cost_events", sa.Column("stage", sa.String(length=20), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ai_cost_events", "stage")
    op.drop_column("recipes", "quality_flags_json")
