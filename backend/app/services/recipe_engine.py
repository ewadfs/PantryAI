"""Recipe generation engine.

One Claude call generates all three difficulty tiers together (so they can
coordinate: avoid overlapping too much, share a shopping trip). We then trust
OUR ``deal_cache`` over whatever the model claims about sales, and emit an honest
per-recipe cost block with no fabricated prices.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from anthropic import AsyncAnthropic
from sqlalchemy import nulls_last, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.deal import DealCache
from app.models.pantry import PantryItem
from app.models.recipe import Recipe, WeekRecipe
from app.models.store import StoreLocation, SupportedChain, UserStore
from app.models.user import User
from app.services import ingredient_matcher
from app.services.vision import _extract_json

_MEALS_PER_DAY = 3          # calorie/protein target divisor (no per-user field yet)
_MAX_TOKENS = 8000          # 3 full recipes; avoids mid-JSON truncation
_DEALS_LIMIT = 60           # deals surfaced to the model
_RECENT_TITLES = 15         # recent recipe titles for variety
_USE_SOON_WINDOW_DAYS = 2

_SYSTEM_PROMPT = """\
You are a skilled, creative home cook generating tonight's dinner options for a \
specific person. Generate exactly 3 recipes: one easy (≤15 min active, ≤6 ingredients), \
one medium (15-30 min active), one hard (30+ min active, impressive result).

Hard requirements:
1. Respect ALL allergies and excluded ingredients — non-negotiable: {allergies_excluded}
2. Prioritize ingredients already in their kitchen; the best recipe needs to buy the least
3. Use items flagged use_soon early and prominently
4. When something must be bought, strongly prefer items from the deals list and say so
5. Target ≈{calorie_per_serving} calories and ≈{protein_per_serving} g protein per serving; state real numbers
6. Lean toward their cuisines: {cuisine_preferences}; avoid repeating these recent \
recipes: {recent_titles}
7. Quantities must be specific ("1.5 lbs chicken thighs", never "some chicken")
8. Assume pantry staples (salt, pepper, oils, common spices they have) are available
9. servings = {household_size}

For every ingredient include: name, quantity, unit, in_pantry (bool), \
on_sale (bool), sale_store + sale_price when on_sale.
Include why_this_recipe: one sentence connecting THEIR pantry/deals/goals to this dish.

Return ONLY valid JSON:
{{"recipes":[{{title, description, difficulty, prep_time_min, cook_time_min, \
total_time_min, servings, why_this_recipe, ingredients:[...], \
instructions:[step strings], nutrition_per_serving:{{calories,protein_g,carbs_g,fat_g,fiber_g}}, \
tags:[...], cuisine}}]}}"""


def week_start_for(today: date) -> date:
    """Most recent Sunday on or before ``today`` (weeks start Sunday)."""
    return today - timedelta(days=(today.weekday() + 1) % 7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _qty_to_float(value) -> float:
    """Best-effort numeric quantity; leading number wins, else 1.0."""
    if value is None:
        return 1.0
    if isinstance(value, (int, float)):
        return float(value)
    num = ""
    for ch in str(value).strip():
        if ch.isdigit() or ch in ".-":
            num += ch
        elif num:
            break
    try:
        return float(num) if num else 1.0
    except ValueError:
        return 1.0


def _use_soon(item: PantryItem, today: date) -> bool:
    if item.freshness == "use_soon":
        return True
    if item.estimated_expiry is not None:
        return item.estimated_expiry <= today + timedelta(days=_USE_SOON_WINDOW_DAYS)
    return False


async def _default_chain(db: AsyncSession, user_id: int) -> tuple[int | None, str | None]:
    """(chain_id, chain_name) of the user's default store, or (None, None)."""
    row = (
        await db.execute(
            select(StoreLocation.chain_id, SupportedChain.chain_name)
            .join(UserStore, UserStore.store_location_id == StoreLocation.id)
            .join(SupportedChain, SupportedChain.id == StoreLocation.chain_id)
            .where(UserStore.user_id == user_id, UserStore.is_default.is_(True))
        )
    ).first()
    return (row[0], row[1]) if row else (None, None)


async def _current_deals(
    db: AsyncSession, chain_id: int, today: date
) -> list[DealCache]:
    """Current-valid deals for the chain, matched-first then by savings desc."""
    return (
        (
            await db.execute(
                select(DealCache)
                .where(
                    DealCache.chain_id == chain_id,
                    DealCache.valid_to >= today,
                    or_(DealCache.valid_from <= today, DealCache.valid_from.is_(None)),
                )
                .order_by(
                    DealCache.matched_ingredient_id.is_(None),  # matched first
                    nulls_last(DealCache.savings_pct.desc()),
                    DealCache.sale_price.asc(),
                )
                .limit(_DEALS_LIMIT)
            )
        )
        .scalars()
        .all()
    )


def _cost_from_ingredients(ingredients: list[dict]) -> dict:
    """Honest cost block from post-processed ingredients. No fabricated prices."""
    known = Decimal("0")
    unknown = 0
    pantry_used = 0
    for ing in ingredients:
        if ing.get("in_pantry"):
            pantry_used += 1
            continue
        price = _to_decimal(ing.get("sale_price"))
        if ing.get("on_sale") and price is not None:
            known += price * Decimal(str(_qty_to_float(ing.get("quantity"))))
        else:
            unknown += 1
    return {
        "known_buy_cost": known.quantize(Decimal("0.01")),
        "unknown_priced_items": unknown,
        "pantry_items_used": pantry_used,
    }


def recipe_to_read(recipe: Recipe) -> dict:
    """Shape an ORM Recipe (with post-processed ingredients) into a read dict."""
    ingredients = recipe.ingredients_json or []
    return {
        "id": recipe.id,
        "title": recipe.title,
        "description": recipe.description,
        "difficulty": recipe.difficulty,
        "prep_time_min": recipe.prep_time_min,
        "cook_time_min": recipe.cook_time_min,
        "total_time_min": recipe.total_time_min,
        "servings": recipe.servings,
        "why_this_recipe": recipe.why_this_recipe,
        "ingredients": ingredients,
        "instructions": recipe.instructions_json or [],
        "nutrition_per_serving": recipe.nutrition_json,
        "tags": recipe.tags,
        "cuisine": recipe.cuisine,
        "rating": recipe.rating,
        "cost": _cost_from_ingredients(ingredients),
    }


def _fmt_list(values: list[str] | None) -> str:
    return ", ".join(values) if values else "none"


def _build_context(
    pantry: list[PantryItem], deals: list[DealCache], chain_name: str | None,
    saved_titles: list[str], today: date,
) -> str:
    lines: list[str] = ["THEIR KITCHEN (active pantry items):"]
    if pantry:
        for it in pantry:
            tags = []
            if _use_soon(it, today):
                tags.append("USE SOON")
            if it.is_staple:
                tags.append("staple")
            qty = " ".join(p for p in (it.quantity_estimate, it.unit) if p)
            suffix = f" [{it.category}]" if it.category else ""
            note = f" ({', '.join(tags)})" if tags else ""
            lines.append(f"- {it.name}{f' — {qty}' if qty else ''}{suffix}{note}")
    else:
        lines.append("- (empty)")

    lines.append("")
    store = chain_name or "their store"
    lines.append(f"CURRENT DEALS at {store} (prefer these when buying):")
    if deals:
        for d in deals:
            sav = f" ({d.savings_pct}% off)" if d.savings_pct is not None else ""
            unit = f" {d.price_unit}" if d.price_unit else ""
            lines.append(f"- {d.product_name}: ${d.sale_price}{unit}{sav}")
    else:
        lines.append("- (no current deals)")

    lines.append("")
    lines.append(
        "ALREADY SAVED THIS WEEK (do not duplicate): " + _fmt_list(saved_titles)
    )
    lines.append("")
    lines.append("Generate tonight's 3 dinner recipes now as specified.")
    return "\n".join(lines)


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _reconcile_ingredients(
    raw_ingredients: list, deal_by_ingredient: dict[int, DealCache],
    chain_name: str | None,
) -> list[dict]:
    """Overwrite the model's sale claims with OUR deal_cache for bought items."""
    out: list[dict] = []
    for raw in raw_ingredients:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        in_pantry = bool(raw.get("in_pantry"))
        ing = {
            "name": name,
            "quantity": raw.get("quantity"),
            "unit": raw.get("unit"),
            "in_pantry": in_pantry,
            "on_sale": False,
            "sale_store": None,
            "sale_price": None,
        }
        if not in_pantry:
            iid, _conf = ingredient_matcher.match_ingredient(name)
            deal = deal_by_ingredient.get(iid) if iid is not None else None
            if deal is not None:
                ing["on_sale"] = True
                ing["sale_store"] = chain_name
                ing["sale_price"] = str(deal.sale_price)
        out.append(ing)
    return out


async def generate_recipes(db: AsyncSession, user: User) -> list[dict]:
    """Generate, persist, and return 3 recipes (easy/medium/hard) with cost blocks."""
    today = date.today()
    await ingredient_matcher.preload(db)

    # 1. Context: pantry, deals, profile, recent titles, this-week's recipes.
    pantry = (
        (
            await db.execute(
                select(PantryItem)
                .where(PantryItem.user_id == user.id, PantryItem.is_active.is_(True))
                .order_by(PantryItem.category, PantryItem.name)
            )
        )
        .scalars()
        .all()
    )

    chain_id, chain_name = await _default_chain(db, user.id)
    deals = await _current_deals(db, chain_id, today) if chain_id else []
    # ingredient_id -> best (lowest-price) current deal for this chain.
    deal_by_ingredient: dict[int, DealCache] = {}
    for d in deals:
        iid = d.matched_ingredient_id
        if iid is None:
            continue
        best = deal_by_ingredient.get(iid)
        if best is None or d.sale_price < best.sale_price:
            deal_by_ingredient[iid] = d

    recent_titles = (
        (
            await db.execute(
                select(Recipe.title)
                .where(Recipe.user_id == user.id)
                .order_by(Recipe.generated_at.desc())
                .limit(_RECENT_TITLES)
            )
        )
        .scalars()
        .all()
    )

    wk = week_start_for(today)
    saved_titles = (
        (
            await db.execute(
                select(Recipe.title)
                .join(WeekRecipe, WeekRecipe.recipe_id == Recipe.id)
                .where(WeekRecipe.user_id == user.id, WeekRecipe.week_start == wk)
            )
        )
        .scalars()
        .all()
    )

    # 2. One Claude call for all three tiers.
    system = _SYSTEM_PROMPT.format(
        allergies_excluded=(
            f"allergies={_fmt_list(user.allergies)}; "
            f"excluded={_fmt_list(user.excluded_ingredients)}"
        ),
        calorie_per_serving=round(user.calorie_target / _MEALS_PER_DAY),
        protein_per_serving=round(user.protein_target / _MEALS_PER_DAY),
        cuisine_preferences=_fmt_list(user.cuisine_preferences),
        recent_titles=_fmt_list(list(recent_titles)),
        household_size=user.household_size,
    )
    context = _build_context(pantry, deals, chain_name, list(saved_titles), today)

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    message = await client.messages.create(
        model=settings.recipe_model,
        max_tokens=_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": context}],
    )
    text = "".join(b.text for b in message.content if b.type == "text")
    data = _extract_json(text)
    raw_recipes = data.get("recipes", []) if isinstance(data, dict) else []

    # 3. Post-process each recipe: trust OUR deal table, then persist.
    persisted: list[Recipe] = []
    for raw in raw_recipes[:3]:
        if not isinstance(raw, dict):
            continue
        ingredients = _reconcile_ingredients(
            raw.get("ingredients", []), deal_by_ingredient, chain_name
        )
        recipe = Recipe(
            user_id=user.id,
            title=(raw.get("title") or "Untitled")[:255],
            description=raw.get("description"),
            difficulty=(raw.get("difficulty") or None),
            prep_time_min=_as_int(raw.get("prep_time_min")),
            cook_time_min=_as_int(raw.get("cook_time_min")),
            total_time_min=_as_int(raw.get("total_time_min")),
            servings=_as_int(raw.get("servings")),
            ingredients_json=ingredients,
            instructions_json=raw.get("instructions") or [],
            nutrition_json=raw.get("nutrition_per_serving"),
            why_this_recipe=raw.get("why_this_recipe"),
            tags=raw.get("tags"),
            cuisine=(raw.get("cuisine") or None),
            ai_model=settings.recipe_model,
        )
        db.add(recipe)
        persisted.append(recipe)

    await db.flush()
    return [recipe_to_read(r) for r in persisted]
