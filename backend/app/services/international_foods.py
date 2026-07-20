"""International-staple nutrition addendum (P43 B4).

Rows mined from the live H Mart / Patel Brothers flyer extractions plus the
nutrition_gap worklist: the protein-bearing items our recipes actually anchor
on but the master couldn't match (or matched without macros). Macros are
per-100g, USDA-equivalent (raw / as-purchased unless the food is only sold
prepared); ``grams_per_typical_unit`` follows the compute engine's contract —
grams per PIECE for count lines, per CUP for volume lines, per CONTAINER for
can/pack lines.

Upsert semantics (idempotent):
- new canonical_name → insert the full row (nutrition_source='curated')
- existing row      → fill ONLY missing macro columns, merge aliases
Run via ``scripts/seed_international_nutrition.py`` or the alembic data
migration (the prod channel).
"""

# (canonical, display, category, aliases, kcal, protein, carbs, fat, fiber, g_per_unit, unit)
ROWS: list[tuple] = [
    # --- Korean BBQ proteins ------------------------------------------------
    ("beef_short_ribs", "Beef short ribs (kalbi)", "meat",
     ["beef short ribs", "short ribs", "kalbi", "galbi", "la galbi",
      "bbq kalbi", "korean short ribs", "flanken ribs",
      "bbq chicken & pork kalbi"],
     320.0, 16.0, 3.0, 27.0, 0.0, 110.0, "lb"),
    ("bulgogi_beef", "Bulgogi (marinated beef)", "meat",
     ["bulgogi", "beef bulgogi", "marinated beef", "korean bbq beef",
      "korean bbq flavored beef", "bulgogi beef", "korean marinated beef"],
     190.0, 17.5, 6.5, 10.5, 0.3, 454.0, "lb"),
    ("pork_belly", "Pork belly", "meat",
     ["pork belly", "fresh pork belly", "samgyeopsal", "sliced pork belly"],
     518.0, 9.3, 0.0, 53.0, 0.0, 454.0, "lb"),
    ("beef_brisket", "Beef brisket", "meat",
     ["beef brisket", "brisket", "sliced brisket", "chadol", "chadolbaegi"],
     331.0, 14.7, 0.0, 29.9, 0.0, 454.0, "lb"),
    ("pork_luncheon_meat", "Pork luncheon meat", "meat",
     ["luncheon meat", "pork luncheon meat", "luncheon meat pork", "spam"],
     315.0, 13.0, 3.0, 27.0, 0.0, 340.0, "can"),
    # --- seafood ------------------------------------------------------------
    ("pollock_roe", "Seasoned pollock roe", "seafood",
     ["pollock roe", "seasoned pollock roe", "myeongnan", "myeongran",
      "mentaiko", "cod roe", "fish roe"],
     143.0, 22.3, 1.5, 6.4, 0.0, 30.0, "oz"),
    ("surf_clams", "Surf clams (hokkigai)", "seafood",
     ["surf clams", "surf clam", "hokkigai", "arctic surf clams",
      "wild arctic surf clams hokkigai", "clam meat", "clams"],
     86.0, 14.7, 3.6, 1.0, 0.0, 20.0, "lb"),
    ("squid", "Squid", "seafood",
     ["squid", "calamari", "fresh squid", "squid tubes", "whole squid"],
     92.0, 15.6, 3.1, 1.4, 0.0, 454.0, "lb"),
    ("dried_squid", "Seasoned dried squid", "seafood",
     ["dried squid", "seasoned dried squid", "spicy dried squid"],
     300.0, 60.0, 6.0, 4.0, 0.0, 43.0, "package"),
    ("salted_jellyfish", "Salted jellyfish", "seafood",
     ["salted jellyfish", "salted jelly fish", "jellyfish", "jellyfish salad"],
     36.0, 5.5, 0.1, 1.4, 0.0, 454.0, "lb"),
    ("canned_mackerel", "Canned mackerel", "seafood",
     ["canned mackerel", "mackerel canned", "canned mackerel pike",
      "canned saury", "saury"],
     156.0, 19.0, 0.0, 8.7, 0.0, 125.0, "can"),
    ("fish_cake", "Korean fish cake (eomuk)", "seafood",
     ["fish cake", "fish cakes", "eomuk", "odeng", "kamaboko",
      "korean fish cake", "fried fish cake"],
     155.0, 11.0, 16.0, 5.0, 0.3, 30.0, "piece"),
    # --- soy / dairy proteins ----------------------------------------------
    ("natto", "Natto (fermented soybeans)", "produce",
     ["natto", "fermented soybeans", "fermented soy beans"],
     212.0, 19.4, 12.7, 11.0, 5.4, 45.0, "package"),
    ("paneer", "Paneer", "dairy",
     ["paneer", "paneer cheese", "indian cottage cheese"],
     296.0, 20.0, 4.0, 22.0, 0.0, 200.0, "package"),
    # --- dumplings ----------------------------------------------------------
    ("pork_dumplings", "Pork dumplings (mandu)", "frozen",
     ["pork dumplings", "pork dumpling", "mandu", "pork mandu", "gyoza",
      "pork gyoza", "potstickers", "kimchi & pork dumpling",
      "pork & vegetable mandu", "umami gyoza dumplings", "dumplings"],
     220.0, 9.0, 25.0, 9.0, 1.2, 22.0, "piece"),
    ("vegetable_dumplings", "Vegetable dumplings", "frozen",
     ["vegetable dumplings", "veggie dumplings", "vegetable mandu",
      "vegetable gyoza"],
     180.0, 6.0, 30.0, 4.0, 1.8, 22.0, "piece"),
    # --- noodles / staples --------------------------------------------------
    ("udon", "Udon noodles (fresh)", "pantry",
     ["udon", "udon noodles", "fresh udon", "sanuki udon"],
     130.0, 3.5, 26.0, 0.5, 1.0, 200.0, "package"),
    ("rice_noodles", "Rice noodles (dry)", "pantry",
     ["rice noodles", "rice noodle", "rice vermicelli", "pad thai noodles",
      "pho noodles"],
     364.0, 5.9, 83.2, 0.6, 1.6, 90.0, "oz"),
    ("rice_cakes_tteok", "Korean rice cakes (tteok)", "pantry",
     ["rice cakes", "tteok", "tteokbokki rice cakes", "garaetteok",
      "topokki", "tteokbokki"],
     234.0, 4.0, 52.0, 0.5, 0.9, 150.0, "cup"),
    # --- ferments / condiments ---------------------------------------------
    ("kimchi", "Kimchi", "produce",
     ["kimchi", "napa kimchi", "cabbage kimchi", "baechu kimchi"],
     23.0, 1.1, 4.0, 0.5, 1.6, 150.0, "cup"),
    ("gochujang", "Gochujang", "sauce",
     ["gochujang", "korean chili paste", "red pepper paste",
      "gochujang paste"],
     185.0, 4.5, 39.0, 1.5, 2.5, 320.0, "tbsp"),
    ("fish_sauce", "Fish sauce", "sauce",
     ["fish sauce", "nam pla", "nuoc mam", "asian fish sauce"],
     35.0, 5.1, 3.6, 0.0, 0.0, 288.0, "tbsp"),
    # --- Patel Brothers dals ------------------------------------------------
    ("toor_dal", "Toor dal (split pigeon peas)", "pantry",
     ["toor dal", "tuvar dal", "arhar dal", "split pigeon peas"],
     343.0, 21.7, 62.8, 1.5, 15.0, 200.0, "cup"),
    ("chana_dal", "Chana dal (split chickpeas)", "pantry",
     ["chana dal", "split chickpeas", "bengal gram"],
     360.0, 21.5, 61.0, 5.3, 12.0, 200.0, "cup"),
    ("moong_dal", "Moong dal (split mung beans)", "pantry",
     ["moong dal", "mung dal", "split mung beans", "yellow moong dal"],
     347.0, 24.5, 59.0, 1.2, 8.0, 200.0, "cup"),
    ("urad_dal", "Urad dal (split black gram)", "pantry",
     ["urad dal", "split black gram", "black gram dal"],
     341.0, 25.2, 58.9, 1.6, 18.3, 200.0, "cup"),
]

UPSERT_SQL = """
INSERT INTO ingredient_master (
    canonical_name, display_name, category, common_aliases,
    kcal_per_100g, protein_g_per_100g, carbs_g_per_100g, fat_g_per_100g,
    fiber_g_per_100g, grams_per_typical_unit, typical_unit,
    nutrition_source, is_pantry_staple
) VALUES (
    :canonical, :display, :category, :aliases,
    :kcal, :protein, :carbs, :fat, :fiber, :gpu, :unit, 'curated', false
)
ON CONFLICT (canonical_name) DO UPDATE SET
    kcal_per_100g       = COALESCE(ingredient_master.kcal_per_100g, EXCLUDED.kcal_per_100g),
    protein_g_per_100g  = COALESCE(ingredient_master.protein_g_per_100g, EXCLUDED.protein_g_per_100g),
    carbs_g_per_100g    = COALESCE(ingredient_master.carbs_g_per_100g, EXCLUDED.carbs_g_per_100g),
    fat_g_per_100g      = COALESCE(ingredient_master.fat_g_per_100g, EXCLUDED.fat_g_per_100g),
    fiber_g_per_100g    = COALESCE(ingredient_master.fiber_g_per_100g, EXCLUDED.fiber_g_per_100g),
    grams_per_typical_unit = COALESCE(ingredient_master.grams_per_typical_unit, EXCLUDED.grams_per_typical_unit),
    nutrition_source    = COALESCE(ingredient_master.nutrition_source, 'curated'),
    common_aliases = (
        SELECT array_agg(DISTINCT a)
        FROM unnest(
            COALESCE(ingredient_master.common_aliases, '{}') || EXCLUDED.common_aliases
        ) AS a
    )
"""


def row_params(row: tuple) -> dict:
    c, d, cat, aliases, kcal, p, cb, f, fib, gpu, unit = row
    return {
        "canonical": c, "display": d, "category": cat, "aliases": aliases,
        "kcal": kcal, "protein": p, "carbs": cb, "fat": f, "fiber": fib,
        "gpu": gpu, "unit": unit,
    }
