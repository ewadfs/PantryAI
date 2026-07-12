"""International staple ingredients for H Mart + Patel Brothers (Prompt 24-lite v2).

Adds the Korean/East-Asian (H Mart) and Indian/South-Asian (Patel Brothers)
staples most likely to appear on those chains' flyers and in recipes, so their
future extracted deals match an ingredient_master row. Idempotent: new rows
upsert on canonical_name; alias additions merge non-destructively.

Run from backend/:
    .venv/Scripts/python.exe scripts/seed_ingredients_intl.py
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
    # --- H Mart (Korean / East-Asian) ---------------------------------------
    ("tofu", "Tofu", "produce", "package", 21, F,
     ["tofu", "firm tofu", "silken tofu", "extra firm tofu", "soft tofu", "bean curd", "dubu"]),
    ("kimchi", "Kimchi", "produce", "jar", 90, F,
     ["kimchi", "napa kimchi", "cabbage kimchi", "baechu kimchi"]),
    ("napa_cabbage", "Napa Cabbage", "produce", "each", 14, F,
     ["napa cabbage", "napa", "chinese cabbage", "baechu"]),
    ("daikon", "Daikon Radish", "produce", "each", 21, F,
     ["daikon", "daikon radish", "korean radish", "white radish", "mu"]),
    ("enoki_mushroom", "Enoki Mushroom", "produce", "package", 10, F,
     ["enoki", "enoki mushroom", "enoki mushrooms", "enokitake"]),
    ("gochugaru", "Gochugaru (Korean Chili Flakes)", "spice", "bag", 730, T,
     ["gochugaru", "korean chili flakes", "korean red pepper flakes", "korean chili powder"]),
    ("doenjang", "Doenjang (Fermented Soybean Paste)", "sauce", "tub", 365, T,
     ["doenjang", "korean soybean paste", "fermented soybean paste", "denjang"]),
    ("miso", "Miso Paste", "sauce", "tub", 365, T,
     ["miso", "miso paste", "white miso", "red miso", "shiro miso", "aka miso"]),
    ("mirin", "Mirin", "oil_vinegar", "bottle", 730, T,
     ["mirin", "sweet rice wine", "aji mirin"]),
    ("dashi", "Dashi Stock", "canned", "box", 365, T,
     ["dashi", "dashi stock", "dashi broth", "hondashi", "bonito stock"]),
    ("nori", "Nori (Dried Seaweed)", "produce", "package", 365, T,
     ["nori", "dried seaweed", "seaweed sheets", "gim", "sushi nori", "roasted seaweed"]),
    ("rice_cake", "Rice Cakes (Tteok)", "grain", "package", 30, F,
     ["rice cake", "rice cakes", "tteok", "tteokbokki rice cakes", "korean rice cakes", "garaetteok"]),
    ("udon", "Udon Noodles", "grain", "package", 180, F,
     ["udon", "udon noodles", "fresh udon", "sanuki udon"]),
    ("soba", "Soba Noodles", "grain", "package", 365, F,
     ["soba", "soba noodles", "buckwheat noodles"]),
    # --- Patel Brothers (Indian / South-Asian) ------------------------------
    ("paneer", "Paneer", "dairy", "package", 14, F,
     ["paneer", "indian cheese", "malai paneer", "fresh paneer"]),
    ("toor_dal", "Toor Dal (Split Pigeon Peas)", "grain", "bag", 365, T,
     ["toor dal", "toor daal", "tuvar dal", "arhar dal", "split pigeon peas", "yellow pigeon peas"]),
    ("moong_dal", "Moong Dal (Split Mung Beans)", "grain", "bag", 365, T,
     ["moong dal", "mung dal", "split mung beans", "yellow moong dal", "green gram dal"]),
    ("chana_dal", "Chana Dal (Split Chickpeas)", "grain", "bag", 365, T,
     ["chana dal", "channa dal", "split chickpeas", "bengal gram"]),
    ("urad_dal", "Urad Dal (Split Black Gram)", "grain", "bag", 365, T,
     ["urad dal", "urad daal", "split black gram", "black gram dal", "white lentils"]),
    ("atta_flour", "Atta (Whole Wheat Flour)", "grain", "bag", 365, T,
     ["atta", "atta flour", "chapati flour", "roti flour", "durum whole wheat atta"]),
    ("besan", "Besan (Chickpea Flour)", "grain", "bag", 365, T,
     ["besan", "chickpea flour", "gram flour", "garbanzo flour", "chana flour"]),
    ("tamarind", "Tamarind", "condiment", "jar", 365, T,
     ["tamarind", "tamarind paste", "tamarind concentrate", "imli", "tamarind pulp"]),
    ("curry_leaves", "Curry Leaves", "produce", "package", 14, F,
     ["curry leaves", "curry leaf", "kadi patta", "fresh curry leaves"]),
    ("jaggery", "Jaggery", "grain", "block", 365, T,
     ["jaggery", "gur", "gud", "palm sugar block", "unrefined cane sugar"]),
]

# Alias merges onto ingredients that already exist.
ALIAS_ADDITIONS = {
    "gochujang": ["korean chili paste", "red pepper paste", "hot pepper paste"],
    "ground_turmeric": ["haldi", "turmeric powder"],
    "garam_masala": ["garam masala powder"],
    "basmati_rice": ["indian basmati", "aged basmati rice"],
    "ghee": ["clarified butter", "desi ghee"],
    "sesame_oil": ["toasted sesame oil", "korean sesame oil"],
}


def _new_rows() -> list[dict]:
    return [
        {
            "canonical_name": c, "display_name": d, "category": cat,
            "typical_unit": unit, "shelf_life_days": shelf,
            "is_pantry_staple": staple, "common_aliases": aliases,
        }
        for (c, d, cat, unit, shelf, staple, aliases) in NEW_INGREDIENTS
    ]


async def main() -> None:
    async with AsyncSessionLocal() as db:
        before = await db.scalar(select(func.count()).select_from(IngredientMaster))
        for row in _new_rows():
            await db.execute(
                insert(IngredientMaster)
                .values(**row)
                .on_conflict_do_update(
                    index_elements=["canonical_name"],
                    set_={
                        "display_name": row["display_name"],
                        "category": row["category"],
                        "typical_unit": row["typical_unit"],
                        "common_aliases": row["common_aliases"],
                    },
                )
            )
        # Non-destructive alias merges.
        for canon, extra in ALIAS_ADDITIONS.items():
            r = (
                await db.execute(
                    select(IngredientMaster).where(IngredientMaster.canonical_name == canon)
                )
            ).scalar_one_or_none()
            if r is None:
                continue
            merged = list(dict.fromkeys([*(r.common_aliases or []), *extra]))
            r.common_aliases = merged
        await db.commit()
        after = await db.scalar(select(func.count()).select_from(IngredientMaster))
    print(f"international staples: {len(NEW_INGREDIENTS)} rows upserted "
          f"(+{after - before} new), {len(ALIAS_ADDITIONS)} alias merges; total={after}")


if __name__ == "__main__":
    asyncio.run(main())
