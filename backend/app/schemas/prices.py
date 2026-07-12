"""Cross-store price comparison.

deal_cache only knows FLYER prices: a missing price at a store means "no deal
listed there," never "not sold there." We report known costs + coverage and
never fabricate totals.
"""

from decimal import Decimal

from pydantic import BaseModel


class MatchedDeal(BaseModel):
    ingredient: str
    sale_price: Decimal
    regular_price: Decimal | None = None


class PriceStore(BaseModel):
    store_id: int
    store_name: str | None = None
    chain_name: str | None = None
    is_default: bool = False
    known_cost_sum: Decimal
    priced_count: int
    total_count: int
    unpriced_count: int
    matched_deals: list[MatchedDeal]


class PriceCompareResponse(BaseModel):
    needed_count: int
    stores: list[PriceStore]
