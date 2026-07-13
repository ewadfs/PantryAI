"""Ingredient addendum from the P38 live activations (≤25 rows, idempotent).

Gaps found while extracting the first real H Mart / Patel Brothers / King
Kullen fetches: produce and staples on those flyers that had no
ingredient_master row (or mis-matched an unrelated row — "rice flour" was
falling onto white rice at 0.5, "dosakai" onto watermelon). Upserts on
canonical_name; alias additions merge non-destructively (same pattern as
seed_ingredients_intl.py).

Run from backend/:
    .venv/bin/python scripts/seed_ingredients_p38.py
"""

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.ingredient import IngredientMaster

T = True
F = False

# (canonical_name, display_name, category, typical_unit, shelf_days, staple, aliases)
NEW_INGREDIENTS = [
    # --- Patel Brothers flyer (produce + staples) ---------------------------
    ("okra", "Okra", "produce", "lb", 5, F,
     ["okra", "fresh okra", "bhindi", "lady finger", "lady fingers"]),
    ("tindora", "Tindora (Ivy Gourd)", "produce", "lb", 7, F,
     ["tindora", "fresh tindora", "ivy gourd", "tendli", "tindli", "dondakaya"]),
    ("guava", "Guava", "produce", "lb", 5, F,
     ["guava", "thai guava", "fresh guava", "amrood"]),
    ("dosakai", "Dosakai (Yellow Cucumber)", "produce", "lb", 10, F,
     ["dosakai", "fresh dosakai", "yellow cucumber", "dosakaya", "vellarikka"]),
    ("rice_flour", "Rice Flour", "baking", "bag", 365, T,
     ["rice flour", "white rice flour", "chawal ka atta"]),
    ("idli_rice", "Idli Rice", "grain", "bag", 365, T,
     ["idli rice", "parboiled idli rice", "idly rice"]),
    # --- H Mart weekly sale --------------------------------------------------
    ("natto", "Natto (Fermented Soybeans)", "produce", "package", 30, F,
     ["natto", "fermented soybeans", "fermented soy beans"]),
    ("mochi", "Mochi", "snacks", "package", 60, F,
     ["mochi", "mochi rice cake", "mochi ice cream", "daifuku"]),
    # --- King Kullen circular (protein the matcher missed) -------------------
    ("smoked_sausage", "Smoked Sausage", "meat", "package", 21, F,
     ["smoked sausage", "cocktail smoked sausage", "smoked sausage links",
      "kielbasa", "cocktail franks", "lit'l smokies", "little smokies"]),
]


async def main() -> None:
    async with AsyncSessionLocal() as db:
        added = merged = 0
        for canonical, display, cat, unit, shelf, staple, aliases in NEW_INGREDIENTS:
            row = (
                await db.execute(
                    select(IngredientMaster).where(
                        IngredientMaster.canonical_name == canonical
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                db.add(IngredientMaster(
                    canonical_name=canonical, display_name=display,
                    category=cat, typical_unit=unit,
                    shelf_life_days=shelf, is_pantry_staple=staple,
                    common_aliases=aliases,
                ))
                added += 1
            else:
                current = set(row.common_aliases or [])
                new = [a for a in aliases if a not in current]
                if new:
                    row.common_aliases = [*(row.common_aliases or []), *new]
                    merged += 1
        await db.commit()
    print(f"seed_ingredients_p38: {added} added, {merged} alias-merged "
          f"({len(NEW_INGREDIENTS)} rows defined)")


if __name__ == "__main__":
    asyncio.run(main())
