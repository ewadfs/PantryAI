"""recipe market picks (Prompt 28 A)

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-07-12

Flags recipes that are deal-anchored MARKET PICKS (built around a current deal
the user does not own) and stores the anchoring deal for surfacing + rotation.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "c6d7e8f9a0b1"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recipes",
        sa.Column(
            "is_market_pick",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("recipes", sa.Column("market_anchor_json", JSONB()))


def downgrade() -> None:
    op.drop_column("recipes", "market_anchor_json")
    op.drop_column("recipes", "is_market_pick")
