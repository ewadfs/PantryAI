from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class SavingsBucket(BaseModel):
    deal_savings: Decimal
    pantry_value_used: Decimal
    trips: int
    items: int


class LastTrip(BaseModel):
    date: datetime | None = None
    store: str | None = None
    deal_savings: Decimal
    known_cost: Decimal


class SavingsResponse(BaseModel):
    all_time: SavingsBucket
    this_month: SavingsBucket
    last_trip: LastTrip | None = None
    cooked_recipe_count: int
