"""Durable Batches-API collection: circular_fetches.pending_batch_id.

When vision extraction outlives the in-process polling ceiling (observed
live: 12-page batches queued >90 min at Anthropic), the batch id is recorded
here instead of abandoning the paid batch; the deals-refresh scheduler
collects finished batches on its next sweep.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-07-13
"""

import sqlalchemy as sa
from alembic import op

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "circular_fetches",
        sa.Column("pending_batch_id", sa.String(length=64)),
    )


def downgrade() -> None:
    op.drop_column("circular_fetches", "pending_batch_id")
