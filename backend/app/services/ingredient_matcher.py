"""Ingredient matcher — fuzzy-map a free-text name to an ``ingredient_master`` row.

Matching tiers (best first):
1. Exact match on normalized ``display_name`` / ``canonical_name`` -> confidence 1.0
2. Exact match on a normalized alias                             -> confidence 0.9
3. Token-overlap (Jaccard) against display + alias token sets,
   accepted at >= 0.5, confidence = the overlap score.

The whole ``ingredient_master`` table is small (~400 rows), so it's preloaded
once into a module-level cache and matched entirely in memory.
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingredient import IngredientMaster

# Cache entries: {"id", "exact": set[str], "aliases": set[str],
#                 "token_sets": list[frozenset[str]]}
_cache: list[dict] | None = None

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ACCEPT_THRESHOLD = 0.5


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
            )
        )
    ).all()

    cache: list[dict] = []
    for id_, canonical, display, aliases in rows:
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
