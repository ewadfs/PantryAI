"""P41: web-push subscriptions + send log, shareable recipe slugs.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-07-20
"""

import sqlalchemy as sa
from alembic import op

revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"),
            nullable=False, index=True,
        ),
        sa.Column("endpoint", sa.Text(), nullable=False, unique=True),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "push_sends",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "fetch_id", sa.Integer(), sa.ForeignKey("circular_fetches.id"),
            nullable=False,
        ),
        sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("region_key", sa.String(length=120)),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_push_sends_user_sent", "push_sends", ["user_id", "sent_at"])
    op.create_index(
        "uq_push_sends_user_fetch", "push_sends", ["user_id", "fetch_id"],
        unique=True,
    )

    op.add_column("recipes", sa.Column("share_slug", sa.String(length=24)))
    op.add_column(
        "recipes", sa.Column("shared_at", sa.DateTime(timezone=True))
    )
    op.create_unique_constraint("uq_recipes_share_slug", "recipes", ["share_slug"])


def downgrade() -> None:
    op.drop_constraint("uq_recipes_share_slug", "recipes")
    op.drop_column("recipes", "shared_at")
    op.drop_column("recipes", "share_slug")
    op.drop_table("push_sends")
    op.drop_table("push_subscriptions")
