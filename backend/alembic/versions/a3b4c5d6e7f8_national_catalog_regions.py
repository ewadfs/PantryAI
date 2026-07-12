"""national catalog + regions (Prompt 24)

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-07-12

Decouples the store CATALOG (national) from the circular PIPELINE (regional):
new chain metadata, region_key on locations/fetches/deals, a demand log, and a
ZIP discovery cache. Backfills existing Long Island data to the {chain}:NY
region so today's users keep their deals.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB


revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # supported_chains: catalog metadata
    op.add_column("supported_chains", sa.Column("parent_company", sa.String(120)))
    op.add_column("supported_chains", sa.Column("category", sa.String(30)))
    op.add_column("supported_chains", sa.Column("areas_served", ARRAY(sa.Text())))
    op.add_column("supported_chains", sa.Column("google_places_query", sa.String(200)))
    op.add_column("supported_chains", sa.Column("source_url", sa.String(500)))
    op.add_column("supported_chains", sa.Column("source_type", sa.String(20)))
    op.add_column(
        "supported_chains",
        sa.Column(
            "deals_status",
            sa.String(20),
            nullable=False,
            server_default="pending_source",
        ),
    )
    # Existing chains already have working aggregator sources — mark them active.
    op.execute(
        "UPDATE supported_chains SET deals_status = 'active', source_type = 'aggregator' "
        "WHERE chain_slug IN ('shoprite', 'stop_and_shop', 'lidl')"
    )

    # store_locations: region + Places id
    op.add_column("store_locations", sa.Column("region_key", sa.String(60)))
    op.add_column("store_locations", sa.Column("google_place_id", sa.String(200)))
    op.create_unique_constraint(
        "uq_store_locations_place_id", "store_locations", ["google_place_id"]
    )

    # circular_fetches + deal_cache: region
    op.add_column("circular_fetches", sa.Column("region_key", sa.String(60)))
    op.add_column("deal_cache", sa.Column("region_key", sa.String(60)))
    op.create_index(
        "ix_deal_cache_region_validity",
        "deal_cache",
        ["region_key", "valid_from", "valid_to"],
    )

    # Backfill existing data to {chain_slug}:{state or NY}.
    # Use ':' || 'NY' (not ':NY') so SQLAlchemy doesn't parse :NY as a bind param.
    op.execute(
        "UPDATE store_locations sl SET region_key = sc.chain_slug || ':' || "
        "COALESCE(sl.state, 'NY') FROM supported_chains sc WHERE sl.chain_id = sc.id "
        "AND sl.region_key IS NULL"
    )
    op.execute(
        "UPDATE circular_fetches cf SET region_key = sc.chain_slug || ':' || 'NY' "
        "FROM supported_chains sc WHERE cf.chain_id = sc.id AND cf.region_key IS NULL"
    )
    op.execute(
        "UPDATE deal_cache dc SET region_key = sc.chain_slug || ':' || 'NY' "
        "FROM supported_chains sc WHERE dc.chain_id = sc.id AND dc.region_key IS NULL"
    )

    # store_requests: demand log
    op.create_table(
        "store_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "chain_id", sa.Integer(), sa.ForeignKey("supported_chains.id"), nullable=False
        ),
        sa.Column("chain_slug", sa.String(120)),
        sa.Column("region_key", sa.String(60)),
        sa.Column("zip_code", sa.String(10)),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "first_requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("chain_id", "region_key", name="uq_store_request_combo"),
    )

    # zip_discovery_cache
    op.create_table(
        "zip_discovery_cache",
        sa.Column("zip_code", sa.String(10), primary_key=True),
        sa.Column("payload", JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("zip_discovery_cache")
    op.drop_table("store_requests")
    op.drop_index("ix_deal_cache_region_validity", table_name="deal_cache")
    op.drop_column("deal_cache", "region_key")
    op.drop_column("circular_fetches", "region_key")
    op.drop_constraint(
        "uq_store_locations_place_id", "store_locations", type_="unique"
    )
    op.drop_column("store_locations", "google_place_id")
    op.drop_column("store_locations", "region_key")
    for col in (
        "deals_status",
        "source_type",
        "source_url",
        "google_places_query",
        "areas_served",
        "category",
        "parent_company",
    ):
        op.drop_column("supported_chains", col)
