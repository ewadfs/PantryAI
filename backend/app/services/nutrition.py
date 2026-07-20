"""Deterministic nutrition compute engine (Prompt 28 B).

Given a recipe's full ingredient lines (quantity + unit + name), convert each to
GRAMS, look up the ingredient's per-100g macros from ``ingredient_master``, sum,
and divide by servings. No LLM, no network — macros are read from the local
columns seeded by ``scripts/seed_nutrition.py``.

Coverage: the fraction of the recipe's *weighable* mass that comes from
ingredients we have macros for. When coverage ≥ 0.70 the caller trusts the
computed figure ("calculated"); below that it keeps the model's estimate ("est").

Unit handling:
- weight (oz/lb/g/kg)  → grams directly (Qty base is ounces).
- count (egg, clove…)  → count × grams_per_typical_unit.
- volume (cup/tbsp…)   → (fl-oz / 8) × grams_per_typical_unit, where the stored
                          value is grams per CUP.
- container (can/jar…) → skipped (not reliably weighable).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingredient import IngredientMaster
from app.services import ingredient_matcher, quantities

_OZ_TO_G = 28.349523

# Coverage at/above which computed nutrition replaces the model's estimate.
COVERAGE_THRESHOLD = 0.70

# id -> {kcal, protein, carbs, fat, fiber (per 100g), grams_per_unit}
_macros: dict[int, dict] | None = None


async def preload(db: AsyncSession) -> None:
    """Load per-100g macros into a module cache (idempotent)."""
    global _macros
    if _macros is not None:
        return
    rows = (
        await db.execute(
            select(
                IngredientMaster.id,
                IngredientMaster.kcal_per_100g,
                IngredientMaster.protein_g_per_100g,
                IngredientMaster.carbs_g_per_100g,
                IngredientMaster.fat_g_per_100g,
                IngredientMaster.fiber_g_per_100g,
                IngredientMaster.grams_per_typical_unit,
            ).where(IngredientMaster.kcal_per_100g.isnot(None))
        )
    ).all()
    _macros = {
        r[0]: {
            "kcal": r[1],
            "protein": r[2] or 0.0,
            "carbs": r[3] or 0.0,
            "fat": r[4] or 0.0,
            "fiber": r[5] or 0.0,
            "grams_per_unit": r[6],
        }
        for r in rows
    }


def _reset() -> None:
    """Clear the cache (test helper / after a re-seed)."""
    global _macros
    _macros = None


def _to_grams(q: quantities.Qty, grams_per_unit: float | None) -> float | None:
    """Convert a parsed quantity to grams, or None if not weighable."""
    if q.family == "weight":
        return q.value * _OZ_TO_G  # Qty base is ounces
    if q.family == "count":
        if grams_per_unit:
            return q.value * grams_per_unit
        return None
    if q.family == "volume":
        if grams_per_unit:
            return (q.value / 8.0) * grams_per_unit  # value is fl-oz; per-cup weight
        return None
    if q.family == "container":
        # A can/jar of a known food (canned beans, broth…) has a usable weight;
        # grams_per_typical_unit doubles as grams-per-container (~a can ≈ a cup).
        if grams_per_unit:
            return q.value * grams_per_unit
        return None
    return None


def compute(ingredients: list[dict], servings: int | None) -> dict | None:
    """Compute per-serving nutrition from ingredient lines.

    Returns ``{calories, protein_g, carbs_g, fat_g, fiber_g, coverage,
    source: 'calculated', matched_lines, total_lines}`` or ``None`` if nothing
    could be weighed. Requires :func:`preload` first.
    """
    if _macros is None or not ingredients:
        return None
    n = servings if isinstance(servings, int) and servings > 0 else 1

    covered_mass = 0.0
    weighable_mass = 0.0
    totals = {"kcal": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0, "fiber": 0.0}
    matched_lines = 0
    total_lines = 0
    # Every line that contributed NO macros — matched-with-no-data, unmatched,
    # or unweighable. The P43 protein gate and the enrichment worklist both
    # read this list; a primary protein must never hide in it silently.
    unmatched: list[str] = []

    for ing in ingredients:
        if not isinstance(ing, dict):
            continue
        name = str(ing.get("generic_name") or ing.get("name") or "").strip()
        if not name:
            continue
        total_lines += 1
        iid, _c = ingredient_matcher.match_ingredient(name)
        macro = _macros.get(iid) if iid is not None else None
        if macro is None:
            unmatched.append(name)

        q = quantities.parse(ing.get("quantity"), ing.get("unit"))
        if q is None:
            if macro is not None:
                # Matched but no magnitude — contributed nothing after all.
                unmatched.append(name)
            continue
        grams = _to_grams(q, macro["grams_per_unit"] if macro else None)
        if grams is None:
            if macro is not None:
                # Matched but unweighable ("1 package", no per-unit weight) —
                # silently contributes ZERO macros AND zero mass, so coverage
                # can't see it. Observed live: '1 package Korean BBQ beef'
                # matched post-enrichment yet still shipped a 6.6g bowl. The
                # protein gate must see these lines too.
                unmatched.append(name)
            continue

        weighable_mass += grams
        if macro is not None:
            covered_mass += grams
            matched_lines += 1
            f = grams / 100.0
            totals["kcal"] += macro["kcal"] * f
            totals["protein"] += macro["protein"] * f
            totals["carbs"] += macro["carbs"] * f
            totals["fat"] += macro["fat"] * f
            totals["fiber"] += macro["fiber"] * f

    if weighable_mass <= 0:
        return None
    coverage = covered_mass / weighable_mass
    return {
        "calories": round(totals["kcal"] / n),
        "protein_g": round(totals["protein"] / n, 1),
        "carbs_g": round(totals["carbs"] / n, 1),
        "fat_g": round(totals["fat"] / n, 1),
        "fiber_g": round(totals["fiber"] / n, 1),
        "coverage": round(coverage, 3),
        "source": "calculated",
        "matched_lines": matched_lines,
        "total_lines": total_lines,
        "unmatched": unmatched,
    }
