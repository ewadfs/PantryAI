from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class RefreshRequest(BaseModel):
    """Optional body for POST /deals/refresh."""

    model_config = ConfigDict(extra="forbid")

    chain_slugs: list[str] | None = None


class ChainRefreshResult(BaseModel):
    chain: str
    status: str
    pages: int = 0
    deals: int = 0
    matched: int = 0
    regular_price: int | None = None
    error: str | None = None


class RefreshResponse(BaseModel):
    results: list[ChainRefreshResult]


class DealRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_name: str
    brand: str | None = None
    sale_price: Decimal
    price_unit: str | None = None
    regular_price: Decimal | None = None
    savings_pct: Decimal | None = None
    deal_type: str | None = None
    deal_details: str | None = None
    category: str | None = None
    purchase_limit: str | None = None
    confidence: Decimal | None = None
    matched_ingredient_id: int | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    page_number: int | None = None


class DealListResponse(BaseModel):
    count: int = Field(description="Total matching deals (before pagination).")
    page: int
    per_page: int
    # 'ready' | 'loading' (activation in progress) | 'pending_source' | 'no_store'
    state: str = "ready"
    deals: list[DealRead]


class DealsStateResponse(BaseModel):
    """Lightweight banner state for the Deals tab / Home."""

    state: str = "no_store"
    chain_name: str | None = None
    region_key: str | None = None
    # Whether the circular viewer feature is exposed (P37 B5 flag) — gates the
    # "View circular" entry points client-side.
    circular_viewer: bool = False


class CircularPage(BaseModel):
    """One flyer page: a short-lived presigned image URL + the deals our
    extractor read off exactly that page."""

    page_number: int
    image_url: str
    deals: list[DealRead]


class CircularResponse(BaseModel):
    """The current circular for one of the user's saved stores (P37 B).

    state: 'ready' (pages render) | 'no_images' (structured-source chain or
    unreadable storage — grouped deal list shows instead) | 'expired' (no
    valid fetch — show when the new one lands) | 'no_store'.
    """

    state: str
    chain_name: str | None = None
    chain_slug: str | None = None
    store_name: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    refresh_day: str | None = None
    pages: list[CircularPage] = Field(default_factory=list)
    # no_images fallback: every current deal, for a grouped list view.
    deals: list[DealRead] = Field(default_factory=list)
