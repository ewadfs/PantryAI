"""add shopping_lists.priced_store_name

Revision ID: d3c4e5f6a7b8
Revises: c2b3d4e5f6a7
Create Date: 2026-07-11

Records which store a shopping list was priced against, so the UI can prompt a
rebuild after the default store changes.
"""

from alembic import op
import sqlalchemy as sa

revision = "d3c4e5f6a7b8"
down_revision = "c2b3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shopping_lists",
        sa.Column("priced_store_name", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("shopping_lists", "priced_store_name")
