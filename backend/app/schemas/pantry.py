from datetime import date
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ScanItem(BaseModel):
    """A single detected item in a scan result (pre-confirmation)."""

    name: str
    quantity_estimate: str | None = None
    unit: str | None = None
    category: str | None = None
    freshness: str = "good"
    confidence: float = 0.0
    ingredient_id: int | None = None
    match_confidence: float = 0.0
    estimated_expiry: str | None = None


class ScanResponse(BaseModel):
    scan_id: int
    items: list[ScanItem]
    uncertain: list[str] = Field(default_factory=list)
    photo_count: int


class ConfirmItem(BaseModel):
    """A user-confirmed (possibly corrected) item to persist to the pantry."""

    model_config = ConfigDict(extra="forbid")

    name: str
    quantity_estimate: str | None = None
    unit: str | None = None
    category: str | None = None
    is_staple: bool = False


class Correction(BaseModel):
    """Feedback pairing what the AI said vs. what the user corrected it to."""

    ai_said: str
    user_said: str


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # replace = reconcile the whole pantry against this scan (absent non-staples
    # are deactivated). merge = upsert confirmed items + explicit removals only;
    # anything not in the scan is left untouched (fridge-only scans).
    mode: Literal["replace", "merge"]
    confirmed: list[ConfirmItem] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    corrections: list[Correction] = Field(default_factory=list)


class ConfirmResponse(BaseModel):
    scan_id: int
    confirmed: int
    removed: int
    active_items: int


class PantryItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    quantity_estimate: str | None = None
    unit: str | None = None
    category: str | None = None


class PantryItemUpdate(BaseModel):
    """Partial update of a pantry item. Accepts ``quantity`` or ``quantity_estimate``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    quantity_estimate: str | None = Field(
        default=None,
        validation_alias=AliasChoices("quantity_estimate", "quantity"),
    )
    freshness: str | None = None
    estimated_expiry: date | None = None
    is_staple: bool | None = None


class PantryItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None = None
    quantity_estimate: str | None = None
    unit: str | None = None
    category: str | None = None
    brand: str | None = None
    freshness: str
    estimated_expiry: date | None = None
    is_staple: bool
    source: str | None = None
    is_active: bool
    use_soon: bool = False


class PantryCategoryGroup(BaseModel):
    category: str
    items: list[PantryItemRead]


class PantryListResponse(BaseModel):
    count: int
    categories: list[PantryCategoryGroup]
