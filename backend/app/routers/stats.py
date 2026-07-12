"""Savings scoreboard — cumulative deal savings from COMPLETED shopping trips.

Honest math throughout: a checked item contributes to deal_savings only when
both its sale price and regular price are known; unknowns are never estimated
into a total.
"""

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.recipe import WeekRecipe
from app.models.shopping import ShoppingList, ShoppingListItem
from app.models.user import User
from app.schemas.stats import LastTrip, SavingsBucket, SavingsResponse
from app.services.auth import get_current_user

router = APIRouter(tags=["stats"])

_Z = Decimal("0.00")


def _bucket(lists: list[ShoppingList], items_by_list: dict[int, list[ShoppingListItem]]) -> SavingsBucket:
    savings = Decimal("0")
    pantry_value = Decimal("0")
    item_count = 0
    for sl in lists:
        pantry_value += sl.pantry_value_used or Decimal("0")
        for it in items_by_list.get(sl.id, []):
            if not it.is_checked:
                continue
            item_count += 1
            if it.price is not None and it.regular_price is not None:
                diff = it.regular_price - it.price
                if diff > 0:
                    savings += diff
    return SavingsBucket(
        deal_savings=savings.quantize(_Z),
        pantry_value_used=pantry_value.quantize(_Z),
        trips=len(lists),
        items=item_count,
    )


@router.get("/stats/savings", response_model=SavingsResponse)
async def savings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SavingsResponse:
    completed = (
        (
            await db.execute(
                select(ShoppingList)
                .where(
                    ShoppingList.user_id == current_user.id,
                    ShoppingList.status == "completed",
                )
                .order_by(ShoppingList.completed_at.desc())
            )
        )
        .scalars()
        .all()
    )

    # Checked items for those lists, grouped by list id.
    items_by_list: dict[int, list[ShoppingListItem]] = {}
    if completed:
        list_ids = [sl.id for sl in completed]
        rows = (
            (
                await db.execute(
                    select(ShoppingListItem).where(
                        ShoppingListItem.list_id.in_(list_ids),
                        ShoppingListItem.is_checked.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        for it in rows:
            items_by_list.setdefault(it.list_id, []).append(it)

    now = datetime.now(timezone.utc)
    this_month_lists = [
        sl
        for sl in completed
        if sl.completed_at is not None
        and sl.completed_at.year == now.year
        and sl.completed_at.month == now.month
    ]

    all_time = _bucket(completed, items_by_list)
    this_month = _bucket(this_month_lists, items_by_list)

    last_trip: LastTrip | None = None
    if completed:
        sl = completed[0]  # ordered by completed_at desc
        trip_savings = Decimal("0")
        trip_known = Decimal("0")
        for it in items_by_list.get(sl.id, []):
            if it.price is not None:
                trip_known += it.price
                if it.regular_price is not None and it.regular_price - it.price > 0:
                    trip_savings += it.regular_price - it.price
        last_trip = LastTrip(
            date=sl.completed_at,
            store=sl.priced_store_name,
            deal_savings=trip_savings.quantize(_Z),
            known_cost=trip_known.quantize(_Z),
        )

    cooked = (
        await db.scalar(
            select(func.count())
            .select_from(WeekRecipe)
            .where(
                WeekRecipe.user_id == current_user.id,
                WeekRecipe.is_cooked.is_(True),
            )
        )
    ) or 0

    return SavingsResponse(
        all_time=all_time,
        this_month=this_month,
        last_trip=last_trip,
        cooked_recipe_count=cooked,
    )
