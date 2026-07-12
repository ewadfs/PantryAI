"""Golden-batch regression fixture (Prompt 32 E).

A FROZEN fixture — snapshot pantry (~80 items), two saved stores with
realistic flyer deals (qualifier-laden meat names, non-anchor distractors),
diner profile (2000 cal target -> 1100-cal band; 160 g protein -> 54 g floor),
prior-batch history — that runs one full generation (Stage 1 -> critic ->
enforcement passes -> Stage 2 details) and prints: titles, anchors,
signatures, computed macros, critic scores, quality flags, and per-stage
models. Run it after any engine/prompt/env change and eyeball in 30 seconds.

Without ANTHROPIC_API_KEY (or with --stub) the Claude calls are served by a
deterministic stub, so every DETERMINISTIC guard is exercised end-to-end for
real: widened market-candidate pool, per-slot anchor assignment, within-batch
anchor diversity, sparse-store fallback, flyer-name matcher, title-diversity
enforcement, protein-floor fortification + anchor replacement, calorie-band
rebalance, and the amber honesty chips. With a key it runs live.

Usage (from backend/):
    .venv/bin/python scripts/golden_batch.py                  # both stores
    .venv/bin/python scripts/golden_batch.py --store lidl
    .venv/bin/python scripts/golden_batch.py --save-reference # update golden
"""

import argparse
import asyncio
import io
import json
import pathlib
import re
import sys
from contextlib import redirect_stdout
from datetime import date, timedelta, timezone
from datetime import datetime as dt
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.ai_cost import AICostEvent
from app.models.deal import CircularFetch, DealCache
from app.models.pantry import PantryItem
from app.models.recipe import Recipe, WeekRecipe
from app.models.store import StoreLocation, SupportedChain, UserStore
from app.models.user import User
from app.services import ingredient_matcher, recipe_engine

REFERENCE_PATH = pathlib.Path(__file__).with_name("golden_batch.reference.txt")

# --------------------------------------------------------------------------- #
# Frozen fixture data
# --------------------------------------------------------------------------- #
PROFILE = dict(
    name="Golden Fixture",
    calorie_target=2000,      # -> 55% band = 1100 cal/serving
    protein_target=160,       # -> floor = ceil(160/3) = 54 g/serving
    household_size=2,
    diet_type="omnivore",
    skill_level="intermediate",
    max_prep_time=45,
    cuisine_preferences=["mexican", "italian", "american"],
    allergies=[],
    excluded_ingredients=[],
    taste_notes=(
        "I love a hard char on meat and big Tex-Mex flavors; "
        "hate mushy vegetables."
    ),
    recipes_per_generation=5,
)

# ~80 pantry items. Exactly ONE viable dinner anchor — ground beef, 1 lb —
# which is also the OWNED PERISHABLE (P34):
# census=1 -> 4 of the 5 slots become market picks. Bacon is below anchor
# quantity and canned tuna is shelf-stable, so neither gets the guarantee.
PANTRY: list[tuple[str, str, str | None, str | None, bool]] = [
    # (name, category, quantity, unit, is_staple)
    ("ground beef", "meat", "1", "lb", False),
    ("bacon", "meat", "4", "oz", False),
    ("canned tuna", "canned", "1", "can", True),
    ("broccoli", "produce", "1", "head", False),
    ("carrots", "produce", "5", "each", False),
    ("celery", "produce", "4", "stalks", False),
    ("yellow onion", "produce", "3", "each", True),
    ("red onion", "produce", "1", "each", False),
    ("garlic", "produce", "2", "heads", True),
    ("ginger", "produce", "1", "knob", False),
    ("lime", "produce", "3", "each", False),
    ("lemon", "produce", "2", "each", False),
    ("jalapeno", "produce", "3", "each", False),
    ("cilantro", "produce", "1", "bunch", False),
    ("flat leaf parsley", "produce", "1", "bunch", False),
    ("green onions", "produce", "1", "bunch", False),
    ("romaine lettuce", "produce", "1", "head", False),
    ("spinach", "produce", "5", "oz", False),
    ("cherry tomatoes", "produce", "1", "pint", False),
    ("tomato", "produce", "2", "each", False),
    ("avocado", "produce", "1", "each", False),
    ("red bell pepper", "produce", "1", "each", False),
    ("zucchini", "produce", "1", "each", False),
    ("cucumber", "produce", "1", "each", False),
    ("baby potatoes", "produce", "1", "lb", False),
    ("mushrooms", "produce", "6", "oz", False),
    ("cheddar cheese", "dairy", "8", "oz", False),
    ("parmesan cheese", "dairy", "5", "oz", True),
    ("mozzarella", "dairy", "8", "oz", False),
    ("sour cream", "dairy", "1", "cup", False),
    ("greek yogurt", "dairy", "16", "oz", False),
    ("butter", "dairy", "8", "oz", True),
    ("whole milk", "dairy", "0.5", "gallon", True),
    ("heavy cream", "dairy", "0.5", "cup", False),
    ("cream cheese", "dairy", "4", "oz", False),
    ("flour tortillas", "grains", "6", "each", False),
    ("corn tortillas", "grains", "10", "each", False),
    ("white rice", "grains", "3", "lb", True),
    ("brown rice", "grains", "1", "lb", True),
    ("spaghetti", "grains", "1", "lb", True),
    ("rigatoni", "grains", "1", "lb", True),
    ("egg noodles", "grains", "12", "oz", True),
    ("quinoa", "grains", "1", "cup", True),
    ("panko breadcrumbs", "grains", "1", "cup", True),
    ("all purpose flour", "baking", "4", "lb", True),
    ("sugar", "baking", "2", "lb", True),
    ("brown sugar", "baking", "1", "lb", True),
    ("baking powder", "baking", "1", "can", True),
    ("cornstarch", "baking", "8", "oz", True),
    ("olive oil", "pantry", "500", "ml", True),
    ("vegetable oil", "pantry", "1", "qt", True),
    ("sesame oil", "pantry", "4", "oz", True),
    ("soy sauce", "pantry", "8", "oz", True),
    ("fish sauce", "pantry", "4", "oz", True),
    ("rice vinegar", "pantry", "8", "oz", True),
    ("apple cider vinegar", "pantry", "8", "oz", True),
    ("balsamic vinegar", "pantry", "4", "oz", True),
    ("dijon mustard", "pantry", "4", "oz", True),
    ("mayonnaise", "pantry", "12", "oz", True),
    ("ketchup", "pantry", "12", "oz", True),
    ("hot sauce", "pantry", "5", "oz", True),
    ("salsa", "pantry", "1", "jar", False),
    ("chipotle peppers in adobo", "canned", "1", "can", True),
    ("canned black beans", "canned", "2", "cans", True),
    ("canned chickpeas", "canned", "1", "can", True),
    ("canned diced tomatoes", "canned", "2", "cans", True),
    ("tomato paste", "canned", "1", "can", True),
    ("chicken broth", "canned", "1", "qt", True),
    ("coconut milk", "canned", "1", "can", True),
    ("peanut butter", "pantry", "1", "jar", True),
    ("honey", "pantry", "12", "oz", True),
    ("maple syrup", "pantry", "8", "oz", True),
    ("cumin", "spices", "1", "jar", True),
    ("smoked paprika", "spices", "1", "jar", True),
    ("chili powder", "spices", "1", "jar", True),
    ("dried oregano", "spices", "1", "jar", True),
    ("ground coriander", "spices", "1", "jar", True),
    ("cinnamon", "spices", "1", "jar", True),
    ("bay leaves", "spices", "1", "jar", True),
    ("red pepper flakes", "spices", "1", "jar", True),
    ("kosher salt", "spices", "1", "box", True),
    ("black peppercorns", "spices", "1", "jar", True),
]

# Deals: (product_name, brand, sale, regular, unit, category)
# Lidl is deliberately SPARSE in anchors (2 proteins + the $2.49 cauliflower):
# with census=1 -> 4 market slots, slot 4 must fall back to Stop & Shop —
# the sparse-store path. Meat names carry the real-world qualifier noise that
# starved the matcher before the 32-3c normalizer.
LIDL_DEALS = [
    ("Fresh Boneless Skinless Chicken Breast Family Pack", None, "1.99", "3.99", "lb", "meat"),
    ("80% Lean Ground Beef Value Pack", None, "3.49", "4.99", "lb", "meat"),
    ("Cauliflower", None, "2.49", "2.99", "each", "produce"),
    ("Honeycrisp Apples", None, "1.49", "1.99", "lb", "produce"),
    ("Greek Yogurt 32 oz", "Lidl", "3.99", None, "each", "dairy"),
    ("Seafood Breading Mix", None, "2.99", None, "each", "seafood"),   # non-anchor
    ("Zesty Chicken Marinade", None, "2.49", None, "each", "meat"),    # non-anchor
    ("Shredded Mozzarella", None, "2.19", "2.99", "each", "dairy"),
]
SNS_DEALS = [
    ("Fresh Atlantic Salmon Fillets", None, "7.99", "12.99", "lb", "seafood"),
    ("Boneless Pork Loin Chops Value Pack", None, "2.49", "3.99", "lb", "meat"),
    ("Ground Turkey 93% Lean", "Nature's Promise", "3.99", None, "lb", "meat"),
    ("USDA Choice Boneless New York Strip Steak Value Pack", None, "6.99", "12.99", "lb", "meat"),
    ("Perdue Boneless Skinless Chicken Thighs Family Pack", "Perdue", "2.29", "3.49", "lb", "meat"),
    ("Cauliflower", None, "2.79", None, "each", "produce"),
    ("Sweet Potatoes", None, "0.89", "1.29", "lb", "produce"),
    ("Broccoli Crowns", None, "1.79", "2.49", "lb", "produce"),
    ("Seafood Salad Kit", None, "4.99", None, "each", "seafood"),      # non-anchor
]

# Prior batch (2h ago, unsaved -> soft negative) — the regression's history:
# three 'Charred Cauliflower' dishes. Feeds RECENTLY SHOWN + rotation + the
# P33 ingredient-overlap pool (full ingredient lists included).
PRIOR_BATCH = [
    ("Charred Cauliflower Power Bowl", "cauliflower", "bowl", "mediterranean",
     ["cauliflower", "white rice", "canned chickpeas", "greek yogurt",
      "red onion", "cucumber", "cherry tomatoes", "tahini", "olive oil"]),
    ("Charred Cauliflower Shawarma Wraps", "cauliflower", "wrap",
     "middle-eastern",
     ["cauliflower", "flour tortillas", "greek yogurt", "romaine lettuce",
      "red onion", "cucumber", "tahini"]),
    ("Charred Cauliflower Curry", "cauliflower", "curry", "indian",
     ["cauliflower", "coconut milk", "canned chickpeas", "yellow onion",
      "ginger", "white rice", "cilantro"]),
    # Recent BEEF dish sharing anchor+cuisine with tonight's beef tacos: the
    # P34 recency exemption must let beef anchor again (different dish).
    ("Charred Beef Fajita Bowls", "ground beef", "bowl", "mexican",
     ["ground beef", "white rice", "red bell pepper", "yellow onion",
      "lime", "salsa"]),
]
LOVED = ("Skillet Chicken Fajitas", "skillet", "mexican", 1)
PASSED = ("Quinoa Stuffed Peppers", "bake", "american", -1)

# Saved to THIS week (P33 B3c + carve-out B6): its purchase-needed ground beef
# is a planned shared purchase, so beef overlap in the new batch stays legal.
SAVED_WEEK = (
    "Weeknight Beef Ragu Rigatoni",
    "pasta", "italian",
    [
        {"name": "ground beef", "generic_name": "ground beef",
         "quantity": "1", "unit": "lb", "in_pantry": False},
        {"name": "rigatoni", "generic_name": "rigatoni",
         "quantity": "1", "unit": "lb", "in_pantry": True},
        {"name": "canned diced tomatoes", "generic_name": "canned diced tomatoes",
         "quantity": "2", "unit": "cans", "in_pantry": True},
        {"name": "yellow onion", "generic_name": "yellow onion",
         "quantity": "1", "unit": "each", "in_pantry": True},
        {"name": "parmesan cheese", "generic_name": "parmesan cheese",
         "quantity": "2", "unit": "oz", "in_pantry": True},
    ],
)

# VERIFY-1 fixture: the two live cauliflower-bowl clones, fed through the
# checker RAW (no carve-outs — neither was a designated anchor when it shipped).
CLONE_A = ("Charred Cauliflower Power Bowl", "cauliflower",
           ["cauliflower", "white rice", "canned chickpeas", "greek yogurt",
            "red onion", "cucumber", "cherry tomatoes", "tahini", "olive oil",
            "kosher salt"])
CLONE_B = ("Roasted Cauliflower & Chickpea Bowl", "cauliflower",
           ["cauliflower", "white rice", "canned chickpeas", "greek yogurt",
            "red onion", "cucumber", "romaine lettuce", "tahini", "olive oil",
            "smoked paprika"])

STORES = {
    "lidl": ("lidl", "Lidl — Golden Fixture", "lidl:GOLDEN"),
    "stop_and_shop": ("stop_and_shop", "Stop & Shop — Golden Fixture",
                      "stop_and_shop:GOLDEN"),
}


# --------------------------------------------------------------------------- #
# Deterministic stub Anthropic client
# --------------------------------------------------------------------------- #
def _usage():
    return SimpleNamespace(
        input_tokens=1800, output_tokens=600,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )


def _msg(payload: dict):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
        usage=_usage(),
    )


_FORMATS = ["skillet", "burgers", "roast", "sheet-pan", "stir-fry"]
_CUISINES = ["italian", "american", "tex-mex", "asian", "mediterranean"]
_RETITLE_SUFFIX = ["Weeknight Supper", "Family Feast", "Harvest Plate",
                   "Table Special"]


class _StubMessages:
    """Deterministic responses keyed off the prompt — simulates a model that
    obeys anchor assignments but (like the live regression) leaks the taste
    notes into naming ('Charred …' on every title) and needs the floor/band
    corrections on two dishes. Every deterministic guard then runs for real."""

    def __init__(self):
        self.prompts: list[tuple[str, str]] = []  # (system_text, user_text)

    async def create(self, *, model, max_tokens, system, messages, **kw):
        sys_text = (
            system if isinstance(system, str)
            else "".join(b.get("text", "") for b in system)
        )
        user = messages[0]["content"]
        self.prompts.append((sys_text, user))

        if "culinary reviewer" in sys_text:
            return _msg(self._critic(user))
        if "retitle dinner concepts" in sys_text:
            return _msg(self._retitle(user))
        if "CONCEPT to write in full" in user:
            return _msg(self._detail(user, sys_text))
        if "ANCHOR CAP VIOLATION" in user:
            return _msg({"recipes": [self._anchor_cap_regen()]})
        if "INGREDIENT OVERLAP VIOLATION" in user:
            return _msg({"recipes": [self._overlap_regen(user)]})
        if "VARIETY VIOLATION" in user:
            return _msg({"recipes": [self._variety_regen(user)]})
        if "MARKET ANCHOR COLLISION" in user:
            return _msg({"recipes": [self._market_concept_from_correction(user)]})
        if "Propose tonight's" in user:
            return _msg(self._concepts(user))
        # variety / contract / critic regen fallbacks: echo one safe concept
        return _msg({"recipes": [self._pantry_concept("medium")]})

    @staticmethod
    def _pin_name(text: str) -> str | None:
        m = re.search(r"ALL of them \(not a garnish\): (.+?) \(have", text)
        return m.group(1).strip() if m else None

    @staticmethod
    def _veg_for(anchor: str) -> str:
        """Distinct supporting veg per anchor so market concepts don't clone
        each other's sides (the live model varies these; the stub must too)."""
        a = anchor.lower()
        if "salmon" in a:
            return "spinach"
        if "beef" in a:
            return "red bell pepper"
        if "pork" in a:
            return "zucchini"
        if "turkey" in a:
            return "red bell pepper"
        if "cauliflower" in a:
            return "greek yogurt"
        if "steak" in a or "strip" in a:
            return "green beans"
        return "broccoli"

    # -- Stage 1 ---------------------------------------------------------- #
    def _concepts(self, user: str) -> dict:
        n = int(re.search(r"propose exactly (\d+) concepts", user).group(1))
        picks = re.findall(
            r"- MARKET PICK \d+: build around (.+?): \$([0-9.]+)(?:/(\w+))?"
            r"((?: — at [^\n\[]+)?)", user,
        )
        tiers = []
        mix = re.search(r"difficulty mix:\s*([^\n]+)", user).group(1)
        for count, tier in re.findall(r"(\d+)\s+(easy|medium|hard)", mix):
            tiers += [tier] * int(count)
        tiers = (tiers + ["medium"] * n)[:n]
        pin = self._pin_name(user)

        def with_pin(keys: list[dict]) -> list[dict]:
            if pin:
                keys = keys + [{"generic_name": pin, "brand": None,
                                "in_pantry": True, "on_sale": False,
                                "sale_price": None}]
            return keys

        # Pantry concepts fill only the NON-market slots; the perishable slot
        # honors the OWNED-PERISHABLE block. Deliberately appended LAST so the
        # engine's feed sort (P34 B5) has to move the $0 dish to the front.
        per = re.search(
            r"OWNED-PERISHABLE SLOT — the user already owns (.+?) \(HAVE", user
        )
        per_name = per.group(1).strip() if per else None
        per_use_soon = "flagged USE SOON" in user
        pantry_needed = max(n - len(picks[:n]), 0)
        pantry_concepts = []
        for j in range(pantry_needed):
            if per_name and j == 0:
                c = self._perishable_concept(per_name, per_use_soon, tiers[0])
            else:
                c = self._fallback_pantry_concept(tiers[0])
            c["key_ingredients"] = with_pin(c["key_ingredients"])
            pantry_concepts.append(c)

        if not picks and n >= 4:
            # The live regression facsimile: no market assignments -> the
            # model piles the whole batch onto the one pantry anchor, legally
            # rotating format/cuisine ("two axes changed"). The engine's
            # anchor cap must break this up.
            regression = self._regression_batch(n, tiers)
            for c in regression:
                c["key_ingredients"] = with_pin(c["key_ingredients"])
            return {"recipes": regression}

        recipes = []
        for k, (name, price, unit, at_tail) in enumerate(picks[: n - len(pantry_concepts)]):
            clean = ingredient_matcher.normalize_flyer_name(name) or name
            store_note = at_tail.replace(" — at ", " at ").strip()
            fmt = _FORMATS[k % len(_FORMATS)]
            veg = self._veg_for(clean)
            recipes.append({
                "title": f"Charred {clean} {fmt.title()}",
                "description": f"{clean} {fmt} over pantry sides.",
                "difficulty": tiers[min(k + 1, n - 1)],
                "prep_time_min": 15, "cook_time_min": 25, "total_time_min": 40,
                "servings": 2,
                "why_this_recipe": (
                    f"Built around {clean.lower()} at ${price}"
                    f"{'/' + unit if unit else ''} this week"
                    + (f" — {store_note}" if store_note else "") + "."
                ),
                "cuisine": _CUISINES[k % len(_CUISINES)],
                "dish_format": fmt,
                "anchor_ingredient": clean.lower(),
                # The regression leaked the taste notes into flavor too: the
                # first three market picks all lead with the same rub — the
                # P33 flavor-lead cap must catch the third.
                "flavor_lead": (
                    ["smoked paprika rub"] if k < 3 else ["lemon-dill"]
                ),
                "market_pick": True,
                "tags": ["market pick"],
                "nutrition_per_serving": {"calories": 650, "protein_g": 55},
                "key_ingredients": with_pin([
                    {"generic_name": clean.lower(), "brand": None,
                     "in_pantry": False, "on_sale": True, "sale_price": price},
                    {"generic_name": "white rice", "brand": None,
                     "in_pantry": True, "on_sale": False, "sale_price": None},
                    {"generic_name": veg, "brand": None,
                     "in_pantry": True, "on_sale": False, "sale_price": None},
                    {"generic_name": "olive oil", "brand": None,
                     "in_pantry": True, "on_sale": False, "sale_price": None},
                ]),
            })
        recipes += pantry_concepts
        return {"recipes": recipes[:n]}

    def _variety_regen(self, user: str) -> dict:
        """Keep the anchor, change format + cuisine — like the live model."""
        m = re.search(r"Concept that repeats:\n(\{.*\})\n\nReturn", user, re.S)
        brief = json.loads(m.group(1)) if m else {}
        c = self._overlap_regen(user)  # same reconstruction shape
        c["title"] = f"{(brief.get('anchor_ingredient') or 'Dinner').title()} Casserole Night"
        c["dish_format"] = "casserole"
        c["cuisine"] = "spanish"
        c["flavor_lead"] = brief.get("flavor_lead") or ["smoked paprika rub"]
        c["why_this_recipe"] = "Reworked format for variety."
        return c

    _REGRESSION = [
        ("BBQ Beef & Pinto Bean Stuffed Rolls", "sandwich", "american",
         ["bbq rub"], ["canned pinto beans", "flour tortillas", "cheddar cheese"]),
        ("Carne Asada Charred Beef & Black Bean Pasta Bake", "bake", "italian",
         ["carne asada seasoning"],
         ["canned black beans", "rigatoni", "mozzarella"]),
        ("Seven-Spice Charred Beef & Chickpea Stuffed Rolls", "rolls",
         "middle-eastern", ["seven-spice"],
         ["canned chickpeas", "flour tortillas", "greek yogurt"]),
        ("Carne Asada Beef & Canned Tomato Pasta Bake", "pasta", "tex-mex",
         ["carne asada seasoning"],
         ["canned diced tomatoes", "spaghetti", "sour cream"]),
        ("Togarashi-Sesame Beef & Egg Stir-Fry with Charred Zucchini",
         "stir-fry", "asian", ["togarashi-sesame"],
         ["zucchini", "white rice", "soy sauce"]),
    ]

    def _regression_batch(self, n: int, tiers: list) -> list:
        out = []
        for k, (title, fmt, cuisine, lead, extras) in enumerate(
            self._REGRESSION[:n]
        ):
            out.append({
                "title": title,
                "description": "Another spin on the pantry beef.",
                "difficulty": tiers[min(k, n - 1)],
                "prep_time_min": 10, "cook_time_min": 20, "total_time_min": 30,
                "servings": 2,
                "why_this_recipe": (
                    "Same beef shelf, but format and cuisine differ — two "
                    "axes changed."
                ),
                "cuisine": cuisine,
                "dish_format": fmt,
                "anchor_ingredient": "ground beef",
                "flavor_lead": lead,
                "market_pick": False,
                "tags": ["pantry-first"],
                "nutrition_per_serving": {"calories": 670, "protein_g": 55},
                "key_ingredients": [
                    {"generic_name": "ground beef", "brand": None,
                     "in_pantry": True, "on_sale": False, "sale_price": None},
                ] + [
                    {"generic_name": x, "brand": None, "in_pantry": True,
                     "on_sale": False, "sale_price": None}
                    for x in extras
                ],
            })
        return out

    _FALLBACKS = [
        ("Cheesy Rigatoni Marinara", "rigatoni", "pasta", "italian",
         ["garlic-oregano marinara"],
         ["canned diced tomatoes", "mozzarella", "parmesan cheese"]),
        ("Smoky Black Bean Chili", "canned black beans", "stew", "tex-mex",
         ["chipotle-cumin"],
         ["canned diced tomatoes", "yellow onion", "chipotle peppers in adobo"]),
        ("Crispy Potato & Bacon Hash", "baby potatoes", "skillet", "american",
         ["black pepper & thyme"], ["bacon", "yellow onion", "cheddar cheese"]),
        ("Charred Broccoli & Chickpea Roast", "broccoli", "roast",
         "mediterranean", ["lemon-garlic"],
         ["canned chickpeas", "lemon", "parmesan cheese"]),
    ]
    _cap_i = 0

    def _anchor_cap_regen(self) -> dict:
        title, anchor, fmt, cuisine, lead, extras = self._FALLBACKS[
            self._cap_i % len(self._FALLBACKS)
        ]
        self._cap_i += 1
        return {
            "title": title,
            "description": "A different pantry anchor entirely.",
            "difficulty": "medium",
            "prep_time_min": 10, "cook_time_min": 25, "total_time_min": 35,
            "servings": 2,
            "why_this_recipe": f"Anchored on {anchor} instead — no beef here.",
            "cuisine": cuisine,
            "dish_format": fmt,
            "anchor_ingredient": anchor,
            "flavor_lead": lead,
            "market_pick": False,
            "tags": ["pantry-first"],
            "nutrition_per_serving": {"calories": 620, "protein_g": 55},
            "key_ingredients": [
                {"generic_name": anchor, "brand": None, "in_pantry": True,
                 "on_sale": False, "sale_price": None},
            ] + [
                {"generic_name": x, "brand": None, "in_pantry": True,
                 "on_sale": False, "sale_price": None}
                for x in extras
            ],
        }

    def _overlap_regen(self, user: str) -> dict:
        """P33 regen: same dish, new seasoning direction (the correction asks
        for a different seasoning family; anchor stays)."""
        m = re.search(
            r"Concept (?:to replace|that repeats):\n(\{.*\})\n\nReturn", user, re.S
        )
        brief = json.loads(m.group(1)) if m else {}
        keys = [
            {"generic_name": str(k), "brand": None, "in_pantry": True,
             "on_sale": False, "sale_price": None}
            for k in (brief.get("key_ingredients") or [])
        ]
        return {
            "title": brief.get("title") or "Rebalanced Dinner",
            "description": brief.get("description"),
            "difficulty": brief.get("difficulty") or "medium",
            "prep_time_min": 15, "cook_time_min": 25, "total_time_min": 40,
            "servings": 2,
            "why_this_recipe": (
                "Same anchor, new seasoning direction for batch variety."
            ),
            "cuisine": brief.get("cuisine"),
            "dish_format": brief.get("dish_format"),
            "anchor_ingredient": brief.get("anchor_ingredient"),
            "flavor_lead": ["ginger & scallion"],
            "market_pick": True,
            "tags": ["market pick"],
            "nutrition_per_serving": {"calories": 650, "protein_g": 55},
            "key_ingredients": keys,
        }

    def _pantry_concept(self, tier: str) -> dict:
        return self._perishable_concept("ground beef", False, tier)

    def _perishable_concept(self, name: str, use_soon: bool, tier: str) -> dict:
        if use_soon:
            # Different dish from the recent taco batch — and NO urgency line,
            # so the engine's deterministic prepend (P34 A4) is exercised.
            return {
                "title": "Seared Beef & Green Beans Stir-Fry",
                "description": "Hot wok beef, snappy green beans, garlic rice.",
                "difficulty": tier,
                "prep_time_min": 10, "cook_time_min": 15, "total_time_min": 25,
                "servings": 2,
                "why_this_recipe": "A fast wok dinner from your kitchen.",
                "cuisine": "asian",
                "dish_format": "stir-fry",
                "anchor_ingredient": name,
                "flavor_lead": ["ginger-garlic"],
                "market_pick": False,
                "tags": ["pantry-first"],
                "nutrition_per_serving": {"calories": 640, "protein_g": 58},
                "key_ingredients": [
                    {"generic_name": name, "brand": None,
                     "in_pantry": True, "on_sale": False, "sale_price": None},
                    {"generic_name": "green beans", "brand": None,
                     "in_pantry": False, "on_sale": False, "sale_price": None},
                    {"generic_name": "white rice", "brand": None,
                     "in_pantry": True, "on_sale": False, "sale_price": None},
                    {"generic_name": "soy sauce", "brand": None,
                     "in_pantry": True, "on_sale": False, "sale_price": None},
                ],
            }
        # The 1104-cal taco fixture: claims heavy at concept stage already.
        return {
            "title": "Smoky Beef Street Tacos",
            "description": "Hard-seared beef, toasted tortillas, lime crema.",
            "difficulty": tier,
            "prep_time_min": 15, "cook_time_min": 20, "total_time_min": 35,
            "servings": 2,
            "why_this_recipe": "Uses the ground beef already in your kitchen.",
            "cuisine": "mexican",
            "dish_format": "tacos",
            "anchor_ingredient": name,
            "flavor_lead": ["carne asada seasoning"],
            "market_pick": False,
            "tags": ["pantry-first"],
            "nutrition_per_serving": {"calories": 1104, "protein_g": 62},
            "key_ingredients": [
                {"generic_name": name, "brand": None,
                 "in_pantry": True, "on_sale": False, "sale_price": None},
                {"generic_name": "flour tortillas", "brand": None,
                 "in_pantry": True, "on_sale": False, "sale_price": None},
                {"generic_name": "cheddar cheese", "brand": None,
                 "in_pantry": True, "on_sale": False, "sale_price": None},
                {"generic_name": "sour cream", "brand": None,
                 "in_pantry": True, "on_sale": False, "sale_price": None},
            ],
        }

    def _fallback_pantry_concept(self, tier: str) -> dict:
        return {
            "title": "Cheesy Rigatoni Marinara",
            "description": "Baked rigatoni, marinara, two cheeses.",
            "difficulty": tier,
            "prep_time_min": 10, "cook_time_min": 30, "total_time_min": 40,
            "servings": 2,
            "why_this_recipe": "All from the pantry shelf.",
            "cuisine": "italian",
            "dish_format": "pasta",
            "anchor_ingredient": "rigatoni",
            "flavor_lead": ["garlic-oregano marinara"],
            "market_pick": False,
            "tags": ["pantry-first"],
            "nutrition_per_serving": {"calories": 620, "protein_g": 55},
            "key_ingredients": [
                {"generic_name": "rigatoni", "brand": None,
                 "in_pantry": True, "on_sale": False, "sale_price": None},
                {"generic_name": "canned diced tomatoes", "brand": None,
                 "in_pantry": True, "on_sale": False, "sale_price": None},
                {"generic_name": "mozzarella", "brand": None,
                 "in_pantry": True, "on_sale": False, "sale_price": None},
                {"generic_name": "parmesan cheese", "brand": None,
                 "in_pantry": True, "on_sale": False, "sale_price": None},
            ],
        }

    def _market_concept_from_correction(self, user: str) -> dict:
        m = re.search(r"assigned deal instead: (.+?): \$([0-9.]+)", user)
        clean = ingredient_matcher.normalize_flyer_name(m.group(1)) if m else "salmon"
        return {**self._pantry_concept("medium"),
                "title": f"{clean.title()} Sheet-Pan Supper",
                "anchor_ingredient": clean.lower(), "market_pick": True,
                "dish_format": "sheet-pan", "cuisine": "american",
                "flavor_lead": ["lemon-dill"],
                "why_this_recipe": f"Built around {clean.lower()} on sale this week."}

    # -- critic + retitle --------------------------------------------------- #
    def _critic(self, user: str) -> dict:
        m = re.search(r"CONCEPTS \(0-indexed\):\n(\[.*?\])\n\n", user, re.S)
        count = len(json.loads(m.group(1))) if m else 5
        return {"reviews": [
            {"index": i, "score": 8, "verdict": "ship",
             "fail_rubrics": [], "worst_issues": []}
            for i in range(count)
        ]}

    def _retitle(self, user: str) -> dict:
        m = re.search(r"(\[.*?\])\n\nReturn ONLY", user, re.S)
        briefs = json.loads(m.group(1)) if m else []
        banned = set()
        bm = re.search(r"Banned words[^:]*: ([^\n.]+)", user)
        if bm:
            banned = {w.strip().lower() for w in bm.group(1).split(",")}
        out = []
        for k, b in enumerate(briefs):
            anchor = (b.get("anchor_ingredient") or "dinner").title()
            title = f"{anchor} {_RETITLE_SUFFIX[k % len(_RETITLE_SUFFIX)]}"
            if any(w in title.lower() for w in banned):
                title = f"{anchor} Supper No. {k + 1}"
            out.append({"index": b.get("index"), "title": title})
        return {"titles": out}

    # -- Stage 2 ------------------------------------------------------------ #
    def _detail(self, user: str, sys_text: str = "") -> dict:
        keys = ""
        km = re.search(r"Key ingredients: ([^\n]+)", user)
        if km:
            keys = km.group(1).lower()
        wants_rebalance = "CORRECTION — CALORIE BAND" in user
        wants_protein = "CORRECTION — PROTEIN FLOOR" in user

        if "tortilla" in keys:
            out = self._taco_detail(trimmed=wants_rebalance)
        elif "green beans" in keys and "beef" in keys:
            out = self._stirfry_detail()
        elif "cauliflower" in keys:
            out = self._cauliflower_detail(fortified=wants_protein)
        else:
            anchor = keys.split(",")[0].strip() if keys else "chicken breast"
            out = self._protein_detail(anchor)
        # The detail system prompt carries the pin block — honor it, like the
        # live model would (P33 carve-out proof: the pin rides in all 5).
        pin = self._pin_name(sys_text)
        if pin and not any(
            i.get("generic_name") == pin for i in out["ingredients"]
        ):
            out["ingredients"].append(
                self._ing(pin, "8", "each", in_pantry=True)
            )
        return out

    @staticmethod
    def _ing(name, qty, unit, in_pantry=False, on_sale=False, price=None):
        return {"generic_name": name, "brand": None, "quantity": qty,
                "unit": unit, "in_pantry": in_pantry, "on_sale": on_sale,
                "sale_price": price}

    def _taco_detail(self, trimmed: bool) -> dict:
        ings = [
            self._ing("ground beef", "1", "lb", in_pantry=True),
            self._ing("flour tortillas", "6", "each", in_pantry=True),
            self._ing("cheddar cheese", "4", "oz", in_pantry=True),
            self._ing("sour cream", "1", "cup", in_pantry=True),
            self._ing("olive oil", "3", "tbsp", in_pantry=True),
        ]
        if not trimmed:
            ings += [
                self._ing("yellow onion", "1", "each", in_pantry=True),
                self._ing("lime", "1", "each", in_pantry=True),
            ]
        return {
            "ingredients": ings,
            "instructions": [
                "Season the beef with chili powder and smoked paprika.",
                "Sear hard in oil without stirring for a deep crust; break up.",
                "Char tortillas over the flame.",
                "Assemble with cheddar and lime crema.",
            ],
            "nutrition_per_serving": {"calories": 1104, "protein_g": 62,
                                      "carbs_g": 48, "fat_g": 70, "fiber_g": 4},
        }

    def _stirfry_detail(self) -> dict:
        return {
            "ingredients": [
                self._ing("ground beef", "1", "lb", in_pantry=True),
                self._ing("green beans", "12", "oz", in_pantry=False),
                self._ing("white rice", "1", "cup", in_pantry=True),
                self._ing("soy sauce", "2", "tbsp", in_pantry=True),
                self._ing("olive oil", "1", "tbsp", in_pantry=True),
            ],
            "instructions": [
                "Get the wok ripping hot; sear the beef in a thin layer.",
                "Blister the green beans; add garlic and ginger late.",
                "Toss with soy and serve over rice.",
            ],
            "nutrition_per_serving": {"calories": 940, "protein_g": 56,
                                      "carbs_g": 78, "fat_g": 34, "fiber_g": 6},
        }

    def _cauliflower_detail(self, fortified: bool) -> dict:
        ings = [
            self._ing("cauliflower", "1.5", "lb", on_sale=True, price="2.49"),
            self._ing("white rice", "1", "cup", in_pantry=True),
            self._ing("greek yogurt", "0.5", "cup", in_pantry=True),
            self._ing("olive oil", "1", "tbsp", in_pantry=True),
        ]
        out = {
            "ingredients": ings,
            "instructions": [
                "Slice cauliflower into thick steaks; season generously.",
                "Roast hot until deeply browned at the edges.",
                "Serve over rice with the yogurt sauce.",
            ],
            "nutrition_per_serving": {"calories": 542, "protein_g": 19,
                                      "carbs_g": 62, "fat_g": 14, "fiber_g": 6},
        }
        if fortified:
            out["ingredients"] = ings + [
                self._ing("chicken breast", "8", "oz", on_sale=True, price="1.99"),
            ]
            out["revised_title"] = "Golden Cauliflower with Crispy Cutlets"
            out["instructions"].insert(1, "Sear the chicken breast alongside.")
            out["nutrition_per_serving"] = {"calories": 678, "protein_g": 44,
                                            "carbs_g": 62, "fat_g": 18,
                                            "fiber_g": 6}
        return out

    def _protein_detail(self, anchor: str) -> dict:
        a = anchor.lower()
        if "salmon" in a:
            protein = self._ing("salmon fillet", "1.25", "lb", on_sale=True, price="7.99")
        elif "breast" in a or ("chicken" in a and "thigh" not in a):
            protein = self._ing("chicken breast", "1.5", "lb", on_sale=True, price="1.99")
        elif "beef" in a:
            protein = self._ing("ground beef", "1.25", "lb", on_sale=True, price="3.49")
        elif "pork" in a:
            protein = self._ing("pork loin chops", "1.25", "lb", on_sale=True, price="2.49")
        elif "turkey" in a:
            protein = self._ing("ground turkey", "1.25", "lb", on_sale=True, price="3.99")
        elif "thigh" in a:
            protein = self._ing("chicken thighs", "1.5", "lb", on_sale=True, price="2.29")
        else:  # e.g. the unmatched New York strip: est-fallback path
            protein = self._ing(a, "1.5", "lb", on_sale=True, price="6.99")
        side = (
            self._ing("canned black beans", "1", "can", in_pantry=True)
            if "beef" in a
            else self._ing("white rice", "1", "cup", in_pantry=True)
        )
        veg_by_name = {
            "spinach": self._ing("spinach", "5", "oz", in_pantry=True),
            "red bell pepper": self._ing("red bell pepper", "2", "each", in_pantry=True),
            "zucchini": self._ing("zucchini", "2", "each", in_pantry=True),
            "green beans": self._ing("green beans", "12", "oz", in_pantry=False),
        }
        veg = veg_by_name.get(
            self._veg_for(a), self._ing("broccoli", "1", "lb", in_pantry=True)
        )
        return {
            "ingredients": [
                protein, side, veg,
                self._ing("olive oil", "1", "tbsp", in_pantry=True),
            ],
            "instructions": [
                f"Pat the {a} dry and season simply.",
                "Sear hard over high heat for a deep crust; don't crowd the pan.",
                "Cook the side; steam the broccoli crisp-tender.",
                "Rest the protein, slice, and plate.",
            ],
            "nutrition_per_serving": {"calories": 760, "protein_g": 58,
                                      "carbs_g": 55, "fat_g": 28, "fiber_g": 7},
        }


class StubAnthropic:
    def __init__(self, api_key=None):
        self.messages = _StubMessages()


# --------------------------------------------------------------------------- #
# Fixture seeding (idempotent — wipes and recreates the fixture users' data)
# --------------------------------------------------------------------------- #
async def _get_or_create_location(db, slug: str) -> StoreLocation:
    chain_slug, store_name, region_key = STORES[slug]
    chain = (
        await db.execute(
            select(SupportedChain).where(SupportedChain.chain_slug == chain_slug)
        )
    ).scalar_one()
    loc = (
        await db.execute(
            select(StoreLocation).where(
                StoreLocation.chain_id == chain.id,
                StoreLocation.store_name == store_name,
            )
        )
    ).scalar_one_or_none()
    if loc is None:
        loc = StoreLocation(
            chain_id=chain.id, store_name=store_name, region_key=region_key
        )
        db.add(loc)
        await db.flush()
    return loc


async def _seed_deals(db, slug: str, rows) -> tuple[int, int]:
    """Insert this store's flyer, matching names the OLD way (raw matcher) to
    mirror the production cache, then re-match with the P32 flyer normalizer —
    returns (matched_before, matched_after) for the report."""
    chain_slug, _name, region_key = STORES[slug]
    chain = (
        await db.execute(
            select(SupportedChain).where(SupportedChain.chain_slug == chain_slug)
        )
    ).scalar_one()
    # Mirror production: these chains have working circular sources.
    chain.deals_status = "active"
    await db.execute(delete(DealCache).where(DealCache.region_key == region_key))
    today = date.today()
    fetch = CircularFetch(
        chain_id=chain.id, fetch_date=today, status="success", page_count=1,
        valid_from=today - timedelta(days=1), valid_to=today + timedelta(days=5),
        region_key=region_key,
    )
    db.add(fetch)
    await db.flush()

    before = after = 0
    for name, brand, sale, regular, unit, category in rows:
        raw_iid, raw_conf = ingredient_matcher.match_ingredient(name)
        fly_iid, fly_conf = ingredient_matcher.match_flyer_name(name, brand)
        before += raw_iid is not None
        after += fly_iid is not None
        savings = None
        if regular is not None:
            savings = round(
                (float(regular) - float(sale)) / float(regular) * 100, 2
            )
        db.add(DealCache(
            chain_id=chain.id, fetch_id=fetch.id, product_name=name, brand=brand,
            sale_price=sale, regular_price=regular, price_unit=unit,
            savings_pct=savings, category=category,
            matched_ingredient_id=fly_iid,
            match_confidence=fly_conf if fly_iid is not None else None,
            valid_from=fetch.valid_from, valid_to=fetch.valid_to,
            region_key=region_key, page_number=1,
        ))
    await db.flush()
    return before, after


async def seed_fixture(db, default_slug: str) -> User:
    sup_id = f"golden-fixture-{default_slug}"
    user = (
        await db.execute(select(User).where(User.supabase_user_id == sup_id))
    ).scalar_one_or_none()
    if user is None:
        user = User(supabase_user_id=sup_id, email=f"{sup_id}@example.test")
        db.add(user)
        await db.flush()
    for k, v in PROFILE.items():
        setattr(user, k, v)

    # Wipe this fixture user's data for a clean, repeatable run.
    await db.execute(delete(WeekRecipe).where(WeekRecipe.user_id == user.id))
    await db.execute(delete(Recipe).where(Recipe.user_id == user.id))
    await db.execute(delete(PantryItem).where(PantryItem.user_id == user.id))
    await db.execute(delete(UserStore).where(UserStore.user_id == user.id))
    await db.execute(delete(AICostEvent).where(AICostEvent.user_id == user.id))

    for name, cat, qty, unit, staple in PANTRY:
        db.add(PantryItem(
            user_id=user.id, name=name, category=cat, quantity_estimate=qty,
            unit=unit, is_staple=staple, is_active=True, freshness="good",
        ))

    other_slug = "stop_and_shop" if default_slug == "lidl" else "lidl"
    for slug, is_default in ((default_slug, True), (other_slug, False)):
        loc = await _get_or_create_location(db, slug)
        db.add(UserStore(
            user_id=user.id, store_location_id=loc.id, is_default=is_default
        ))

    # Prior batch (2h ago, unsaved) + rated history for the taste blocks.
    # Full ingredient lists feed the P33 ingredient-overlap pool.
    prior_at = dt.now(timezone.utc) - timedelta(hours=2)
    cauliflower_iid, _c = ingredient_matcher.match_ingredient("cauliflower")
    for title, anchor, fmt, cuisine, ings in PRIOR_BATCH:
        is_cauli = anchor == "cauliflower"
        db.add(Recipe(
            user_id=user.id, status="ready", title=title, cuisine=cuisine,
            difficulty="medium", servings=2, generated_at=prior_at,
            why_this_recipe=(
                "Cauliflower is on sale this week." if is_cauli
                else "Used up the ground beef."
            ),
            ingredients_json=[
                {"name": n, "generic_name": n, "in_pantry": True} for n in ings
            ],
            signature_json={"anchor_ingredient": anchor,
                            "dish_format": fmt, "cuisine": cuisine},
            is_market_pick=is_cauli,
            # Legacy (pre-P32) anchor shape — exercises the rotation fallback.
            market_anchor_json=(
                {"name": "cauliflower", "ingredient_id": cauliflower_iid,
                 "sale_price": "2.49", "store": "Lidl"} if is_cauli else None
            ),
        ))
    old_at = dt.now(timezone.utc) - timedelta(days=4)
    for title, fmt, cuisine, rating in (LOVED, PASSED):
        db.add(Recipe(
            user_id=user.id, status="ready", title=title, cuisine=cuisine,
            difficulty="easy", servings=2, generated_at=old_at, rating=rating,
            why_this_recipe="From last week.",
            signature_json={"anchor_ingredient": "chicken thighs" if rating == 1
                            else "quinoa", "dish_format": fmt, "cuisine": cuisine},
        ))
    # A recipe SAVED to this week (P33 B3c): the checker compares against it,
    # and its purchase-needed ground beef becomes a carve-out (B6).
    sw_title, sw_fmt, sw_cuisine, sw_ings = SAVED_WEEK
    saved = Recipe(
        user_id=user.id, status="ready", title=sw_title, cuisine=sw_cuisine,
        difficulty="medium", servings=2, generated_at=old_at,
        why_this_recipe="Saved for this week.",
        ingredients_json=sw_ings,
        signature_json={"anchor_ingredient": "ground beef",
                        "dish_format": sw_fmt, "cuisine": sw_cuisine},
    )
    db.add(saved)
    await db.flush()
    db.add(WeekRecipe(
        user_id=user.id, recipe_id=saved.id,
        week_start=recipe_engine.week_start_for(dt.now(timezone.utc).date()),
    ))
    await db.flush()
    return user


# --------------------------------------------------------------------------- #
# Run + report
# --------------------------------------------------------------------------- #
def _fmt_nut(n: dict | None) -> str:
    if not n:
        return "—"
    src = n.get("source") or "?"
    cov = f", cov {n['coverage']:.2f}" if n.get("coverage") is not None else ""
    cal = n.get("calories")
    pro = n.get("protein_g")
    return (f"{round(cal) if cal is not None else '?'} cal / "
            f"{round(pro) if pro is not None else '?'} g protein ({src}{cov})")


def _fmt_flags(f: dict | None) -> str:
    if not f:
        return "—"
    parts = []
    if f.get("protein_below_floor"):
        d = f["protein_below_floor"]
        parts.append(f"⚠ {d['protein_g']}g protein — below your {d['floor_g']}g target")
    if f.get("heavy"):
        parts.append(f"⚠ heavy: {f['heavy']['calories']} cal")
    return "; ".join(parts)


async def run_store(default_slug: str, stub: bool) -> None:
    async with AsyncSessionLocal() as db:
        await ingredient_matcher.preload(db)
        user = await seed_fixture(db, default_slug)
        stats = {}
        for slug, rows in (("lidl", LIDL_DEALS), ("stop_and_shop", SNS_DEALS)):
            stats[slug] = await _seed_deals(db, slug, rows)
        await db.commit()

        stub_client = None
        if stub:
            stub_client = StubAnthropic()
            recipe_engine.AsyncAnthropic = lambda api_key=None: stub_client

        # Pin a NON-staple item (P33 verify-3): it must ride in all 5 recipes
        # without tripping the overlap guard (pins are carved out).
        pin = (
            await db.execute(
                select(PantryItem).where(PantryItem.user_id == user.id,
                                         PantryItem.name == "cherry tomatoes")
            )
        ).scalar_one()
        recipes = await recipe_engine.generate_concepts(
            db, user, pinned_ids=[pin.id], direction="smoky and weeknight-fast",
        )
        await db.commit()
        ids = [r.id for r in recipes]

    await recipe_engine.run_details_bg(user.id, ids)

    async with AsyncSessionLocal() as db:
        rows = (
            (await db.execute(
                select(Recipe).where(Recipe.id.in_(ids)).order_by(Recipe.id)
            )).scalars().all()
        )
        events = (
            (await db.execute(
                select(AICostEvent).where(AICostEvent.user_id == user.id)
            )).scalars().all()
        )

        store_label = STORES[default_slug][1]
        print("=" * 76)
        print(f"GOLDEN BATCH — default store: {store_label}  (N=5, "
              f"floor 54 g, calorie band {round(PROFILE['calorie_target'] * 0.55)})")
        print("=" * 76)
        print(f"flyer matching (raw → normalized): "
              f"Lidl {stats['lidl'][0]}→{stats['lidl'][1]} of {len(LIDL_DEALS)}; "
              f"Stop & Shop {stats['stop_and_shop'][0]}→{stats['stop_and_shop'][1]} "
              f"of {len(SNS_DEALS)}")

        by_stage: dict[str, set[str]] = {}
        for e in events:
            by_stage.setdefault(e.stage or "?", set()).add(e.model)
        print("per-stage models: " + "  ".join(
            f"{s}={'+'.join(sorted(ms))}" for s, ms in sorted(by_stage.items())
        ))

        if stub_client is not None:
            concept_prompt = next(
                (sys + "\n" + u for sys, u in stub_client.messages.prompts
                 if "Propose tonight's" in u), ""
            )
            blocks = {
                "taste_notes": "THEIR TASTE" in concept_prompt,
                "LOVED/PASSED": "WHAT THEY THINK OF PAST RECIPES" in concept_prompt,
                "RECENTLY SHOWN": "RECENTLY SHOWN" in concept_prompt,
                "direction": "DIRECTION for THIS batch" in concept_prompt,
                "pins": "HARD REQUIREMENT" in concept_prompt,
                "market assignments": "MARKET PICK 1" in concept_prompt,
            }
            print("stage-1 prompt blocks: " + "  ".join(
                f"{k}={'✓' if v else 'MISSING'}" for k, v in blocks.items()
            ))

        titles = [r.title for r in rows]
        over = recipe_engine._overused_title_words(titles)
        print(f"title-word check (>2 repeats): "
              f"{'CLEAN' if not over else 'VIOLATION ' + str(sorted(over))}")

        anchors = []
        for i, r in enumerate(rows, 1):
            sig = r.signature_json or {}
            critic = r.critic_json or {}
            print(f"\n#{i} [{r.difficulty}] {r.title}")
            print(f"    signature: anchor={sig.get('anchor_ingredient')!r} · "
                  f"format={sig.get('dish_format')} · cuisine={sig.get('cuisine')}")
            leads = sig.get("flavor_lead")
            print(f"    flavor lead: {', '.join(leads) if leads else '—'}")
            if r.is_market_pick and r.market_anchor_json:
                a = r.market_anchor_json
                at = f" — at {a.get('store')}" if a.get("cross_store") else ""
                print(f"    market pick: built around {a.get('name')}, "
                      f"${a.get('sale_price')}"
                      f"{'/' + a['price_unit'] if a.get('price_unit') else ''}{at}")
                anchors.append(a.get("anchor_key"))
            print(f"    macros: {_fmt_nut(r.nutrition_json)}")
            print(f"    critic: score={critic.get('score')} "
                  f"verdict={critic.get('verdict')}")
            print(f"    flags:  {_fmt_flags(r.quality_flags_json)}")

        distinct = len(anchors) == len(set(anchors))
        print(f"\nmarket anchors distinct: "
              f"{'YES' if distinct else 'NO — DUPLICATES: ' + str(anchors)}")

        # -- P33: pairwise Jaccard matrix (batch × batch + recent + saved) -- #
        octx = recipe_engine._Ctx(
            pantry=[], chain_name=None, store_name=None,
            deal_by_ingredient={}, context_text="",
        )
        await recipe_engine._load_detail_overlap(db, user.id, rows, octx)
        carve = octx.overlap_carveout
        batch_entries = [
            recipe_engine._entry_for_recipe(r, carve, "batch") for r in rows
        ]
        pool = octx.overlap_pool
        cols = batch_entries + pool
        col_ids = [f"B{i + 1}" for i in range(len(batch_entries))] + [
            f"P{i + 1}" for i in range(len(pool))
        ]
        print("\npairwise Jaccard (non-staple sets, carve-outs removed):")
        for cid, e in zip(col_ids, cols):
            print(f"  {cid}: {e.title} ({e.origin})")
        print("      " + "  ".join(f"{c:>4}" for c in col_ids))
        worst = 0.0
        for i, e in enumerate(batch_entries):
            cells = []
            for k, o in enumerate(cols):
                if k == i:
                    cells.append("   —")
                else:
                    j = recipe_engine._jaccard(e.keys, o.keys)
                    worst = max(worst, j)
                    cells.append(f"{j:.2f}")
            print(f"  B{i + 1}  " + "  ".join(f"{c:>4}" for c in cells))
        print(f"  max pairwise J = {worst:.2f} "
              f"(violation thresholds: >{recipe_engine.J_HARD} any, "
              f">{recipe_engine.J_SAME_ANCHOR} same-anchor)")

        # -- P33 carve-out proof: the pin rides in all 5, guard untripped -- #
        pin_in_all = all(
            any("cherry tomato" in n.lower()
                for n in recipe_engine._recipe_ing_names(r))
            for r in rows
        )
        undisclosed = sum(
            1 for i, e in enumerate(batch_entries)
            if recipe_engine._overlap_violation(
                e.keys, e.anchor_key,
                pool + [batch_entries[j] for j in range(len(batch_entries)) if j != i],
            ) is not None
            and not any(
                m in (rows[i].why_this_recipe or "").lower()
                for m in recipe_engine._OVERLAP_DISCLOSURE_MARKERS
            )
        )
        print(f"carve-out proof: pin 'cherry tomatoes' in all 5 recipes = "
              f"{'YES' if pin_in_all else 'NO'}; overlap guard tripped by pin: NO "
              f"(pins are carved out of every Jaccard set)")
        print(f"overlap violations shipped unannotated: {undisclosed}")

        # -- P34: owned-perishable slot + recency exemption + feed order ---- #
        beef_idx = [
            i for i, r in enumerate(rows)
            if "beef" in str((r.signature_json or {}).get("anchor_ingredient") or "")
            and not r.is_market_pick
        ]
        if beef_idx:
            k = beef_idx[0]
            print(f"owned-perishable slot: #{k + 1} {rows[k].title} "
                  f"(anchored on the owned ground beef; market pick: NO)")
            prior_beef = next(
                (e for e in pool if "Fajita" in e.title), None
            )
            if prior_beef is not None:
                sig = rows[k].signature_json or {}
                shared = recipe_engine._axes_shared(sig, {
                    "anchor_ingredient": "ground beef",
                    "dish_format": "bowl", "cuisine": "mexican",
                })
                j_beef = recipe_engine._jaccard(
                    batch_entries[k].keys, prior_beef.keys
                )
                print(
                    f"recency exemption: vs recent '{prior_beef.title}' — "
                    f"{shared} signature axes shared incl. the beef anchor; "
                    f"exempt on the anchor axis (owned perishable) -> effective "
                    f"{max(shared - 1, 0)}, below the 2-axis suppression. "
                    f"Ingredient J vs it = {j_beef:.2f} (beef itself is a "
                    f"planned shared purchase for the saved ragu, so it's "
                    f"carved out — dishes must differ beyond the beef)."
                )
        else:
            print("owned-perishable slot: MISSING (expected a beef-anchored "
                  "non-market dish!)")
        first_cost = rows[0].key_ingredients_json or []
        first_all_pantry = all(
            k.get("in_pantry") is True for k in first_cost if isinstance(k, dict)
        )
        print(f"feed order: tier-leading dish is the all-pantry $0 dish "
              f"(stub emitted it LAST; the sort promoted it): "
              f"{'YES' if first_all_pantry and beef_idx == [0] else 'NO'}")


async def clone_check() -> None:
    """P33 verify-1: feed the two live cauliflower-bowl clones through the
    checker RAW (no carve-outs — neither was a designated market anchor when
    it shipped) and confirm they'd violate."""
    async with AsyncSessionLocal() as db:
        await ingredient_matcher.preload(db)
    a_title, a_anchor, a_ings = CLONE_A
    b_title, b_anchor, b_ings = CLONE_B
    a_set = recipe_engine._overlap_set(a_ings, set())
    b_set = recipe_engine._overlap_set(b_ings, set())
    j = recipe_engine._jaccard(a_set, b_set)
    same_anchor = (
        recipe_engine._ing_key(a_anchor) == recipe_engine._ing_key(b_anchor)
    )
    rules = []
    if j > recipe_engine.J_HARD:
        rules.append(f"J > {recipe_engine.J_HARD}")
    if j > recipe_engine.J_SAME_ANCHOR and same_anchor:
        rules.append(f"J > {recipe_engine.J_SAME_ANCHOR} with same anchor")
    print("=" * 76)
    print("CLONE CHECK (P33 verify-1) — the two live cauliflower-bowl clones")
    print("=" * 76)
    print(f"A: {a_title} — non-staple set: "
          f"{len(a_set)} keys (staples like rice/chickpeas/tahini/oil excluded)")
    print(f"B: {b_title} — non-staple set: {len(b_set)} keys")
    print(f"Jaccard = {j:.3f}; same anchor ({a_anchor}) = {same_anchor}")
    print("verdict: " + ("VIOLATION — " + " AND ".join(rules)
                         if rules else "legal (unexpected!)"))


async def mini_checks() -> None:
    """P34 verify-2/3: (a) a use_soon perishable gets the guarantee at N=3
    with the urgency why-line; (b) once the beef is consumed, the next batch
    has no forced beef slot. Concept-stage only; reuses the Lidl fixture user
    (whom the main run just seeded)."""
    print("=" * 76)
    print("MINI-CHECKS (P34 verify-2/3) — use_soon at N=3, then beef consumed")
    print("=" * 76)
    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(
                select(User).where(
                    User.supabase_user_id == "golden-fixture-lidl"
                )
            )
        ).scalar_one()
        beef = (
            await db.execute(
                select(PantryItem).where(
                    PantryItem.user_id == user.id,
                    PantryItem.name == "ground beef",
                )
            )
        ).scalar_one()

        # (a) use_soon + N=3
        beef.freshness = "use_soon"
        user.recipes_per_generation = 3
        await db.flush()
        concepts = await recipe_engine.generate_concepts(db, user)
        await db.commit()
        slot = [
            r for r in concepts
            if "beef" in str((r.signature_json or {}).get("anchor_ingredient") or "")
            and not r.is_market_pick
        ]
        markets = sum(1 for r in concepts if r.is_market_pick)
        print(f"use_soon @ N=3: batch = {len(concepts)} concepts "
              f"({markets} market picks); beef-anchored non-market: {len(slot)}")
        if slot:
            why = (slot[0].why_this_recipe or "")
            leads_urgency = why.lower().startswith("your ground beef should be used")
            print(f"  #{concepts.index(slot[0]) + 1} {slot[0].title}")
            print(f"  why leads with urgency: {'YES' if leads_urgency else 'NO'} "
                  f"-> \"{why[:90]}\"")

        # (b) beef consumed -> no forced slot
        beef.is_active = False
        user.recipes_per_generation = 5
        await db.flush()
        concepts = await recipe_engine.generate_concepts(db, user)
        await db.commit()
        beef_forced = sum(
            1 for r in concepts
            if "beef" in str((r.signature_json or {}).get("anchor_ingredient") or "")
            and not r.is_market_pick
        )
        beef_market = sum(
            1 for r in concepts
            if "beef" in str((r.signature_json or {}).get("anchor_ingredient") or "")
            and r.is_market_pick
        )
        markets = sum(1 for r in concepts if r.is_market_pick)
        print(f"beef consumed @ N=5: batch = {len(concepts)} concepts "
              f"({markets} market picks); FORCED beef slot: {beef_forced} "
              f"(guarantee inactive — nothing perishable left to protect). "
              f"Beef market picks: {beef_market} — the flyer's beef deal is "
              f"fair game again once the owned beef is gone.")

        # restore the beef before the starvation check
        beef.is_active = True
        beef.freshness = "good"
        await db.commit()

        # (c) DEALS STARVATION -> anchor cap (the live all-beef regression).
        # Wipe every golden flyer AND the fetch history (so the self-heal
        # debounce doesn't suppress); the stub then proposes the exact
        # all-beef batch from the field screenshots ("two axes changed") and
        # the engine's one-anchor-one-dish cap must break it up.
        golden_regions = [v[2] for v in STORES.values()]
        await db.execute(
            delete(DealCache).where(DealCache.region_key.in_(golden_regions))
        )
        await db.execute(
            delete(CircularFetch).where(
                CircularFetch.region_key.in_(golden_regions)
            )
        )
        await db.commit()
        # Capture the self-heal instead of fetching real flyers.
        scheduled: list = []
        real_hook = recipe_engine._schedule_deal_refresh
        recipe_engine._schedule_deal_refresh = (
            lambda combos, zip_code=None: scheduled.extend(combos)
        )
        try:
            concepts = await recipe_engine.generate_concepts(db, user)
        finally:
            recipe_engine._schedule_deal_refresh = real_hook
        await db.commit()
        anchors = [
            str((r.signature_json or {}).get("anchor_ingredient") or "?")
            for r in concepts
        ]
        beef_n = sum(1 for a in anchors if "beef" in a)
        distinct = len(set(anchors)) == len(anchors)
        print("deals starved @ N=5 (flyers wiped; stub proposes the live "
              "ALL-BEEF batch — 5× ground beef, formats/cuisines rotated):")
        print(f"  anchors shipped: {anchors}")
        print(f"  beef-anchored: {beef_n} (one anchor, one dish); "
              f"anchors distinct: {'YES' if distinct else 'NO'}; "
              f"market picks: {sum(1 for r in concepts if r.is_market_pick)}")
        titles = [r.title for r in concepts]
        over = recipe_engine._overused_title_words(titles)
        print(f"  title-word check: {'CLEAN' if not over else 'VIOLATION ' + str(sorted(over))}")
        print(f"  self-heal: background circular refresh scheduled for "
              f"{len(scheduled)} starved store(s) "
              f"(the scheduler also sweeps every {settings.deals_refresh_hours}h)")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", choices=["lidl", "stop_and_shop"], default=None)
    ap.add_argument("--stub", action="store_true",
                    help="force the deterministic stub even with an API key")
    ap.add_argument("--save-reference", action="store_true",
                    help=f"write output to {REFERENCE_PATH.name}")
    args = ap.parse_args()

    use_stub = args.stub or not settings.anthropic_api_key
    if use_stub:
        print("(no ANTHROPIC_API_KEY / --stub: deterministic stub serves the "
              "model calls; all deterministic guards run for real)\n")

    slugs = [args.store] if args.store else ["lidl", "stop_and_shop"]
    buf = io.StringIO()

    class _Tee(io.TextIOBase):
        def write(self, s):
            sys.__stdout__.write(s)
            buf.write(s)
            return len(s)

    with redirect_stdout(_Tee()):
        await clone_check()
        print()
        for slug in slugs:
            await run_store(slug, use_stub)
            print()
        if "lidl" in slugs:
            await mini_checks()
            print()

    if args.save_reference:
        REFERENCE_PATH.write_text(buf.getvalue())
        print(f"reference saved -> {REFERENCE_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
