"""add recipes.pinned_items_json

Revision ID: e5d6f7a8b9c0
Revises: d3c4e5f6a7b8
Create Date: 2026-07-11

Records the pinned pantry items ("cook with this") a batch was built around, so
/recipes/latest can label the batch.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e5d6f7a8b9c0"
down_revision = "d3c4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recipes",
        sa.Column(
            "pinned_items_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("recipes", "pinned_items_json")
