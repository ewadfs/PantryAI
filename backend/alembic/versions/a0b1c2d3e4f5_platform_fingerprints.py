"""Prompt 38 (any circular fetchable): platform fingerprints + profiles.

- supported_chains.platform / platform_evidence — the probe v2 fingerprint
  (which viewer family serves this chain's weekly ad, and what proved it).
- platform_profiles — one row of viewer hints (ready/next/page selectors,
  navigation mode) per platform family, so a single profile serves every
  chain on that platform.

Revision ID: a0b1c2d3e4f5
Revises: f9a0b1c2d3e4
Create Date: 2026-07-13
"""

import sqlalchemy as sa
from alembic import op

revision = "a0b1c2d3e4f5"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "supported_chains", sa.Column("platform", sa.String(length=40))
    )
    op.add_column(
        "supported_chains", sa.Column("platform_evidence", sa.Text())
    )
    op.create_table(
        "platform_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(length=40), nullable=False, unique=True),
        # 'paginated' (next-button loop) or 'scroll' (segmented long capture).
        sa.Column("viewer_mode", sa.String(length=12), nullable=False),
        # Substring that identifies the viewer iframe (capture inside it).
        sa.Column("frame_url_pattern", sa.String(length=200)),
        sa.Column("ready_selector", sa.String(length=300)),
        sa.Column("next_selector", sa.String(length=300)),
        sa.Column("page_selector", sa.String(length=300)),
        sa.Column(
            "max_pages", sa.Integer(), nullable=False, server_default=sa.text("24")
        ),
        sa.Column("notes", sa.Text()),
    )


def downgrade() -> None:
    op.drop_table("platform_profiles")
    op.drop_column("supported_chains", "platform_evidence")
    op.drop_column("supported_chains", "platform")
