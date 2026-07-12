from pydantic import BaseModel, ConfigDict, Field


class StoreLocationRead(BaseModel):
    """A store location with its parent chain info flattened in."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_name: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    is_active: bool
    chain_id: int
    chain_name: str
    chain_slug: str


class UserStoreRead(BaseModel):
    """A store the user has saved, with its default flag."""

    is_default: bool
    store: StoreLocationRead


class StoreSelectionUpdate(BaseModel):
    """Replace the user's saved store set (max 5)."""

    model_config = ConfigDict(extra="forbid")

    store_location_ids: list[int] = Field(default_factory=list, max_length=5)
    default_store_id: int | None = None


class DiscoveredStore(BaseModel):
    """A store surfaced by ZIP discovery, with deals-source status."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_name: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    chain_id: int
    chain_name: str
    chain_slug: str
    distance_miles: float | None = None
    has_deals_source: bool = False
    deals_status: str = "pending_source"


class DiscoverResponse(BaseModel):
    zip_code: str
    source: str  # 'places' | 'catalog'
    stores: list[DiscoveredStore]
