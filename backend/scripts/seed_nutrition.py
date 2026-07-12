"""Populate ingredient_master per-100g macros for deterministic nutrition (P28 B).

Two data paths:

  curated  — a hand-verified table of real USDA FoodData Central per-100g macros
             (RAW / as-purchased, because recipes list raw weights) for the
             mass-bearing ingredients: every protein + seafood, grains, dairy,
             eggs, oils, canned goods, and common produce. Also carries
             grams_per_typical_unit for count/volume lines (1 egg≈50 g,
             1 clove garlic≈3 g, 1 cup rice≈185 g, 1 cup oil≈218 g). Matched to
             rows by canonical_name. nutrition_source='curated'.

  usda     — key-gated bulk pass (needs USDA_FDC_API_KEY) that fills any row
             STILL missing macros by best-match search against FoodData Central
             (SR Legacy + Foundation), writing usda_fdc_id + macros
             (source='usda'). Low-confidence matches are NOT written; they are
             printed as a manual-review list.

Both are idempotent. Runtime nutrition never calls USDA — it reads these local
columns only.

Run from backend/:
    .venv/Scripts/python.exe scripts/seed_nutrition.py curated
    .venv/Scripts/python.exe scripts/seed_nutrition.py usda      # needs key
    .venv/Scripts/python.exe scripts/seed_nutrition.py all
"""

import asyncio
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.ingredient import IngredientMaster

# canonical_name -> (kcal, protein_g, carbs_g, fat_g, fiber_g, grams_per_unit)
# per 100 g, RAW. grams_per_unit is grams for ONE count (egg, clove) OR grams
# per CUP for volume-measured items (rice, flour, oil, milk); None when the
# ingredient is essentially always bought/used by weight. All values are real
# USDA FoodData Central figures (SR Legacy / Foundation), rounded.
CURATED: dict[str, tuple[float, float, float, float, float, float | None]] = {
    # ---- meat (raw) -----------------------------------------------------
    "ground_beef": (254, 17.2, 0.0, 20.0, 0.0, None),        # 80/20
    "ground_chicken": (143, 17.4, 0.0, 8.1, 0.0, None),
    "ground_turkey": (170, 18.0, 0.0, 10.0, 0.0, None),      # 85/15
    "ground_pork": (263, 16.9, 0.0, 21.2, 0.0, None),
    "ground_lamb": (282, 16.6, 0.0, 23.4, 0.0, None),
    "chicken_breast": (120, 22.5, 0.0, 2.6, 0.0, 174.0),     # 1 breast≈174 g
    "chicken_thigh": (143, 18.0, 0.0, 7.5, 0.0, 116.0),
    "chicken_drumstick": (116, 20.0, 0.0, 3.4, 0.0, 88.0),
    "chicken_wing": (222, 20.4, 0.0, 15.5, 0.0, 34.0),
    "chicken_tenders": (120, 22.5, 0.0, 2.6, 0.0, 30.0),
    "whole_chicken": (215, 18.6, 0.0, 15.0, 0.0, None),
    "turkey_breast": (111, 24.0, 0.0, 1.7, 0.0, None),
    "pork_chop": (198, 20.7, 0.0, 12.6, 0.0, 170.0),
    "pork_shoulder": (211, 17.4, 0.0, 15.3, 0.0, None),
    "pork_tenderloin": (120, 20.5, 0.0, 3.5, 0.0, None),
    "pork_ribs": (277, 17.0, 0.0, 23.0, 0.0, None),
    "beef_chuck_roast": (250, 18.0, 0.0, 19.0, 0.0, None),
    "beef_brisket": (210, 18.0, 0.0, 15.0, 0.0, None),
    "beef_short_ribs": (320, 15.0, 0.0, 29.0, 0.0, None),
    "flank_steak": (127, 21.0, 0.0, 4.3, 0.0, None),
    "sirloin_steak": (150, 22.0, 0.0, 6.4, 0.0, None),
    "ribeye_steak": (291, 18.0, 0.0, 24.0, 0.0, None),
    "lamb_chop": (282, 16.6, 0.0, 23.4, 0.0, None),
    "bacon": (458, 12.0, 1.4, 45.0, 0.0, 12.0),              # 1 slice≈12 g raw
    "italian_sausage": (290, 16.0, 3.0, 24.0, 0.0, 83.0),    # 1 link≈83 g
    "breakfast_sausage": (330, 12.0, 1.0, 31.0, 0.0, 27.0),
    "hot_dog": (290, 10.0, 4.0, 26.0, 0.0, 45.0),
    "deli_ham": (145, 17.0, 4.0, 6.0, 0.0, None),
    "deli_turkey": (104, 17.0, 4.0, 2.0, 0.0, None),
    "ham": (145, 17.0, 4.0, 6.0, 0.0, None),
    "pepperoni": (494, 20.0, 1.2, 44.0, 0.0, None),
    "salami": (336, 22.0, 2.0, 26.0, 0.0, None),
    # ---- seafood (raw) --------------------------------------------------
    "salmon_fillet": (208, 20.0, 0.0, 13.0, 0.0, None),
    "smoked_salmon": (117, 18.0, 0.0, 4.3, 0.0, None),
    "shrimp": (99, 24.0, 0.2, 0.3, 0.0, None),
    "cod": (82, 18.0, 0.0, 0.7, 0.0, None),
    "tilapia": (96, 20.1, 0.0, 1.7, 0.0, None),
    "tuna_steak": (130, 28.0, 0.0, 1.3, 0.0, None),
    "catfish": (105, 18.0, 0.0, 2.9, 0.0, None),
    "flounder": (86, 18.8, 0.0, 1.2, 0.0, None),
    "halibut": (111, 21.0, 0.0, 2.3, 0.0, None),
    "mahi_mahi": (85, 18.5, 0.0, 0.7, 0.0, None),
    "sea_bass": (97, 18.4, 0.0, 2.0, 0.0, None),
    "swordfish": (144, 20.0, 0.0, 6.7, 0.0, None),
    "trout": (141, 20.0, 0.0, 6.2, 0.0, None),
    "scallops": (88, 16.8, 2.4, 0.8, 0.0, None),
    "crab_legs": (87, 18.0, 0.0, 1.1, 0.0, None),
    "lobster_tail": (89, 19.0, 0.0, 0.9, 0.0, None),
    "clams": (86, 15.0, 3.0, 1.0, 0.0, None),
    "mussels": (86, 12.0, 3.7, 2.2, 0.0, None),
    "oysters": (81, 9.0, 4.7, 2.3, 0.0, None),
    "calamari": (92, 15.6, 3.1, 1.4, 0.0, None),
    # ---- eggs -----------------------------------------------------------
    "large_eggs": (143, 12.6, 0.7, 9.5, 0.0, 50.0),
    "brown_eggs": (143, 12.6, 0.7, 9.5, 0.0, 50.0),
    "extra_large_eggs": (143, 12.6, 0.7, 9.5, 0.0, 56.0),
    "egg_whites": (52, 10.9, 0.7, 0.2, 0.0, 33.0),
    "liquid_eggs": (143, 12.6, 0.7, 9.5, 0.0, None),
    # ---- grains / baking (dry/uncooked) ---------------------------------
    "white_rice": (365, 7.1, 80.0, 0.7, 1.3, 185.0),
    "brown_rice": (370, 7.9, 77.0, 2.9, 3.5, 190.0),
    "jasmine_rice": (365, 7.0, 80.0, 0.6, 1.0, 185.0),
    "basmati_rice": (360, 7.5, 78.0, 0.9, 1.2, 185.0),
    "sushi_rice": (358, 6.5, 79.0, 0.6, 0.8, 190.0),
    "arborio_rice": (359, 7.0, 79.0, 0.6, 1.4, 200.0),
    "quinoa": (368, 14.1, 64.0, 6.1, 7.0, 170.0),
    "farro": (340, 12.0, 72.0, 2.0, 10.0, 200.0),
    "barley": (352, 9.9, 78.0, 1.2, 15.6, 200.0),
    "couscous": (376, 12.8, 77.0, 0.6, 5.0, 173.0),
    "orzo": (371, 13.0, 75.0, 1.5, 3.0, 200.0),
    "spaghetti": (371, 13.0, 75.0, 1.5, 3.2, 100.0),
    "angel_hair": (371, 13.0, 75.0, 1.5, 3.2, 100.0),
    "fettuccine": (371, 13.0, 75.0, 1.5, 3.2, 100.0),
    "penne": (371, 13.0, 75.0, 1.5, 3.2, 105.0),
    "rotini": (371, 13.0, 75.0, 1.5, 3.2, 105.0),
    "elbow_macaroni": (371, 13.0, 75.0, 1.5, 3.2, 105.0),
    "lasagna_noodles": (371, 13.0, 75.0, 1.5, 3.2, None),
    "egg_noodles": (384, 14.2, 71.0, 4.4, 3.3, 38.0),
    "couscous_pearl": (376, 12.8, 77.0, 0.6, 5.0, 173.0),
    "rolled_oats": (389, 16.9, 66.3, 6.9, 10.6, 90.0),
    "steel_cut_oats": (379, 13.0, 68.0, 6.5, 10.1, 170.0),
    "all_purpose_flour": (364, 10.3, 76.3, 1.0, 2.7, 120.0),
    "bread_flour": (361, 12.0, 73.0, 1.7, 2.4, 120.0),
    "whole_wheat_flour": (340, 13.2, 72.0, 2.5, 10.7, 120.0),
    "cornmeal": (370, 8.1, 79.0, 3.6, 7.3, 157.0),
    "breadcrumbs": (395, 13.4, 72.0, 5.3, 4.5, 108.0),
    "panko": (395, 13.4, 72.0, 5.3, 4.5, 54.0),
    "granulated_sugar": (387, 0.0, 100.0, 0.0, 0.0, 200.0),
    "brown_sugar": (380, 0.0, 98.0, 0.0, 0.0, 220.0),
    "powdered_sugar": (389, 0.0, 100.0, 0.0, 0.0, 120.0),
    "chocolate_chips": (480, 4.2, 63.0, 30.0, 5.9, 170.0),
    "cocoa_powder": (228, 19.6, 58.0, 13.7, 33.0, 86.0),
    "dry_black_beans": (341, 21.6, 62.4, 1.4, 15.5, 190.0),
    "dry_lentils": (352, 24.6, 63.0, 1.1, 10.7, 192.0),
    # ---- dairy ----------------------------------------------------------
    "whole_milk": (61, 3.2, 4.8, 3.3, 0.0, 244.0),
    "milk_2_percent": (50, 3.4, 4.9, 2.0, 0.0, 244.0),
    "skim_milk": (34, 3.4, 5.0, 0.1, 0.0, 245.0),
    "buttermilk": (62, 3.3, 4.9, 3.3, 0.0, 245.0),
    "heavy_cream": (340, 2.8, 2.8, 36.0, 0.0, 238.0),
    "half_and_half": (130, 3.0, 4.3, 11.5, 0.0, 242.0),
    "sour_cream": (198, 2.4, 4.6, 19.0, 0.0, 230.0),
    "butter": (717, 0.9, 0.1, 81.0, 0.0, 227.0),
    "unsalted_butter": (717, 0.9, 0.1, 81.0, 0.0, 227.0),
    "ghee": (900, 0.0, 0.0, 100.0, 0.0, 205.0),
    "greek_yogurt": (97, 9.0, 3.9, 5.0, 0.0, 245.0),
    "plain_yogurt": (61, 3.5, 4.7, 3.3, 0.0, 245.0),
    "cottage_cheese": (98, 11.1, 3.4, 4.3, 0.0, 226.0),
    "cheddar_cheese": (403, 24.9, 3.4, 33.0, 0.0, 113.0),
    "mozzarella_cheese": (300, 22.2, 2.2, 22.4, 0.0, 112.0),
    "parmesan_cheese": (431, 38.5, 4.1, 29.0, 0.0, 100.0),
    "cream_cheese": (342, 6.2, 4.1, 34.0, 0.0, 232.0),
    "feta_cheese": (264, 14.2, 4.1, 21.3, 0.0, 150.0),
    "ricotta_cheese": (174, 11.3, 3.0, 13.0, 0.0, 246.0),
    "swiss_cheese": (380, 27.0, 5.0, 28.0, 0.0, 108.0),
    "provolone_cheese": (351, 25.6, 2.1, 26.6, 0.0, 113.0),
    "american_cheese": (300, 16.0, 8.0, 23.0, 0.0, 113.0),
    # ---- oils / vinegars ------------------------------------------------
    "olive_oil": (884, 0.0, 0.0, 100.0, 0.0, 216.0),
    "extra_virgin_olive_oil": (884, 0.0, 0.0, 100.0, 0.0, 216.0),
    "vegetable_oil": (884, 0.0, 0.0, 100.0, 0.0, 218.0),
    "canola_oil": (884, 0.0, 0.0, 100.0, 0.0, 218.0),
    "avocado_oil": (884, 0.0, 0.0, 100.0, 0.0, 216.0),
    "coconut_oil": (862, 0.0, 0.0, 100.0, 0.0, 218.0),
    "peanut_oil": (884, 0.0, 0.0, 100.0, 0.0, 216.0),
    "sesame_oil": (884, 0.0, 0.0, 100.0, 0.0, 218.0),
    # ---- canned / broth -------------------------------------------------
    "canned_black_beans": (91, 6.0, 16.6, 0.3, 6.9, 240.0),
    "canned_kidney_beans": (84, 5.5, 15.0, 0.3, 6.4, 256.0),
    "canned_chickpeas": (139, 7.0, 22.5, 2.6, 6.4, 240.0),
    "canned_pinto_beans": (88, 5.4, 16.0, 0.7, 5.5, 240.0),
    "canned_cannellini_beans": (91, 6.0, 16.6, 0.3, 6.9, 240.0),
    "refried_beans": (92, 5.5, 15.0, 1.5, 5.0, 260.0),
    "diced_tomatoes": (32, 1.6, 7.0, 0.3, 1.5, 240.0),
    "crushed_tomatoes": (32, 1.6, 7.0, 0.3, 1.5, 240.0),
    "tomato_sauce": (29, 1.3, 6.4, 0.2, 1.4, 245.0),
    "tomato_paste": (82, 4.3, 18.9, 0.5, 4.1, 262.0),
    "coconut_milk": (230, 2.3, 6.0, 24.0, 0.0, 240.0),
    "canned_tuna": (116, 25.5, 0.0, 0.8, 0.0, None),
    "canned_salmon": (139, 19.8, 0.0, 6.0, 0.0, None),
    "canned_corn": (86, 3.2, 19.0, 1.2, 2.7, 165.0),
    "chicken_broth": (7, 0.5, 0.5, 0.2, 0.0, 240.0),
    "beef_broth": (7, 0.9, 0.4, 0.2, 0.0, 240.0),
    "vegetable_broth": (7, 0.3, 1.0, 0.1, 0.0, 240.0),
    # ---- condiments / sauces (used in volume) ---------------------------
    "soy_sauce": (53, 8.1, 4.9, 0.6, 0.8, 255.0),
    "bbq_sauce": (172, 0.8, 41.0, 0.6, 0.8, 288.0),
    "ketchup": (101, 1.0, 27.0, 0.1, 0.3, 240.0),
    "mayonnaise": (680, 1.0, 0.6, 75.0, 0.0, 220.0),
    "honey": (304, 0.3, 82.4, 0.0, 0.2, 340.0),
    "maple_syrup": (260, 0.0, 67.0, 0.1, 0.0, 322.0),
    "peanut_butter": (588, 25.1, 20.0, 50.0, 6.0, 258.0),
    "almond_butter": (614, 21.0, 19.0, 56.0, 10.0, 250.0),
    "dijon_mustard": (66, 4.4, 5.3, 3.6, 3.3, 249.0),
    "yellow_mustard": (67, 3.7, 6.0, 4.0, 3.0, 249.0),
    "pesto": (450, 4.0, 6.0, 45.0, 2.0, 230.0),
    "marinara_sauce": (55, 1.6, 9.0, 1.5, 2.0, 245.0),
    "pizza_sauce": (55, 1.6, 9.0, 1.5, 2.0, 245.0),
    "salsa": (36, 1.5, 7.0, 0.2, 1.8, 240.0),
    "hoisin_sauce": (220, 3.3, 44.0, 3.4, 2.8, 280.0),
    "teriyaki_sauce": (89, 5.9, 15.6, 0.0, 0.1, 280.0),
    "oyster_sauce": (51, 1.4, 11.0, 0.3, 0.3, 280.0),
    "sriracha": (93, 1.9, 19.0, 0.9, 2.2, 255.0),
    "hot_sauce": (11, 0.5, 1.8, 0.4, 0.3, 240.0),
    "alfredo_sauce": (146, 3.0, 6.0, 12.0, 0.0, 245.0),
    "enchilada_sauce": (56, 1.4, 8.0, 2.5, 1.5, 245.0),
    "tahini": (595, 17.0, 21.0, 54.0, 9.3, 240.0),
    # ---- produce (raw) --------------------------------------------------
    "russet_potato": (79, 2.1, 18.1, 0.1, 1.3, None),
    "red_potato": (70, 1.9, 15.9, 0.1, 1.7, None),
    "yukon_gold_potato": (73, 2.0, 17.0, 0.1, 1.5, None),
    "sweet_potato": (86, 1.6, 20.1, 0.1, 3.0, None),
    "yellow_onion": (40, 1.1, 9.3, 0.1, 1.7, 110.0),
    "white_onion": (40, 1.1, 9.3, 0.1, 1.7, 110.0),
    "red_onion": (40, 1.1, 9.3, 0.1, 1.7, 110.0),
    "green_onion": (32, 1.8, 7.3, 0.2, 2.6, 15.0),
    "garlic": (149, 6.4, 33.1, 0.5, 2.1, 3.0),               # per clove
    "ginger_root": (80, 1.8, 17.8, 0.8, 2.0, None),
    "tomato": (18, 0.9, 3.9, 0.2, 1.2, 123.0),
    "roma_tomato": (18, 0.9, 3.9, 0.2, 1.2, 62.0),
    "cherry_tomato": (18, 0.9, 3.9, 0.2, 1.2, 17.0),
    "carrot": (41, 0.9, 9.6, 0.2, 2.8, 61.0),
    "celery": (16, 0.7, 3.0, 0.2, 1.6, 40.0),
    "broccoli": (34, 2.8, 6.6, 0.4, 2.6, 148.0),
    "cauliflower": (25, 1.9, 5.0, 0.3, 2.0, 100.0),
    "green_bell_pepper": (20, 0.9, 4.6, 0.2, 1.7, 119.0),
    "red_bell_pepper": (31, 1.0, 6.0, 0.3, 2.1, 119.0),
    "yellow_bell_pepper": (27, 1.0, 6.3, 0.2, 0.9, 119.0),
    "spinach": (23, 2.9, 3.6, 0.4, 2.2, 30.0),
    "kale": (49, 4.3, 8.8, 0.9, 3.6, 67.0),
    "arugula": (25, 2.6, 3.7, 0.7, 1.6, 20.0),
    "romaine_lettuce": (17, 1.2, 3.3, 0.3, 2.1, 47.0),
    "iceberg_lettuce": (14, 0.9, 3.0, 0.1, 1.2, 89.0),
    "zucchini": (17, 1.2, 3.1, 0.3, 1.0, 196.0),
    "yellow_squash": (16, 1.2, 3.4, 0.2, 1.1, 196.0),
    "eggplant": (25, 1.0, 5.9, 0.2, 3.0, 82.0),
    "cucumber": (15, 0.7, 3.6, 0.1, 0.5, 133.0),
    "green_beans": (31, 1.8, 7.0, 0.2, 2.7, 110.0),
    "asparagus": (20, 2.2, 3.9, 0.1, 2.1, 134.0),
    "cabbage": (25, 1.3, 5.8, 0.1, 2.5, 89.0),
    "brussels_sprouts": (43, 3.4, 9.0, 0.3, 3.8, 88.0),
    "butternut_squash": (45, 1.0, 11.7, 0.1, 2.0, 205.0),
    "white_mushroom": (22, 3.1, 3.3, 0.3, 1.0, 70.0),
    "portobello_mushroom": (22, 2.1, 3.9, 0.4, 1.3, 84.0),
    "corn_on_cob": (86, 3.2, 19.0, 1.2, 2.7, 103.0),
    "avocado": (160, 2.0, 8.5, 14.7, 6.7, 150.0),
    "lemon": (29, 1.1, 9.3, 0.3, 2.8, 58.0),
    "lime": (30, 0.7, 10.5, 0.2, 2.8, 67.0),
    "jalapeno": (29, 0.9, 6.5, 0.4, 2.8, 14.0),
    "banana": (89, 1.1, 22.8, 0.3, 2.6, 118.0),
    "gala_apple": (52, 0.3, 13.8, 0.2, 2.4, 182.0),
    "honeycrisp_apple": (52, 0.3, 13.8, 0.2, 2.4, 200.0),
    "granny_smith_apple": (52, 0.3, 13.8, 0.2, 2.4, 182.0),
    "strawberry": (32, 0.7, 7.7, 0.3, 2.0, 144.0),
    "blueberry": (57, 0.7, 14.5, 0.3, 2.4, 148.0),
}


def _norm(name: str) -> str:
    return name.strip().lower()


async def seed_curated(db: AsyncSession) -> None:
    rows = (await db.execute(select(IngredientMaster))).scalars().all()
    by_canon = {_norm(r.canonical_name): r for r in rows}
    updated, missing = 0, []
    for canon, vals in CURATED.items():
        row = by_canon.get(_norm(canon))
        if row is None:
            missing.append(canon)
            continue
        kcal, p, c, f, fib, gpu = vals
        row.kcal_per_100g = kcal
        row.protein_g_per_100g = p
        row.carbs_g_per_100g = c
        row.fat_g_per_100g = f
        row.fiber_g_per_100g = fib
        if gpu is not None:
            row.grams_per_typical_unit = gpu
        row.nutrition_source = "curated"
        updated += 1
    await db.commit()
    print(f"curated: updated {updated} rows; {len(missing)} curated keys had no "
          f"matching ingredient row: {missing}")


# --------------------------------------------------------------------------- #
# USDA bulk pass (key-gated)
# --------------------------------------------------------------------------- #
USDA_SEARCH = "https://api.nal.usda.gov/fdc/v1/foods/search"
_MIN_CONFIDENCE = 0.45  # below this, defer to manual review rather than write


def _macros_from_food(food: dict) -> dict[str, float] | None:
    """Pull kcal/protein/carbs/fat/fiber per 100 g from a FDC food record.

    Energy is returned in BOTH kJ and kcal — select the KCAL entry (nutrient
    number 208 / unitName KCAL), never the kJ (number 268)."""
    out = {"kcal": None, "protein": None, "carbs": None, "fat": None, "fiber": None}
    for n in food.get("foodNutrients", []):
        num = str(n.get("nutrientNumber") or n.get("nutrient", {}).get("number") or "")
        unit = str(n.get("unitName") or n.get("nutrient", {}).get("unitName") or "").upper()
        val = n.get("value")
        if val is None:
            val = n.get("amount")
        if val is None:
            continue
        val = float(val)
        if num == "208" and unit == "KCAL" and out["kcal"] is None:
            out["kcal"] = val
        elif num == "203" and out["protein"] is None:
            out["protein"] = val
        elif num == "205" and out["carbs"] is None:
            out["carbs"] = val
        elif num == "204" and out["fat"] is None:
            out["fat"] = val
        elif num == "291" and out["fiber"] is None:
            out["fiber"] = val
    if out["kcal"] is None or out["protein"] is None:
        return None
    return out


def _confidence(query: str, description: str) -> float:
    """Token-overlap (Jaccard) between the query and the FDC description."""
    q = set(re.findall(r"[a-z0-9]+", query.lower()))
    d = set(re.findall(r"[a-z0-9]+", description.lower()))
    if not q or not d:
        return 0.0
    return len(q & d) / len(q | d)


async def bulk_usda(db: AsyncSession) -> None:
    key = settings.usda_fdc_api_key or "DEMO_KEY"
    if not settings.usda_fdc_api_key:
        print("USDA_FDC_API_KEY not set — using DEMO_KEY (heavily rate-limited). "
              "Set the key to backfill all rows.")
    rows = (
        (
            await db.execute(
                select(IngredientMaster).where(
                    IngredientMaster.kcal_per_100g.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    print(f"usda: {len(rows)} rows still missing macros")
    written, review = 0, []
    async with httpx.AsyncClient(timeout=30) as client:
        for row in rows:
            query = (row.display_name or row.canonical_name.replace("_", " ")).strip()
            try:
                resp = await client.post(
                    USDA_SEARCH,
                    params={"api_key": key},
                    json={
                        "query": query,
                        "pageSize": 5,
                        "dataType": ["SR Legacy", "Foundation"],
                    },
                )
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                review.append((row.canonical_name, f"request failed: {exc}"))
                continue
            foods = resp.json().get("foods", [])
            best = None
            best_conf = 0.0
            for food in foods:
                macros = _macros_from_food(food)
                if macros is None:
                    continue
                conf = _confidence(query, food.get("description", ""))
                if conf > best_conf:
                    best, best_conf = (food, macros), conf
            if best is None or best_conf < _MIN_CONFIDENCE:
                desc = best[0].get("description") if best else "(no usable match)"
                review.append((row.canonical_name, f"low conf {best_conf:.2f}: {desc}"))
                continue
            food, macros = best
            row.usda_fdc_id = food.get("fdcId")
            row.kcal_per_100g = macros["kcal"]
            row.protein_g_per_100g = macros["protein"]
            row.carbs_g_per_100g = macros["carbs"] or 0.0
            row.fat_g_per_100g = macros["fat"] or 0.0
            row.fiber_g_per_100g = macros["fiber"] or 0.0
            row.nutrition_source = "usda"
            written += 1
    await db.commit()
    print(f"usda: wrote {written} rows; {len(review)} need manual review:")
    for name, why in review:
        print(f"  - {name}: {why}")


async def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "curated"
    async with AsyncSessionLocal() as db:
        if mode in ("curated", "all"):
            await seed_curated(db)
        if mode in ("usda", "all"):
            await bulk_usda(db)
        # Coverage report.
        rows = (await db.execute(select(IngredientMaster))).scalars().all()
        have = sum(1 for r in rows if r.kcal_per_100g is not None)
        print(f"\ncoverage: {have}/{len(rows)} ingredient rows have macros "
              f"({100*have/len(rows):.0f}%)")


if __name__ == "__main__":
    asyncio.run(main())
