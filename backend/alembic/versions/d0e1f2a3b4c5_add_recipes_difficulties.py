"""add recipes.difficulties

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-12

Prompt 26: per-batch difficulty selection (subset of easy/medium/hard;
NULL = all three).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY


revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recipes",
        sa.Column("difficulties", ARRAY(sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recipes", "difficulties")
