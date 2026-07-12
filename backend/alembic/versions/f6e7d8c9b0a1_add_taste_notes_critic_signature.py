"""add users.taste_notes and recipes.critic_json/signature_json

Revision ID: f6e7d8c9b0a1
Revises: e5d6f7a8b9c0
Create Date: 2026-07-11

Prompt 19 (Recipe Quality Engine): free-text taste profile on users, plus
critic scores and a variety signature persisted per recipe.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "f6e7d8c9b0a1"
down_revision = "e5d6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("taste_notes", sa.Text(), nullable=True))
    op.add_column("recipes", sa.Column("critic_json", JSONB(), nullable=True))
    op.add_column("recipes", sa.Column("signature_json", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("recipes", "signature_json")
    op.drop_column("recipes", "critic_json")
    op.drop_column("users", "taste_notes")
