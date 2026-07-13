from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CircularFetch(Base):
    __tablename__ = "circular_fetches"

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(
        ForeignKey("supported_chains.id"), nullable=False
    )
    fetch_date: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(String(500))
    page_count: Mapped[int | None] = mapped_column(Integer)
    image_keys: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    status: Mapped[str | None] = mapped_column(String(20))
    error_message: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    # Deals region this fetch covers ({chain_slug}:{state}).
    region_key: Mapped[str | None] = mapped_column(String(60))
    # A submitted-but-unfinished Batches-API extraction: the paid batch id is
    # recorded instead of abandoned when it outlives the in-process polling
    # ceiling; the deals-refresh scheduler collects it when it ends.
    pending_batch_id: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DealCache(Base):
    __tablename__ = "deal_cache"
    __table_args__ = (
        CheckConstraint("sale_price > 0", name="ck_deal_cache_sale_price_positive"),
        Index("ix_deal_cache_chain_validity", "chain_id", "valid_from", "valid_to"),
        Index("ix_deal_cache_matched_ingredient", "matched_ingredient_id"),
        Index("ix_deal_cache_region_validity", "region_key", "valid_from", "valid_to"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(
        ForeignKey("supported_chains.id"), nullable=False
    )
    fetch_id: Mapped[int] = mapped_column(
        ForeignKey("circular_fetches.id"), nullable=False
    )
    product_name: Mapped[str] = mapped_column(String(300), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(200))
    sale_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    price_unit: Mapped[str | None] = mapped_column(String(50))
    regular_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    savings_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    deal_type: Mapped[str | None] = mapped_column(String(30))
    deal_details: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(50))
    purchase_limit: Mapped[str | None] = mapped_column(String(50))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    matched_ingredient_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingredient_master.id")
    )
    match_confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    # Deals region ({chain_slug}:{state}); filters deals to the user's store.
    region_key: Mapped[str | None] = mapped_column(String(60))
    page_number: Mapped[int | None] = mapped_column(Integer)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
