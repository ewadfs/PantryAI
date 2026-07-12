"""add users.recipes_per_generation

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-12

Prompt 25: recipes proposed per generation, 3 or 5 (default 5).
"""

from alembic import op
import sqlalchemy as sa

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "recipes_per_generation",
            sa.SmallInteger(),
            nullable=False,
            server_default="5",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "recipes_per_generation")
