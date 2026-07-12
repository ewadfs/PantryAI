"""add recipes.direction

Revision ID: a7b8c9d0e1f2
Revises: f6e7d8c9b0a1
Create Date: 2026-07-11

Prompt 21 (Generation Composer): ephemeral per-batch direction the user typed.
"""

from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "f6e7d8c9b0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recipes", sa.Column("direction", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("recipes", "direction")
