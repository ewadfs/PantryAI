"""add recipes.generated_store_name

Revision ID: c2b3d4e5f6a7
Revises: b1a2c3d4e5f6
Create Date: 2026-07-11

Records which store a recipe batch was generated against, so the weekly
store-selector can flag a batch as stale after the default store changes.
"""

from alembic import op
import sqlalchemy as sa

revision = "c2b3d4e5f6a7"
down_revision = "b1a2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recipes",
        sa.Column("generated_store_name", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recipes", "generated_store_name")
