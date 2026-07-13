"""Prompt 35 (Pantry Mode): recipes.pantry_mode — the per-batch
minimize-buying switch, persisted so /latest and warm-cache reuse it.

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-07-13
"""

import sqlalchemy as sa

from alembic import op

revision = "f9a0b1c2d3e4"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recipes",
        sa.Column("pantry_mode", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("recipes", "pantry_mode")
