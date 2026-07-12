"""add shopping_lists.pantry_value_used

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-11

Prompt 22 (Savings Scoreboard): known market value of pantry items a list's
recipes reused instead of buying.
"""

from alembic import op
import sqlalchemy as sa

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shopping_lists",
        sa.Column("pantry_value_used", sa.Numeric(10, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("shopping_lists", "pantry_value_used")
