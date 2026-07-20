"""P43 B4: international-staples nutrition addendum (data migration).

Idempotent upsert of ≤30 protein-bearing international rows mined from the
live H Mart / Patel flyer extractions + the nutrition_gap worklist. The prod
DB is only reachable through migrations, so the addendum ships here; the
same rows are runnable locally via scripts/seed_international_nutrition.py.

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-07-20
"""

import sqlalchemy as sa
from alembic import op

from app.services.international_foods import ROWS, UPSERT_SQL, row_params

revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    for row in ROWS:
        conn.execute(sa.text(UPSERT_SQL), row_params(row))


def downgrade() -> None:
    # Data addendum — rows are additive and safe to keep; no-op downgrade.
    pass
