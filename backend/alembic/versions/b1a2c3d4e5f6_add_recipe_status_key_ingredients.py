"""add recipe status and key_ingredients

Revision ID: b1a2c3d4e5f6
Revises: cc021e29fc12
Create Date: 2026-07-11

Two-stage recipe generation: a recipe is first persisted as a 'concept'
(fast, small Claude call) and later filled in to 'ready'. ``key_ingredients_json``
holds the 4-6 defining ingredients from the concept stage.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b1a2c3d4e5f6"
down_revision = "cc021e29fc12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recipes",
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="ready",
            nullable=False,
        ),
    )
    op.add_column(
        "recipes",
        sa.Column(
            "key_ingredients_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("recipes", "key_ingredients_json")
    op.drop_column("recipes", "status")
