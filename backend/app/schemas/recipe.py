"""Schemas for recipe generation, rating, and the This Week list."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RecipeIngredient(BaseModel):
    """One ingredient line. ``on_sale``/``sale_*`` reflect OUR deal table.

    ``name`` mirrors ``generic_name`` (kept for the shopping-list builder);
    ``brand`` is a separate, optional sub-label — never embedded in the name.
    ``in_pantry`` is tri-state: True (have enough), "partial" (have some, must
    buy the shortfall), or False (buy it all).
    """

    model_config = ConfigDict(extra="ignore")

    name: str
    generic_name: str | None = None
    brand: str | None = None
    quantity: str | float | None = None
    unit: str | None = None
    in_pantry: bool | Literal["partial"] = False
    pantry_quantity: str | None = None
    shortfall_quantity: str | None = None
    on_sale: bool = False
    sale_store: str | None = None
    sale_price: Decimal | None = None


class KeyIngredient(BaseModel):
    """A defining ingredient from the fast concept stage."""

    model_config = ConfigDict(extra="ignore")

    generic_name: str
    brand: str | None = None
    in_pantry: bool = False
    on_sale: bool = False
    sale_store: str | None = None
    sale_price: Decimal | None = None


class NutritionPerServing(BaseModel):
    model_config = ConfigDict(extra="ignore")

    calories: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    fiber_g: float | None = None
    # 'calculated' = deterministic USDA compute (coverage ≥ 70%); 'est' = model
    # estimate. None on legacy/concept rows. ``coverage`` present only when
    # calculated (fraction of recipe mass with known macros).
    source: Literal["calculated", "est"] | None = None
    coverage: float | None = None


class RecipeCost(BaseModel):
    """Honest cost block — no fabricated prices."""

    known_buy_cost: Decimal
    unknown_priced_items: int
    pantry_items_used: int


class MarketAnchor(BaseModel):
    """The deal a market-pick recipe is built around (Prompt 28 A)."""

    model_config = ConfigDict(extra="ignore")

    name: str
    sale_price: Decimal | None = None
    price_unit: str | None = None
    savings_pct: float | None = None
    store: str | None = None
    # True when the anchor comes from a saved store other than the batch's
    # default — the sparse-store fallback (Prompt 32 #4); the UI labels it.
    cross_store: bool = False


class ProteinBelowFloorFlag(BaseModel):
    model_config = ConfigDict(extra="ignore")

    protein_g: float
    floor_g: float


class HeavyFlag(BaseModel):
    model_config = ConfigDict(extra="ignore")

    calories: float
    cap: float
    daily_target: float | None = None


class QualityFlags(BaseModel):
    """Honesty flags (Prompt 32 C): a recipe below the protein floor or above
    the calorie band ships ONLY with these, rendered as amber chips."""

    model_config = ConfigDict(extra="ignore")

    protein_below_floor: ProteinBelowFloorFlag | None = None
    heavy: HeavyFlag | None = None


class RecipeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str = "ready"
    title: str
    description: str | None = None
    difficulty: str | None = None
    prep_time_min: int | None = None
    cook_time_min: int | None = None
    total_time_min: int | None = None
    servings: int | None = None
    why_this_recipe: str | None = None
    key_ingredients: list[KeyIngredient] = Field(default_factory=list)
    ingredients: list[RecipeIngredient] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)
    nutrition_per_serving: NutritionPerServing | None = None
    tags: list[str] | None = None
    cuisine: str | None = None
    rating: int | None = None
    generated_at: datetime | None = None
    cost: RecipeCost
    is_market_pick: bool = False
    market_anchor: MarketAnchor | None = None
    quality_flags: QualityFlags | None = None


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pinned_pantry_item_ids: list[int] = Field(default_factory=list, max_length=3)
    # Ephemeral, per-batch steer ("grill something", "use the wok"). Applies only
    # to the batch it's typed for; does not carry into the next generation.
    direction: str | None = Field(default=None, max_length=200)
    # Which difficulty tiers to draw from. Empty (or omitted) means all three.
    difficulties: list[Literal["easy", "medium", "hard"]] = Field(default_factory=list)

    @field_validator("difficulties")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        order = {"easy": 0, "medium": 1, "hard": 2}
        seen = {d for d in v if d in order}
        return sorted(seen, key=lambda d: order[d])


class GenerateResponse(BaseModel):
    recipes: list[RecipeRead]


class LatestResponse(BaseModel):
    generated_at: datetime | None = None
    store_name: str | None = None
    pinned: list[str] = Field(default_factory=list)
    direction: str | None = None
    difficulties: list[str] = Field(default_factory=list)
    recipes: list[RecipeRead]


class RateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: int

    @field_validator("rating")
    @classmethod
    def _thumbs(cls, v: int) -> int:
        if v not in (1, -1):
            raise ValueError("rating must be 1 (up) or -1 (down)")
        return v


class SaveToWeekRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date | None = None


class WeekRecipeRead(BaseModel):
    week_start: date
    is_cooked: bool
    cooked_at: datetime | None = None
    recipe: RecipeRead


class WeekResponse(BaseModel):
    week_start: date
    recipes: list[WeekRecipeRead]
