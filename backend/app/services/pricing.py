"""Cross-store price comparison over the user's saved stores.

For a set of NEEDED ingredients (pantry-covered items already excluded by the
caller), report per saved store the known flyer-price coverage and sum. Honest:
a store with no listed deal for an item counts toward unpriced_count, never a
made-up price.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deal import DealCache
from app.models.recipe import Recipe
from app.models.shopping import ShoppingListItem
from app.models.store import StoreLocation, SupportedChain, UserStore
from app.services import ingredient_matcher


def specs_from_recipe(recipe: Recipe) -> list[dict]:
    """Needed (non-pantry) ingredients -> [{id, name}], deduped. Preload first."""
    specs: dict = {}
    for ing in recipe.ingredients_json or []:
        if not isinstance(ing, dict) or ing.get("in_pantry"):
            continue
        name = (ing.get("generic_name") or ing.get("name") or "").strip()
        if not name:
            continue
        iid, _c = ingredient_matcher.match_ingredient(name)
        key = iid if iid is not None else f"nm:{name.lower()}"
        specs.setdefault(key, {"id": iid, "name": name})
    return list(specs.values())


def specs_from_list(items: list[ShoppingListItem]) -> list[dict]:
    specs: dict = {}
    for it in items:
        name = (it.display_name or "").strip()
        if not name:
            continue
        key = it.ingredient_id if it.ingredient_id is not None else f"nm:{name.lower()}"
        specs.setdefault(key, {"id": it.ingredient_id, "name": name})
    return list(specs.values())


async def _saved_stores(db: AsyncSession, user_id: int):
    return (
        await db.execute(
            select(
                StoreLocation.id,
                StoreLocation.store_name,
                StoreLocation.chain_id,
                SupportedChain.chain_name,
                UserStore.is_default,
            )
            .join(UserStore, UserStore.store_location_id == StoreLocation.id)
            .join(SupportedChain, SupportedChain.id == StoreLocation.chain_id)
            .where(UserStore.user_id == user_id)
            .order_by(UserStore.is_default.desc(), SupportedChain.chain_name)
        )
    ).all()


async def compare(db: AsyncSession, user_id: int, specs: list[dict]) -> list[dict]:
    """Per saved store: known cost + coverage for the needed ingredient set."""
    today = date.today()
    stores = await _saved_stores(db, user_id)
    chain_ids = {s.chain_id for s in stores}
    needed_ids = [s["id"] for s in specs if s["id"] is not None]

    deals: list[DealCache] = []
    if needed_ids and chain_ids:
        deals = (
            (
                await db.execute(
                    select(DealCache).where(
                        DealCache.chain_id.in_(chain_ids),
                        DealCache.matched_ingredient_id.in_(needed_ids),
                        DealCache.valid_to >= today,
                        or_(
                            DealCache.valid_from <= today,
                            DealCache.valid_from.is_(None),
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
    # best (lowest) deal per (chain, ingredient)
    best: dict[tuple[int, int], DealCache] = {}
    for d in deals:
        k = (d.chain_id, d.matched_ingredient_id)
        if k not in best or d.sale_price < best[k].sale_price:
            best[k] = d

    out: list[dict] = []
    for st in stores:
        matched: list[dict] = []
        known = Decimal("0")
        for spec in specs:
            d = best.get((st.chain_id, spec["id"])) if spec["id"] is not None else None
            if d is not None:
                matched.append(
                    {
                        "ingredient": spec["name"],
                        "sale_price": d.sale_price,
                        "regular_price": d.regular_price,
                    }
                )
                known += d.sale_price
        priced = len(matched)
        out.append(
            {
                "store_id": st.id,
                "store_name": st.store_name,
                "chain_name": st.chain_name,
                "is_default": st.is_default,
                "known_cost_sum": known.quantize(Decimal("0.01")),
                "priced_count": priced,
                "total_count": len(specs),
                "unpriced_count": len(specs) - priced,
                "matched_deals": matched,
            }
        )
    return out
