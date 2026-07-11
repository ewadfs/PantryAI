"""Schemas for consolidated shopping-list generation and management."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class FromRecipe(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recipe_id: int
    title: str
    qty: float | str | None = None
    unit: str | None = None


class ShoppingItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ingredient_id: int | None = None
    display_name: str | None = None
    quantity: str | None = None          # maps from buy_quantity
    unit: str | None = None
    category: str | None = None
    price: Decimal | None = None
    is_on_sale: bool = False
    regular_price: Decimal | None = None
    deal_id: int | None = None
    from_recipes: list[FromRecipe] | None = None
    is_checked: bool = False
    is_manual_add: bool = False
    notes: str | None = None


class ShoppingListRead(BaseModel):
    id: int
    week_start: date | None = None
    status: str
    store_name: str | None = None
    total_known_cost: Decimal | None = None
    deal_savings: Decimal | None = None
    item_count: int | None = None
    items: list[ShoppingItemRead] = Field(default_factory=list)


class CategoryGroup(BaseModel):
    category: str
    items: list[ShoppingItemRead]


class AlsoOnSale(BaseModel):
    deal_id: int
    product_name: str
    sale_price: Decimal
    regular_price: Decimal | None = None
    savings_pct: Decimal | None = None
    price_unit: str | None = None


class CurrentListResponse(BaseModel):
    id: int
    week_start: date | None = None
    status: str
    store_name: str | None = None
    total_known_cost: Decimal | None = None
    deal_savings: Decimal | None = None
    item_count: int | None = None
    categories: list[CategoryGroup]
    also_on_sale: list[AlsoOnSale]


class BuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date


class ManualAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str
    quantity: str | None = None
    unit: str | None = None
    notes: str | None = None


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_checked: bool


class CompleteResponse(BaseModel):
    items_added_to_pantry: int
