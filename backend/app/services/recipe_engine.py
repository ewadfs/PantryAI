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
import re
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
from app.services import ai_metering, ingredient_matcher, nutrition, quantities
from app.services.vision import _extract_json

logger = logging.getLogger(__name__)

_MEALS_PER_DAY = 3
_CONCEPT_MAX_TOKENS = 1600     # terse concepts stay fast (few hundred tokens)
_DETAIL_MAX_TOKENS = 3500      # one full recipe
_EAGER_DETAILS = 3            # top-N concepts detailed eagerly; rest are lazy
_CONTEXT_DEALS = 30            # relevant deals shown to the model (of 400+)
_RECENT_TITLES = 15
_USE_SOON_WINDOW_DAYS = 2
_STAPLE_DEAL_CATS = {"meat", "seafood", "produce", "dairy"}
# Calorie band (Prompt 32 C7): one serving may not exceed this fraction of the
# daily calorie target without a rebalance attempt + an honest "heavy" chip.
_CAL_BAND = 0.55

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

# L1 — static rules (no per-call variables), the cacheable prefix shared across
# every concept call and across consecutive presses within the cache TTL.
_CONCEPT_SYSTEM = (
    """You are a skilled, creative home cook proposing tonight's dinner options for \
a specific person. Propose recipe CONCEPTS at the exact count and difficulty mix \
given in the request. Difficulty guide: easy = ≤15 min active & ≤6 ingredients; \
medium = 15-30 min active; hard = 30+ min active, impressive result. Order them \
easy → medium → hard. Concepts only — no full quantities or steps yet.

Hard requirements:
1. Respect ALL allergies and excluded ingredients in the DINER PROFILE — non-negotiable.
2. Prioritize ingredients already in their kitchen; the best recipe buys the least.
3. Use items flagged use_soon early and prominently.
4. When something must be bought, strongly prefer items from the deals list and say so.
5. Hit the per-serving calorie goal in the profile (protein has a hard floor below).
6. Lean toward the profile's cuisines; avoid repeating the recent titles in the request.
7. servings = the household size in the profile.
8. Assume pantry staples (salt, pepper, oils, common spices) are available.
9. The concepts must be mutually distinct dinners — each must differ from EVERY other \
in this batch on at least TWO of the three signature axes \
{anchor_ingredient, dish_format, cuisine}. No two near-identical dishes.
10. TITLE DIVERSITY — titles are distinct headlines: no signature word may appear \
in more than 2 titles in a batch. Taste notes inform TECHNIQUE, not naming — \
"loves char" means hard sears happen in the method, NOT that 'Charred' headlines \
every title.
11. INGREDIENT VARIETY — new concepts must not share more than about half their \
non-staple ingredients with anything recently shown or saved this week, and a \
distinctive seasoning blend may lead at most two concepts per batch. (Pinned \
items, assigned market anchors, and purchases already planned for saved recipes \
don't count as overlap.)

"""
    + _TECHNIQUE_RULES
    + """

For each concept give EXACTLY 4 key_ingredients — the defining ones — each with \
generic_name, brand (null if none/store brand), in_pantry (bool), on_sale (bool), \
and sale_price only when on_sale.

For each concept also give its SIGNATURE: anchor_ingredient (the single defining \
pantry/deal item the dish is built around), dish_format (one word: pasta, bowl, \
roast, tacos, soup, skillet, stir-fry, salad, sandwich, bake, curry, stew, …), and \
flavor_lead — the 1-2 dominant seasonings or blends that define the dish \
(e.g. "carne asada seasoning", "curry powder", "lemon-dill"). \
total_time_min MUST include passive time (water boiling, oven preheat, rests) — a \
"15 min" dish that needs a 20-min braise is a lie.

BE TERSE — this is a fast preview, not the full recipe. description ≤ 12 words; \
why_this_recipe ≤ 14 words; at most 3 tags. No extra prose or explanation.

Set market_pick true ONLY on a concept the request explicitly designates as a \
MARKET PICK (default false).

Return ONLY valid JSON:
{"recipes":[{title, description, difficulty, prep_time_min, cook_time_min, \
total_time_min, servings, why_this_recipe, cuisine, dish_format, anchor_ingredient, \
flavor_lead:[1-2 strings], market_pick, tags:[...], \
nutrition_per_serving:{calories, protein_g}, \
key_ingredients:[{generic_name, brand, in_pantry, on_sale, sale_price}]}]}"""
)


def _concept_profile(user: User) -> str:
    """L2 — the slow-changing diner profile (allergies, targets, cuisines,
    household). Cached alongside pantry/deals; changes at most a few times a day."""
    return (
        "DINER PROFILE:\n"
        f"- allergies: {_fmt_list(user.allergies)}; "
        f"excluded: {_fmt_list(user.excluded_ingredients)}\n"
        f"- target ≈{round(user.calorie_target / _MEALS_PER_DAY)} calories per serving\n"
        f"- cuisines: {_fmt_list(user.cuisine_preferences)}\n"
        f"- household size (servings): {user.household_size}"
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
- Keep it to at most 8 numbered steps. Be concise — merge trivial actions into one \
step and cut filler. But KEEP the one or two lines that teach a technique-critical \
"why" (e.g. why a dry surface crisps, why spices bloom late); those earn their words.

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


async def _saved_stores(db: AsyncSession, user_id: int) -> list:
    """The user's saved stores, DEFAULT FIRST, deduped by (chain, region).
    Rows carry (chain_id, chain_name, store_name, region_key).

    Self-healing (Prompt 31): a partial unique index should guarantee exactly one
    default, but if the data is ever inconsistent (multiple defaults, or none with
    stores saved) we pick the most-recently-added candidate and log a warning
    rather than crashing the whole generation."""
    rows = (
        await db.execute(
            select(
                UserStore.is_default,
                StoreLocation.chain_id,
                SupportedChain.chain_name,
                StoreLocation.store_name,
                StoreLocation.region_key,
            )
            .join(StoreLocation, StoreLocation.id == UserStore.store_location_id)
            .join(SupportedChain, SupportedChain.id == StoreLocation.chain_id)
            .where(UserStore.user_id == user_id)
            .order_by(UserStore.added_at.desc(), UserStore.id.desc())
        )
    ).all()
    if not rows:
        return []
    defaults = [r for r in rows if r.is_default]
    if len(defaults) == 1:
        chosen = defaults[0]
    elif len(defaults) > 1:
        logger.warning(
            "User %s has %d default stores (expected 1) — using most recent.",
            user_id, len(defaults),
        )
        chosen = defaults[0]  # rows are added_at-desc → most recent first
    else:
        logger.warning(
            "User %s has %d saved store(s) but no default — using most recent.",
            user_id, len(rows),
        )
        chosen = rows[0]
    ordered = [chosen] + [r for r in rows if r is not chosen]
    seen: set[tuple] = set()
    deduped = []
    for r in ordered:
        k = (r.chain_id, r.region_key)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(r)
    return deduped


async def _all_current_deals(
    db: AsyncSession, chain_id: int, today: date, region_key: str | None = None
) -> list[DealCache]:
    # Region-scoped when the store has a region; falls back to chain for legacy.
    scope = (
        DealCache.region_key == region_key
        if region_key is not None
        else DealCache.chain_id == chain_id
    )
    return (
        (
            await db.execute(
                select(DealCache).where(
                    scope,
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


# --------------------------------------------------------------------------- #
# Market picks (Prompt 28 A) — deal-anchored recipes when the pantry lacks
# distinct anchors, or (for N=5) always at least one, so deals can LEAD.
# --------------------------------------------------------------------------- #
_PROTEIN_CATS = {"meat", "seafood", "eggs", "protein"}
# Produce hearty enough to anchor a dinner (a "hero" vegetable).
_HERO_PRODUCE = {
    "portobello_mushroom", "white_mushroom", "mushroom", "butternut_squash",
    "eggplant", "cauliflower", "sweet_potato", "russet_potato", "yukon_gold_potato",
    "red_potato", "zucchini", "cabbage", "broccoli",
}
_HERO_IIDS: set[int] | None = None


def _hero_produce_iids() -> set[int]:
    """Ingredient ids of hero (dinner-anchorable) produce. Lazy — needs the
    ingredient_matcher cache preloaded (it is, before generation)."""
    global _HERO_IIDS
    if _HERO_IIDS is None:
        ids: set[int] = set()
        for name in _HERO_PRODUCE:
            iid, _c = ingredient_matcher.match_ingredient(name)
            if iid is not None:
                ids.add(iid)
        _HERO_IIDS = ids
    return _HERO_IIDS


def _anchor_sufficient(item: PantryItem, household: int) -> bool:
    """Is a pantry item present in enough quantity to ANCHOR a dinner?"""
    q = quantities.parse(item.quantity_estimate, item.unit)
    if q is None:
        return True  # present but amount unknown — assume it can anchor
    hh = max(1, household)
    if q.family == "weight":
        return q.value >= hh * 4.0  # ≥4 oz/serving of a main protein
    if q.family == "count":
        return q.value >= hh
    return True  # container / volume — assume viable


def _anchor_census(pantry: list[PantryItem], household: int) -> set[str]:
    """Distinct viable anchor keys (proteins + hero produce) in the pantry."""
    keys: set[str] = set()
    for it in pantry:
        cat = (it.category or "").lower()
        norm = ingredient_matcher._norm(it.name or "")
        is_protein = cat in _PROTEIN_CATS
        is_hero = cat == "produce" and norm in _HERO_PRODUCE
        if not (is_protein or is_hero):
            continue
        if not _anchor_sufficient(it, household):
            continue
        iid, _c = ingredient_matcher.match_ingredient(it.name or "")
        keys.add(f"i{iid}" if iid is not None else norm)
    return keys


def _market_slot_count(n: int, anchors: int) -> int:
    """How many of the N slots become market picks. Surplus slots (beyond
    distinct pantry anchors) convert; N=5 always reserves ≥1 even at full
    diversity; N=3 keeps one only when anchors < 3 (which the surplus already
    captures)."""
    slots = max(n - anchors, 0)
    if n == 5:
        slots = max(slots, 1)
    return min(slots, n)


def _hero_produce_name(name_tokens: set[str]) -> bool:
    """Does a (normalized) deal name look like hero produce? True when every
    token of some hero entry appears in the name ('butternut squash medley')."""
    for hero in _HERO_PRODUCE:
        h_tokens = set(ingredient_matcher._tokens(hero.replace("_", " ")))
        if h_tokens and h_tokens <= name_tokens:
            return True
    return False


# Deal names in an anchor category that still can't anchor a dinner — breading,
# sauces, sides, snacks. Light sanity filter for the WIDENED pool (Prompt 32 3b).
_NON_ANCHOR_RE = re.compile(
    r"\b(?:breading|bread\s*crumbs?|batter|coating|sauce|marinade|dressing|"
    r"seasoning|rub|spice|gravy|dip|salsa|hummus|broth|stock|bouillon|base|"
    r"juice|drink|smoothie|snack|chips?|sticks?|nuggets?|poppers|jerky|"
    r"croutons?|topping|glaze|paste|powder|mix|kit|salad\s+kit|deli|"
    r"lunch\s*meat|cold\s+cuts?|sliced\s+to\s+order)\b",
    re.IGNORECASE,
)


@dataclass
class MarketCandidate:
    """One viable market-pick anchor. ``iid`` is None for widened-pool deals
    that never matched ingredient_master — anchoring only needs the deal's
    name/category/price; ingredient matching stays required only for pantry
    math (Prompt 32 3b)."""

    deal: DealCache
    store: str | None            # chain the deal belongs to
    iid: int | None              # matched ingredient id, when there is one
    key: str                     # stable anchor identity for rotation/dedup
    tier: int                    # 0 proteins, 1 hero produce, 2 other produce
    cross_store: bool = False    # anchored at a saved store other than default
    repeat: bool = False         # deliberate repeat after full-pool exhaustion

    @property
    def clean_name(self) -> str:
        return ingredient_matcher.normalize_flyer_name(
            self.deal.product_name or "", self.deal.brand
        )


def _market_candidates(
    deals: list[DealCache],
    pantry_iids: set[int],
    pantry_norms: set[str],
    store: str | None,
    cross_store: bool = False,
    exclude_terms: list[str] | None = None,
) -> list[MarketCandidate]:
    """The WIDENED anchor pool for one store (Prompt 32 3b): ingredient-matched
    deals ∪ any current deal whose extraction category is meat/seafood/produce
    and whose name passes a light non-anchor sanity filter. Proteins rank
    first, then hero produce, by savings_pct with a null-safe fallback to
    absolute price. Never something already in the pantry; distinct anchors.

    ``exclude_terms`` (Prompt 30): a counted direction caps categories, so
    surplus market picks must avoid the requested terms."""
    hero = _hero_produce_iids()
    terms = [t.lower() for t in (exclude_terms or [])]
    out: list[MarketCandidate] = []
    seen: set[str] = set()

    for d in deals:
        if d.sale_price is None:  # a market pick must cite a real price
            continue
        name = d.product_name or ""
        cat = (d.category or "").lower()
        iid = d.matched_ingredient_id
        if terms and any(t in name.lower() for t in terms):
            continue
        clean = ingredient_matcher.normalize_flyer_name(name, d.brand)
        norm = ingredient_matcher._norm(clean or name)
        tokens = set(ingredient_matcher._tokens(clean or name))

        if cat in {"meat", "seafood"}:
            tier = 0
        elif cat == "produce":
            tier = 1 if (iid in hero or _hero_produce_name(tokens)) else 2
        else:
            continue  # anchor categories only, matched or not
        if _NON_ANCHOR_RE.search(name):
            continue  # breading, sauces, kits — not dinner anchors
        # Never anchor on something the user already owns.
        if (iid is not None and iid in pantry_iids) or (norm and norm in pantry_norms):
            continue

        key = f"i{iid}" if iid is not None else f"n:{norm}"
        if not norm or key in seen:
            continue
        seen.add(key)
        out.append(
            MarketCandidate(
                deal=d, store=store, iid=iid, key=key, tier=tier,
                cross_store=cross_store,
            )
        )

    def rank(c: MarketCandidate):
        sav = float(c.deal.savings_pct) if c.deal.savings_pct is not None else None
        # savings-ranked; unknown savings fall back to absolute price (cheap first)
        return (
            c.tier,
            0 if sav is not None else 1,
            -(sav or 0.0),
            float(c.deal.sale_price),
        )

    out.sort(key=rank)
    return out


def _same_product(a: MarketCandidate, b: MarketCandidate) -> bool:
    """Are two candidates the SAME UNDERLYING PRODUCT? Distinct deal rows —
    even ones matching different ingredient_master variants (chicken_breast
    vs chicken_tenders) — must not both anchor a batch (Prompt 34 C6: the
    duplicate-chicken-breast leak). Same iid, or token containment / ≥0.5
    overlap between the qualifier-stripped names."""
    if a.iid is not None and a.iid == b.iid:
        return True
    ta = set(ingredient_matcher._tokens(a.clean_name or a.deal.product_name or ""))
    tb = set(ingredient_matcher._tokens(b.clean_name or b.deal.product_name or ""))
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / len(ta | tb)
    return ta <= tb or tb <= ta or overlap >= 0.5


def _select_market_anchors(
    primary: list[MarketCandidate],
    other_stores: list[list[MarketCandidate]],
    count: int,
    exclude_keys: set[str],
) -> list[MarketCandidate]:
    """Pick ``count`` anchors that are DISTINCT DEALS **and** DISTINCT
    UNDERLYING PRODUCTS. Order: the anchored store's fresh candidates (not
    used in recent batches, 28-A5 rotation), then its recently-used ones,
    then each other saved store's (sparse-store fallback, Prompt 32 #4,
    clearly labeled). Tier-2 produce (an apple can't anchor a dinner)
    backfills only after every store's proteins + hero produce are exhausted.
    Only after every saved store's whole pool is exhausted may an anchor
    repeat — marked so the disclosure is forced."""
    if count <= 0:
        return []
    chosen: list[MarketCandidate] = []
    used: set[str] = set()

    def take(pool: list[MarketCandidate], fresh_only: bool, max_tier: int) -> None:
        for c in pool:
            if len(chosen) >= count:
                return
            if c.key in used or c.tier > max_tier:
                continue
            if any(_same_product(c, ch) for ch in chosen):
                continue  # distinct products, not just distinct deal rows (P34)
            if fresh_only and c.key in exclude_keys:
                continue
            used.add(c.key)
            chosen.append(c)

    for max_tier in (1, 2):  # proteins + hero produce everywhere, THEN tier-2
        take(primary, True, max_tier)
        take(primary, False, max_tier)  # rotation backfill at the anchored store
        for pool in other_stores:
            take(pool, True, max_tier)
            take(pool, False, max_tier)

    # Every saved store exhausted — anchors may repeat, with disclosure.
    base = list(chosen)
    i = 0
    while base and len(chosen) < count:
        c = base[i % len(base)]
        chosen.append(
            MarketCandidate(
                deal=c.deal, store=c.store, iid=c.iid, key=c.key, tier=c.tier,
                cross_store=c.cross_store, repeat=True,
            )
        )
        i += 1
    return chosen


def _market_block(cands: list[MarketCandidate], chain_name: str | None) -> str:
    if not cands:
        return ""
    lines = []
    for i, c in enumerate(cands, 1):
        d = c.deal
        unit = f"/{d.price_unit}" if d.price_unit else ""
        sav = f", {d.savings_pct}% off" if d.savings_pct is not None else ""
        at = (
            f" — at {c.store}" if c.cross_store and c.store
            else ""
        )
        rep = (
            " [REPEAT ANCHOR — every distinct sale anchor across the saved "
            "stores is already in use tonight; say so in why_this_recipe]"
            if c.repeat
            else ""
        )
        lines.append(
            f"- MARKET PICK {i}: build around {d.product_name}: "
            f"${d.sale_price}{unit}{sav}{at}{rep}"
        )
    return (
        f"\n\nMARKET PICKS — this batch MUST include exactly {len(cands)} MARKET PICK "
        "concept(s), one per ASSIGNED anchor below. A market pick is a dinner ANCHORED "
        f"on a strong current deal at {chain_name or 'the store'} (or the labeled "
        "store) that the user does NOT own — the intentional purchase of the week, "
        "chosen to LEAD the recipe:\n" + "\n".join(lines) + "\n"
        "Each market pick uses EXACTLY its assigned anchor — never substitute, and "
        "never build two market picks on the same anchor. Set anchor_ingredient to "
        "the assigned item, set \"market_pick\": true, and in why_this_recipe name "
        "the deal and its price (e.g. \"built around pork shoulder at $1.99/lb this "
        "week\") — and, when the anchor is at a different store, name that store. "
        "The pantry-first rule (#2) is WAIVED for a market pick's anchor — buying it "
        "is the point. Every OTHER concept stays pantry-first."
    )


def _match_candidate(
    anchor_name: str, cands: list[MarketCandidate], pantry_iids: set[int]
) -> MarketCandidate | None:
    """The designated market candidate ``anchor_name`` refers to, if any.
    Ingredient-id match first; widened (unmatched) candidates match by token
    overlap against the qualifier-stripped deal name."""
    if not anchor_name or not cands:
        return None
    iid, _c = ingredient_matcher.match_ingredient(anchor_name)
    if iid is not None and iid not in pantry_iids:
        for c in cands:
            if c.iid is not None and c.iid == iid:
                return c
    a_tokens = set(ingredient_matcher._tokens(anchor_name))
    if not a_tokens:
        return None
    for c in cands:
        c_tokens = set(ingredient_matcher._tokens(c.clean_name or c.deal.product_name or ""))
        if not c_tokens:
            continue
        overlap = len(a_tokens & c_tokens) / len(a_tokens | c_tokens)
        if a_tokens <= c_tokens or c_tokens <= a_tokens or overlap >= 0.5:
            return c
    return None


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


# --------------------------------------------------------------------------- #
# Owned-perishable guarantee (Prompt 34 A)
# --------------------------------------------------------------------------- #
# Fresh non-staple proteins outside the meat/seafood categories that still
# spoil (the scanner may file tofu under dairy/refrigerated).
_FRESH_PROTEIN_NAMES = ("tofu", "tempeh", "seitan")
# Shelf-stable markers: these never spoil, so they get no urgency slot.
_SHELF_STABLE_MARKERS = ("canned", "jerky", "dried", "dehydrated", "cured",
                         "shelf stable", "shelf-stable")


def _owned_perishables(
    pantry: list[PantryItem], household: int, today: date,
    excluded_terms: list[str] | None = None,
) -> list[PantryItem]:
    """Active pantry items that WILL SPOIL and can anchor a dinner (P34 A1):
    meat/seafood categories plus fresh non-staple proteins (tofu), with
    sufficient quantity (the P23 parser via ``_anchor_sufficient``). Staples
    and shelf-stable proteins (canned beans/tuna) are excluded — they don't
    need urgency. use_soon items sort first, then by estimated expiry."""
    excl = [t.lower() for t in (excluded_terms or [])]
    out: list[PantryItem] = []
    for it in pantry:
        if it.is_staple:
            continue
        cat = (it.category or "").lower()
        name = (it.name or "").lower()
        is_fresh_protein = any(t in name for t in _FRESH_PROTEIN_NAMES)
        if cat not in {"meat", "seafood"} and not is_fresh_protein:
            continue
        if cat == "canned" or any(m in name for m in _SHELF_STABLE_MARKERS):
            continue
        if excl and any(t in name for t in excl):
            continue  # never force an allergen / excluded ingredient
        if not _anchor_sufficient(it, household):
            continue
        out.append(it)
    out.sort(key=lambda it: (
        not _use_soon(it, today),
        it.estimated_expiry or date.max,
        it.name or "",
    ))
    return out


def _perishable_block(item: PantryItem, use_soon: bool) -> str:
    qty = " ".join(p for p in (item.quantity_estimate, item.unit) if p) or "some"
    urgency = (
        f' Its why_this_recipe must LEAD with the urgency (e.g. "Your '
        f'{item.name} should be used in the next day or two.").'
        if use_soon
        else ""
    )
    flag = ", flagged USE SOON — it should be used in the next day or two"
    return (
        f"\n\nOWNED-PERISHABLE SLOT — the user already owns {item.name} "
        f"(HAVE {qty}), which is perishable{flag if use_soon else ''}. "
        f"Exactly ONE concept in this batch (NOT a market pick) must be "
        f'ANCHORED on it: set its anchor_ingredient to "{item.name}". '
        f"This slot is EXEMPT from the recently-shown anchor-variety rule — "
        f"{item.name} may anchor again tonight even if it anchored recently — "
        f"but the dish must still differ from recent {item.name} dishes in "
        "format, cuisine, and ingredients (never the same dish again)."
        + urgency
    )


def _anchor_is_item(anchor_name: str, item: PantryItem) -> bool:
    """Does a concept's anchor refer to this pantry item? Ingredient-id match
    first, else token containment / ≥0.5 overlap."""
    if not anchor_name:
        return False
    a_key = _ing_key(anchor_name)
    i_key = _ing_key(item.name or "")
    if a_key is not None and a_key == i_key:
        return True
    ta = set(ingredient_matcher._tokens(anchor_name))
    ti = set(ingredient_matcher._tokens(item.name or ""))
    if not ta or not ti:
        return False
    overlap = len(ta & ti) / len(ta | ti)
    return ta <= ti or ti <= ta or overlap >= 0.5


_URGENCY_MARKERS = ("next day", "use soon", "should be used", "use it up",
                    "before it turns", "won't keep")


def _apply_urgency_line(concept: dict, item: PantryItem) -> None:
    """use_soon escalation (P34 A4): the perishable slot's why_this_recipe
    LEADS with urgency; prepend deterministically if the model didn't."""
    w = (concept.get("why_this_recipe") or "").strip()
    if any(m in w.lower() for m in _URGENCY_MARKERS):
        return
    line = f"Your {item.name} should be used in the next day or two."
    concept["why_this_recipe"] = f"{line} {w}".strip()


async def _enforce_perishable_slot(
    client: AsyncAnthropic,
    concepts: list[dict],
    item: PantryItem,
    cands: list[MarketCandidate],
    pantry_iids: set[int],
    base_system: list[dict] | str,
    ctx_text: str,
    use_soon: bool,
) -> list[dict]:
    """Deterministic backstop for the owned-perishable guarantee (P34 A2):
    ≥1 non-market concept anchored on ``item``. If the model skipped it, one
    targeted regeneration replaces a non-market concept; the use_soon urgency
    line is applied to whichever concept holds the slot."""

    def slot_indices() -> list[int]:
        return [
            i for i, c in enumerate(concepts)
            if _anchor_is_item(c.get("anchor_ingredient") or "", item)
            and _match_candidate(
                c.get("anchor_ingredient") or "", cands, pantry_iids
            ) is None
        ]

    idx = slot_indices()
    if not idx:
        non_market = [
            i for i, c in enumerate(concepts)
            if _match_candidate(
                c.get("anchor_ingredient") or "", cands, pantry_iids
            ) is None
        ]
        target = non_market[0] if non_market else 0
        qty = " ".join(
            p for p in (item.quantity_estimate, item.unit) if p
        ) or "some"
        tier = (concepts[target].get("difficulty") or "").strip() or "the same"
        correction = (
            f"\n\nOWNED-PERISHABLE SLOT MISSING: no concept is anchored on the "
            f"user's {item.name} (HAVE {qty}), which is perishable"
            + (" and flagged USE SOON" if use_soon else "")
            + ". Return ONE replacement concept as JSON "
            '{"recipes":[{...same shape...}]} ANCHORED on it: set '
            f'anchor_ingredient exactly to "{item.name}" and market_pick '
            f"false. Differ from recent {item.name} dishes in format and "
            f"cuisine. Keep the SAME difficulty tier ('{tier}')."
        )
        prior = json.dumps(_concept_brief(concepts[target]), ensure_ascii=False)
        msg = (
            f"{ctx_text}{correction}\n\nConcept to replace:\n{prior}\n\n"
            "Return the replacement now."
        )
        data = await _call_json(
            client, model=settings.recipe_model, max_tokens=_CONCEPT_MAX_TOKENS,
            system=base_system, user_msg=msg, category="generation",
            stage="concept_fix",
        )
        recs = data.get("recipes") if isinstance(data, dict) else None
        if isinstance(recs, list) and recs and isinstance(recs[0], dict):
            concepts[target] = recs[0]
        idx = slot_indices()
        if idx:
            logger.info("Perishable slot: regenerated concept %d onto %r",
                        target, item.name)
        else:
            logger.warning(
                "Perishable slot: guarantee for %r not satisfied after one "
                "regeneration — shipping without it this batch.", item.name,
            )
    if idx and use_soon:
        _apply_urgency_line(concepts[idx[0]], item)
    return concepts


def _feed_sort(
    pairs: list[tuple[dict, dict]],
    cands: list[MarketCandidate],
    pantry_iids: set[int],
) -> list[tuple[dict, dict]]:
    """Feed order (P34 B5): within each difficulty tier, pantry-anchored
    dishes lead ($0 buys first, then cheapest), market picks follow. Stable
    within groups. Operates on (concept, critic) pairs so critic metadata
    stays attached."""
    tier_rank = {t: i for i, t in enumerate(_TIER_ORDER)}

    def key(pair: tuple[dict, dict]):
        c, _critic = pair
        is_market = _match_candidate(
            c.get("anchor_ingredient") or "", cands, pantry_iids
        ) is not None
        cost = _cost_from_ingredients([
            k for k in (c.get("key_ingredients") or []) if isinstance(k, dict)
        ])
        return (
            tier_rank.get((c.get("difficulty") or "").strip().lower(), 1),
            is_market,
            int(cost["unknown_priced_items"] > 0 or cost["known_buy_cost"] > 0),
            float(cost["known_buy_cost"]),
            cost["unknown_priced_items"],
        )

    return sorted(pairs, key=key)


# --------------------------------------------------------------------------- #
# Ingredient-overlap variety math (Prompt 33)
# --------------------------------------------------------------------------- #
# Violation thresholds: hard Jaccard cap, the lower same-anchor cap, and the
# max concepts one flavor lead may head per batch.
J_HARD = 0.6
J_SAME_ANCHOR = 0.45
LEAD_BATCH_MAX = 2

# Fallback staple words for names that don't match ingredient_master — every
# token generic ⇒ staple (salt, oils, water, basic baking).
_STAPLE_TOKENS = {
    "salt", "kosher", "sea", "pepper", "black", "peppercorn", "oil", "olive",
    "vegetable", "canola", "cooking", "butter", "sugar", "flour", "water",
    "spice", "spices",
}


def _ing_key(name: str) -> str | None:
    """Stable identity for an ingredient name: the matched ingredient id when
    there is one (normalizing through the 18-B4/32-3c qualifier stripper),
    else the normalized name. None for empty names."""
    if not name or not str(name).strip():
        return None
    clean = ingredient_matcher.normalize_flyer_name(str(name)) or str(name)
    iid, _conf = ingredient_matcher.match_ingredient(clean)
    if iid is None:
        iid, _conf = ingredient_matcher.match_ingredient(str(name))
    if iid is not None:
        return f"i{iid}"
    norm = ingredient_matcher._norm(clean)
    return f"n:{norm}" if norm else None


def _is_staple_name(name: str, key: str | None) -> bool:
    """Staples (salt, oils, basic spices) don't count as overlap (P33 A1)."""
    if key is not None and key.startswith("i"):
        return ingredient_matcher.is_staple_id(int(key[1:]))
    tokens = ingredient_matcher._tokens(name or "")
    return bool(tokens) and all(t in _STAPLE_TOKENS for t in tokens)


def _overlap_set(names: list[str], carveout: set[str]) -> set[str]:
    """Normalized, NON-STAPLE ingredient keys, minus the carve-outs (pins,
    market anchors, planned shared purchases — good overlap stays legal)."""
    out: set[str] = set()
    for name in names:
        key = _ing_key(name)
        if key is None or key in carveout or _is_staple_name(name, key):
            continue
        out.add(key)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _concept_ing_names(c: dict) -> list[str]:
    return [
        str(k.get("generic_name") or "")
        for k in (c.get("key_ingredients") or [])
        if isinstance(k, dict)
    ]


def _recipe_ing_names(r: Recipe) -> list[str]:
    """A stored recipe's ingredient names — full list once detailed, else the
    concept's key ingredients."""
    src = r.ingredients_json or r.key_ingredients_json or []
    return [
        str(i.get("generic_name") or i.get("name") or "")
        for i in src
        if isinstance(i, dict)
    ]


def _flavor_leads(c: dict) -> list[str]:
    """The concept's declared flavor lead(s), normalized, at most 2."""
    raw = c.get("flavor_lead")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out = []
    for x in raw[:2]:
        s = str(x).strip().lower()
        if s:
            out.append(s[:60])
    return out


@dataclass
class _OverlapEntry:
    """One comparison target: a batchmate, a recent recipe, or a saved one."""

    title: str
    origin: str                 # "batch" | "recent" | "saved this week"
    keys: set[str]
    anchor_key: str | None


def _entry_for_recipe(r: Recipe, carveout: set[str], origin: str) -> _OverlapEntry:
    sig = r.signature_json if isinstance(r.signature_json, dict) else {}
    return _OverlapEntry(
        title=r.title,
        origin=origin,
        keys=_overlap_set(_recipe_ing_names(r), carveout),
        anchor_key=_ing_key(str(sig.get("anchor_ingredient") or "")),
    )


async def _overlap_pool(
    db: AsyncSession, user_id: int, carveout: set[str],
    exclude_ids: set[int] | None = None,
) -> list[_OverlapEntry]:
    """Comparison targets outside the batch (P33 B3): all recipes from the
    last 3 batches / 48h, plus the current week's saved recipes."""
    exclude_ids = exclude_ids or set()
    cutoff = _now() - timedelta(hours=_RECENT_HOURS)
    recent = (
        (
            await db.execute(
                select(Recipe)
                .where(Recipe.user_id == user_id, Recipe.generated_at >= cutoff)
                .order_by(Recipe.generated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    batches: list[tuple[datetime, list[Recipe]]] = []
    for r in recent:
        if r.id in exclude_ids:
            continue
        if batches and batches[-1][0] == r.generated_at:
            batches[-1][1].append(r)
        else:
            batches.append((r.generated_at, [r]))
    entries = [
        _entry_for_recipe(r, carveout, "recent")
        for _ts, rs in batches[:_RECENT_BATCHES]
        for r in rs
    ]

    saved = (
        (
            await db.execute(
                select(Recipe)
                .join(WeekRecipe, WeekRecipe.recipe_id == Recipe.id)
                .where(
                    WeekRecipe.user_id == user_id,
                    WeekRecipe.week_start == week_start_for(date.today()),
                )
            )
        )
        .scalars()
        .all()
    )
    seen_titles = {e.title for e in entries}
    for r in saved:
        if r.id in exclude_ids or r.title in seen_titles:
            continue
        entries.append(_entry_for_recipe(r, carveout, "saved this week"))
    return entries


def _saved_week_purchase_keys(saved: list[Recipe]) -> set[str]:
    """Ingredients the user must PURCHASE for already-saved recipes this week —
    shared purchases are shopping efficiency, not monotony (P33 B6)."""
    keys: set[str] = set()
    for r in saved:
        for ing in r.ingredients_json or []:
            if isinstance(ing, dict) and ing.get("in_pantry") is not True:
                k = _ing_key(str(ing.get("generic_name") or ing.get("name") or ""))
                if k is not None:
                    keys.add(k)
    return keys


def _overlap_carveout(
    pins: list[dict],
    market_candidates: list[MarketCandidate],
    purchase_keys: set[str],
) -> set[str]:
    """Keys excluded from every Jaccard set (P33 B6): pinned items, the
    batch's market anchors, and this week's planned shared purchases."""
    keys: set[str] = set(purchase_keys)
    for p in pins:
        k = _ing_key(str(p.get("name") or ""))
        if k is not None:
            keys.add(k)
    for c in market_candidates:
        keys.add(c.key)  # same key space (i{iid} / n:{norm})
        k = _ing_key(c.clean_name or c.deal.product_name or "")
        if k is not None:
            keys.add(k)
    return keys


def _overlap_violation(
    keys: set[str], anchor_key: str | None, entries: list[_OverlapEntry]
) -> tuple[_OverlapEntry, float] | None:
    """The worst comparison this concept violates (P33 B4), or None.
    Violation = J > 0.6, OR (J > 0.45 AND same anchor)."""
    worst: tuple[_OverlapEntry, float] | None = None
    for e in entries:
        j = _jaccard(keys, e.keys)
        same_anchor = (
            anchor_key is not None and e.anchor_key is not None
            and anchor_key == e.anchor_key
        )
        if j > J_HARD or (j > J_SAME_ANCHOR and same_anchor):
            if worst is None or j > worst[1]:
                worst = (e, j)
    return worst


async def _regen_for_overlap(
    client: AsyncAnthropic,
    concept: dict,
    reason: str,
    base_system: list[dict] | str,
    ctx_text: str,
) -> dict:
    tier = (concept.get("difficulty") or "").strip() or "the same"
    correction = (
        f"\n\nINGREDIENT OVERLAP VIOLATION: {reason} Return ONE replacement "
        'concept as JSON {"recipes":[{...same shape...}]} that differs in '
        "protein treatment, seasoning family, or dish format — not just the "
        f"title. Keep the SAME difficulty tier ('{tier}'), and if this is a "
        "MARKET PICK or the OWNED-PERISHABLE slot, keep the SAME anchor — "
        "change how it's treated, not what it is. Use ONLY ingredients "
        "actually in their kitchen or on the deals list."
    )
    prior = json.dumps(_concept_brief(concept), ensure_ascii=False)
    msg = f"{ctx_text}{correction}\n\nConcept to replace:\n{prior}\n\nReturn the replacement now."
    data = await _call_json(
        client, model=settings.recipe_model, max_tokens=_CONCEPT_MAX_TOKENS,
        system=base_system, user_msg=msg, category="generation", stage="concept_fix",
    )
    recs = data.get("recipes") if isinstance(data, dict) else None
    if isinstance(recs, list) and recs and isinstance(recs[0], dict):
        return recs[0]
    return concept


_OVERLAP_DISCLOSURE_MARKERS = ("shares", "close cousin", "overlap")


def _disclose_overlap(concept_or_none: dict | None, recipe_or_none: Recipe | None,
                      entry: _OverlapEntry, j: float) -> None:
    """Existing disclosed-relaxation style (P33 B5): a post-retry survivor
    ships, but never silently."""
    note = (
        f"Close cousin of {entry.title} ({round(j * 100)}% shared non-staple "
        "ingredients) — kept after a retry; the pantry steers hard tonight."
    )
    if concept_or_none is not None:
        w = (concept_or_none.get("why_this_recipe") or "").strip()
        if not any(m in w.lower() for m in _OVERLAP_DISCLOSURE_MARKERS):
            concept_or_none["why_this_recipe"] = f"{w} {note}".strip()
    if recipe_or_none is not None:
        w = (recipe_or_none.why_this_recipe or "").strip()
        if not any(m in w.lower() for m in _OVERLAP_DISCLOSURE_MARKERS):
            recipe_or_none.why_this_recipe = f"{w} {note}".strip()


async def _enforce_ingredient_overlap(
    client: AsyncAnthropic,
    concepts: list[dict],
    pool: list[_OverlapEntry],
    carveout: set[str],
    base_system: list[dict] | str,
    ctx_text: str,
) -> list[dict]:
    """Deterministic ingredient-overlap check (P33 B): every concept vs its
    batchmates + the recent/saved pool. Violations regenerate ONCE with the
    overlapping recipe NAMED; survivors ship with the disclosure note. Also
    enforces the flavor-lead cap (one lead heads at most 2 concepts/batch)."""

    def concept_entry(i: int) -> _OverlapEntry:
        c = concepts[i]
        return _OverlapEntry(
            title=str(c.get("title") or f"concept {i}"),
            origin="batch",
            keys=_overlap_set(_concept_ing_names(c), carveout),
            anchor_key=_ing_key(str(c.get("anchor_ingredient") or "")),
        )

    def find_violations() -> dict[int, str]:
        entries = [concept_entry(i) for i in range(len(concepts))]
        reasons: dict[int, str] = {}
        for i, e in enumerate(entries):
            others = pool + [entries[j] for j in range(i)]  # earlier batchmates
            hit = _overlap_violation(e.keys, e.anchor_key, others)
            if hit is not None:
                v, j = hit
                reasons[i] = (
                    f"this concept shares {round(j * 100)}% of its non-staple "
                    f"ingredients with the {v.origin} recipe '{v.title}' "
                    f"(limit {round(J_HARD * 100)}%, or "
                    f"{round(J_SAME_ANCHOR * 100)}% with the same anchor)."
                )
        # Flavor-lead cap: one lead may head at most LEAD_BATCH_MAX concepts.
        lead_where: dict[str, list[int]] = {}
        for i, c in enumerate(concepts):
            for lead in _flavor_leads(c):
                lead_where.setdefault(lead, []).append(i)
        for lead, idxs in lead_where.items():
            for i in idxs[LEAD_BATCH_MAX:]:
                reasons.setdefault(
                    i,
                    f"the flavor lead {lead!r} already leads "
                    f"{LEAD_BATCH_MAX} other concepts in this batch "
                    f"(max {LEAD_BATCH_MAX} per batch).",
                )
        return reasons

    reasons = find_violations()
    if not reasons:
        return concepts
    idxs = sorted(reasons)
    fixed = await asyncio.gather(
        *(
            _regen_for_overlap(client, concepts[i], reasons[i], base_system, ctx_text)
            for i in idxs
        ),
        return_exceptions=True,
    )
    for i, res in zip(idxs, fixed):
        if isinstance(res, dict):
            concepts[i] = res
    logger.info("Ingredient overlap: regenerated %d concept(s): %s", len(idxs), idxs)

    # Post-retry survivors ship, but disclosed (P33 B5).
    entries = [concept_entry(i) for i in range(len(concepts))]
    for i in idxs:
        others = pool + [entries[j] for j in range(len(concepts)) if j != i]
        hit = _overlap_violation(entries[i].keys, entries[i].anchor_key, others)
        if hit is not None:
            _disclose_overlap(concepts[i], None, hit[0], hit[1])
            logger.info("Concept %r still overlaps %r (J=%.2f) after retry; "
                        "disclosed", concepts[i].get("title"), hit[0].title, hit[1])
    return concepts


_TIER_ORDER = ["easy", "medium", "hard"]
_ALLOWED_N = (3, 5)


def _clamp_n(n: int | None) -> int:
    return n if n in _ALLOWED_N else 5


def _clean_difficulties(difficulties: list[str] | None) -> list[str]:
    """Normalize to an ordered, de-duped subset of easy/medium/hard.
    Empty result means 'all three'."""
    if not difficulties:
        return []
    seen = {d.strip().lower() for d in difficulties}
    return [t for t in _TIER_ORDER if t in seen]


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


# Food/dish/protein words a counted direction can enumerate (Prompt 30). Used to
# tell an enumerated "slot contract" ("one chicken, one beef, …") from a vibe
# direction ("light + fast", "one pan only" — 'pan' is not a food).
_FOOD_WORDS = {
    "chicken", "beef", "pork", "fish", "salmon", "shrimp", "seafood", "cod",
    "tuna", "tilapia", "steak", "lamb", "turkey", "tofu", "bean", "beans",
    "egg", "eggs", "pasta", "noodle", "noodles", "rice", "pizza", "taco",
    "tacos", "soup", "salad", "curry", "stew", "roast", "sandwich", "burger",
    "burgers", "bowl", "stir", "stirfry", "veg", "vegetable", "vegetables",
    "vegetarian", "veggie", "veggies", "vegan", "meatball", "meatballs",
    "sausage", "duck", "scallop", "scallops", "crab", "lobster", "mussels",
    "chili", "quesadilla", "wrap", "wraps", "risotto", "casserole", "bake",
}
_COUNT_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight",
    "1", "2", "3", "4", "5", "6", "7", "8", "a", "an", "single",
}


def _direction_asks(direction: str | None) -> tuple[bool, int, list[str]]:
    """Parse a direction for an enumerated SLOT CONTRACT (Prompt 30).

    Returns (enumerated, ask_count, requested_terms). ``enumerated`` is True when
    the direction names specific dishes/proteins with counts or as a list —
    e.g. "one chicken, one beef, one pasta, one fish" or "one beef only". Vibe
    directions ("grill something", "one pan only") return (False, 0, [])."""
    d = (direction or "").strip().lower()
    if not d:
        return (False, 0, [])
    tokens = re.findall(r"[a-z0-9]+", d)
    tokenset = set(tokens)
    counted: list[str] = []   # food words preceded (≤2 tokens) by a count word
    foods: list[str] = []     # all distinct food words mentioned
    for i, tok in enumerate(tokens):
        if tok not in _FOOD_WORDS:
            continue
        if tok not in foods:
            foods.append(tok)
        window = tokens[max(0, i - 2):i]
        if any(w in _COUNT_WORDS for w in window) and tok not in counted:
            counted.append(tok)

    # Explicit counts on ≥2 dishes, or a single counted dish with "only" (a cap),
    # is a contract. Also a bare list of ≥2 foods separated by commas/"and".
    if len(counted) >= 2 or (len(counted) == 1 and "only" in tokenset):
        return (True, len(counted), counted)
    listy = ("," in d) or (" and " in d) or len(foods) >= 3
    if len(foods) >= 2 and listy:
        return (True, len(foods), foods)
    return (False, 0, [])


def _direction_block(direction: str | None, enumerated: bool = False) -> str:
    d = (direction or "").strip()
    if not d:
        return ""
    base = (
        f"\n\nThe user's DIRECTION for THIS batch: '{d}'. Every concept should "
        "honor it. This steer ranks ABOVE their cuisine preferences, but it never "
        "overrides the hard constraints (allergies, excluded ingredients, the "
        "protein floor, or pinned items) — satisfy every one of those first, then "
        "shape the batch to the direction."
    )
    if not enumerated:
        return base
    return base + (
        "\n\nThis direction ENUMERATES specific dishes/proteins/counts — treat it as "
        "a SLOT CONTRACT, binding across the whole batch:\n"
        "- Fill exactly the enumerated slots as specified. ONE concept may satisfy "
        "two compatible asks (e.g. beef + pasta in a single dish) ONLY if you say so "
        "explicitly in that concept's why_this_recipe.\n"
        "- Stated counts are CAPS: 'one beef' means AT MOST one beef-anchored concept "
        "in the entire batch, surplus slots included. Never exceed a stated count.\n"
        "- SURPLUS slots (batch size > asks) must extend with COMPLEMENTARY, non-"
        "conflicting concepts: a different take on a requested category that isn't "
        "capped, a vegetable-forward dish, or a market pick in an UNREQUESTED "
        "category. NEVER fall back to a capped anchor to fill a surplus slot.\n"
        "- If the batch is SMALLER than the number of asks, honor as many as possible "
        "and state which asks you dropped in the FIRST concept's why_this_recipe."
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
You are a demanding culinary reviewer. You are given a set of dinner CONCEPTS and a
diner profile. Score each concept 1-10 overall and flag which rubric items it fails.
Judge against this rubric:

1. TECHNIQUE PHYSICS — no wet/sugary marinades before a high-heat sear; ground
   spices don't survive long hard sears (bloom later); "crispy" needs dry surfaces;
   covered things don't crisp.
2. FLAVOR COHERENCE — a defensible culinary logic (fusion fine, randomness not).
   The test: would a competent home cook CHOOSE to make this dish? Ingredient
   substitutions that undermine the dish's identity (naan as taco shells, pancake
   "lasagna") FAIL. Rate coherence on its own 1-10: below 6 means include 2 in
   fail_rubrics — it is a hard fail.
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
8. INGREDIENT VARIETY — concepts must not share more than about half their
   non-staple ingredients with another concept in the set, and no seasoning blend
   (flavor_lead) may lead 3 or more concepts. (A deterministic checker enforces
   exact thresholds afterward; flag the obvious near-clones.)

Return ONLY valid JSON:
{"reviews":[{"index":0-based, "score":1-10, "verdict":"ship"|"revise",
"fail_rubrics":[ints of failed rubric numbers], "worst_issues":[short strings]}]}
Be strict: a concept that fails rubric 1, 2, 4, 5, or 6, or scores below 7 overall,
is "revise"."""


@dataclass
class _Ctx:
    pantry: list[PantryItem]
    chain_name: str | None
    store_name: str | None
    deal_by_ingredient: dict[int, DealCache]
    context_text: str
    pin_block: str = ""
    protein_floor: int = 0
    # Calorie band (Prompt 32 C7): a serving above this many calories
    # (55% of the daily target) triggers a portion-rebalance regeneration.
    calorie_cap: int = 0
    calorie_target: int = 0
    taste_block: str = ""
    history_block: str = ""
    variety_block: str = ""
    direction: str = ""
    pantry_by_iid: dict[int, PantryItem] = field(default_factory=dict)
    pantry_by_norm: dict[str, PantryItem] = field(default_factory=dict)
    all_deals: list[DealCache] = field(default_factory=list)
    # Other saved stores' current deals: [(chain_name, deals)] — the sparse-store
    # fallback pool for market anchors (Prompt 32 #4).
    other_store_deals: list[tuple[str | None, list[DealCache]]] = field(
        default_factory=list
    )
    # Candidates designated as this batch's market-pick anchors (Prompt 28 A,
    # widened + per-slot assigned in Prompt 32 B).
    market_candidates: list[MarketCandidate] = field(default_factory=list)
    # Ingredient-overlap variety math (Prompt 33): comparison targets (recent
    # batches + saved week) and the carve-out key set (pins, market anchors,
    # planned shared purchases).
    overlap_pool: list[_OverlapEntry] = field(default_factory=list)
    overlap_carveout: set[str] = field(default_factory=set)


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
    await nutrition.preload(db)

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

    stores = await _saved_stores(db, user.id)
    default = stores[0] if stores else None
    chain_name = default.chain_name if default else None
    store_name = default.store_name if default else None
    all_deals = (
        await _all_current_deals(db, default.chain_id, today, default.region_key)
        if default
        else []
    )
    # Other saved stores' deals: the sparse-store fallback anchor pool (P32 #4).
    other_store_deals: list[tuple[str | None, list[DealCache]]] = []
    for s in stores[1:]:
        deals_s = await _all_current_deals(db, s.chain_id, today, s.region_key)
        if deals_s:
            other_store_deals.append((s.chain_name, deals_s))
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
        calorie_cap=round(user.calorie_target * _CAL_BAND),
        calorie_target=user.calorie_target,
        taste_block=_taste_block(user.taste_notes),
        pantry_by_iid=pantry_by_iid,
        pantry_by_norm=pantry_by_norm,
        all_deals=list(all_deals),
        other_store_deals=other_store_deals,
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


async def _recent_market_anchors(db: AsyncSession, user_id: int) -> set[str]:
    """Anchor keys used as market picks in the last few batches, so a new batch
    can rotate to different deals (A5). Keys are ``anchor_key`` when present
    (Prompt 32) with a legacy fallback to the ingredient id."""
    cutoff = _now() - timedelta(hours=_RECENT_HOURS)
    rows = (
        (
            await db.execute(
                select(Recipe.market_anchor_json).where(
                    Recipe.user_id == user_id,
                    Recipe.is_market_pick.is_(True),
                    Recipe.generated_at >= cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    keys: set[str] = set()
    for a in rows:
        if not isinstance(a, dict):
            continue
        if isinstance(a.get("anchor_key"), str):
            keys.add(a["anchor_key"])
        elif isinstance(a.get("ingredient_id"), int):
            keys.add(f"i{a['ingredient_id']}")
    return keys


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
        "is_market_pick": bool(recipe.is_market_pick),
        "market_anchor": recipe.market_anchor_json,
        "quality_flags": recipe.quality_flags_json,
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
        "flavor_lead": c.get("flavor_lead"),
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


def _needs_revision(
    review: dict, is_market: bool = False, enforce_contract: bool = False
) -> bool:
    try:
        score = int(review.get("score"))
    except (TypeError, ValueError):
        score = 0
    fails = {
        int(x)
        for x in (review.get("fail_rubrics") or [])
        if str(x).strip().lstrip("-").isdigit()
    }
    # A market pick's anchor is an intentional purchase — a lone rubric-6
    # (pantry-realism) flag on it is expected, not a defect. Rubric 2 (flavor
    # coherence, the would-a-home-cook-make-this test) is a hard fail (P32 D8).
    hard_set = {1, 2, 4, 5} if is_market else {1, 2, 4, 5, 6}
    # Prompt 30: with an enumerated direction, a PROFILE-FIT (rubric 7) fail is a
    # slot-contract violation (a cap exceeded / undisclosed merge) — a hard fail.
    if enforce_contract:
        hard_set = hard_set | {7}
    hard = bool(fails & hard_set)
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
    market_idx: list[int] | None = None,
    contract_note: str = "",
) -> list[dict]:
    briefs = [_concept_brief(c) for c in concepts]
    market_note = ""
    if market_idx:
        market_note = (
            f"\nMARKET PICKS at indices {sorted(market_idx)}: each is anchored on a "
            "current store DEAL the user intentionally buys — do NOT fail these on "
            "rubric 6 (pantry realism) or rubric 2 for the anchor NOT being in the "
            "pantry. Judge the rest of the dish normally.\n"
        )
    user_msg = (
        f"PROTEIN FLOOR: {floor} g per serving.\n"
        f"DINER PROFILE:\n{profile_text}\n\n"
        f"THEIR PANTRY (with quantities — check rubric 6 against these numbers):\n"
        f"{pantry_lines or '- (unknown)'}\n"
        f"{market_note}"
        f"{contract_note}\n"
        f"CONCEPTS (0-indexed):\n{json.dumps(briefs, ensure_ascii=False)}\n\n"
        "Review each concept now."
    )
    data = await _call_json(
        client, model=settings.critic_model_id, max_tokens=1200,
        system=_cached_system(_CRITIC_SYSTEM), user_msg=user_msg,
        category="critic", stage="critic",
    )
    reviews = data.get("reviews") if isinstance(data, dict) else None
    return reviews if isinstance(reviews, list) else []


async def _regen_concept(
    client: AsyncAnthropic, concept: dict, review: dict, base_system: str, ctx_text: str,
    contract_hint: str = "",
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
        + contract_hint
    )
    prior = json.dumps(_concept_brief(concept), ensure_ascii=False)
    # Correction rides in the user message so the cached L1+L2 system prefix
    # (shared with the main concept call) still yields cache reads.
    msg = f"{ctx_text}{correction}\n\nConcept to fix:\n{prior}\n\nReturn the fixed concept now."
    data = await _call_json(
        client, model=settings.recipe_model, max_tokens=_CONCEPT_MAX_TOKENS,
        system=base_system, user_msg=msg, category="generation", stage="concept_fix",
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
    market_idx: list[int] | None = None,
    contract: tuple[bool, int, list[str]] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Score concepts, regenerate weak ones once, return (concepts, critic_meta)."""
    enumerated, ask_count, terms = contract or (False, 0, [])
    contract_note = ""
    contract_hint = ""
    if enumerated:
        contract_note = (
            "\nSLOT CONTRACT (the direction ENUMERATES dishes/proteins/counts): under "
            "rubric 7 (PROFILE FIT) verify the contract across the WHOLE batch — stated "
            f"counts are CAPS ({', '.join(terms)} — at most one concept each), a single "
            "concept may cover two asks ONLY if its why_this_recipe says so, and surplus "
            "slots must be COMPLEMENTARY (not a repeat of a capped anchor). Any concept "
            "that breaks a cap or is an undisclosed duplicate of a capped anchor FAILS "
            "rubric 7; name the exceeded cap in worst_issues.\n"
        )
        contract_hint = (
            "\n\nSLOT CONTRACT: this batch's direction caps these anchors: "
            f"{', '.join(terms)} (at most one concept each). Your replacement must NOT "
            "add another concept anchored on an already-satisfied capped item; make it a "
            "complementary, non-conflicting dish (a different uncapped category, a "
            "vegetable-forward dish, or an on-sale item in an unrequested category)."
        )
    reviews = await _run_critic(
        client, concepts, floor, profile_text, pantry_lines, market_idx, contract_note
    )
    market_set = set(market_idx or [])
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
        if by_index.get(i)
        and _needs_revision(by_index[i], is_market=i in market_set, enforce_contract=enumerated)
    ]
    if regen_idx:
        fixed = await asyncio.gather(
            *(
                _regen_concept(
                    client, concepts[i], by_index[i], base_system, ctx_text, contract_hint
                )
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
    msg = f"{ctx_text}{correction}\n\nConcept that repeats:\n{prior}\n\nReturn the fresh concept now."
    data = await _call_json(
        client, model=settings.recipe_model, max_tokens=_CONCEPT_MAX_TOKENS,
        system=base_system, user_msg=msg, category="generation", stage="concept_fix",
    )
    recs = data.get("recipes") if isinstance(data, dict) else None
    if isinstance(recs, list) and recs and isinstance(recs[0], dict):
        return recs[0]
    return concept


def _concept_covers(concept: dict, term: str) -> bool:
    """Does a concept anchor on / feature a requested term (e.g. 'beef')?"""
    kis = " ".join(
        str(k.get("generic_name") or "")
        for k in (concept.get("key_ingredients") or [])
        if isinstance(k, dict)
    )
    blob = (
        f"{concept.get('anchor_ingredient') or ''} {concept.get('title') or ''} {kis}"
    ).lower()
    return term in blob


async def _regen_for_contract(
    client: AsyncAnthropic, concept: dict, terms: list[str], base_system: str, ctx_text: str
) -> dict:
    tier = (concept.get("difficulty") or "").strip() or "the same"
    correction = (
        "\n\nSLOT CONTRACT VIOLATION: this concept is a DUPLICATE of an already-filled "
        f"capped ask. The direction caps each of {terms} at ONE concept in the batch, "
        "and those are already used. Return ONE replacement concept as JSON "
        '{"recipes":[{...same shape...}]} that is COMPLEMENTARY and does NOT anchor on '
        f"or feature ANY of: {', '.join(terms)}. Prefer a vegetable-forward dish or an "
        f"on-sale item in an unrequested category. Keep the SAME difficulty tier "
        f"('{tier}'). Use ONLY pantry or on-sale items."
    )
    prior = json.dumps(_concept_brief(concept), ensure_ascii=False)
    msg = f"{ctx_text}{correction}\n\nConcept to replace:\n{prior}\n\nReturn the replacement now."
    data = await _call_json(
        client, model=settings.recipe_model, max_tokens=_CONCEPT_MAX_TOKENS,
        system=base_system, user_msg=msg, category="generation", stage="concept_fix",
    )
    recs = data.get("recipes") if isinstance(data, dict) else None
    if isinstance(recs, list) and recs and isinstance(recs[0], dict):
        return recs[0]
    return concept


async def _enforce_slot_contract(
    client: AsyncAnthropic,
    concepts: list[dict],
    terms: list[str],
    ask_count: int,
    base_system: str,
    ctx_text: str,
) -> list[dict]:
    """Deterministic backstop for a counted direction (Prompt 30): the Haiku critic
    is unreliable at counting, so enforce each stated count as a hard CAP of one —
    keep the first concept covering a capped term, regenerate any others as
    complementary non-conflicting dishes. Also disclose dropped asks when the batch
    is smaller than the number of asks."""
    if not terms:
        return concepts
    excess: list[int] = []
    for term in terms:
        covering = [i for i, c in enumerate(concepts) if _concept_covers(c, term)]
        excess.extend(covering[1:])  # cap = 1: everything past the first is excess
    excess = sorted(set(excess))
    if excess:
        fixed = await asyncio.gather(
            *(_regen_for_contract(client, concepts[i], terms, base_system, ctx_text)
              for i in excess),
            return_exceptions=True,
        )
        for i, res in zip(excess, fixed):
            if isinstance(res, dict):
                concepts[i] = res
        logger.info("Slot contract: regenerated %d over-cap concept(s): %s",
                    len(excess), excess)

    # Disclose dropped asks when the batch can't hold every ask (N < asks).
    if concepts and len(concepts) < ask_count:
        covered = {t for t in terms if any(_concept_covers(c, t) for c in concepts)}
        dropped = [t for t in terms if t not in covered]
        if dropped:
            w = (concepts[0].get("why_this_recipe") or "").strip()
            if not any(t in w.lower() for t in dropped):
                note = (
                    f"(Only {len(concepts)} slots for {ask_count} asks — dropped "
                    f"{', '.join(dropped)} this batch.)"
                )
                concepts[0]["why_this_recipe"] = f"{w} {note}".strip()
    return concepts


def _shared_axes_with_exemption(
    s: dict, o: dict, is_recent: bool, exempt_anchor_keys: set[str]
) -> int:
    """Axes shared between two signatures, honoring the owned-perishable
    RECENCY EXEMPTION (P34 A3): vs RECENTLY SHOWN signatures, a shared anchor
    that is an owned perishable doesn't count — beef can anchor daily until
    eaten. Format/cuisine axes (and batchmate comparisons) still count fully,
    so it's beef again but never the same beef dish again."""
    shared = _axes_shared(s, o)
    if not is_recent or not exempt_anchor_keys or shared == 0:
        return shared
    a, b = _norm_sig(s), _norm_sig(o)
    anchors_match = bool(
        a["anchor_ingredient"] and a["anchor_ingredient"] == b["anchor_ingredient"]
    )
    if anchors_match:
        key = _ing_key(s.get("anchor_ingredient") or "")
        if key is not None and key in exempt_anchor_keys:
            return shared - 1
    return shared


async def _enforce_variety(
    client: AsyncAnthropic,
    concepts: list[dict],
    recent_sigs: list[dict],
    base_system: str,
    ctx_text: str,
    market_pool_exhausted: bool = True,
    exempt_anchor_keys: set[str] | None = None,
) -> list[dict]:
    """Regenerate (once) any concept whose signature repeats a recent one — or an
    earlier concept in this same batch — on 2+ of the 3 axes.

    ``market_pool_exhausted``: whether the WIDENED market-anchor pool has no
    unused candidates left. The "pantry's too small" relaxation note may only
    fire when that pool was genuinely exhausted — never as cover for selector
    starvation (Prompt 32 3d).

    ``exempt_anchor_keys``: owned-perishable anchor keys whose RECENT anchor
    axis is exempt (P34 A3)."""
    exempt = exempt_anchor_keys or set()

    def collisions(sigs: list[dict]) -> list[int]:
        out: list[int] = []
        for i, s in enumerate(sigs):
            recent_hit = any(
                _shared_axes_with_exemption(s, o, True, exempt) >= 2
                for o in recent_sigs
            )
            batch_hit = any(
                _axes_shared(s, sigs[j]) >= 2
                for j in range(len(sigs)) if j != i
            )
            if recent_hit or batch_hit:
                out.append(i)
        return out

    sigs = [_concept_sig(c) for c in concepts]
    collide = collisions(sigs)
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
    still = set(collisions(all_sigs))
    for i in collide:
        s = _norm_sig(all_sigs[i])
        if i in still:
            anchor = s.get("anchor_ingredient") or "a pantry staple"
            w = (concepts[i].get("why_this_recipe") or "").strip()
            if not any(m in w.lower() for m in _RELAX_MARKERS):
                if market_pool_exhausted:
                    note = (
                        f"Another take on {anchor} — the pantry's too small for a "
                        "fully fresh anchor tonight, so this reshapes it instead."
                    )
                else:
                    # Unused deal anchors still exist — the small-pantry claim
                    # would be false. Disclose the repeat without the excuse.
                    note = f"Another take on {anchor}, reshaped as a different dish."
                concepts[i]["why_this_recipe"] = f"{w} {note}".strip()
            logger.info("Concept %r kept a repeated anchor after variety retry; "
                        "relaxation disclosed (market pool exhausted: %s)",
                        concepts[i].get("title"), market_pool_exhausted)
    return concepts


# --------------------------------------------------------------------------- #
# Within-batch market anchor diversity (Prompt 32 B3)
# --------------------------------------------------------------------------- #
async def _regen_for_market_anchor(
    client: AsyncAnthropic,
    concept: dict,
    cand: MarketCandidate,
    base_system: list[dict] | str,
    ctx_text: str,
) -> dict:
    d = cand.deal
    unit = f"/{d.price_unit}" if d.price_unit else ""
    at = f" at {cand.store}" if cand.store else ""
    tier = (concept.get("difficulty") or "").strip() or "the same"
    correction = (
        "\n\nMARKET ANCHOR COLLISION: two market picks in tonight's batch anchored "
        "on the SAME deal. Market picks in one batch must use DIFFERENT anchors. "
        "Return ONE replacement MARKET PICK concept as JSON "
        '{"recipes":[{...same shape...}]} built around this assigned deal instead: '
        f"{d.product_name}: ${d.sale_price}{unit}{at}. Set anchor_ingredient to it, "
        'set "market_pick": true, and name the deal, its price'
        + (", and the store" if cand.cross_store else "")
        + f" in why_this_recipe. Keep the SAME difficulty tier ('{tier}'). "
        "Surround the anchor with items actually in their kitchen."
    )
    prior = json.dumps(_concept_brief(concept), ensure_ascii=False)
    msg = f"{ctx_text}{correction}\n\nConcept to replace:\n{prior}\n\nReturn the replacement now."
    data = await _call_json(
        client, model=settings.recipe_model, max_tokens=_CONCEPT_MAX_TOKENS,
        system=base_system, user_msg=msg, category="generation", stage="concept_fix",
    )
    recs = data.get("recipes") if isinstance(data, dict) else None
    if isinstance(recs, list) and recs and isinstance(recs[0], dict):
        return recs[0]
    return concept


async def _enforce_market_diversity(
    client: AsyncAnthropic,
    concepts: list[dict],
    cands: list[MarketCandidate],
    pantry_iids: set[int],
    base_system: list[dict] | str,
    ctx_text: str,
) -> list[dict]:
    """Market-pick slots in the SAME batch must use DIFFERENT anchors whenever
    ≥2 viable candidates exist (Prompt 32 B3 — three cauliflower slots proved
    the cross-batch rotation alone doesn't cover this). Deterministic backstop:
    duplicates are regenerated once, each re-pointed at a specific UNUSED
    assigned anchor; a repeat may stand only when the pool itself repeats
    (marked ``repeat=True`` after full exhaustion) and then must disclose."""
    if not cands:
        return concepts
    matched: dict[int, MarketCandidate] = {}
    for i, c in enumerate(concepts):
        cand = _match_candidate(c.get("anchor_ingredient") or "", cands, pantry_iids)
        if cand is not None:
            matched[i] = cand

    used_keys: set[str] = set()
    dup_idx: list[int] = []
    for i in sorted(matched):
        k = matched[i].key
        if k in used_keys:
            dup_idx.append(i)
        else:
            used_keys.add(k)
    # Allow exactly as many repeats as the selector itself marked (exhaustion).
    allowed_repeats = sum(1 for c in cands if c.repeat)
    dup_idx = dup_idx[allowed_repeats:] if allowed_repeats else dup_idx
    if not dup_idx:
        return concepts

    unused = [c for c in cands if not c.repeat and c.key not in used_keys]
    jobs: list[tuple[int, MarketCandidate]] = []
    for i in dup_idx:
        if not unused:
            break
        jobs.append((i, unused.pop(0)))
    still_dup: list[int] = []
    if jobs:
        fixed = await asyncio.gather(
            *(
                _regen_for_market_anchor(client, concepts[i], cand, base_system, ctx_text)
                for i, cand in jobs
            ),
            return_exceptions=True,
        )
        for (i, cand), res in zip(jobs, fixed):
            if isinstance(res, dict):
                concepts[i] = res
            # P34 C6 fix: VERIFY the regen actually re-anchored where it was
            # told. The old code trusted the result and marked the assigned
            # key used — a failed or stubborn regeneration shipped the
            # duplicate silently. Re-match what actually came back.
            got = _match_candidate(
                concepts[i].get("anchor_ingredient") or "", cands, pantry_iids
            )
            if got is not None and got.key == cand.key:
                used_keys.add(cand.key)
            else:
                still_dup.append(i)
                logger.warning(
                    "Market diversity: regen for concept %d did not adopt the "
                    "assigned anchor %r (got %r) — demoting at persist.",
                    i, cand.deal.product_name,
                    concepts[i].get("anchor_ingredient"),
                )
        logger.info(
            "Market diversity: re-anchored %d duplicate market pick(s): %s",
            len(jobs), [i for i, _ in jobs],
        )
    # Any duplicate left had NO unused candidate — the pool is genuinely
    # exhausted; force the existing repeat disclosure. (Verified-failed regens
    # are NOT disclosed as intentional repeats — the persist-time gate demotes
    # them from market picks instead.)
    for i in dup_idx[len(jobs):]:
        anchor = concepts[i].get("anchor_ingredient") or "tonight's deal"
        w = (concepts[i].get("why_this_recipe") or "").strip()
        if "repeat" not in w.lower() and "already" not in w.lower():
            note = (
                f"Intentionally repeats the {anchor} deal — every distinct sale "
                "anchor across your saved stores is already in use tonight."
            )
            concepts[i]["why_this_recipe"] = f"{w} {note}".strip()
    return concepts


# --------------------------------------------------------------------------- #
# Title diversity (Prompt 32 D9)
# --------------------------------------------------------------------------- #
_TITLE_STOPWORDS = {
    "a", "an", "the", "and", "with", "of", "in", "on", "for", "over", "under",
    "au", "aux", "al", "alla", "de", "la", "le", "el", "du", "des", "et", "di",
    "e", "da", "style", "night", "dinner", "weeknight",
}


def _title_words(title: str) -> set[str]:
    return {
        t
        for t in ingredient_matcher._tokens(title or "")
        if len(t) > 2 and t not in _TITLE_STOPWORDS and not t.isdigit()
    }


def _overused_title_words(titles: list[str], limit: int = 2) -> dict[str, list[int]]:
    """Words appearing in more than ``limit`` titles -> the title indices."""
    where: dict[str, list[int]] = {}
    for i, t in enumerate(titles):
        for w in _title_words(t):
            where.setdefault(w, []).append(i)
    return {w: idxs for w, idxs in where.items() if len(idxs) > limit}


async def _enforce_title_diversity(
    client: AsyncAnthropic, concepts: list[dict]
) -> list[dict]:
    """No signature word in more than 2 titles per batch (Prompt 32 D9 — the
    'Charred everything' fixture). Deterministic detection; offenders beyond
    the first two keep their dish but get retitled in one cheap call."""
    titles = [str(c.get("title") or "") for c in concepts]
    over = _overused_title_words(titles)
    if not over:
        return concepts
    offenders: list[int] = []
    for w, idxs in over.items():
        offenders.extend(idxs[2:])  # first two titles keep the word
    offenders = sorted(set(offenders))
    banned = sorted(over.keys())
    briefs = [
        {
            "index": i,
            "title": titles[i],
            "description": concepts[i].get("description"),
            "anchor_ingredient": concepts[i].get("anchor_ingredient"),
            "cuisine": concepts[i].get("cuisine"),
        }
        for i in offenders
    ]
    msg = (
        "These dinner concepts share overused title words with others in the same "
        f"batch. Rewrite ONLY their titles. Banned words (already in 2 other titles): "
        f"{', '.join(banned)}. Keep each title honest to its dish (anchor, format, "
        "cuisine), appetizing, ≤ 8 words, and free of every banned word. Taste notes "
        "inform technique, not naming.\n\n"
        f"{json.dumps(briefs, ensure_ascii=False)}\n\n"
        'Return ONLY valid JSON: {"titles": [{"index": <int>, "title": "<new title>"}]}'
    )
    data = await _call_json(
        client, model=settings.recipe_model, max_tokens=400,
        system="You retitle dinner concepts. Return only the requested JSON.",
        user_msg=msg, category="generation", stage="concept_fix",
    )
    items = data.get("titles") if isinstance(data, dict) else None
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                i = int(it.get("index"))
            except (TypeError, ValueError):
                continue
            new_title = str(it.get("title") or "").strip()
            if i in offenders and new_title and not (
                _title_words(new_title) & set(banned)
            ):
                concepts[i]["title"] = new_title
    still = _overused_title_words([str(c.get("title") or "") for c in concepts])
    if still:
        logger.warning("Title diversity: word(s) still overused after retitle: %s",
                       sorted(still))
    else:
        logger.info("Title diversity: retitled %d concept(s); banned %s",
                    len(offenders), banned)
    return concepts


# --------------------------------------------------------------------------- #
# Stage 1: concepts
# --------------------------------------------------------------------------- #
async def generate_concepts(
    db: AsyncSession,
    user: User,
    pinned_ids: list[int] | None = None,
    direction: str | None = None,
    difficulties: list[str] | None = None,
    *,
    category: str = "generation",
) -> list[Recipe]:
    """One fast Claude call → N persisted concept recipes (status='concept').

    ``difficulties`` is a subset of {easy, medium, hard}; empty/None draws from
    all three. N is split evenly across the selected tiers, remainder to the
    easiest selected tier.
    """
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
    tiers = _clean_difficulties(difficulties)

    # Counted-direction SLOT CONTRACT (Prompt 30): an enumerated direction caps
    # categories, so surplus market picks are limited to the leftover slots and
    # must avoid the requested (capped) categories.
    enumerated, ask_count, requested_terms = _direction_asks(ctx.direction)

    # Market picks (A1/A2): census the pantry's distinct viable anchors, convert
    # the surplus slots to deal-anchored picks, and select this batch's anchors
    # (rotating away from recent ones). Deals can LEAD, not just fill gaps.
    pantry_iids = set(ctx.pantry_by_iid.keys())
    census = _anchor_census(ctx.pantry, user.household_size or 2)
    if enumerated:
        # Only genuine SURPLUS slots (batch beyond the asks) become market picks,
        # and they must land in UNREQUESTED categories.
        market_slots = max(n - ask_count, 0)
    else:
        market_slots = _market_slot_count(n, len(census))

    # Owned-perishable guarantee (P34 A2): while owned perishables exist with
    # sufficient quantity, ≥1 slot is reserved for one — it outranks the
    # market-pick reservation when they conflict (incl. at N=3).
    perishables = _owned_perishables(
        ctx.pantry, user.household_size or 2, date.today(),
        excluded_terms=[*(user.allergies or []), *(user.excluded_ingredients or [])],
    )
    perishable = perishables[0] if perishables else None
    perishable_use_soon = bool(perishable) and _use_soon(perishable, date.today())
    if perishable is not None:
        market_slots = min(market_slots, n - 1)
    recent_market = await _recent_market_anchors(db, user.id)
    pantry_norms = set(ctx.pantry_by_norm.keys())
    exclude_terms = requested_terms if enumerated else None
    primary_pool = _market_candidates(
        ctx.all_deals, pantry_iids, pantry_norms, store=ctx.chain_name,
        exclude_terms=exclude_terms,
    )
    other_pools = [
        _market_candidates(
            deals, pantry_iids, pantry_norms, store=name, cross_store=True,
            exclude_terms=exclude_terms,
        )
        for name, deals in ctx.other_store_deals
    ]
    ctx.market_candidates = _select_market_anchors(
        primary_pool, other_pools, market_slots, recent_market
    )
    # Is the WIDENED pool (across every saved store) genuinely exhausted after
    # this selection? Gates the "pantry's too small" relaxation note (P32 3d).
    chosen_keys = {c.key for c in ctx.market_candidates}
    pool_exhausted = not any(
        c.key not in chosen_keys
        for pool in [primary_pool, *other_pools]
        for c in pool
    )
    logger.info(
        "Market picks: n=%d pantry_anchors=%d slots=%d pool=%d(+%d other-store) "
        "selected=%s pool_exhausted=%s",
        n, len(census), market_slots, len(primary_pool),
        sum(len(p) for p in other_pools),
        [
            f"{c.deal.product_name} @ ${c.deal.sale_price}"
            + (f" [{c.store}]" if c.cross_store else "")
            + (" [repeat]" if c.repeat else "")
            for c in ctx.market_candidates
        ],
        pool_exhausted,
    )

    # Ingredient-overlap variety math (P33): carve out the good overlap (pins,
    # this batch's market anchors, planned shared purchases for the saved
    # week), then build the comparison pool (recent batches + saved week).
    saved_week = (
        (
            await db.execute(
                select(Recipe)
                .join(WeekRecipe, WeekRecipe.recipe_id == Recipe.id)
                .where(
                    WeekRecipe.user_id == user.id,
                    WeekRecipe.week_start == week_start_for(date.today()),
                )
            )
        )
        .scalars()
        .all()
    )
    ctx.overlap_carveout = _overlap_carveout(
        pin_dicts, ctx.market_candidates, _saved_week_purchase_keys(saved_week)
    )
    ctx.overlap_pool = await _overlap_pool(db, user.id, ctx.overlap_carveout)

    # Cache layers: L1 = static rules (_CONCEPT_SYSTEM), L2 = slow-changing
    # context (profile + protein floor + taste + rating history + pantry/deals).
    # Per-press variables (tier plan, recent titles, variety, direction, pins,
    # market picks) live in the user message so the cached prefix survives.
    concept_l2 = (
        _concept_profile(user)
        + _protein_block(ctx.protein_floor)
        + ctx.taste_block
        + ctx.history_block
        + "\n\n"
        + ctx.context_text
    )
    system_blocks = _cached_system(_CONCEPT_SYSTEM, concept_l2)
    market_block = _market_block(ctx.market_candidates, ctx.chain_name)
    perishable_block = (
        _perishable_block(perishable, perishable_use_soon) if perishable else ""
    )
    user_msg = (
        f"THIS BATCH: propose exactly {n} concepts with difficulty mix: "
        f"{_tier_plan_text(n, tiers)}.\n"
        f"Avoid repeating these recent titles: {_fmt_list(list(recent_titles))}."
        + ctx.variety_block
        + perishable_block
        + market_block
        + _direction_block(ctx.direction, enumerated)
        + ctx.pin_block
        + f"\n\nPropose tonight's {n} dinner concepts now."
    )

    # Prompt-context audit (Prompt 32 A2): one INFO line proves every context
    # block survived the post-27 cache restructure; LOG_PROMPTS=1 dumps the
    # fully-assembled prompt for a live generation.
    logger.info(
        "Stage 1 prompt assembled [model=%s]: L1=%d chars, L2=%d chars, "
        "user=%d chars; blocks: taste_notes=%s loved_passed=%s recently_shown=%s "
        "direction=%s pins=%s market_assignments=%s perishable_slot=%s",
        settings.recipe_model, len(_CONCEPT_SYSTEM), len(concept_l2), len(user_msg),
        bool(ctx.taste_block), bool(ctx.history_block), bool(ctx.variety_block),
        bool(ctx.direction), bool(ctx.pin_block), bool(market_block),
        f"{perishable.name}{' (use_soon)' if perishable_use_soon else ''}"
        if perishable else "none",
    )
    if settings.log_prompts:
        logger.info(
            "STAGE 1 FULL PROMPT\n=== L1 (static rules) ===\n%s\n"
            "=== L2 (profile/taste/history/pantry/deals) ===\n%s\n"
            "=== USER (per-press) ===\n%s",
            _CONCEPT_SYSTEM, concept_l2, user_msg,
        )

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    with ai_metering.metering(category, user_id=user.id) as events:
        data = await _call_json(
            client, model=settings.recipe_model,
            max_tokens=max(_CONCEPT_MAX_TOKENS, 560 * n),
            system=system_blocks, user_msg=user_msg, category=category,
            stage="concepts",
        )
        raw = [r for r in (data.get("recipes", []) if isinstance(data, dict) else [])
               if isinstance(r, dict)][:n]

        # Stage 1.5 critic: score, then regenerate weak concepts once. Market
        # picks are exempt from pantry-realism penalties on their anchor.
        critics: list[dict] = [{} for _ in raw]
        if raw:
            market_idx = [
                i for i, c in enumerate(raw)
                if _match_candidate(
                    c.get("anchor_ingredient") or "", ctx.market_candidates, pantry_iids
                )
                is not None
            ]
            raw, critics = await _critique_and_fix(
                client, raw, ctx.protein_floor, _profile_text(user, ctx),
                system_blocks, user_msg, _pantry_qty_lines(ctx.pantry),
                market_idx=market_idx,
                contract=(enumerated, ask_count, requested_terms),
            )
            # Variety guard: never re-serve a recent signature under a new name,
            # and keep the batch mutually distinct (each concept differs from
            # EVERY other on 2+ axes). Runs even with no history. Owned
            # perishables are exempt on the RECENT anchor axis (P34 A3).
            exempt_keys = {
                k for k in (_ing_key(it.name or "") for it in perishables)
                if k is not None
            }
            raw = await _enforce_variety(
                client, raw, recent_sigs, system_blocks, user_msg,
                market_pool_exhausted=pool_exhausted,
                exempt_anchor_keys=exempt_keys,
            )
            # Slot-contract CAP backstop (Prompt 30): deterministic, has the final
            # say — the critic is unreliable at counting caps across the batch.
            if enumerated:
                raw = await _enforce_slot_contract(
                    client, raw, requested_terms, ask_count, system_blocks, user_msg
                )
            # Owned-perishable guarantee (P34 A2): ≥1 non-market concept
            # anchored on the top perishable; use_soon leads with urgency.
            if perishable is not None:
                raw = await _enforce_perishable_slot(
                    client, raw, perishable, ctx.market_candidates, pantry_iids,
                    system_blocks, user_msg, perishable_use_soon,
                )
            # Ingredient-overlap variety math (P33 B): deterministic Jaccard +
            # flavor-lead check vs batchmates, recent batches, and the saved
            # week; one named regeneration, survivors disclosed.
            raw = await _enforce_ingredient_overlap(
                client, raw, ctx.overlap_pool, ctx.overlap_carveout,
                system_blocks, user_msg,
            )
            # Within-batch market anchor diversity (P32 B3, hardened P34 C6):
            # no two market picks on the same deal — and regen results are
            # verified, not trusted.
            raw = await _enforce_market_diversity(
                client, raw, ctx.market_candidates, pantry_iids,
                system_blocks, user_msg,
            )
            # Title diversity (P32 D9): no signature word in 3+ titles.
            raw = await _enforce_title_diversity(client, raw)

    # Feed order (P34 B5): within each tier, pantry-anchored ($0/cheapest)
    # first, market picks after — persisted in this order so /latest (ordered
    # by id) serves the all-pantry dish as its tier's headline.
    pairs = _feed_sort(list(zip(raw, critics)), ctx.market_candidates, pantry_iids)

    # Persist-time distinctness gate (P34 C6): the final say on market anchors.
    # Selection dedups products, enforcement verifies regens — and this gate
    # guarantees no two persisted market picks share a deal OR product even if
    # a stubborn regeneration slipped through. Exhaustion repeats stay allowed.
    used_anchor_cands: list[MarketCandidate] = []
    allowed_repeats = sum(1 for c in ctx.market_candidates if c.repeat)

    persisted: list[Recipe] = []
    for r, critic in pairs:
        key_ings = [
            _reconcile_key(k, ctx.deal_by_ingredient, ctx.chain_name)
            for k in (r.get("key_ingredients") or [])
            if isinstance(k, dict)
        ]
        cuisine = (r.get("cuisine") or None)
        # Market pick when the concept's anchor matches a designated deal the
        # user doesn't own (source of truth: OUR deal cache, not the model flag).
        anchor_name = r.get("anchor_ingredient") or ""
        cand = _match_candidate(anchor_name, ctx.market_candidates, pantry_iids)
        if cand is not None:
            duplicate = any(
                cand.key == u.key or _same_product(cand, u)
                for u in used_anchor_cands
            )
            if duplicate:
                if allowed_repeats > 0:
                    allowed_repeats -= 1  # disclosed exhaustion repeat (P32)
                else:
                    logger.warning(
                        "Persist gate: demoting %r — its anchor %r duplicates "
                        "another market pick in this batch.",
                        r.get("title"), cand.deal.product_name,
                    )
                    cand = None
            if cand is not None:
                used_anchor_cands.append(cand)
        market_anchor = None
        if cand is not None:
            deal = cand.deal
            market_anchor = {
                "name": anchor_name or cand.clean_name or deal.product_name,
                "ingredient_id": cand.iid,
                "anchor_key": cand.key,
                "sale_price": str(deal.sale_price),
                "price_unit": deal.price_unit,
                "savings_pct": (
                    float(deal.savings_pct) if deal.savings_pct is not None else None
                ),
                "store": cand.store or ctx.chain_name,
                "cross_store": cand.cross_store,
            }
        # Provisional honesty flags on the CONCEPT's claimed macros (P32 C6/C7):
        # never render a sub-floor or heavy number unannotated, even before the
        # detail stage recomputes with USDA figures (which overwrites these).
        flags = _quality_flags(
            _protein_of(r), _calories_of(r.get("nutrition_per_serving")),
            ctx.protein_floor, ctx.calorie_cap, ctx.calorie_target,
        )
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
            difficulties=tiers or None,
            critic_json=critic or None,
            signature_json={
                "anchor_ingredient": r.get("anchor_ingredient"),
                "dish_format": r.get("dish_format"),
                "cuisine": cuisine,
                # Dominant seasonings/blends (P33 A2) — persisted per recipe
                # so future batches can compare flavor leads too.
                "flavor_lead": _flavor_leads(r) or None,
            },
            is_market_pick=market_anchor is not None,
            market_anchor_json=market_anchor,
            quality_flags_json=flags,
            ai_model=settings.recipe_model,
        )
        db.add(recipe)
        persisted.append(recipe)

    await db.flush()
    # Stamp the batch timestamp onto this press's cost events, then persist them.
    batch_at = persisted[0].generated_at if persisted else None
    if batch_at:
        for e in events:
            e["batch_at"] = batch_at
    await ai_metering.persist_events(db, events)
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
    # Only the per-recipe concept goes here (L3). The shared pantry/deals context
    # lives in the cached system prefix so the N detail calls reuse it.
    return (
        f"CONCEPT to write in full:\n"
        f"Title: {recipe.title}\n"
        f"Difficulty: {recipe.difficulty}\n"
        f"Servings: {recipe.servings}\n"
        f"Description: {recipe.description}\n"
        f"Key ingredients: {key_lines}\n\n"
        f"Write the full recipe now."
    )


def _cached_system(l1: str, l2: str = "") -> list[dict]:
    """Layer a system prompt for prompt caching: L1 (static rules) and an
    optional L2 (slow-changing context), each ending in a cache_control
    breakpoint. Calls that share these exact blocks reuse the cached prefix
    (one write, then ~90%-off reads)."""
    blocks: list[dict] = [
        {"type": "text", "text": l1, "cache_control": {"type": "ephemeral"}}
    ]
    if l2:
        blocks.append(
            {"type": "text", "text": l2, "cache_control": {"type": "ephemeral"}}
        )
    return blocks


async def _call_json(
    client: AsyncAnthropic,
    *,
    model: str,
    max_tokens: int,
    system: str | list[dict],
    user_msg: str,
    category: str | None = None,
    stage: str | None = None,
) -> dict:
    """Claude call returning parsed JSON, retrying once on a parse failure.

    ``system`` may be a plain string or a list of content blocks (from
    ``_cached_system``) for prompt caching. Every call's token usage + cost is
    recorded into the active metering scope, tagged ``category`` and pipeline
    ``stage`` (concepts / critic / concept_fix / details / detail_fix) so the
    ledger can attribute the model that served each stage (Prompt 32 A1).
    """
    for _ in range(2):
        message = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        ai_metering.record_usage(model, message.usage, category=category, stage=stage)
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


def _calories_of(nut: dict | None) -> float | None:
    if not isinstance(nut, dict):
        return None
    try:
        return float(nut.get("calories"))
    except (TypeError, ValueError):
        return None


def _quality_flags(
    protein: float | None,
    calories: float | None,
    floor: int,
    cap: int,
    daily_target: int,
) -> dict | None:
    """Honesty flags (Prompt 32 C6/C7). A recipe below the protein floor or
    above the calorie band may ship ONLY carrying these — the frontend renders
    them as amber chips on card and detail. None = clean."""
    flags: dict = {}
    if floor > 0 and protein is not None and protein < floor:
        flags["protein_below_floor"] = {
            "protein_g": round(protein), "floor_g": floor,
        }
    if cap > 0 and calories is not None and calories > cap:
        flags["heavy"] = {
            "calories": round(calories), "cap": cap, "daily_target": daily_target,
        }
    return flags or None


def _fortify_correction(protein: float, floor: int, src: str) -> str:
    """Fortification with teeth (P32 C5): name protein-dense options WITH MASS
    and permit anchor replacement when the anchor can't carry the floor."""
    return (
        f"\n\nCORRECTION — PROTEIN FLOOR: this recipe delivers only {protein:.0f} g "
        f"protein per serving ({src}), below the required {floor} g floor. Fix it "
        "with MASS, not adjectives:\n"
        "- Add or substantially increase a protein-dense ingredient with a real "
        "per-serving quantity: 6-8 oz chicken breast or thighs, 6 oz fish or "
        "shrimp, 1 cup Greek yogurt (~23 g), 1 cup cottage cheese (~25 g), 7 oz "
        "firm tofu (~20 g), or a legume + grain pairing (1 cup cooked lentils "
        "with rice, ~20 g). Pantry proteins first, then on-sale proteins.\n"
        "- ANCHOR REPLACEMENT IS PERMITTED: if the dish's anchor is protein-"
        "incapable (cauliflower cannot reach the floor by seasoning harder), "
        "replace it or pair it with a protein co-anchor, and include a "
        '"revised_title" field in the JSON reflecting the honest new dish.\n'
        "- Do NOT merely restate higher numbers; the ingredient amounts must "
        "actually change."
    )


def _rebalance_correction(calories: float, cap: int, target: int) -> str:
    """Calorie band (P32 C7): one portion-rebalance regeneration for a serving
    above 55% of the daily calorie target."""
    return (
        f"\n\nCORRECTION — CALORIE BAND: a serving computes to {calories:.0f} "
        f"calories, more than {int(_CAL_BAND * 100)}% of the diner's {target}-"
        f"calorie day (limit {cap}). Rebalance the PORTION: raise the number of "
        "servings the same totals yield, or trim the calorie-dense components "
        "(oil, cheese, fried elements, oversized starch) — while keeping protein "
        "per serving at or above the floor. Do NOT just relabel the numbers."
    )


def _model_nutrition(data: dict) -> dict | None:
    """The model's per-serving estimate, tagged 'est' (used only when computed
    coverage is too low to trust the deterministic figure)."""
    n = data.get("nutrition_per_serving") if isinstance(data, dict) else None
    if not isinstance(n, dict):
        return None
    out = {
        k: n.get(k)
        for k in ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g")
        if n.get(k) is not None
    }
    out["source"] = "est"
    return out or None


def _reconcile_and_compute(
    data: dict, recipe: Recipe, ctx: _Ctx
) -> tuple[list[dict], dict | None, dict | None]:
    """Reconcile the model's ingredient lines against pantry/deals, then compute
    deterministic nutrition. Returns (ingredients, model_nutrition, computed)."""
    raw_ings = data.get("ingredients") if isinstance(data, dict) else None
    ingredients = _reconcile_ingredients(
        raw_ings or [], ctx.deal_by_ingredient, ctx.chain_name,
        ctx.pantry_by_iid, ctx.pantry_by_norm,
    )
    if not ingredients:
        ingredients = _reconcile_ingredients(
            recipe.key_ingredients_json or [], ctx.deal_by_ingredient, ctx.chain_name,
            ctx.pantry_by_iid, ctx.pantry_by_norm,
        )
    computed = nutrition.compute(ingredients, recipe.servings)
    return ingredients, _model_nutrition(data), computed


def _effective_nutrition(
    model_nut: dict | None, computed: dict | None
) -> tuple[dict | None, float | None]:
    """Policy (P28 B3): when deterministic coverage ≥ threshold, the COMPUTED
    figure (labeled 'calculated') replaces the model estimate everywhere and
    drives the protein floor; below it we keep the model numbers ('est'). Never
    blended. Returns (nutrition_dict_to_store, authoritative_protein_g)."""
    if computed and computed.get("coverage", 0) >= nutrition.COVERAGE_THRESHOLD:
        final = {
            "calories": computed["calories"],
            "protein_g": computed["protein_g"],
            "carbs_g": computed["carbs_g"],
            "fat_g": computed["fat_g"],
            "fiber_g": computed["fiber_g"],
            "source": "calculated",
            "coverage": computed["coverage"],
        }
        return final, computed["protein_g"]
    protein = None
    if model_nut and model_nut.get("protein_g") is not None:
        try:
            protein = float(model_nut["protein_g"])
        except (TypeError, ValueError):
            protein = None
    return model_nut, protein


async def _fill_details(
    db: AsyncSession, recipes: list[Recipe], ctx: _Ctx, *, category: str = "generation"
) -> None:
    """Generate full details for concept recipes: parallel Claude, serial writes.

    Enforces the protein floor: a slot whose detail comes back under the floor
    gets one corrective regeneration; if it still falls short we serve it but
    log a warning (the prompt needs work, not the user's dinner blocked).

    The shared context (rules L1 + pantry/deals/floor/pins L2) is a cached system
    prefix, so the N parallel detail calls do one cache write + N-1 cheap reads.
    """
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    floor = ctx.protein_floor
    cap = ctx.calorie_cap
    detail_l2 = _protein_block(floor) + ctx.pin_block + "\n\n" + ctx.context_text
    detail_system = _cached_system(_DETAIL_SYSTEM, detail_l2)
    msgs = [_detail_user_msg(r, ctx) for r in recipes]
    results = await asyncio.gather(
        *(
            _call_json(
                client, model=settings.detail_model, max_tokens=_DETAIL_MAX_TOKENS,
                system=detail_system, user_msg=m, category=category, stage="details",
            )
            for m in msgs
        ),
        return_exceptions=True,
    )

    for i, (recipe, res) in enumerate(zip(recipes, results)):
        data = res if isinstance(res, dict) else {}
        ingredients, model_nut, computed = _reconcile_and_compute(data, recipe, ctx)
        final_nut, protein = _effective_nutrition(model_nut, computed)
        calories = _calories_of(final_nut)

        # Floor + calorie-band validation on the AUTHORITATIVE figures — the
        # computed (USDA) numbers when coverage is high enough, else the model's
        # estimate. One corrective regeneration; the retry only wins if it
        # doesn't worsen any violated dimension (never adopt a worse version).
        viol_protein = floor > 0 and protein is not None and protein < floor
        viol_cal = cap > 0 and calories is not None and calories > cap
        if viol_protein or viol_cal:
            src = (final_nut or {}).get("source", "est")
            correction = ""
            if viol_protein:
                correction += _fortify_correction(protein, floor, src)
            if viol_cal:
                correction += _rebalance_correction(calories, cap, ctx.calorie_target)
            retry = await _call_json(
                client, model=settings.detail_model, max_tokens=_DETAIL_MAX_TOKENS,
                system=detail_system, user_msg=msgs[i] + correction,
                category=category, stage="detail_fix",
            )
            if isinstance(retry, dict) and retry:
                r_ings, r_model, r_comp = _reconcile_and_compute(retry, recipe, ctx)
                r_final, r_protein = _effective_nutrition(r_model, r_comp)
                r_cal = _calories_of(r_final)
                better = True
                if viol_protein and (r_protein is None or r_protein < protein):
                    better = False
                if viol_cal and (r_cal is None or r_cal > calories):
                    better = False
                if better:
                    data, ingredients, final_nut = retry, r_ings, r_final
                    protein, calories = r_protein, r_cal
                    # Anchor replacement (P32 C5) may honestly rename the dish.
                    new_title = str(retry.get("revised_title") or "").strip()
                    if new_title:
                        recipe.title = new_title[:255]

        # Honesty flags (P32 C6/C7): a recipe that still lands below the floor
        # or above the calorie band ships ONLY with a visible amber chip.
        flags = _quality_flags(protein, calories, floor, cap, ctx.calorie_target)
        recipe.quality_flags_json = flags
        if flags:
            logger.warning(
                "Recipe %s (%r) ships flagged after correction: %s "
                "(nutrition source: %s)",
                recipe.id, recipe.title, flags, (final_nut or {}).get("source", "?"),
            )

        recipe.ingredients_json = ingredients
        instructions = data.get("instructions") if isinstance(data, dict) else None
        recipe.instructions_json = instructions or recipe.instructions_json or []
        if final_nut:
            recipe.nutrition_json = final_nut
        recipe.ai_model = settings.detail_model
        recipe.status = "ready"

    # Ingredient-overlap RE-CHECK on the FULL ingredient lists (P33 B3 —
    # concepts were checked on 4 key ingredients; details can drift). A
    # violation at this stage ships with the disclosure note, never silently.
    if ctx.overlap_pool or len(recipes) > 1:
        entries = [
            _entry_for_recipe(r, ctx.overlap_carveout, "batch") for r in recipes
        ]
        for i, recipe in enumerate(recipes):
            others = [e for e in ctx.overlap_pool if e.title != recipe.title] + [
                entries[j] for j in range(len(recipes)) if j != i
            ]
            hit = _overlap_violation(entries[i].keys, entries[i].anchor_key, others)
            if hit is not None:
                _disclose_overlap(None, recipe, hit[0], hit[1])
                logger.warning(
                    "Recipe %s (%r) post-detail ingredient overlap with %r "
                    "(J=%.2f); disclosed",
                    recipe.id, recipe.title, hit[0].title, hit[1],
                )

    await db.flush()


async def _load_detail_overlap(
    db: AsyncSession, user_id: int, recipes: list[Recipe], ctx: _Ctx
) -> None:
    """Populate ctx.overlap_pool/carveout for a detail run (P33 recheck):
    same carve-outs as Stage 1 (pins, the batch's market anchors, saved-week
    purchases), pool = recent batches + saved week + the OTHER recipes of this
    batch (lazy details may fill one recipe at a time)."""
    batch_at = recipes[0].generated_at
    batch = (
        (
            await db.execute(
                select(Recipe).where(
                    Recipe.user_id == user_id, Recipe.generated_at == batch_at
                )
            )
        )
        .scalars()
        .all()
    )
    anchor_keys: set[str] = set()
    for r in batch:
        a = r.market_anchor_json
        if isinstance(a, dict):
            if isinstance(a.get("anchor_key"), str):
                anchor_keys.add(a["anchor_key"])
            k = _ing_key(str(a.get("name") or ""))
            if k is not None:
                anchor_keys.add(k)
    saved_week = (
        (
            await db.execute(
                select(Recipe)
                .join(WeekRecipe, WeekRecipe.recipe_id == Recipe.id)
                .where(
                    WeekRecipe.user_id == user_id,
                    WeekRecipe.week_start == week_start_for(date.today()),
                )
            )
        )
        .scalars()
        .all()
    )
    carve = set(_saved_week_purchase_keys(saved_week)) | anchor_keys
    for p in recipes[0].pinned_items_json or []:
        if isinstance(p, dict):
            k = _ing_key(str(p.get("name") or ""))
            if k is not None:
                carve.add(k)
    ctx.overlap_carveout = carve
    batch_ids = {r.id for r in batch}
    pool = await _overlap_pool(db, user_id, carve, exclude_ids=batch_ids)
    detail_ids = {r.id for r in recipes}
    pool += [
        _entry_for_recipe(r, carve, "batch")
        for r in batch
        if r.id not in detail_ids
    ]
    ctx.overlap_pool = pool


async def run_details_bg(
    user_id: int, recipe_ids: list[int], *, category: str = "generation"
) -> None:
    """Background entrypoint: fill details for the given concept recipes.

    Only recipes still in 'concept' status are detailed, so the on-demand path
    (tap / save) and the eager path never double-bill the same recipe.
    """
    async with AsyncSessionLocal() as db:
        recipes = (
            (
                await db.execute(
                    select(Recipe).where(
                        Recipe.id.in_(recipe_ids),
                        Recipe.user_id == user_id,
                        Recipe.status == "concept",
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
        await _load_detail_overlap(db, user_id, recipes, ctx)
        batch_at = recipes[0].generated_at
        with ai_metering.metering(category, user_id=user_id, batch_at=batch_at) as events:
            await _fill_details(db, recipes, ctx, category=category)
        await ai_metering.persist_events(db, events)
        await db.commit()


def _eager_detail_ids(recipes: list[Recipe], k: int = _EAGER_DETAILS) -> list[int]:
    """The top-k concepts by critic score to detail eagerly; the rest wait for a
    tap or save (lazy details, Prompt 27)."""
    def score(r: Recipe) -> float:
        c = r.critic_json if isinstance(r.critic_json, dict) else {}
        try:
            return float(c.get("score"))
        except (TypeError, ValueError):
            return 0.0
    ranked = sorted(recipes, key=score, reverse=True)
    return [r.id for r in ranked[:k]]


async def _last_difficulties(db: AsyncSession, user_id: int) -> list[str]:
    """The difficulty selection from the user's most recent batch (for warm-cache)."""
    row = await db.scalar(
        select(Recipe.difficulties)
        .where(Recipe.user_id == user_id)
        .order_by(Recipe.generated_at.desc())
        .limit(1)
    )
    return list(row) if row else []


async def warm_generate(user_id: int) -> None:
    """Background: two-stage pre-generation so the Recipes tab is warm.

    Lazy details apply here too: only the top-3 concepts are detailed eagerly;
    the rest fill in on the user's first tap or save.
    """
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return
        difficulties = await _last_difficulties(db, user_id)
        recipes = await generate_concepts(
            db, user, difficulties=difficulties, category="pre-generation"
        )
        await db.commit()
        if recipes:
            eager_ids = set(_eager_detail_ids(recipes))
            eager = [r for r in recipes if r.id in eager_ids]
            ctx = await _load_context(db, user)
            await _load_detail_overlap(db, user_id, eager, ctx)
            batch_at = recipes[0].generated_at
            with ai_metering.metering(
                "pre-generation", user_id=user_id, batch_at=batch_at
            ) as events:
                await _fill_details(db, eager, ctx, category="pre-generation")
            await ai_metering.persist_events(db, events)
            await db.commit()
