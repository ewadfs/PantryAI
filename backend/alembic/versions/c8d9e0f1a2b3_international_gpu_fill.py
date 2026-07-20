"""P43 follow-up: package weights for pack-sold proteins.

The first live recompute exposed a matched-but-unweighable hole: '1 package'
of bulgogi matched with macros but had no grams_per_typical_unit, so it
contributed zero mass AND zero macros invisibly. Re-runs the (idempotent)
international upsert whose ROWS now carry package weights — COALESCE fills
only the NULLs the first pass left.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-07-20
"""

import sqlalchemy as sa
from alembic import op

from app.services.international_foods import ROWS, UPSERT_SQL, row_params

revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    for row in ROWS:
        conn.execute(sa.text(UPSERT_SQL), row_params(row))


def downgrade() -> None:
    pass
