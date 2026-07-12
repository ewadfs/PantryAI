from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SupportedChain(Base):
    __tablename__ = "supported_chains"

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_name: Mapped[str] = mapped_column(String(120), nullable=False)
    chain_slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    has_weekly_circular: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    circular_refresh_day: Mapped[str | None] = mapped_column(String(10))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    # --- National catalog (Prompt 24) ---
    # Banner-level rows; the corporate parent is stored separately.
    parent_company: Mapped[str | None] = mapped_column(String(120))
    # Coarse grouping: national/regional/local/discount/international/natural/club.
    category: Mapped[str | None] = mapped_column(String(30))
    # States/regions the banner serves (from Wikipedia), for discovery + probing.
    areas_served: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    # Google Places text-search query for store discovery.
    google_places_query: Mapped[str | None] = mapped_column(String(200))
    # Deals source resolved by the probe.
    source_url: Mapped[str | None] = mapped_column(String(500))
    # 'aggregator' | 'chain_site' | 'structured' | None
    source_type: Mapped[str | None] = mapped_column(String(20))
    # 'active' once a source is known, else 'pending_source'.
    deals_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending_source'")
    )


class StoreLocation(Base):
    __tablename__ = "store_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(
        ForeignKey("supported_chains.id"), nullable=False
    )
    store_name: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(2))
    zip_code: Mapped[str | None] = mapped_column(String(10))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    # Deals region this location belongs to ({chain_slug}:{state}); groups a
    # chain's stores that share a circular so fetches aren't duplicated.
    region_key: Mapped[str | None] = mapped_column(String(60))
    # Google Places id when discovered via the Places API (dedupes upserts).
    google_place_id: Mapped[str | None] = mapped_column(String(200), unique=True)


class UserStore(Base):
    __tablename__ = "user_stores"
    __table_args__ = (
        UniqueConstraint("user_id", "store_location_id", name="uq_user_store"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    store_location_id: Mapped[int] = mapped_column(
        ForeignKey("store_locations.id"), nullable=False
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StoreRequest(Base):
    """Demand log for chain×region combos we can't yet source deals for.

    When a user selects a store whose chain is ``pending_source``, we bump the
    count here so the pipeline knows where sourcing effort would pay off.
    """

    __tablename__ = "store_requests"
    __table_args__ = (
        UniqueConstraint("chain_id", "region_key", name="uq_store_request_combo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(
        ForeignKey("supported_chains.id"), nullable=False
    )
    chain_slug: Mapped[str | None] = mapped_column(String(120))
    region_key: Mapped[str | None] = mapped_column(String(60))
    zip_code: Mapped[str | None] = mapped_column(String(10))
    request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    first_requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ZipDiscoveryCache(Base):
    """Cached Google Places discovery payload for a ZIP (30-day TTL)."""

    __tablename__ = "zip_discovery_cache"

    zip_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    payload: Mapped[dict | list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
