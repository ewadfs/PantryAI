"""Recipe generation engine — two stage: concepts, then details.

Stage 1 (fast, ONE small Claude call) proposes three recipe CONCEPTS and returns
immediately, persisting them with status='concept'. Stage 2 fills in full
ingredients/instructions/nutrition for each concept IN PARALLEL (one small call
each) in the background, flipping status to 'ready'. Throughout we trust OUR
``deal_cache`` over the model's price claims and emit an honest cost block.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass, field
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
from app.services import ingredient_matcher, quantities
from app.services.vision import _extract_json

logger = logging.getLogger(__name__)

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
Never assume more of a pantry item than the quantity shown in THEIR KITCHEN. If a \
dish needs more than is on hand, either scale the recipe down to the amount \
available OR treat the difference as a purchase — and say which in the ingredient \
list (mark it to buy, don't silently claim it's in the pantry).
Ingredient naming: put a GENERIC name in generic_name (e.g. "chipotle salsa"), and \
any brand in a SEPARATE nullable brand field. Never embed the brand in the name."""

_CONCEPT_SYSTEM = (
    """You are a skilled, creative home cook proposing tonight's dinner options for \
a specific person. Propose exactly {n_concepts} recipe CONCEPTS with this difficulty \
mix: {tier_plan}. Difficulty guide: easy = ≤15 min active & ≤6 ingredients; medium = \
15-30 min active; hard = 30+ min active, impressive result. Order them easy → medium \
→ hard. Concepts only — no full quantities or steps yet.

Hard requirements:
1. Respect ALL allergies and excluded ingredients — non-negotiable: {allergies_excluded}
2. Prioritize ingredients already in their kitchen; the best recipe buys the least.
3. Use items flagged use_soon early and prominently.
4. When something must be bought, strongly prefer items from the deals list and say so.
5. Target ≈{calorie_per_serving} calories per serving (protein has a hard floor below).
6. Lean toward their cuisines: {cuisine_preferences}; avoid repeating: {recent_titles}
7. servings = {household_size}
8. Assume pantry staples (salt, pepper, oils, common spices) are available.
9. The {n_concepts} concepts must be mutually distinct dinners — each must differ \
from EVERY other in this batch on at least TWO of the three signature axes \
{{anchor_ingredient, dish_format, cuisine}}. No two near-identical dishes.

"""
    + _TECHNIQUE_RULES
    + """

For each concept give EXACTLY 4 key_ingredients — the defining ones — each with \
generic_name, brand (null if none/store brand), in_pantry (bool), on_sale (bool), \
and sale_price only when on_sale.

For each concept also give its SIGNATURE: anchor_ingredient (the single defining \
pantry/deal item the dish is built around) and dish_format (one word: pasta, bowl, \
roast, tacos, soup, skillet, stir-fry, salad, sandwich, bake, curry, stew, …). \
total_time_min MUST include passive time (water boiling, oven preheat, rests) — a \
"15 min" dish that needs a 20-min braise is a lie.

BE TERSE — this is a fast preview, not the full recipe. description ≤ 12 words; \
why_this_recipe ≤ 14 words; at most 3 tags. No extra prose or explanation.

Return ONLY valid JSON:
{{"recipes":[{{title, description, difficulty, prep_time_min, cook_time_min, \
total_time_min, servings, why_this_recipe, cuisine, dish_format, anchor_ingredient, \
tags:[...], nutrition_per_serving:{{calories, protein_g}}, \
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


async def _default_store(
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


def _protein_block(floor: int) -> str:
    if floor <= 0:
        return ""
    return (
        "\n\nProtein is a CONSTRAINT, not a target. Every recipe MUST deliver at "
        f"least {floor} g protein per serving. If a pantry-driven concept falls "
        "short, fortify it with a protein source — pantry proteins first, then "
        "on-sale proteins — or discard the concept. Carb-anchored dishes must be "
        "protein-fortified, never served as-is."
    )


_TIER_ORDER = ["easy", "medium", "hard"]
_ALLOWED_N = (3, 5)


def _clamp_n(n: int | None) -> int:
    return n if n in _ALLOWED_N else 5


def _tier_counts(n: int, difficulties: list[str] | None = None) -> dict[str, int]:
    """Split N across selected tiers as evenly as possible; remainder to the
    easiest selected tiers first. Default (no selection) = all three tiers."""
    sel = [t for t in _TIER_ORDER if difficulties and t in difficulties]
    if not sel:
        sel = list(_TIER_ORDER)
    base, rem = divmod(n, len(sel))
    counts = {t: base for t in sel}
    for i in range(rem):
        counts[sel[i]] += 1  # easiest selected tier(s) get the remainder
    return counts


def _tier_list(n: int, difficulties: list[str] | None = None) -> list[str]:
    counts = _tier_counts(n, difficulties)
    out: list[str] = []
    for t in _TIER_ORDER:
        out += [t] * counts.get(t, 0)
    return out


def _tier_plan_text(n: int, difficulties: list[str] | None = None) -> str:
    counts = _tier_counts(n, difficulties)
    return ", ".join(f"{counts[t]} {t}" for t in _TIER_ORDER if counts.get(t))


def _direction_block(direction: str | None) -> str:
    d = (direction or "").strip()
    if not d:
        return ""
    return (
        f"\n\nThe user's DIRECTION for THIS batch: '{d}'. All three concepts should "
        "honor it. This steer ranks ABOVE their cuisine preferences, but it never "
        "overrides the hard constraints (allergies, excluded ingredients, the "
        "protein floor, or pinned items) — satisfy every one of those first, then "
        "shape the batch to the direction."
    )


def _taste_block(taste_notes: str | None) -> str:
    notes = (taste_notes or "").strip()
    if not notes:
        return ""
    return (
        "\n\nTHEIR TASTE (in their own words — weight this heavily, it is the single "
        f"best signal of what they'll love): {notes}"
    )


def _history_block(loved: list[str], passed: list[str]) -> str:
    if not loved and not passed:
        return ""
    parts = ["\n\nWHAT THEY THINK OF PAST RECIPES:"]
    if loved:
        parts.append("LOVED (👍 / cooked — rhyme with these flavor directions and "
                     "formats, but do NOT repeat the titles):")
        parts.extend(f"- {x}" for x in loved)
    if passed:
        parts.append("PASSED (👎 / skipped — avoid these patterns):")
        parts.extend(f"- {x}" for x in passed)
    return "\n".join(parts)


def _variety_block(recent_sigs: list[str], skipped_sigs: list[str]) -> str:
    if not recent_sigs and not skipped_sigs:
        return ""
    parts = ["\n\nRECENTLY SHOWN (do NOT re-serve these dishes under new names):"]
    parts.extend(f"- {s}" for s in recent_sigs)
    if skipped_sigs:
        parts.append("The user regenerated past these without saving (soft negative — "
                     "lean away):")
        parts.extend(f"- {s}" for s in skipped_sigs)
    parts.append(
        "VARIETY RULE: each new concept must differ from every RECENTLY SHOWN "
        "signature on at least TWO of the three axes {anchor_ingredient, dish_format, "
        "cuisine}. Anchor this batch on different pantry/deal items than last time "
        "where inventory allows. If your pantry is too small to satisfy this without "
        "leaving the pantry, you MAY relax the anchor axis — but you MUST say so in "
        "why_this_recipe (e.g. \"another take on your pasta shelf, but as a bake\")."
    )
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Critic pass (Stage 1.5)
# --------------------------------------------------------------------------- #
_CRITIC_SYSTEM = """\
You are a demanding culinary reviewer. You are given 3 dinner CONCEPTS and a diner
profile. Score each concept 1-10 overall and flag which rubric items it fails.
Judge against this rubric:

1. TECHNIQUE PHYSICS — no wet/sugary marinades before a high-heat sear; ground
   spices don't survive long hard sears (bloom later); "crispy" needs dry surfaces;
   covered things don't crisp.
2. FLAVOR COHERENCE — a defensible culinary logic (fusion fine, randomness not).
3. STARCH/SHAPE LOGIC — chunky sauces get gripping shapes (rigatoni, penne), silky
   sauces get ribbons; rice type matches the dish tradition where possible.
4. PROTEIN FLOOR — per-serving protein ≥ the stated floor.
5. TIME HONESTY — total_time_min must include passive time (boiling, preheat, rests).
6. PANTRY REALISM — the amounts a concept implies must not exceed the quantities
   the pantry actually holds (you are given the pantry with quantities — check the
   NUMBERS, not vibes). Assuming "1 lb ground beef" when only 1 lb is on hand and
   the dish clearly needs more, without treating the extra as a purchase, FAILS.
7. PROFILE FIT — matches stated cuisines/skill/max-prep AND the taste notes + rating
   history.

Return ONLY valid JSON:
{"reviews":[{"index":0-based, "score":1-10, "verdict":"ship"|"revise",
"fail_rubrics":[ints of failed rubric numbers], "worst_issues":[short strings]}]}
Be strict: a concept that fails rubric 1, 4, 5, or 6, or scores below 7 overall, is
"revise"."""


@dataclass
class _Ctx:
    pantry: list[PantryItem]
    chain_name: str | None
    store_name: str | None
    deal_by_ingredient: dict[int, DealCache]
    context_text: str
    pin_block: str = ""
    protein_floor: int = 0
    taste_block: str = ""
    history_block: str = ""
    variety_block: str = ""
    direction: str = ""
    pantry_by_iid: dict[int, PantryItem] = field(default_factory=dict)
    pantry_by_norm: dict[str, PantryItem] = field(default_factory=dict)


async def _resolve_pins(
    db: AsyncSession, user_id: int, pinned_ids: list[int]
) -> list[PantryItem]:
    """Active pantry items the user pinned. Raises ValueError on any bad id."""
    if not pinned_ids:
        return []
    ids = list(dict.fromkeys(pinned_ids))
    items = (
        (
            await db.execute(
                select(PantryItem).where(
                    PantryItem.id.in_(ids),
                    PantryItem.user_id == user_id,
                    PantryItem.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {i.id: i for i in items}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise ValueError(f"Pinned items not found or inactive: {missing}")
    return [by_id[i] for i in ids]


def _pin_dicts(items: list[PantryItem]) -> list[dict]:
    return [
        {"name": it.name, "quantity": it.quantity_estimate, "freshness": it.freshness}
        for it in items
    ]


def _pin_block(pins: list[dict]) -> str:
    if not pins:
        return ""
    lines = "; ".join(
        f"{p.get('name')} (have {p.get('quantity') or 'some'}, "
        f"{p.get('freshness') or 'good'})"
        for p in pins
    )
    return (
        "\n\nHARD REQUIREMENT — the user has designated these pantry items and EVERY "
        f"recipe MUST make prominent use of ALL of them (not a garnish): {lines}. "
        "Build each recipe around them. Distribute across main/side within a recipe "
        "if needed, but NEVER omit one. Pinned items lead the pantry-first priority; "
        "the protein target and all other constraints still apply."
    )


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

    chain_id, chain_name, store_name = await _default_store(db, user.id)
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
    floor = math.ceil(user.protein_target / _MEALS_PER_DAY)

    # Quantity-aware pantry lookup (by matched ingredient id + normalized name).
    pantry_by_iid: dict[int, PantryItem] = {}
    pantry_by_norm: dict[str, PantryItem] = {}
    for it in pantry:
        iid, _c = ingredient_matcher.match_ingredient(it.name or "")
        if iid is not None:
            pantry_by_iid.setdefault(iid, it)
        pantry_by_norm.setdefault(ingredient_matcher._norm(it.name or ""), it)

    return _Ctx(
        pantry, chain_name, store_name, deal_by_ingredient, context_text,
        protein_floor=floor,
        taste_block=_taste_block(user.taste_notes),
        pantry_by_iid=pantry_by_iid,
        pantry_by_norm=pantry_by_norm,
    )


# --------------------------------------------------------------------------- #
# Taste learning: rating history + variety signatures
# --------------------------------------------------------------------------- #
_HISTORY_LIMIT = 8
_RECENT_BATCHES = 3
_RECENT_HOURS = 48


def _signature_of(recipe: Recipe) -> dict:
    sig = recipe.signature_json if isinstance(recipe.signature_json, dict) else {}
    return {
        "anchor_ingredient": sig.get("anchor_ingredient"),
        "dish_format": sig.get("dish_format"),
        "cuisine": sig.get("cuisine") or recipe.cuisine,
    }


def _sig_str(sig: dict) -> str:
    return (
        f"{sig.get('dish_format') or '?'} · anchor: "
        f"{sig.get('anchor_ingredient') or '?'} · {sig.get('cuisine') or 'any'} cuisine"
    )


def _recipe_signature_line(r: Recipe) -> str:
    sig = r.why_this_recipe or r.description or ""
    cuisine = f" [{r.cuisine}]" if r.cuisine else ""
    return f"{r.title}{cuisine}: {sig}".strip()


def _norm_sig(sig: dict) -> dict:
    return {
        k: (str(sig.get(k) or "")).strip().lower()
        for k in ("anchor_ingredient", "dish_format", "cuisine")
    }


def _axes_shared(a: dict, b: dict) -> int:
    """How many of the 3 signature axes two (normalized) signatures share."""
    a, b = _norm_sig(a), _norm_sig(b)
    return sum(1 for k in a if a[k] and a[k] == b[k])


async def _build_taste_history(
    db: AsyncSession, user_id: int
) -> tuple[str, str, list[dict]]:
    """(history_block, variety_block, recent_signatures) from ratings/cooked/batches."""
    # LOVED: thumbs-up OR cooked. PASSED: thumbs-down.
    loved_rows = (
        (
            await db.execute(
                select(Recipe)
                .where(Recipe.user_id == user_id, Recipe.rating == 1)
                .order_by(Recipe.generated_at.desc())
                .limit(_HISTORY_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    cooked_rows = (
        (
            await db.execute(
                select(Recipe)
                .join(WeekRecipe, WeekRecipe.recipe_id == Recipe.id)
                .where(
                    WeekRecipe.user_id == user_id,
                    WeekRecipe.is_cooked.is_(True),
                )
                .order_by(WeekRecipe.cooked_at.desc())
                .limit(_HISTORY_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    passed_rows = (
        (
            await db.execute(
                select(Recipe)
                .where(Recipe.user_id == user_id, Recipe.rating == -1)
                .order_by(Recipe.generated_at.desc())
                .limit(_HISTORY_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    loved_seen: set[int] = set()
    loved: list[str] = []
    for r in [*cooked_rows, *loved_rows]:  # cooked first = strongest signal
        if r.id in loved_seen:
            continue
        loved_seen.add(r.id)
        loved.append(_recipe_signature_line(r))
    passed = [_recipe_signature_line(r) for r in passed_rows]
    history_block = _history_block(loved[:_HISTORY_LIMIT], passed)

    # Variety: signatures from the last few batches (within 48h), and which of
    # those batches were regenerated-past without any save (soft negatives).
    cutoff = _now() - timedelta(hours=_RECENT_HOURS)
    recent = (
        (
            await db.execute(
                select(Recipe)
                .where(
                    Recipe.user_id == user_id,
                    Recipe.generated_at >= cutoff,
                )
                .order_by(Recipe.generated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    # Group into batches by generated_at; keep the most recent few.
    batches: list[tuple[datetime, list[Recipe]]] = []
    for r in recent:
        if batches and batches[-1][0] == r.generated_at:
            batches[-1][1].append(r)
        else:
            batches.append((r.generated_at, [r]))
    batches = batches[:_RECENT_BATCHES]

    saved_ids = set(
        (
            await db.execute(
                select(WeekRecipe.recipe_id).where(WeekRecipe.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    recent_sigs: list[str] = []
    skipped_sigs: list[str] = []
    recent_struct: list[dict] = []
    for _ts, recipes in batches:
        batch_saved = any(r.id in saved_ids for r in recipes)
        for r in recipes:
            struct = _signature_of(r)
            recent_struct.append(struct)
            s = _sig_str(struct)
            recent_sigs.append(s)
            if not batch_saved:
                skipped_sigs.append(s)
    variety_block = _variety_block(recent_sigs, skipped_sigs)
    return history_block, variety_block, recent_struct


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
            # Quantity is prominent: the model must not assume more than HAVE.
            have = f" — HAVE {qty}" if qty else " — HAVE (amount unknown)"
            lines.append(f"- {it.name}{have}{suffix}{note}")
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
        # A PARTIAL item is only partly on hand — it still needs a purchase.
        if ing.get("in_pantry") is True:
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
    raw_ingredients: list,
    deals: dict[int, DealCache],
    chain_name: str | None,
    pantry_by_iid: dict[int, PantryItem] | None = None,
    pantry_by_norm: dict[str, PantryItem] | None = None,
) -> list[dict]:
    pantry_by_iid = pantry_by_iid or {}
    pantry_by_norm = pantry_by_norm or {}
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
        iid, _c = ingredient_matcher.match_ingredient(name)
        norm = ingredient_matcher._norm(name)

        def _apply_deal() -> None:
            deal = deals.get(iid) if iid is not None else None
            if deal is not None:
                ing["on_sale"] = True
                ing["sale_store"] = chain_name
                ing["sale_price"] = str(deal.sale_price)

        if not in_pantry:
            _apply_deal()
        else:
            # Quantity-aware: does the pantry actually cover what the recipe wants?
            pitem = (pantry_by_iid.get(iid) if iid is not None else None) or (
                pantry_by_norm.get(norm)
            )
            if pitem is not None:
                have = quantities.parse(pitem.quantity_estimate, pitem.unit)
                need = quantities.parse(raw.get("quantity"), raw.get("unit"))
                state, shortfall = quantities.sufficiency(have, need)
                if state == "partial":
                    _, buy_disp = quantities.format_amount(shortfall, need)
                    ing["in_pantry"] = "partial"
                    ing["pantry_quantity"] = quantities.describe(have)
                    ing["shortfall_quantity"] = buy_disp
                    _apply_deal()  # the shortfall is a purchase — price it
        out.append(ing)
    return out


# --------------------------------------------------------------------------- #
# Critic pass helpers
# --------------------------------------------------------------------------- #
def _concept_brief(c: dict) -> dict:
    return {
        "title": c.get("title"),
        "difficulty": c.get("difficulty"),
        "cuisine": c.get("cuisine"),
        "dish_format": c.get("dish_format"),
        "anchor_ingredient": c.get("anchor_ingredient"),
        "total_time_min": c.get("total_time_min"),
        "protein_g": (c.get("nutrition_per_serving") or {}).get("protein_g")
        if isinstance(c.get("nutrition_per_serving"), dict)
        else None,
        "description": c.get("description"),
        "key_ingredients": [
            k.get("generic_name")
            for k in (c.get("key_ingredients") or [])
            if isinstance(k, dict)
        ],
    }


def _profile_text(user: User, ctx: _Ctx) -> str:
    lines = [
        f"cuisines: {_fmt_list(user.cuisine_preferences)}",
        f"skill: {user.skill_level}; max prep: {user.max_prep_time} min",
        f"diet: {user.diet_type}; allergies: {_fmt_list(user.allergies)}",
    ]
    if ctx.direction:
        lines.append(
            f"DIRECTION for this batch (ranks above cuisines): '{ctx.direction}'"
        )
    return "\n".join(lines) + ctx.taste_block + ctx.history_block


def _needs_revision(review: dict) -> bool:
    try:
        score = int(review.get("score"))
    except (TypeError, ValueError):
        score = 0
    fails = {
        int(x)
        for x in (review.get("fail_rubrics") or [])
        if str(x).strip().lstrip("-").isdigit()
    }
    hard = bool(fails & {1, 4, 5, 6})
    return score < 7 or hard or review.get("verdict") == "revise"


def _pantry_qty_lines(pantry: list[PantryItem]) -> str:
    """Compact 'name: HAVE qty' list so the critic checks pantry realism on
    actual numbers rather than vibes."""
    out = []
    for it in pantry:
        q = " ".join(p for p in (it.quantity_estimate, it.unit) if p) or "amount unknown"
        out.append(f"- {it.name}: HAVE {q}")
    return "\n".join(out) if out else "- (empty)"


async def _run_critic(
    client: AsyncAnthropic,
    concepts: list[dict],
    floor: int,
    profile_text: str,
    pantry_lines: str = "",
) -> list[dict]:
    briefs = [_concept_brief(c) for c in concepts]
    user_msg = (
        f"PROTEIN FLOOR: {floor} g per serving.\n"
        f"DINER PROFILE:\n{profile_text}\n\n"
        f"THEIR PANTRY (with quantities — check rubric 6 against these numbers):\n"
        f"{pantry_lines or '- (unknown)'}\n\n"
        f"CONCEPTS (0-indexed):\n{json.dumps(briefs, ensure_ascii=False)}\n\n"
        "Review each concept now."
    )
    data = await _call_json(
        client, model=settings.critic_model_id, max_tokens=1200,
        system=_CRITIC_SYSTEM, user_msg=user_msg,
    )
    reviews = data.get("reviews") if isinstance(data, dict) else None
    return reviews if isinstance(reviews, list) else []


async def _regen_concept(
    client: AsyncAnthropic, concept: dict, review: dict, base_system: str, ctx_text: str
) -> dict:
    issues = "; ".join(str(i) for i in (review.get("worst_issues") or [])) or (
        "quality below bar"
    )
    fails = review.get("fail_rubrics") or []
    tier = (concept.get("difficulty") or "").strip() or "the same"
    correction = (
        f"\n\nREVISE ONE CONCEPT. A reviewer flagged it (score {review.get('score')}, "
        f"failed rubric(s) {fails}): {issues}. Return exactly one improved concept as "
        'JSON {"recipes":[{...single concept, same shape...}]}, fixing these specific '
        f"issues while keeping what already worked. Keep the SAME difficulty tier "
        f"('{tier}'). Use ONLY ingredients actually in their kitchen or on the deals "
        "list — never invent a protein or item the pantry doesn't show (the flagged "
        "issue above is usually exactly this)."
    )
    prior = json.dumps(_concept_brief(concept), ensure_ascii=False)
    msg = f"{ctx_text}\n\nConcept to fix:\n{prior}\n\nReturn the fixed concept now."
    data = await _call_json(
        client, model=settings.recipe_model, max_tokens=_CONCEPT_MAX_TOKENS,
        system=base_system + correction, user_msg=msg,
    )
    recs = data.get("recipes") if isinstance(data, dict) else None
    if isinstance(recs, list) and recs and isinstance(recs[0], dict):
        return recs[0]
    return concept  # regen failed — ship the original


async def _critique_and_fix(
    client: AsyncAnthropic,
    concepts: list[dict],
    floor: int,
    profile_text: str,
    base_system: str,
    ctx_text: str,
    pantry_lines: str = "",
) -> tuple[list[dict], list[dict]]:
    """Score concepts, regenerate weak ones once, return (concepts, critic_meta)."""
    reviews = await _run_critic(client, concepts, floor, profile_text, pantry_lines)
    by_index: dict[int, dict] = {}
    for rv in reviews:
        if not isinstance(rv, dict):
            continue
        try:
            by_index[int(rv.get("index"))] = rv
        except (TypeError, ValueError):
            continue

    regen_idx = [
        i for i in range(len(concepts))
        if by_index.get(i) and _needs_revision(by_index[i])
    ]
    if regen_idx:
        fixed = await asyncio.gather(
            *(
                _regen_concept(client, concepts[i], by_index[i], base_system, ctx_text)
                for i in regen_idx
            ),
            return_exceptions=True,
        )
        for i, res in zip(regen_idx, fixed):
            if isinstance(res, dict):
                concepts[i] = res
        logger.info("Critic regenerated %d/%d concepts: %s",
                    len(regen_idx), len(concepts), regen_idx)

    critics: list[dict] = []
    for i in range(len(concepts)):
        rv = by_index.get(i) or {}
        critics.append({
            "score": rv.get("score"),
            "verdict": rv.get("verdict"),
            "fail_rubrics": rv.get("fail_rubrics"),
            "worst_issues": rv.get("worst_issues"),
            "regenerated": i in regen_idx,
        })
    return concepts, critics


def _concept_sig(c: dict) -> dict:
    return {
        "anchor_ingredient": c.get("anchor_ingredient"),
        "dish_format": c.get("dish_format"),
        "cuisine": c.get("cuisine"),
    }


async def _regen_for_variety(
    client: AsyncAnthropic, concept: dict, base_system: str, ctx_text: str
) -> dict:
    s = _norm_sig(_concept_sig(concept))
    correction = (
        "\n\nVARIETY VIOLATION: this concept repeats a recently shown dish — or another "
        "concept in tonight's batch — on 2+ of "
        f"the three axes (dish_format={s['dish_format']!r}, anchor={s['anchor_ingredient']!r}, "
        f"cuisine={s['cuisine']!r}). Return ONE replacement concept as JSON "
        '{"recipes":[{...same shape...}]} that changes at least TWO of the three axes — '
        "a different dish_format AND/OR a different anchor_ingredient AND/OR a different "
        "cuisine, drawing on other pantry/deal items. If the pantry genuinely can't "
        "support a different anchor, keep it but change BOTH dish_format and cuisine, "
        "and say so explicitly in why_this_recipe. "
        f"Keep the SAME difficulty tier ('{(concept.get('difficulty') or '').strip() or 'the same'}'). "
        "Use ONLY ingredients actually in their kitchen or on the deals list — never "
        "invent a protein or item the pantry doesn't show."
    )
    prior = json.dumps(_concept_brief(concept), ensure_ascii=False)
    msg = f"{ctx_text}\n\nConcept that repeats:\n{prior}\n\nReturn the fresh concept now."
    data = await _call_json(
        client, model=settings.recipe_model, max_tokens=_CONCEPT_MAX_TOKENS,
        system=base_system + correction, user_msg=msg,
    )
    recs = data.get("recipes") if isinstance(data, dict) else None
    if isinstance(recs, list) and recs and isinstance(recs[0], dict):
        return recs[0]
    return concept


async def _enforce_variety(
    client: AsyncAnthropic,
    concepts: list[dict],
    recent_sigs: list[dict],
    base_system: str,
    ctx_text: str,
) -> list[dict]:
    """Regenerate (once) any concept whose signature repeats a recent one — or an
    earlier concept in this same batch — on 2+ of the 3 axes."""
    sigs = [_concept_sig(c) for c in concepts]
    collide: list[int] = []
    for i, s in enumerate(sigs):
        others = recent_sigs + [sigs[j] for j in range(len(sigs)) if j != i]
        if any(_axes_shared(s, o) >= 2 for o in others):
            collide.append(i)
    if not collide:
        return concepts
    fixed = await asyncio.gather(
        *(_regen_for_variety(client, concepts[i], base_system, ctx_text) for i in collide),
        return_exceptions=True,
    )
    for i, res in zip(collide, fixed):
        if isinstance(res, dict):
            concepts[i] = res
    # A concept that still repeats a recent signature — or another concept in this
    # same batch — after its single retry ships anyway (spec: regenerate ONCE), but
    # MUST honestly disclose the relaxation. Append a note to why_this_recipe if the
    # model didn't already say so.
    all_sigs = [_concept_sig(c) for c in concepts]
    _RELAX_MARKERS = ("another take", "too small", "reshapes", "same anchor",
                      "same shelf", "pantry can't", "pantry couldn't")
    for i in collide:
        s = _norm_sig(all_sigs[i])
        others = recent_sigs + [all_sigs[j] for j in range(len(concepts)) if j != i]
        if any(_axes_shared(s, o) >= 2 for o in others):
            anchor = s.get("anchor_ingredient") or "a pantry staple"
            w = (concepts[i].get("why_this_recipe") or "").strip()
            if not any(m in w.lower() for m in _RELAX_MARKERS):
                note = (
                    f"Another take on {anchor} — the pantry's too small for a fully "
                    "fresh anchor tonight, so this reshapes it instead."
                )
                concepts[i]["why_this_recipe"] = f"{w} {note}".strip()
            logger.info("Concept %r kept a repeated anchor after variety retry; "
                        "relaxation disclosed", concepts[i].get("title"))
    return concepts


# --------------------------------------------------------------------------- #
# Stage 1: concepts
# --------------------------------------------------------------------------- #
async def generate_concepts(
    db: AsyncSession,
    user: User,
    pinned_ids: list[int] | None = None,
    direction: str | None = None,
) -> list[Recipe]:
    """One fast Claude call → N persisted concept recipes (status='concept')."""
    pins = await _resolve_pins(db, user.id, pinned_ids or [])
    pin_dicts = _pin_dicts(pins)
    ctx = await _load_context(db, user)
    ctx.pin_block = _pin_block(pin_dicts)
    ctx.direction = (direction or "").strip()
    ctx.history_block, ctx.variety_block, recent_sigs = await _build_taste_history(
        db, user.id
    )

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

    n = _clamp_n(user.recipes_per_generation)
    system = _CONCEPT_SYSTEM.format(
        n_concepts=n,
        tier_plan=_tier_plan_text(n),
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
    full_system = (
        system
        + _protein_block(ctx.protein_floor)
        + ctx.taste_block
        + _direction_block(ctx.direction)
        + ctx.history_block
        + ctx.variety_block
        + ctx.pin_block
    )
    user_msg = ctx.context_text + f"\n\nPropose tonight's {n} dinner concepts now."

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    data = await _call_json(
        client, model=settings.recipe_model,
        max_tokens=max(_CONCEPT_MAX_TOKENS, 560 * n),
        system=full_system, user_msg=user_msg,
    )
    raw = [r for r in (data.get("recipes", []) if isinstance(data, dict) else [])
           if isinstance(r, dict)][:n]

    # Stage 1.5 critic: score, then regenerate weak concepts once.
    critics: list[dict] = [{} for _ in raw]
    if raw:
        raw, critics = await _critique_and_fix(
            client, raw, ctx.protein_floor, _profile_text(user, ctx),
            full_system, user_msg, _pantry_qty_lines(ctx.pantry),
        )
        # Variety guard: never re-serve a recent signature under a new name, and
        # keep the batch mutually distinct (each concept differs from EVERY other
        # on 2+ signature axes). Runs even with no history so intra-batch holds.
        raw = await _enforce_variety(client, raw, recent_sigs, full_system, user_msg)

    persisted: list[Recipe] = []
    for r, critic in zip(raw, critics):
        key_ings = [
            _reconcile_key(k, ctx.deal_by_ingredient, ctx.chain_name)
            for k in (r.get("key_ingredients") or [])
            if isinstance(k, dict)
        ]
        cuisine = (r.get("cuisine") or None)
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
            cuisine=cuisine,
            generated_store_name=ctx.store_name,
            pinned_items_json=pin_dicts or None,
            direction=ctx.direction or None,
            critic_json=critic or None,
            signature_json={
                "anchor_ingredient": r.get("anchor_ingredient"),
                "dish_format": r.get("dish_format"),
                "cuisine": cuisine,
            },
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


def _protein_of(data: dict) -> float | None:
    n = data.get("nutrition_per_serving") if isinstance(data, dict) else None
    if not isinstance(n, dict):
        return None
    try:
        return float(n.get("protein_g"))
    except (TypeError, ValueError):
        return None


async def _fill_details(db: AsyncSession, recipes: list[Recipe], ctx: _Ctx) -> None:
    """Generate full details for concept recipes: parallel Claude, serial writes.

    Enforces the protein floor: a slot whose detail comes back under the floor
    gets one corrective regeneration; if it still falls short we serve it but
    log a warning (the prompt needs work, not the user's dinner blocked).
    """
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    floor = ctx.protein_floor
    detail_system = _DETAIL_SYSTEM + _protein_block(floor) + ctx.pin_block
    msgs = [_detail_user_msg(r, ctx) for r in recipes]
    results = await asyncio.gather(
        *(
            _call_json(
                client, model=settings.detail_model, max_tokens=_DETAIL_MAX_TOKENS,
                system=detail_system, user_msg=m,
            )
            for m in msgs
        ),
        return_exceptions=True,
    )

    for i, (recipe, res) in enumerate(zip(recipes, results)):
        data = res if isinstance(res, dict) else {}

        # Protein-floor validation: one corrective regeneration if short.
        protein = _protein_of(data)
        if floor > 0 and protein is not None and protein < floor:
            correction = (
                f"\n\nCORRECTION: your previous version delivered only {protein:.0f} g "
                f"protein per serving, below the required {floor} g floor. Increase or "
                "add a protein source (pantry proteins first, then on-sale) and "
                "recompute the nutrition honestly."
            )
            retry = await _call_json(
                client, model=settings.detail_model, max_tokens=_DETAIL_MAX_TOKENS,
                system=detail_system + correction, user_msg=msgs[i],
            )
            if isinstance(retry, dict) and retry:
                data = retry
                protein = _protein_of(data)
            if protein is None or protein < floor:
                logger.warning(
                    "Recipe %s (%r) protein %sg still below floor %dg after correction",
                    recipe.id, recipe.title,
                    "unknown" if protein is None else f"{protein:.0f}", floor,
                )

        raw_ings = data.get("ingredients") if isinstance(data, dict) else None
        ingredients = _reconcile_ingredients(
            raw_ings or [], ctx.deal_by_ingredient, ctx.chain_name,
            ctx.pantry_by_iid, ctx.pantry_by_norm,
        )
        if not ingredients:
            # Fallback: derive usable ingredients from the concept's key list so
            # the recipe is never stuck as a 'concept' / blocking list builds.
            ingredients = _reconcile_ingredients(
                recipe.key_ingredients_json or [], ctx.deal_by_ingredient, ctx.chain_name,
                ctx.pantry_by_iid, ctx.pantry_by_norm,
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
        # Re-apply the batch's pin requirement to the detail stage.
        ctx.pin_block = _pin_block(recipes[0].pinned_items_json or [])
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
