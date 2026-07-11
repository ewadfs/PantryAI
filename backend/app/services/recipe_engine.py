"""Recipe generation engine — two stage: concepts, then details.

Stage 1 (fast, ONE small Claude call) proposes three recipe CONCEPTS and returns
immediately, persisting them with status='concept'. Stage 2 fills in full
ingredients/instructions/nutrition for each concept IN PARALLEL (one small call
each) in the background, flipping status to 'ready'. Throughout we trust OUR
``deal_cache`` over the model's price claims and emit an honest cost block.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from anthropic import AsyncAnthropic
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.deal import DealCache
from app.models.pantry import PantryItem
from app.models.recipe import Recipe, WeekRecipe
from app.models.store import StoreLocation, SupportedChain, UserStore
from app.models.user import User
from app.services import ingredient_matcher
from app.services.vision import _extract_json

_MEALS_PER_DAY = 3
_CONCEPT_MAX_TOKENS = 1600     # terse concepts stay fast (few hundred tokens)
_DETAIL_MAX_TOKENS = 3500      # one full recipe
_CONTEXT_DEALS = 30            # relevant deals shown to the model (of 400+)
_RECENT_TITLES = 15
_USE_SOON_WINDOW_DAYS = 2
_STAPLE_DEAL_CATS = {"meat", "seafood", "produce", "dairy"}

# Shared technique + honesty rules injected into both prompts.
_TECHNIQUE_RULES = """\
Respect cooking physics. If a recipe promises crispy skin or a hard sear, do NOT \
marinate in wet/sugary liquids before high-heat cooking — use a dry rub and apply \
wet/sugary sauces as a glaze in the final 8-10 minutes. Sugary marinades burn \
before proteins finish at 400°F+.
Nutrition must be computed from the actual ingredient quantities, not vibes; when \
uncertain, estimate slightly high on calories.
Ingredient naming: put a GENERIC name in generic_name (e.g. "chipotle salsa"), and \
any brand in a SEPARATE nullable brand field. Never embed the brand in the name."""

_CONCEPT_SYSTEM = (
    """You are a skilled, creative home cook proposing tonight's dinner options for \
a specific person. Propose exactly 3 recipe CONCEPTS: one easy (≤15 min active, \
≤6 ingredients), one medium (15-30 min active), one hard (30+ min active, \
impressive result). Concepts only — no full quantities or steps yet.

Hard requirements:
1. Respect ALL allergies and excluded ingredients — non-negotiable: {allergies_excluded}
2. Prioritize ingredients already in their kitchen; the best recipe buys the least.
3. Use items flagged use_soon early and prominently.
4. When something must be bought, strongly prefer items from the deals list and say so.
5. Target ≈{calorie_per_serving} calories and ≈{protein_per_serving} g protein per serving.
6. Lean toward their cuisines: {cuisine_preferences}; avoid repeating: {recent_titles}
7. servings = {household_size}
8. Assume pantry staples (salt, pepper, oils, common spices) are available.

"""
    + _TECHNIQUE_RULES
    + """

For each concept give EXACTLY 4 key_ingredients — the defining ones — each with \
generic_name, brand (null if none/store brand), in_pantry (bool), on_sale (bool), \
and sale_price only when on_sale.

BE TERSE — this is a fast preview, not the full recipe. description ≤ 12 words; \
why_this_recipe ≤ 14 words; at most 3 tags. No extra prose or explanation.

Return ONLY valid JSON:
{{"recipes":[{{title, description, difficulty, prep_time_min, cook_time_min, \
total_time_min, servings, why_this_recipe, cuisine, tags:[...], \
nutrition_per_serving:{{calories, protein_g}}, \
key_ingredients:[{{generic_name, brand, in_pantry, on_sale, sale_price}}]}}]}}"""
)

_DETAIL_SYSTEM = (
    """You are a skilled home cook writing the FULL recipe for a dinner concept you \
already proposed. Produce the complete ingredient list, numbered instructions, and \
per-serving nutrition, honoring the concept's title, difficulty, and key ingredients.

Rules:
- Quantities must be specific ("1.5 lbs chicken thighs", never "some chicken"); \
include every ingredient with a unit.
- Assume pantry staples (salt, pepper, oils, common spices) are available.
- Mark in_pantry true for ingredients the person already has (see their pantry). \
Mark on_sale only if it appears in the deals list, and include sale_price then.

"""
    + _TECHNIQUE_RULES
    + """

Return ONLY valid JSON:
{{"ingredients":[{{generic_name, brand, quantity, unit, in_pantry, on_sale, \
sale_price}}], "instructions":[step strings], \
"nutrition_per_serving":{{calories, protein_g, carbs_g, fat_g, fiber_g}}}}"""
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def week_start_for(today: date) -> date:
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


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fmt_list(values: list[str] | None) -> str:
    return ", ".join(values) if values else "none"


def _use_soon(item: PantryItem, today: date) -> bool:
    if item.freshness == "use_soon":
        return True
    if item.estimated_expiry is not None:
        return item.estimated_expiry <= today + timedelta(days=_USE_SOON_WINDOW_DAYS)
    return False


async def _default_chain(db: AsyncSession, user_id: int) -> tuple[int | None, str | None]:
    row = (
        await db.execute(
            select(StoreLocation.chain_id, SupportedChain.chain_name)
            .join(UserStore, UserStore.store_location_id == StoreLocation.id)
            .join(SupportedChain, SupportedChain.id == StoreLocation.chain_id)
            .where(UserStore.user_id == user_id, UserStore.is_default.is_(True))
        )
    ).first()
    return (row[0], row[1]) if row else (None, None)


async def _all_current_deals(db: AsyncSession, chain_id: int, today: date) -> list[DealCache]:
    return (
        (
            await db.execute(
                select(DealCache).where(
                    DealCache.chain_id == chain_id,
                    DealCache.valid_to >= today,
                    or_(DealCache.valid_from <= today, DealCache.valid_from.is_(None)),
                )
            )
        )
        .scalars()
        .all()
    )


def _relevant_deals(deals: list[DealCache], pantry: list[PantryItem]) -> list[DealCache]:
    """Trim to the ~30 most relevant: pantry-adjacent categories or staple
    protein/produce, sorted by savings — keeps the full list out of the prompt."""
    pantry_cats = {p.category for p in pantry if p.category}
    picked = [
        d
        for d in deals
        if (d.category in pantry_cats) or (d.category in _STAPLE_DEAL_CATS)
    ]
    picked.sort(key=lambda d: -(float(d.savings_pct) if d.savings_pct is not None else 0.0))
    return picked[:_CONTEXT_DEALS]


@dataclass
class _Ctx:
    pantry: list[PantryItem]
    chain_name: str | None
    deal_by_ingredient: dict[int, DealCache]
    context_text: str


async def _load_context(db: AsyncSession, user: User) -> _Ctx:
    today = date.today()
    await ingredient_matcher.preload(db)

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
    all_deals = await _all_current_deals(db, chain_id, today) if chain_id else []
    deal_by_ingredient: dict[int, DealCache] = {}
    for d in all_deals:
        iid = d.matched_ingredient_id
        if iid is None:
            continue
        best = deal_by_ingredient.get(iid)
        if best is None or d.sale_price < best.sale_price:
            deal_by_ingredient[iid] = d

    relevant = _relevant_deals(all_deals, pantry)
    context_text = _build_context(pantry, relevant, chain_name, today)
    return _Ctx(pantry, chain_name, deal_by_ingredient, context_text)


def _build_context(
    pantry: list[PantryItem], deals: list[DealCache], chain_name: str | None, today: date
) -> str:
    lines = ["THEIR KITCHEN (active pantry items):"]
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
    lines.append(f"CURRENT DEALS at {chain_name or 'their store'} (prefer when buying):")
    if deals:
        for d in deals:
            sav = f" ({d.savings_pct}% off)" if d.savings_pct is not None else ""
            unit = f" {d.price_unit}" if d.price_unit else ""
            lines.append(f"- {d.product_name}: ${d.sale_price}{unit}{sav}")
    else:
        lines.append("- (no current deals)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# cost + serialization
# --------------------------------------------------------------------------- #
def _cost_from_ingredients(ingredients: list[dict]) -> dict:
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
    ingredients = recipe.ingredients_json or []
    key_ingredients = recipe.key_ingredients_json or []
    cost_source = ingredients if ingredients else key_ingredients
    return {
        "id": recipe.id,
        "status": recipe.status,
        "title": recipe.title,
        "description": recipe.description,
        "difficulty": recipe.difficulty,
        "prep_time_min": recipe.prep_time_min,
        "cook_time_min": recipe.cook_time_min,
        "total_time_min": recipe.total_time_min,
        "servings": recipe.servings,
        "why_this_recipe": recipe.why_this_recipe,
        "key_ingredients": key_ingredients,
        "ingredients": ingredients,
        "instructions": recipe.instructions_json or [],
        "nutrition_per_serving": recipe.nutrition_json,
        "tags": recipe.tags,
        "cuisine": recipe.cuisine,
        "rating": recipe.rating,
        "generated_at": recipe.generated_at,
        "cost": _cost_from_ingredients(cost_source),
    }


def _reconcile_key(raw: dict, deals: dict[int, DealCache], chain_name: str | None) -> dict:
    name = str(raw.get("generic_name") or raw.get("name") or "").strip()
    out = {
        "generic_name": name,
        "brand": (str(raw["brand"]).strip() if raw.get("brand") else None),
        "in_pantry": bool(raw.get("in_pantry")),
        "on_sale": False,
        "sale_store": None,
        "sale_price": None,
    }
    if name and not out["in_pantry"]:
        iid, _c = ingredient_matcher.match_ingredient(name)
        deal = deals.get(iid) if iid is not None else None
        if deal is not None:
            out["on_sale"] = True
            out["sale_store"] = chain_name
            out["sale_price"] = str(deal.sale_price)
    return out


def _reconcile_ingredients(
    raw_ingredients: list, deals: dict[int, DealCache], chain_name: str | None
) -> list[dict]:
    out: list[dict] = []
    for raw in raw_ingredients:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("generic_name") or raw.get("name") or "").strip()
        if not name:
            continue
        in_pantry = bool(raw.get("in_pantry"))
        ing = {
            "name": name,  # kept for the shopping-list builder / matcher
            "generic_name": name,
            "brand": (str(raw["brand"]).strip() if raw.get("brand") else None),
            "quantity": raw.get("quantity"),
            "unit": raw.get("unit"),
            "in_pantry": in_pantry,
            "on_sale": False,
            "sale_store": None,
            "sale_price": None,
        }
        if not in_pantry:
            iid, _c = ingredient_matcher.match_ingredient(name)
            deal = deals.get(iid) if iid is not None else None
            if deal is not None:
                ing["on_sale"] = True
                ing["sale_store"] = chain_name
                ing["sale_price"] = str(deal.sale_price)
        out.append(ing)
    return out


# --------------------------------------------------------------------------- #
# Stage 1: concepts
# --------------------------------------------------------------------------- #
async def generate_concepts(db: AsyncSession, user: User) -> list[Recipe]:
    """One fast Claude call → 3 persisted concept recipes (status='concept')."""
    ctx = await _load_context(db, user)

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

    system = _CONCEPT_SYSTEM.format(
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
    user_msg = ctx.context_text + "\n\nPropose tonight's 3 dinner concepts now."

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    data = await _call_json(
        client, model=settings.recipe_model, max_tokens=_CONCEPT_MAX_TOKENS,
        system=system, user_msg=user_msg,
    )
    raw = data.get("recipes", []) if isinstance(data, dict) else []

    persisted: list[Recipe] = []
    for r in raw[:3]:
        if not isinstance(r, dict):
            continue
        key_ings = [
            _reconcile_key(k, ctx.deal_by_ingredient, ctx.chain_name)
            for k in (r.get("key_ingredients") or [])
            if isinstance(k, dict)
        ]
        recipe = Recipe(
            user_id=user.id,
            status="concept",
            title=(r.get("title") or "Untitled")[:255],
            description=r.get("description"),
            difficulty=(r.get("difficulty") or None),
            prep_time_min=_as_int(r.get("prep_time_min")),
            cook_time_min=_as_int(r.get("cook_time_min")),
            total_time_min=_as_int(r.get("total_time_min")),
            servings=_as_int(r.get("servings")),
            key_ingredients_json=key_ings,
            nutrition_json=r.get("nutrition_per_serving"),
            why_this_recipe=r.get("why_this_recipe"),
            tags=r.get("tags"),
            cuisine=(r.get("cuisine") or None),
            ai_model=settings.recipe_model,
        )
        db.add(recipe)
        persisted.append(recipe)

    await db.flush()
    return persisted


# --------------------------------------------------------------------------- #
# Stage 2: details (parallel Claude calls, sequential writes)
# --------------------------------------------------------------------------- #
def _detail_user_msg(recipe: Recipe, ctx: _Ctx) -> str:
    keys = recipe.key_ingredients_json or []
    key_lines = ", ".join(
        f"{k.get('generic_name')}" + (f" ({k['brand']})" if k.get("brand") else "")
        for k in keys
        if isinstance(k, dict)
    )
    return (
        f"CONCEPT to write in full:\n"
        f"Title: {recipe.title}\n"
        f"Difficulty: {recipe.difficulty}\n"
        f"Servings: {recipe.servings}\n"
        f"Description: {recipe.description}\n"
        f"Key ingredients: {key_lines}\n\n"
        f"{ctx.context_text}\n\n"
        f"Write the full recipe now."
    )


async def _call_json(
    client: AsyncAnthropic, *, model: str, max_tokens: int, system: str, user_msg: str
) -> dict:
    """Claude call returning parsed JSON, retrying once on a parse failure."""
    for _ in range(2):
        message = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in message.content if b.type == "text")
        try:
            data = _extract_json(text)
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001 - retry once on any parse issue
            continue
    return {}


async def _detail_call(client: AsyncAnthropic, user_msg: str) -> dict:
    return await _call_json(
        client, model=settings.detail_model, max_tokens=_DETAIL_MAX_TOKENS,
        system=_DETAIL_SYSTEM, user_msg=user_msg,
    )


async def _fill_details(db: AsyncSession, recipes: list[Recipe], ctx: _Ctx) -> None:
    """Generate full details for concept recipes: parallel Claude, serial writes."""
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    msgs = [_detail_user_msg(r, ctx) for r in recipes]
    results = await asyncio.gather(
        *(_detail_call(client, m) for m in msgs), return_exceptions=True
    )

    for recipe, res in zip(recipes, results):
        data = res if isinstance(res, dict) else {}
        raw_ings = data.get("ingredients") if isinstance(data, dict) else None
        ingredients = _reconcile_ingredients(
            raw_ings or [], ctx.deal_by_ingredient, ctx.chain_name
        )
        if not ingredients:
            # Fallback: derive usable ingredients from the concept's key list so
            # the recipe is never stuck as a 'concept' / blocking list builds.
            ingredients = _reconcile_ingredients(
                recipe.key_ingredients_json or [], ctx.deal_by_ingredient, ctx.chain_name
            )
        recipe.ingredients_json = ingredients
        instructions = data.get("instructions") if isinstance(data, dict) else None
        recipe.instructions_json = instructions or recipe.instructions_json or []
        nutrition = data.get("nutrition_per_serving") if isinstance(data, dict) else None
        if nutrition:
            recipe.nutrition_json = nutrition
        recipe.ai_model = settings.detail_model
        recipe.status = "ready"

    await db.flush()


async def run_details_bg(user_id: int, recipe_ids: list[int]) -> None:
    """Background entrypoint: fill details for the given concept recipes."""
    async with AsyncSessionLocal() as db:
        recipes = (
            (
                await db.execute(
                    select(Recipe).where(
                        Recipe.id.in_(recipe_ids), Recipe.user_id == user_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if not recipes:
            return
        user = await db.get(User, user_id)
        if user is None:
            return
        ctx = await _load_context(db, user)
        await _fill_details(db, recipes, ctx)
        await db.commit()


async def warm_generate(user_id: int) -> None:
    """Background: full two-stage generation so the Recipes tab is warm."""
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return
        recipes = await generate_concepts(db, user)
        await db.commit()
        if recipes:
            ctx = await _load_context(db, user)
            await _fill_details(db, recipes, ctx)
            await db.commit()
