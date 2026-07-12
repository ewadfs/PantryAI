"""Consolidated shopping-list builder.

Turns a week's saved recipes into one de-duplicated, honestly-priced buy list:
skip what's already in the pantry (re-checked live), never list staples, aggregate
duplicates across recipes, and price each line only when OUR deal_cache backs it —
otherwise the price is null (the frontend renders an em dash).
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, nulls_last, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deal import DealCache
from app.models.ingredient import IngredientMaster
from app.models.pantry import PantryItem
from app.models.recipe import Recipe, WeekRecipe
from app.models.shopping import ShoppingList, ShoppingListItem
from app.models.store import StoreLocation, SupportedChain, UserStore
from app.models.user import User
from app.services import ingredient_matcher


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _qty_to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    num = ""
    for ch in str(value).strip():
        if ch.isdigit() or ch in ".-":
            num += ch
        elif num:
            break
    try:
        return float(num) if num else None
    except ValueError:
        return None


def _fmt_qty(value: float | None) -> str | None:
    if value is None:
        return None
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _unit_key(unit: str | None) -> str:
    return (unit or "").strip().lower()


async def default_chain(
    db: AsyncSession, user_id: int
) -> tuple[int | None, str | None, str | None]:
    """(chain_id, chain_name, store_name) of the user's default store."""
    row = (
        await db.execute(
            select(
                StoreLocation.chain_id,
                SupportedChain.chain_name,
                StoreLocation.store_name,
            )
            .join(UserStore, UserStore.store_location_id == StoreLocation.id)
            .join(SupportedChain, SupportedChain.id == StoreLocation.chain_id)
            .where(UserStore.user_id == user_id, UserStore.is_default.is_(True))
        )
    ).first()
    return (row[0], row[1], row[2]) if row else (None, None, None)


async def current_deals(
    db: AsyncSession, chain_id: int, today: date
) -> list[DealCache]:
    """Current-valid deals for a chain, best savings first."""
    return (
        (
            await db.execute(
                select(DealCache)
                .where(
                    DealCache.chain_id == chain_id,
                    DealCache.valid_to >= today,
                    or_(DealCache.valid_from <= today, DealCache.valid_from.is_(None)),
                )
                .order_by(nulls_last(DealCache.savings_pct.desc()))
            )
        )
        .scalars()
        .all()
    )


async def _best_deal_by_ingredient(
    db: AsyncSession, chain_id: int | None, today: date
) -> dict[int, DealCache]:
    if chain_id is None:
        return {}
    out: dict[int, DealCache] = {}
    for d in await current_deals(db, chain_id, today):
        iid = d.matched_ingredient_id
        if iid is None:
            continue
        best = out.get(iid)
        if best is None or d.sale_price < best.sale_price:
            out[iid] = d
    return out


def serialize_item(item: ShoppingListItem) -> dict:
    return {
        "id": item.id,
        "ingredient_id": item.ingredient_id,
        "display_name": item.display_name,
        "quantity": item.buy_quantity,
        "unit": item.unit,
        "category": item.category,
        "price": item.price,
        "is_on_sale": item.is_on_sale,
        "regular_price": item.regular_price,
        "deal_id": item.deal_id,
        "from_recipes": item.from_recipes,
        "is_checked": item.is_checked,
        "is_manual_add": item.is_manual_add,
        "notes": item.notes,
    }


def serialize_list(list_obj: ShoppingList, items: list[ShoppingListItem]) -> dict:
    return {
        "id": list_obj.id,
        "week_start": list_obj.week_start,
        "status": list_obj.status,
        "store_name": list_obj.priced_store_name,
        "total_known_cost": list_obj.total_known_cost,
        "deal_savings": list_obj.deal_savings,
        "item_count": list_obj.item_count,
        "items": [serialize_item(i) for i in items],
    }


def compute_totals(items: list[ShoppingListItem]) -> tuple[Decimal, Decimal, int]:
    """(total_known_cost, deal_savings, item_count)."""
    total = Decimal("0")
    savings = Decimal("0")
    for it in items:
        if it.price is not None:
            total += it.price
        if it.price is not None and it.regular_price is not None:
            diff = it.regular_price - it.price
            if diff > 0:
                savings += diff
    return total.quantize(Decimal("0.01")), savings.quantize(Decimal("0.01")), len(items)


async def _await_week_ready(
    db: AsyncSession, user_id: int, week_start: date, timeout: float = 20.0
) -> None:
    """Wait (briefly) for this week's recipes to finish Stage-2 detail generation.

    Building a list from a 'concept' recipe would produce a partial list, so we
    poll until all are 'ready' or raise so the caller can surface a clear error.
    """
    deadline = timeout
    while True:
        statuses = (
            (
                await db.execute(
                    select(Recipe.status)
                    .join(WeekRecipe, WeekRecipe.recipe_id == Recipe.id)
                    .where(
                        WeekRecipe.user_id == user_id,
                        WeekRecipe.week_start == week_start,
                    )
                )
            )
            .scalars()
            .all()
        )
        if all(s == "ready" for s in statuses):
            return
        if deadline <= 0:
            raise ValueError(
                "Some saved recipes are still being written. "
                "Give it a few seconds and try again."
            )
        await asyncio.sleep(2)
        deadline -= 2


async def build_list(
    db: AsyncSession, user: User, week_start: date
) -> tuple[ShoppingList, list[ShoppingListItem]]:
    """Build (and persist, replacing any active list for the week) the buy list."""
    today = date.today()
    await _await_week_ready(db, user.id, week_start)
    await ingredient_matcher.preload(db)

    # Reference maps: ingredient category/shelf-life/staple, live pantry, deals.
    master = {
        row.id: row
        for row in (await db.execute(select(IngredientMaster))).scalars().all()
    }
    active_items = (
        (
            await db.execute(
                select(PantryItem).where(
                    PantryItem.user_id == user.id, PantryItem.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    active_by_iid: dict[int, PantryItem] = {}
    active_by_norm: dict[str, PantryItem] = {}
    for it in active_items:
        if it.ingredient_id is not None:
            active_by_iid.setdefault(it.ingredient_id, it)
        active_by_norm.setdefault(ingredient_matcher._norm(it.name or ""), it)

    chain_id, _chain_name, store_name = await default_chain(db, user.id)
    deal_by_ingredient = await _best_deal_by_ingredient(db, chain_id, today)

    # 1. Collect ingredients from all of this week's recipes.
    rows = (
        (
            await db.execute(
                select(Recipe)
                .join(WeekRecipe, WeekRecipe.recipe_id == Recipe.id)
                .where(
                    WeekRecipe.user_id == user.id,
                    WeekRecipe.week_start == week_start,
                )
                .order_by(WeekRecipe.added_at)
            )
        )
        .scalars()
        .all()
    )

    # key -> {unit_key -> aggregate}
    groups: dict[str, dict[str, dict]] = {}
    order: list[str] = []
    # Value of pantry items these recipes reuse instead of buying — summed only
    # where a current known price exists (honest math: unknowns count as nothing).
    pantry_value = Decimal("0")
    pantry_valued_iids: set[int] = set()
    for recipe in rows:
        for ing in recipe.ingredients_json or []:
            if not isinstance(ing, dict):
                continue
            name = str(ing.get("name") or "").strip()
            if not name:
                continue
            iid, _conf = ingredient_matcher.match_ingredient(name)
            norm = ingredient_matcher._norm(name)

            # 2. Drop staples and things still in the pantry (re-checked live).
            pantry_item = (
                active_by_iid.get(iid) if iid is not None else None
            ) or active_by_norm.get(norm)
            is_staple = (
                iid is not None
                and master.get(iid) is not None
                and master[iid].is_pantry_staple
            ) or (pantry_item is not None and pantry_item.is_staple)
            if is_staple:
                continue
            if ing.get("in_pantry") and pantry_item is not None:
                # Pantry put to work: credit its known price once per ingredient.
                if iid is not None and iid not in pantry_valued_iids:
                    deal = deal_by_ingredient.get(iid)
                    if deal is not None and deal.sale_price is not None:
                        pantry_value += deal.sale_price
                        pantry_valued_iids.add(iid)
                continue  # already have it

            key = f"id:{iid}" if iid is not None else f"nm:{norm}"
            ukey = _unit_key(ing.get("unit"))
            qty = _qty_to_float(ing.get("quantity"))

            if key not in groups:
                groups[key] = {}
                order.append(key)
            agg = groups[key].setdefault(
                ukey,
                {
                    "iid": iid,
                    "name": name,
                    "unit": ing.get("unit"),
                    "qty_sum": 0.0,
                    "qty_known": False,
                    "from_recipes": [],
                },
            )
            if qty is not None:
                agg["qty_sum"] += qty
                agg["qty_known"] = True
            agg["from_recipes"].append(
                {
                    "recipe_id": recipe.id,
                    "title": recipe.title,
                    "qty": ing.get("quantity"),
                    "unit": ing.get("unit"),
                }
            )

    # 3 + 4. One line per (ingredient, unit); cross-note differing units; price.
    line_specs: list[dict] = []
    for key in order:
        unit_aggs = list(groups[key].values())
        for i, agg in enumerate(unit_aggs):
            others = [
                f"{_fmt_qty(o['qty_sum']) or '?'} {o['unit'] or ''}".strip()
                for j, o in enumerate(unit_aggs)
                if j != i
            ]
            note = ("also needed as " + "; ".join(others)) if others else None

            iid = agg["iid"]
            deal = deal_by_ingredient.get(iid) if iid is not None else None
            price = deal.sale_price if deal is not None else None
            regular = deal.regular_price if deal is not None else None
            cat = master[iid].category if (iid is not None and iid in master) else None

            line_specs.append(
                {
                    "ingredient_id": iid,
                    "display_name": agg["name"],
                    "buy_quantity": _fmt_qty(agg["qty_sum"]) if agg["qty_known"] else None,
                    "unit": agg["unit"],
                    "category": cat,
                    "price": price,
                    "is_on_sale": deal is not None,
                    "regular_price": regular,
                    "deal_id": deal.id if deal is not None else None,
                    "from_recipes": agg["from_recipes"],
                    "notes": note,
                }
            )

    # 5. Replace any existing active list for this week, then persist.
    existing = (
        (
            await db.execute(
                select(ShoppingList.id).where(
                    ShoppingList.user_id == user.id,
                    ShoppingList.week_start == week_start,
                    ShoppingList.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    if existing:
        await db.execute(
            delete(ShoppingListItem).where(ShoppingListItem.list_id.in_(existing))
        )
        await db.execute(delete(ShoppingList).where(ShoppingList.id.in_(existing)))

    shopping_list = ShoppingList(
        user_id=user.id, week_start=week_start, status="active",
        priced_store_name=store_name,
    )
    db.add(shopping_list)
    await db.flush()

    items: list[ShoppingListItem] = []
    for spec in line_specs:
        item = ShoppingListItem(list_id=shopping_list.id, **spec)
        db.add(item)
        items.append(item)

    total, savings, count = compute_totals(items)
    shopping_list.total_known_cost = total
    shopping_list.deal_savings = savings
    shopping_list.pantry_value_used = pantry_value.quantize(Decimal("0.01"))
    shopping_list.item_count = count
    await db.flush()
    return shopping_list, items
