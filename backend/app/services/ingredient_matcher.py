"""Ingredient matcher — fuzzy-map a free-text name to an ``ingredient_master`` row.

Matching tiers (best first):
1. Exact match on normalized ``display_name`` / ``canonical_name`` -> confidence 1.0
2. Exact match on a normalized alias                             -> confidence 0.9
3. Token-overlap (Jaccard) against display + alias token sets,
   accepted at >= 0.5, confidence = the overlap score.

The whole ``ingredient_master`` table is small (~400 rows), so it's preloaded
once into a module-level cache and matched entirely in memory.

Flyer names (Prompt 32 3c): grocery circulars bury the food noun under pack/
grade/marketing qualifiers ("Fresh Boneless Skinless Chicken Breast Family
Pack, 80% Lean...") that sink the Jaccard overlap below the accept threshold.
:func:`normalize_flyer_name` strips those qualifiers (mirroring the 18-B4
produce qualifier-stripping) and :func:`match_flyer_name` matches on both the
raw and normalized forms, keeping whichever scores higher.
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingredient import IngredientMaster

# Cache entries: {"id", "exact": set[str], "aliases": set[str],
#                 "token_sets": list[frozenset[str]]}
_cache: list[dict] | None = None
# Ingredient ids flagged is_pantry_staple (salt, oils, basic spices…) —
# excluded from ingredient-overlap variety math (Prompt 33 A1).
_staple_ids: set[int] = set()
# id -> master category (pantry-mode purchase rules need to know whether a
# purchased line is a protein, Prompt 35 B3).
_categories: dict[int, str] = {}

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ACCEPT_THRESHOLD = 0.5

# --------------------------------------------------------------------------- #
# Flyer-name normalization (Prompt 32 3c)
# --------------------------------------------------------------------------- #
# Pack / grade / marketing qualifiers that carry no food identity. Adjectives
# like "boneless skinless" go; the protein noun stays.
_FLYER_NOISE = re.compile(
    r"""\b(?:
        (?:family|value|mega|party|club|bonus|econo(?:my)?|variety)\s*-?\s*pack|
        \d{1,3}\s*%\s*(?:lean|fat(?:\s*free)?)|
        \d+(?:\.\d+)?\s*(?:oz|lbs?|ct|count|pk|pcs?)\.?(?:\s*(?:avg|average|bag|box|pkg|package))?|
        boneless|skinless|bone[-\s]?in|semi[-\s]?boneless|
        thin(?:ly)?\s+sliced|thick\s+cut|center\s+cut|split|quartered|
        jumbo|extra\s+large|super|premium|select(?:ed)?|choice|prime|
        usda\s+(?:choice|prime|select|inspected|grade\s+a)|grade\s+a{1,3}|
        fresh|frozen|previously\s+frozen|never\s+frozen|
        all[-\s]?natural|natural|organic|antibiotic[-\s]?free|hormone[-\s]?free|
        farm[-\s]?raised|wild[-\s]?caught|cage[-\s]?free|free[-\s]?range|
        grass[-\s]?fed|air[-\s]?chilled|
        store\s+(?:made|cut)|hand[-\s]?trimmed|restaurant\s+quality|
        great\s+on\s+the\s+grill|perfect\s+for\s+grilling|
        sold\s+(?:whole\s+)?in\s+(?:the\s+)?bag|must\s+buy\s+\d+|limit\s+\d+|
        with\s+card|digital\s+(?:coupon|deal)|
        sale|special|weekly\s+deal
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)
# Brand prefixes commonly headlining meat/seafood flyer lines. A deal row's own
# ``brand`` field (when the extractor filled it) is stripped too.
_FLYER_BRANDS = (
    "perdue", "tyson", "foster farms", "smithfield", "hatfield", "butterball",
    "oscar mayer", "hillshire farm", "johnsonville", "jimmy dean",
    "bell & evans", "bell and evans", "nature's promise", "natures promise",
    "bowl & basket", "bowl and basket", "wholesome pantry", "sanderson farms",
    "springer mountain farms", "shady brook farms", "jennie-o", "jennie o",
    "al fresco", "applegate", "boar's head", "boars head", "sea best",
    "fresh catch", "lidl", "shoprite", "stop & shop", "stop and shop",
)
_WS_RE = re.compile(r"\s+")


def normalize_flyer_name(name: str, brand: str | None = None) -> str:
    """Strip pack/grade/marketing qualifiers and brand prefixes from a flyer
    product name, leaving the food identity ("Fresh Boneless Skinless Chicken
    Breast Family Pack" -> "Chicken Breast")."""
    if not name:
        return ""
    out = name
    lowered = out.lower().strip()
    for b in filter(None, [(brand or "").lower().strip(), *_FLYER_BRANDS]):
        if lowered.startswith(b):
            out = out[len(b):]
            lowered = out.lower().strip()
    out = _FLYER_NOISE.sub(" ", out)
    out = re.sub(r"[,/]", " ", out)
    return _WS_RE.sub(" ", out).strip(" -–—.")


def match_flyer_name(name: str, brand: str | None = None) -> tuple[int | None, float]:
    """Match a flyer product name against ingredient_master, trying both the
    raw and qualifier-stripped forms; the higher-confidence match wins."""
    raw_id, raw_conf = match_ingredient(name)
    cleaned = normalize_flyer_name(name, brand)
    if not cleaned or _norm(cleaned) == _norm(name):
        return (raw_id, raw_conf)
    clean_id, clean_conf = match_ingredient(cleaned)
    if clean_conf > raw_conf:
        return (clean_id, clean_conf)
    return (raw_id, raw_conf)


def _singular(token: str) -> str:
    """Naive singularization: strip a single trailing 's' from longer tokens."""
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _tokens(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, singularize each token."""
    return [_singular(t) for t in _TOKEN_RE.findall(text.lower())]


def _norm(text: str) -> str:
    """Normalized comparison form: space-joined singularized tokens."""
    return " ".join(_tokens(text))


async def preload(db: AsyncSession) -> None:
    """Load the ingredient table into the module cache (idempotent)."""
    global _cache
    if _cache is not None:
        return
    rows = (
        await db.execute(
            select(
                IngredientMaster.id,
                IngredientMaster.canonical_name,
                IngredientMaster.display_name,
                IngredientMaster.common_aliases,
                IngredientMaster.is_pantry_staple,
                IngredientMaster.category,
            )
        )
    ).all()

    cache: list[dict] = []
    for id_, canonical, display, aliases, is_staple, category in rows:
        if is_staple:
            _staple_ids.add(id_)
        if category:
            _categories[id_] = category
        aliases = aliases or []
        exact = {_norm(canonical)}
        if display:
            exact.add(_norm(display))
        alias_norms = {_norm(a) for a in aliases if a}
        token_sets = [frozenset(_tokens(canonical))]
        if display:
            token_sets.append(frozenset(_tokens(display)))
        token_sets.extend(frozenset(_tokens(a)) for a in aliases if a)
        cache.append(
            {
                "id": id_,
                "exact": exact,
                "aliases": alias_norms,
                "token_sets": [ts for ts in token_sets if ts],
            }
        )
    _cache = cache


def _reset() -> None:
    """Clear the cache (test helper)."""
    global _cache
    _cache = None
    _staple_ids.clear()
    _categories.clear()


def is_staple_id(ingredient_id: int | None) -> bool:
    """Is this ingredient_master row flagged is_pantry_staple? Requires
    :func:`preload`."""
    return ingredient_id is not None and ingredient_id in _staple_ids


def category_of(ingredient_id: int | None) -> str | None:
    """Master category for an ingredient id (requires :func:`preload`)."""
    if ingredient_id is None:
        return None
    return _categories.get(ingredient_id)


def match_ingredient(name: str) -> tuple[int | None, float]:
    """Return ``(ingredient_id, confidence)`` for ``name``, else ``(None, 0.0)``.

    Requires :func:`preload` to have populated the cache first.
    """
    if not _cache:
        return (None, 0.0)

    q = _norm(name)
    q_tokens = set(_tokens(name))
    if not q_tokens:
        return (None, 0.0)

    # Tier 1: exact display / canonical.
    for ing in _cache:
        if q in ing["exact"]:
            return (ing["id"], 1.0)

    # Tier 2: alias exact.
    for ing in _cache:
        if q in ing["aliases"]:
            return (ing["id"], 0.9)

    # Tier 3: best token-overlap (Jaccard) over display + alias token sets.
    best_id: int | None = None
    best_score = 0.0
    for ing in _cache:
        for ts in ing["token_sets"]:
            overlap = len(q_tokens & ts) / len(q_tokens | ts)
            if overlap > best_score:
                best_score = overlap
                best_id = ing["id"]

    if best_score >= _ACCEPT_THRESHOLD:
        return (best_id, round(best_score, 2))
    return (None, 0.0)
