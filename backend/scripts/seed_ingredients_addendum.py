"""Addendum to the ingredient_master seed.

Adds ingredient rows and aliases for real foods that failed to match in stored
pantry scans (chipotle salsa, sesame seeds, marshmallows, sazon, sushi rice,
hot chocolate mix, shichimi/seven-spice, arborio, chipotle-in-adobo, …).

Idempotent — new rows upsert on canonical_name; alias additions MERGE into any
existing row's common_aliases (never clobbering). Safe to re-run.

Run from backend/:
    .venv/Scripts/python.exe scripts/seed_ingredients_addendum.py
"""

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.database import AsyncSessionLocal
from app.models.ingredient import IngredientMaster

T = True
F = False

# (canonical_name, display_name, category, typical_unit, shelf, staple, aliases)
NEW_INGREDIENTS = [
    ("sushi_rice", "Sushi Rice", "grain", "bag", 730, T,
     ["sushi rice", "short grain rice", "short grain white rice", "calrose rice", "short grain sushi rice"]),
    ("arborio_rice", "Arborio Rice", "grain", "bag", 730, T,
     ["arborio", "arborio rice", "risotto rice", "arborio risotto", "arborio risotto rice"]),
    ("sesame_seeds", "Sesame Seeds", "spice", "jar", 730, T,
     ["sesame seeds", "sesame seed", "toasted sesame seeds", "black sesame seeds", "white sesame seeds"]),
    ("marshmallows", "Marshmallows", "snack", "bag", 240, F,
     ["marshmallows", "mini marshmallows", "jumbo marshmallows", "large marshmallows", "campfire marshmallows"]),
    ("sazon_seasoning", "Sazón Seasoning", "spice", "box", 1095, T,
     ["sazon", "sazon goya", "goya sazon", "sazon seasoning", "sazon con culantro y achiote"]),
    ("adobo_seasoning", "Adobo Seasoning", "spice", "jar", 1095, T,
     ["adobo", "adobo seasoning", "goya adobo", "adobo all purpose seasoning"]),
    ("chipotle_in_adobo", "Chipotle in Adobo", "canned", "can", 730, T,
     ["chipotle in adobo", "chipotles in adobo", "adobo de chipotle", "chipotle adobo", "chipotle peppers in adobo"]),
    ("hot_chocolate_mix", "Hot Chocolate Mix", "beverage", "box", 365, T,
     ["hot chocolate", "hot cocoa", "hot cocoa mix", "cocoa mix", "hot chocolate mix", "drinking chocolate"]),
    ("shichimi_togarashi", "Shichimi Togarashi", "spice", "jar", 1095, T,
     ["shichimi", "togarashi", "shichimi togarashi", "japanese seven spice", "seven spice", "seven spice blend"]),
    ("caraway_seeds", "Caraway Seeds", "spice", "jar", 1095, T,
     ["caraway", "caraway seeds", "caraway seed"]),
    ("bone_broth", "Bone Broth", "canned", "carton", 545, T,
     ["bone broth", "beef bone broth", "chicken bone broth", "instant bone broth"]),
    ("sparkling_water", "Sparkling Water", "beverage", "bottle", 365, F,
     ["sparkling water", "seltzer", "seltzer water", "club soda", "carbonated water"]),
    ("brownie_mix", "Brownie Mix", "grain", "box", 365, T,
     ["brownie mix", "chocolate baking mix", "brownie batter mix", "fudge brownie mix"]),
    ("cake_mix", "Cake Mix", "grain", "box", 365, T,
     ["cake mix", "boxed cake mix", "yellow cake mix", "chocolate cake mix"]),
    ("carne_asada_seasoning", "Carne Asada Seasoning", "spice", "jar", 1095, T,
     ["carne asada seasoning", "carne asada rub", "fajita seasoning", "mexican grill seasoning"]),
]

# canonical_name -> extra aliases to MERGE into the existing row (if present).
ALIAS_ADDITIONS = {
    "salsa": ["chipotle salsa", "fire roasted salsa", "fire roasted chipotle salsa", "roasted salsa", "restaurant style salsa"],
    "dried_oregano": ["oregano leaves", "organic oregano", "mexican oregano"],
    "almonds": ["california almonds", "whole natural almonds", "raw california almonds"],
    "breadcrumbs": ["italian breadcrumbs", "italian style bread crumbs", "crispy breadcrumbs", "seasoned bread crumbs"],
    "canned_cannellini_beans": ["greek beans", "fasolia", "gigante beans", "butter beans"],
    "rice_vinegar": ["rice wine", "sushi vinegar"],
}


def _new_rows() -> list[dict]:
    rows: dict[str, dict] = {}
    for canonical, display, category, unit, shelf, staple, aliases in NEW_INGREDIENTS:
        rows[canonical] = {
            "canonical_name": canonical,
            "display_name": display,
            "category": category,
            "typical_unit": unit,
            "shelf_life_days": shelf,
            "is_pantry_staple": staple,
            "common_aliases": aliases,
        }
    return list(rows.values())


async def main() -> None:
    rows = _new_rows()
    async with AsyncSessionLocal() as session:
        # 1. Upsert new ingredient rows.
        stmt = insert(IngredientMaster).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["canonical_name"],
            set_={
                "display_name": stmt.excluded.display_name,
                "category": stmt.excluded.category,
                "typical_unit": stmt.excluded.typical_unit,
                "shelf_life_days": stmt.excluded.shelf_life_days,
                "is_pantry_staple": stmt.excluded.is_pantry_staple,
                "common_aliases": stmt.excluded.common_aliases,
            },
        )
        await session.execute(stmt)

        # 2. Merge extra aliases into existing rows.
        merged = 0
        for canonical, extra in ALIAS_ADDITIONS.items():
            row = await session.scalar(
                select(IngredientMaster).where(
                    IngredientMaster.canonical_name == canonical
                )
            )
            if row is None:
                continue
            current = list(row.common_aliases or [])
            combined = list(dict.fromkeys([*current, *extra]))
            if combined != current:
                row.common_aliases = combined
                merged += 1

        await session.commit()
        total = await session.scalar(select(func.count()).select_from(IngredientMaster))

    print(f"addendum: new/upserted rows={len(rows)} alias-merged rows={merged}")
    print(f"ingredient_master total={total}")


if __name__ == "__main__":
    asyncio.run(main())
