"""P38 E8: seed King Kullen's nearest-LI store location (data migration).

King Kullen had zero store_locations in production, so no user could save it
and the demand-activation path could never fire. Address verified against
kingkullen.com's own store-marker API (wp-json/wpgmza/v1/markers). Idempotent:
inserts only when the chain exists and the location doesn't.

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-07-13
"""

from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO store_locations
            (chain_id, store_name, address, city, state, zip_code,
             latitude, longitude, is_active, region_key)
        SELECT sc.id, 'King Kullen of Garden City Park',
               '2305 Jericho Turnpike', 'Garden City Park', 'NY', '11040',
               40.7430760, -73.6635944, true, 'king_kullen:NY'
        FROM supported_chains sc
        WHERE sc.chain_slug = 'king_kullen'
          AND NOT EXISTS (
              SELECT 1 FROM store_locations sl
              WHERE sl.chain_id = sc.id AND sl.city = 'Garden City Park'
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM store_locations sl
        USING supported_chains sc
        WHERE sl.chain_id = sc.id
          AND sc.chain_slug = 'king_kullen'
          AND sl.city = 'Garden City Park'
        """
    )
