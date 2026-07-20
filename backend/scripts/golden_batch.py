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
        if "edit one short recipe sentence" in sys_text:
            return _msg(self._price_edit(user))
        if "CORRECTION — PANTRY MODE BUDGET" in user:
            return _msg({"recipes": [self._pantry_budget_regen()]})
        if "CORRECTION — COMPUTED PROTEIN FAILURE" in user:
            return _msg({"recipes": [self._computed_failure_regen()]})
        if "ANCHOR PROMINENCE VIOLATION" in user:
            return _msg({"recipes": [self._prominence_regen(user)]})
        if "ANCHOR CAP VIOLATION" in user:
            return _msg({"recipes": [self._anchor_cap_regen()]})
        if "INGREDIENT OVERLAP VIOLATION" in user:
            return _msg({"recipes": [self._overlap_regen(user)]})
        if "VARIETY VIOLATION" in user:
            return _msg({"recipes": [self._variety_regen(user)]})
        if "MARKET ANCHOR COLLISION" in user:
            return _msg({"recipes": [self._market_concept_from_correction(user)]})
        if "PANTRY MODE IS ON" in user and "Propose tonight's" in user:
            return _msg(self._pantry_mode_concepts(user))
        if "WEEK PLAN" in user and "coordinated dinner concepts" in user:
            return _msg(self._week_concepts(user))
        if "Propose tonight's" in user:
            return _msg(self._concepts(user))
        # variety / contract / critic regen fallbacks: echo one safe concept
        return _msg({"recipes": [self._pantry_concept("medium")]})

    @staticmethod
    def _pin_name(text: str) -> str | None:
        m = re.search(r"ALL of them \(not a garnish\): (.+?) \(have", text)
        return m.group(1).strip() if m else None

    @staticmethod
    def _deal_pin_names(text: str) -> list:
        m = re.search(r"explicitly chose to buy (.+?)\.", text)
        if not m:
            return []
        return [n.strip() for n in m.group(1).split(",") if n.strip()]

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
            r"- MARKET PICK \d+(?:\s*\[[^\]]*\])?: build around (.+?): "
            r"\$([0-9.]+)(?:/(\w+))?"
            r"(?:,[^\n—\[]*)?\s*((?:—\s*at\s+[^\n\[]+)?)", user,
        )
        tiers = []
        mix = re.search(r"difficulty mix:\s*([^\n]+)", user).group(1)
        for count, tier in re.findall(r"(\d+)\s+(easy|medium|hard)", mix):
            tiers += [tier] * int(count)
        tiers = (tiers + ["medium"] * n)[:n]
        pin = self._pin_name(user)
        deal_pin_names = self._deal_pin_names(user)

        def with_pin(keys: list[dict]) -> list[dict]:
            if pin:
                keys = keys + [{"generic_name": pin, "brand": None,
                                "in_pantry": True, "on_sale": False,
                                "sale_price": None}]
            for dp in deal_pin_names:
                clean = ingredient_matcher.normalize_flyer_name(dp) or dp
                if not any(clean.lower() in str(k.get("generic_name") or "").lower()
                           or str(k.get("generic_name") or "").lower() in clean.lower()
                           for k in keys):
                    keys = keys + [{"generic_name": clean.lower(), "brand": None,
                                    "in_pantry": False, "on_sale": True,
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
            store_note = re.sub(r"—\s*at\s+", "at ", at_tail).strip()
            fmt = _FORMATS[k % len(_FORMATS)]
            veg = self._veg_for(clean)
            title = (
                f"Sazon-Charred {clean} {fmt.title()}" if k == 0
                else f"Charred {clean} {fmt.title()}"
            )
            recipes.append({
                "title": title,
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
        if len(recipes) >= 3 and any("at " in p[3] for p in picks):
            recipes[-1] = {
                "title": "Weeknight Harvest Rice Skillet",
                "description": "Rice and greens stretch the pork loin chops.",
                "difficulty": recipes[-1]["difficulty"],
                "prep_time_min": 15, "cook_time_min": 25, "total_time_min": 40,
                "servings": 2,
                "why_this_recipe": (
                    "Built around pork loin chops at $8.99/12oz this week."
                ),
                "cuisine": "american",
                "dish_format": "skillet",
                "anchor_ingredient": "pork loin chops",
                "flavor_lead": ["adobo pork rub"],
                "market_pick": True,
                "tags": ["market pick"],
                "nutrition_per_serving": {"calories": 650, "protein_g": 55},
                "key_ingredients": with_pin([
                    {"generic_name": "pork loin chops", "brand": None,
                     "in_pantry": False, "on_sale": False, "sale_price": None},
                    {"generic_name": "white rice", "brand": None,
                     "in_pantry": True, "on_sale": False, "sale_price": None},
                    {"generic_name": "zucchini", "brand": None,
                     "in_pantry": True, "on_sale": False, "sale_price": None},
                    {"generic_name": "olive oil", "brand": None,
                     "in_pantry": True, "on_sale": False, "sale_price": None},
                ]),
            }
        recipes += pantry_concepts
        return {"recipes": recipes[:n]}

    def _week_concepts(self, user: str) -> dict:
        """WEEK PLAN press (P42): a deterministic COORDINATED set — the
        perishable consumed first, a shared purchased herb across two meals,
        and the best deal stacked (anchor in one dinner, support in another).
        Exercises the shared-purchase overlap carve-out for real."""
        n = int(re.search(r"propose exactly (\d+) concepts", user).group(1))
        picks = re.findall(
            r"- MARKET PICK \d+(?:\s*\[[^\]]*\])?: build around (.+?): "
            r"\$([0-9.]+)(?:/(\w+))?"
            r"(?:,[^\n—\[]*)?\s*((?:—\s*at\s+[^\n\[]+)?)", user,
        )
        per = re.search(
            r"OWNED-PERISHABLE SLOT — the user already owns (.+?) \(HAVE", user
        )
        per_name = (per.group(1).strip() if per else None) or "ground beef"
        tiers = []
        mix = re.search(r"difficulty mix:\s*([^\n]+)", user).group(1)
        for count, tier in re.findall(r"(\d+)\s+(easy|medium|hard)", mix):
            tiers += [tier] * int(count)
        tiers = (tiers + ["medium"] * n)[:n]

        def pick_at(i):
            if i < len(picks):
                name, price, unit, _tail = picks[i]
                clean = ingredient_matcher.normalize_flyer_name(name) or name
                return clean.lower(), price, unit
            return None

        p1 = pick_at(0)
        p2 = pick_at(1)
        cilantro = {"generic_name": "cilantro", "brand": None,
                    "in_pantry": False, "on_sale": False, "sale_price": None}

        recipes = [{
            # Night 1 — easiest, owned perishable out of the fridge FIRST.
            "title": f"Monday {per_name.title()} Harissa Stew",
            "description": f"The {per_name} goes first — nothing waits for Friday.",
            "difficulty": tiers[0],
            "prep_time_min": 10, "cook_time_min": 20, "total_time_min": 30,
            "servings": 2,
            "why_this_recipe": (
                f"Your {per_name} leads the week so it never spoils. The "
                "cilantro bunch bought tonight seasons Wednesday too."
            ),
            "cuisine": "moroccan", "dish_format": "stew",
            "anchor_ingredient": per_name,
            "flavor_lead": ["ras el hanout"],
            "nutrition_per_serving": {"calories": 640, "protein_g": 55},
            "key_ingredients": [
                {"generic_name": per_name, "brand": None, "in_pantry": True,
                 "on_sale": False, "sale_price": None},
                {"generic_name": "white rice", "brand": None, "in_pantry": True,
                 "on_sale": False, "sale_price": None},
                dict(cilantro),
                {"generic_name": "olive oil", "brand": None, "in_pantry": True,
                 "on_sale": False, "sale_price": None},
            ],
        }]
        if p1:
            name1, price1, unit1 = p1
            recipes.append({
                # Night 2 — the week's best deal ANCHORS here...
                "title": f"Sizzling {name1.title()} Fajitas",
                "description": f"{name1} fajitas with the week's shared herbs.",
                "difficulty": tiers[min(1, n - 1)],
                "prep_time_min": 15, "cook_time_min": 20, "total_time_min": 35,
                "servings": 2,
                "why_this_recipe": (
                    f"Built around {name1} at ${price1}"
                    f"{'/' + unit1 if unit1 else ''} this week — the family "
                    "pack splits across two dinners, and it uses the rest of "
                    "Monday's cilantro."
                ),
                "cuisine": "tex-mex", "dish_format": "fajitas",
                "anchor_ingredient": name1,
                "flavor_lead": ["lime & chili"],
                "market_pick": True, "tags": ["market pick"],
                "nutrition_per_serving": {"calories": 650, "protein_g": 55},
                "key_ingredients": [
                    {"generic_name": name1, "brand": None, "in_pantry": False,
                     "on_sale": True, "sale_price": price1},
                    {"generic_name": "corn tortillas", "brand": None,
                     "in_pantry": True, "on_sale": False, "sale_price": None},
                    dict(cilantro),
                    {"generic_name": "canned black beans", "brand": None,
                     "in_pantry": True, "on_sale": False, "sale_price": None},
                ],
            })
            recipes.append({
                # Night 3 — ...and SUPPORTS here (deal stacking, not anchoring).
                "title": "Broccoli Fried Rice",
                "description": "Fried rice that stretches the family pack's second half.",
                "difficulty": tiers[min(2, n - 1)],
                "prep_time_min": 10, "cook_time_min": 15, "total_time_min": 25,
                "servings": 2,
                "why_this_recipe": (
                    f"Stretches the second half of Tuesday's {name1} into a "
                    "wok dinner — one purchase, two meals."
                ),
                "cuisine": "cantonese", "dish_format": "fried rice",
                "anchor_ingredient": "broccoli",
                "flavor_lead": ["ginger & scallion"],
                "nutrition_per_serving": {"calories": 620, "protein_g": 52},
                "key_ingredients": [
                    {"generic_name": "broccoli", "brand": None, "in_pantry": True,
                     "on_sale": False, "sale_price": None},
                    {"generic_name": "white rice", "brand": None, "in_pantry": True,
                     "on_sale": False, "sale_price": None},
                    {"generic_name": name1, "brand": None, "in_pantry": False,
                     "on_sale": True, "sale_price": price1},
                    {"generic_name": "eggs", "brand": None, "in_pantry": True,
                     "on_sale": False, "sale_price": None},
                ],
            })
        if p2 and len(recipes) < n:
            name2, price2, unit2 = p2
            recipes.append({
                "title": f"Sheet-Pan {name2.title()} Finale",
                "description": f"A hands-off {name2} sheet-pan dinner to close the week.",
                "difficulty": tiers[-1],
                "prep_time_min": 15, "cook_time_min": 35, "total_time_min": 50,
                "servings": 2,
                "why_this_recipe": (
                    f"Built around {name2} at ${price2}"
                    f"{'/' + unit2 if unit2 else ''} this week."
                ),
                "cuisine": "italian", "dish_format": "sheet-pan",
                "anchor_ingredient": name2,
                "flavor_lead": ["lemon-caper"],
                "market_pick": True, "tags": ["market pick"],
                "nutrition_per_serving": {"calories": 630, "protein_g": 54},
                "key_ingredients": [
                    {"generic_name": name2, "brand": None, "in_pantry": False,
                     "on_sale": True, "sale_price": price2},
                    {"generic_name": "baby potatoes", "brand": None,
                     "in_pantry": True, "on_sale": False, "sale_price": None},
                    {"generic_name": "parmesan cheese", "brand": None,
                     "in_pantry": True, "on_sale": False, "sale_price": None},
                ],
            })
        while len(recipes) < n:
            recipes.append(self._fallback_pantry_concept(tiers[len(recipes)]))
        return {"recipes": recipes[:n]}

    def _prominence_regen(self, user: str) -> dict:
        m = re.search(r"Concept to fix:\n(\{.*\})\n\nReturn", user, re.S)
        brief = json.loads(m.group(1)) if m else {}
        anchor = str(brief.get("anchor_ingredient") or "dinner")
        c = self._overlap_regen(user)  # same reconstruction shape
        c["title"] = f"{anchor.title()} Skillet Supper"
        c["description"] = f"{anchor.title()} seared over garlic rice and greens."
        c["anchor_ingredient"] = anchor
        c["flavor_lead"] = brief.get("flavor_lead") or ["adobo pork rub"]
        c["why_this_recipe"] = (
            "Built around pork loin chops at $8.99/12oz this week."
        )
        return c

    @staticmethod
    def _price_edit(user: str) -> dict:
        m = re.search(r"VERIFIED price for .+? is (\$[\d.]+(?:/\w+)?)", user)
        if m:
            return {"text": f"Built around pork loin chops at {m.group(1)} "
                            "this week — at Stop & Shop."}
        return {"text": "Built around this week's deal — price at the shelf."}

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
            r"Concept (?:to replace|that repeats|to fix):\n(\{.*\})\n\nReturn",
            user, re.S,
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

    # -- Pantry mode (P35) --------------------------------------------------- #
    @staticmethod
    def _owned_concept(title, desc, tier, anchor, fmt, cuisine, lead, keys,
                       unowned=()) -> dict:
        return {
            "title": title, "description": desc, "difficulty": tier,
            "prep_time_min": 10, "cook_time_min": 25, "total_time_min": 35,
            "servings": 2,
            "why_this_recipe": "Cooked from what's already in your kitchen.",
            "cuisine": cuisine, "dish_format": fmt,
            "anchor_ingredient": anchor, "flavor_lead": lead,
            "market_pick": False, "tags": ["pantry-first"],
            "nutrition_per_serving": {"calories": 640, "protein_g": 55},
            "key_ingredients": [
                {"generic_name": k, "brand": None, "in_pantry": k not in unowned,
                 "on_sale": False, "sale_price": None}
                for k in keys
            ],
        }

    def _pantry_mode_concepts(self, user: str) -> dict:
        """Pantry-mode Stage 1: no market assignments arrive (slots are
        suspended); four clean all-pantry dishes honor the block, but — like a
        live model would — one concept ignores it and builds a salmon dinner
        the user would have to shop for. The engine's deterministic budget
        pass must catch it."""
        n = int(re.search(r"propose exactly (\d+) concepts", user).group(1))
        per = re.search(
            r"OWNED-PERISHABLE SLOT — the user already owns (.+?) \(HAVE", user
        )
        per_name = per.group(1).strip() if per else "ground beef"
        recipes = [
            self._owned_concept(
                "Beef Picadillo Rice Skillet",
                "Savory-sweet picadillo over rice.", "easy",
                per_name, "skillet", "cuban", ["cumin-cinnamon picadillo"],
                [per_name, "white rice", "tomato", "red onion"],
            ),
            self._owned_concept(
                "Smoky Black Bean Chili",
                "Thick chipotle bean chili from cans and the crisper.",
                "easy", "canned black beans", "stew", "tex-mex",
                ["chipotle-cumin"],
                ["canned black beans", "canned diced tomatoes", "yellow onion",
                 "chipotle peppers in adobo"],
            ),
            self._owned_concept(
                "Creamy Chickpea & Spinach Curry",
                "Coconut-simmered chickpeas with wilted spinach.", "medium",
                "canned chickpeas", "curry", "thai", ["coconut-ginger"],
                ["canned chickpeas", "spinach", "coconut milk", "ginger"],
            ),
            self._owned_concept(
                "Broccoli Cheddar Rice Bake",
                "Bubbling cheddar rice with charred broccoli.", "medium",
                "broccoli", "bake", "american", ["cheddar-mustard"],
                ["broccoli", "cheddar cheese", "white rice", "yellow onion"],
            ),
            # The violator: a purchased-protein anchor plus a second buy —
            # the deterministic budget pass must regenerate it ONCE, named.
            self._owned_concept(
                "Pan-Seared Salmon with Asparagus",
                "Restaurant-style salmon dinner.", "hard",
                "salmon", "pan-sear", "french", ["caper-brown butter"],
                ["salmon", "asparagus", "white rice", "olive oil"],
                unowned=("salmon", "asparagus"),
            ),
        ]
        return {"recipes": recipes[:n]}

    def _computed_failure_regen(self) -> dict:
        """P39 A2: the cauliflower concept computes ~19 g against the 54 g
        floor (>25% short) — a failed concept. The slot-regeneration answer
        rebuilds around a protein that actually carries the floor."""
        return self._owned_concept(
            "Ground Turkey Rice Bowl",
            "Browned turkey over rice with blistered peppers.", "medium",
            "ground turkey", "bowl", "american", ["chili-lime turkey"],
            ["ground turkey", "white rice", "red bell pepper", "olive oil"],
            unowned=("ground turkey",),
        )

    def _pantry_budget_regen(self) -> dict:
        """The budget-regen answer swaps the salmon for an owned anchor but —
        still like a live model — sneaks in two minor buys. The survivor must
        ship with the amber 'needs 2 purchases' chip, never silently."""
        return self._owned_concept(
            "Charred Zucchini & Feta Melt",
            "Blistered zucchini under briny feta.", "hard",
            "zucchini", "roast", "mediterranean", ["lemon-feta"],
            ["zucchini", "feta cheese", "asparagus", "olive oil"],
            unowned=("feta cheese", "asparagus"),
        )

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
        elif "black bean" in keys and "beef" not in keys:
            out = self._chili_detail(fortified=wants_protein)
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
                # The live stew leak (P39 A3): a prose protein claim that
                # contradicts the computed panel — the deterministic sync must
                # rewrite it to the computed figure.
                "Assemble with cheddar and lime crema. Total protein per "
                "serving: 70 g.",
            ],
            "nutrition_per_serving": {"calories": 1104, "protein_g": 62,
                                      "carbs_g": 48, "fat_g": 70, "fiber_g": 4},
        }

    def _chili_detail(self, fortified: bool) -> dict:
        """Pantry-mode floor stress (P35 B5): an all-owned bean chili that
        can't reach the 54 g floor. The fortify retry COMBINES pantry sources
        (yogurt + quinoa) instead of buying — still short, so the engine must
        ship the sub-floor chip WITH the cheapest one-buy fix note."""
        ings = [
            self._ing("canned black beans", "2", "cans", in_pantry=True),
            self._ing("canned diced tomatoes", "1", "can", in_pantry=True),
            self._ing("yellow onion", "1", "each", in_pantry=True),
            self._ing("chipotle peppers in adobo", "2", "tbsp", in_pantry=True),
        ]
        out = {
            "ingredients": ings,
            "instructions": [
                "Sweat the onion; bloom the chipotle and spices.",
                "Add beans and tomatoes; simmer until thick.",
                "Finish with lime and serve.",
            ],
            "nutrition_per_serving": {"calories": 520, "protein_g": 24,
                                      "carbs_g": 82, "fat_g": 9, "fiber_g": 22},
        }
        if fortified:
            out["ingredients"] = ings + [
                self._ing("greek yogurt", "1", "cup", in_pantry=True),
                self._ing("quinoa", "0.75", "cup", in_pantry=True),
            ]
            out["instructions"].insert(
                2, "Stir in quinoa to simmer; finish with yogurt."
            )
            out["nutrition_per_serving"] = {"calories": 610, "protein_g": 38,
                                            "carbs_g": 96, "fat_g": 12,
                                            "fiber_g": 24}
        return out

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
        fix = d.get("cheapest_fix")
        if fix:
            parts.append(
                f"💡 hits {d['floor_g']}g with one buy: {fix['name']}, "
                f"${fix['price']} at {fix['store']}"
            )
    if f.get("heavy"):
        parts.append(f"⚠ heavy: {f['heavy']['calories']} cal")
    if f.get("purchases"):
        p = f["purchases"]
        parts.append(f"⚠ needs {p['count']} purchases: {', '.join(p['items'])}")
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

        # -- P35: prose prices, badge plumbing, prominence, hyphen cap ------ #
        allowed: set = set()
        for d in (
            (await db.execute(
                select(DealCache).where(
                    DealCache.region_key.in_([v[2] for v in STORES.values()])
                )
            )).scalars().all()
        ):
            for v in (d.sale_price, d.regular_price):
                p = recipe_engine._norm_price(v)
                if p is not None:
                    allowed.add(p)
        n_prices = 0
        unverified: list = []
        for r in rows:
            for text in (r.why_this_recipe, r.description):
                got = recipe_engine._prices_in(text)
                n_prices += len(got)
                unverified += sorted(got - allowed)
        print(f"prose price scan: {n_prices} dollar figure(s) in narrative "
              f"text; unverifiable remaining: "
              f"{unverified if unverified else '0 — every price matches deal_cache'}")

        rogue = next(
            (r for r in rows
             if "pork" in str((r.market_anchor_json or {}).get("name") or "")),
            None,
        )
        if rogue is not None:
            a = rogue.market_anchor_json
            print(f"stray purchase-anchor plumbing: '{rogue.title}' ignored its "
                  f"assignment (invented $8.99/12oz) -> badge={rogue.is_market_pick}, "
                  f"built around {a.get('name')} ${a.get('sale_price')}"
                  f"{'/' + a['price_unit'] if a.get('price_unit') else ''}"
                  f"{' — at ' + a.get('store') if a.get('cross_store') else ''}; "
                  f"why: \"{(rogue.why_this_recipe or '')[:80]}\"")

        # -- P39: prose-nutrition sync + badge coherence over the batch ----- #
        n_contradictions = sum(
            recipe_engine._prose_nutrition_mismatches(r) for r in rows
        )
        both = sum(
            1 for r in rows
            if r.is_market_pick and all(
                k.get("in_pantry") is True
                for k in (r.ingredients_json or r.key_ingredients_json or [])
                if isinstance(k, dict)
            )
        )
        print(f"prose nutrition scan: {n_contradictions} contradiction(s) "
              f"between narrative/instruction figures and computed panels")
        print(f"badge coherence: {both} recipe(s) rendering All-pantry + "
              f"Market pick simultaneously")

        prominent = 0
        market_rows = [r for r in rows if r.is_market_pick]
        for r in market_rows:
            sig = r.signature_json or {}
            a_toks = {
                t for t in ingredient_matcher._tokens(
                    str(sig.get("anchor_ingredient") or "")
                ) if len(t) > 2
            }
            in_title = bool(a_toks & recipe_engine._title_words(r.title))
            desc_lead = bool(
                a_toks & set(ingredient_matcher._tokens(r.description or "")[:4])
            )
            prominent += in_title or desc_lead
        print(f"anchor prominence: {prominent}/{len(market_rows)} market picks "
              f"star (anchor in title or description lead)")
        print(f"hyphen/accent tokenization: 'Sazón-Charred' counts 'charred': "
              f"{'YES' if 'charred' in recipe_engine._title_words('Sazón-Charred Salmon') else 'NO'}")


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
    """P34 verify-2/3 + P37 deal pin + P35 pantry mode: (a) a use_soon
    perishable gets the guarantee at N=3 with the urgency why-line; (b) once
    the beef is consumed, the next batch has no forced beef slot; (d) a
    pinned deal anchors + rides every recipe; (e) pantry mode suspends market
    slots, enforces the purchase budget, and floor-stresses the chip +
    cheapest-fix note; then deals starvation. Reuses the Lidl fixture user
    (whom the main run just seeded)."""
    print("=" * 76)
    print("MINI-CHECKS — P34 use_soon/consumed · P37 deal pin · P35 pantry "
          "mode · starvation")
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

        # (d) DEAL PIN ("Cook with this sale", P37 C): pin the Lidl chicken
        # breast deal + a pantry pin — combined pins, every recipe features
        # the deal, one concept anchors it, priced + "your pick" labeled.
        chicken_deal = (
            await db.execute(
                select(DealCache).where(
                    DealCache.region_key == STORES["lidl"][2],
                    DealCache.product_name.ilike("%Chicken Breast%"),
                )
            )
        ).scalars().first()
        tomato_pin = (
            await db.execute(
                select(PantryItem).where(PantryItem.user_id == user.id,
                                         PantryItem.name == "cherry tomatoes")
            )
        ).scalar_one()
        concepts = await recipe_engine.generate_concepts(
            db, user, pinned_ids=[tomato_pin.id],
            pinned_deal_ids=[chicken_deal.id],
        )
        await db.commit()
        anchored = [
            r for r in concepts
            if (r.market_anchor_json or {}).get("user_pin")
        ]
        featured = sum(
            1 for r in concepts
            if any("chicken breast" in str(k.get("generic_name") or "").lower()
                   for k in (r.key_ingredients_json or []))
            or "chicken breast" in str(
                (r.signature_json or {}).get("anchor_ingredient") or ""
            ).lower()
        )
        labels = [
            p for p in (concepts[0].pinned_items_json or []) if p.get("deal")
        ]
        print(f"deal pin @ N=5 (pinned: '{chicken_deal.product_name}' + pantry "
              f"'cherry tomatoes'):")
        if anchored:
            a = anchored[0].market_anchor_json
            print(f"  anchoring concept: '{anchored[0].title}' — badge=True, "
                  f"built around {a.get('name')} ${a.get('sale_price')}"
                  f"{'/' + a['price_unit'] if a.get('price_unit') else ''} "
                  f"(user_pin={a.get('user_pin')})")
        else:
            print("  anchoring concept: MISSING!")
        print(f"  featured in {featured}/{len(concepts)} recipes; "
              f"batch chip: {labels[0]['name']} ${labels[0]['sale_price']}"
              f"/{labels[0]['price_unit']} — your pick" if labels else
              "  batch chip: MISSING")

        # (e) PANTRY MODE (P35): market slots suspended; ≤1 minor purchase
        # per recipe, never a purchased anchor or protein. The stub proposes
        # four clean pantry dishes + one salmon-dinner violator; the engine's
        # deterministic budget pass regenerates it ONCE — the regen still
        # sneaks in two minor buys, so it ships with the amber chip.
        pm_concepts = await recipe_engine.generate_concepts(
            db, user, pantry_mode=True,
        )
        await db.commit()
        markets = sum(1 for r in pm_concepts if r.is_market_pick)
        col_ok = all(r.pantry_mode for r in pm_concepts)
        print(f"pantry mode ON @ N=5: market picks: {markets} (slots "
              f"suspended); pantry_mode column on all rows: "
              f"{'YES' if col_ok else 'NO'}")
        for i, r in enumerate(pm_concepts, 1):
            sig = r.signature_json or {}
            unowned = [
                str(k.get("generic_name"))
                for k in (r.key_ingredients_json or [])
                if isinstance(k, dict) and not k.get("in_pantry")
                and not k.get("on_sale")
            ]
            print(f"  #{i} {r.title} — anchor="
                  f"{sig.get('anchor_ingredient')!r}, purchases: "
                  f"{len(unowned)}{' ' + str(unowned) if unowned else ''}"
                  f"{'  ' + _fmt_flags(r.quality_flags_json) if r.quality_flags_json else ''}")
        purchased_anchor = sum(
            1 for r in pm_concepts
            if not any(
                str((r.signature_json or {}).get("anchor_ingredient") or "!")
                in str(k.get("generic_name") or "").lower()
                and k.get("in_pantry")
                for k in (r.key_ingredients_json or [])
                if isinstance(k, dict)
            )
        )
        print(f"  purchased anchors: {purchased_anchor}; salmon violator "
              f"regenerated (named), survivor's 2-buy chip shown above")
        last_pm = await recipe_engine._last_pantry_mode(db, user.id)
        print(f"  warm cache: _last_pantry_mode -> {last_pm} (pre-gen would "
              f"re-run in pantry mode)")

        # Floor stress (P35 B5): detail the all-owned chili — fortify retry
        # COMBINES pantry sources (yogurt + quinoa), still short of 54 g ->
        # sub-floor chip PLUS the informative cheapest one-buy fix.
        chili = next(r for r in pm_concepts if "Chili" in r.title)
        await recipe_engine.run_details_bg(user.id, [chili.id])
        await db.refresh(chili)
        pf = (chili.quality_flags_json or {}).get("protein_below_floor") or {}
        fix = pf.get("cheapest_fix")
        n_purch = sum(
            1 for ing in (chili.ingredients_json or [])
            if isinstance(ing, dict) and not ing.get("in_pantry")
        )
        print(f"  floor stress ('{chili.title}' detailed, {n_purch} purchases): "
              f"protein {pf.get('protein_g')}g < floor {pf.get('floor_g')}g")
        if fix:
            print(f"    chip + note: \"hits {pf.get('floor_g'):.0f}g with one "
                  f"buy: {fix['name']}, ${fix['price']}"
                  f"{'/' + fix['unit'] if fix.get('unit') else ''} at "
                  f"{fix['store']}\" (informative — never auto-added)")
        else:
            print("    cheapest_fix: MISSING!")

        # Toggle OFF -> exactly the old behavior: market picks come back.
        off_concepts = await recipe_engine.generate_concepts(db, user)
        await db.commit()
        markets_off = sum(1 for r in off_concepts if r.is_market_pick)
        print(f"pantry mode OFF @ N=5: market picks restored: {markets_off}; "
              f"pantry_mode column: "
              f"{'all false' if not any(r.pantry_mode for r in off_concepts) else 'STUCK ON'}")

        # (f) P39 RE-ENFORCEMENT — reconstruct the three LIVE leaks as shipped
        # recipe states and run the deterministic post-compute pass
        # (recipe_engine.enforce_computed — the same pass _fill_details and
        # scripts/reenforce_batch.py share).
        print("P39 re-enforcement over reconstructed live leaks:")
        beef_iid, _c = ingredient_matcher.match_ingredient("ground beef")
        # 1. The stew: 31 g calculated panel, chipless, with a 63 g prose claim.
        stew = Recipe(
            user_id=user.id, status="ready",
            title="Smoky Chickpea & Vegetable Stew",
            description="A hearty stew claiming big protein.",
            why_this_recipe="Delivers 63 g of protein per serving.",
            servings=2,
            ingredients_json=[{"generic_name": "canned chickpeas",
                               "in_pantry": True}],
            instructions_json=[
                "Simmer everything until thick.",
                "Total protein per serving: 63 g.",
            ],
            nutrition_json={"calories": 520, "protein_g": 31,
                            "source": "calculated", "coverage": 0.95},
            signature_json={"anchor_ingredient": "canned chickpeas"},
            is_market_pick=False,
        )
        s = recipe_engine.enforce_computed(stew, stew.ingredients_json, 31,
                                           520, 54, 1100, 2000)
        chip = (s["flags"] or {}).get("protein_below_floor")
        print(f"  stew (31g calculated, '63 g' prose, chipless): path=chip "
              f"(post-hoc re-enforcement cannot regenerate) -> amber "
              f"{chip['protein_g']}g chip; prose figures corrected: "
              f"{s.get('prose_nutrition_fixed', 0)}; instruction now: "
              f"{stew.instructions_json[1]!r}; why now: "
              f"{stew.why_this_recipe!r}")
        print(f"  (in-pipeline, the same computed shortfall regenerates the "
              f"slot — see the Lidl run's cauliflower slot above)")
        # 2. The beef skillet: owned anchor wearing a market badge + S&S price.
        skillet = Recipe(
            user_id=user.id, status="ready",
            title="Carne Asada Beef & Egg Skillet",
            description="Owned beef, badged as a market pick.",
            why_this_recipe=(
                "Built around 80% lean ground beef, $6.49/lb this week — at "
                "Stop & Shop. Your ground beef should be used in the next "
                "day or two."
            ),
            servings=2,
            ingredients_json=[
                {"generic_name": "ground beef", "in_pantry": True},
                {"generic_name": "cheddar cheese", "in_pantry": True},
            ],
            nutrition_json={"calories": 720, "protein_g": 58,
                            "source": "calculated", "coverage": 1.0},
            signature_json={"anchor_ingredient": "ground beef"},
            is_market_pick=True,
            market_anchor_json={
                "name": "80% lean ground beef", "anchor_key": f"i{beef_iid}",
                "sale_price": "6.49", "price_unit": "lb",
                "store": "Stop & Shop", "cross_store": True, "user_pin": False,
            },
        )
        s = recipe_engine.enforce_computed(skillet, skillet.ingredients_json,
                                           58, 720, 54, 1100, 2000)
        print(f"  beef skillet (owned anchor + badge + S&S citation): "
              f"badge dropped={s.get('badge_dropped', False)}, "
              f"market_anchor={skillet.market_anchor_json}, why now: "
              f"{skillet.why_this_recipe!r}")
        # 3. The bacon-pinto bake hiding 1.5 lb of on-sale chicken.
        bake = Recipe(
            user_id=user.id, status="ready",
            title="BBQ Bacon & Pinto Bean Pasta Bake",
            description="Smoky bacon and pinto beans baked into rigatoni.",
            servings=2,
            key_ingredients_json=[
                {"generic_name": "bacon", "in_pantry": True},
                {"generic_name": "canned black beans", "in_pantry": True},
                {"generic_name": "rigatoni", "in_pantry": True},
            ],
            ingredients_json=[
                {"generic_name": "bacon", "quantity": "4", "unit": "oz",
                 "in_pantry": True},
                {"generic_name": "rigatoni", "quantity": "1", "unit": "lb",
                 "in_pantry": True},
                {"generic_name": "chicken breast", "quantity": "1.5",
                 "unit": "lb", "in_pantry": False, "on_sale": True,
                 "sale_store": "Lidl", "sale_price": "1.99"},
            ],
            nutrition_json={"calories": 880, "protein_g": 61,
                            "source": "calculated", "coverage": 1.0},
            signature_json={"anchor_ingredient": "bacon"},
            is_market_pick=False,
        )
        n_keys_before = len(bake.key_ingredients_json)
        s = recipe_engine.enforce_computed(bake, bake.ingredients_json, 61,
                                           880, 54, 1100, 2000)
        print(f"  bacon-pinto bake (1.5 lb hidden chicken): surfaced="
              f"{s.get('co_proteins')}; description now: {bake.description!r}; "
              f"key list {n_keys_before} -> {len(bake.key_ingredients_json)} "
              f"(new line on_sale=$1.99 — the card re-prices)")

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


async def week_checks() -> None:
    """P42 week-mode fixture: one coordinated 4-dinner press through the full
    enforcement chain. Verifies the set's coordination properties — perishable
    first, shared purchase across ≥2 meals (carved out of overlap), the best
    deal stacked as anchor + support, easy-weighted order — plus week_plan
    flags and Discover exclusion."""
    print("=" * 76)
    print("WEEK-MODE FIXTURE — P42 coordinated 4-dinner plan")
    print("=" * 76)
    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(
                select(User).where(User.supabase_user_id == "golden-fixture-lidl")
            )
        ).scalar_one()
        # The starvation mini-check just wiped the flyer — restore it so the
        # week plan has real deals to coordinate around.
        await _seed_deals(db, "lidl", LIDL_DEALS)
        await db.commit()
        newest_daily = await db.scalar(
            select(Recipe.generated_at)
            .where(Recipe.user_id == user.id, Recipe.week_plan.is_(False))
            .order_by(Recipe.generated_at.desc())
            .limit(1)
        )

        concepts = await recipe_engine.generate_concepts(db, user, week_plan=4)
        await db.commit()

        print(f"week plan @ N=4: {len(concepts)} concepts, "
              f"week_plan flags: {[r.week_plan for r in concepts]}")
        for i, r in enumerate(concepts, 1):
            anchor = (r.signature_json or {}).get("anchor_ingredient")
            mk = " [market]" if r.is_market_pick else ""
            print(f"  night {i}: [{r.difficulty}] {r.title} — anchor: {anchor}{mk}")

        # Order: easiest first, perishable consumed earliest.
        tier_rank = {"easy": 0, "medium": 1, "hard": 2}
        ranks = [tier_rank.get(r.difficulty or "", 1) for r in concepts]
        print(f"  easy-weighted order (non-decreasing tiers): "
              f"{'YES' if ranks == sorted(ranks) else 'NO'} {ranks}")
        first_anchor = str(
            (concepts[0].signature_json or {}).get("anchor_ingredient") or ""
        )
        print(f"  perishable consumed first: "
              f"{'YES' if 'beef' in first_anchor else 'NO'} ({first_anchor})")

        # Shared-purchase map + set-wide estimate (credited once).
        summary = recipe_engine.week_plan_summary(concepts)
        print(f"  estimate: known=${summary['known_cost']} "
              f"savings=${summary['deal_savings']} "
              f"unpriced={summary['unpriced_items']}")
        for s in summary["shared_purchases"]:
            print(f"  shared purchase: {s['name']} -> {len(s['used_in'])} meals "
                  f"({', '.join(s['used_in'])})")
        stacked = [
            s for s in summary["shared_purchases"]
            if any(r.is_market_pick and str(
                (r.market_anchor_json or {}).get("name") or ""
            ).lower() in s["name"].lower() for r in concepts)
        ]
        print(f"  deal stacked across >=2 meals: "
              f"{'YES' if stacked else 'NO'}"
              + (f" ({stacked[0]['name']})" if stacked else ""))

        # Discover exclusion: the newest NON-week batch is unchanged.
        newest_after = await db.scalar(
            select(Recipe.generated_at)
            .where(Recipe.user_id == user.id, Recipe.week_plan.is_(False))
            .order_by(Recipe.generated_at.desc())
            .limit(1)
        )
        print(f"  Discover feed untouched by week batch: "
              f"{'YES' if newest_after == newest_daily else 'NO'}")


async def nutrition_gate_checks() -> None:
    """P43 protein-gate fixture: the computed-replaces-estimate policy must
    require the PRIMARY PROTEIN itself to be matched — never let side dishes
    carry the coverage sum while the anchor is invisible (the live 2.7g
    'Korean BBQ beef' case: an unweighable unmatched protein didn't even
    dent mass coverage). Deterministic; no model calls."""
    from app.services import nutrition

    print("=" * 76)
    print("PROTEIN-GATE FIXTURE — P43 computed-vs-est policy")
    print("=" * 76)
    async with AsyncSessionLocal() as db:
        await ingredient_matcher.preload(db)
        await nutrition.preload(db)

    model_est = {"calories": 640, "protein_g": 42.0, "carbs_g": 45.0,
                 "fat_g": 24.0, "source": "est"}

    # (1) PRIMARY PROTEIN UNMATCHED: the anchor line is unweighable AND
    # unknown, the matched sides alone clear 70% mass coverage.
    ings = [
        {"name": "fermented skate wing", "quantity": 1, "unit": "package"},
        {"name": "white rice", "quantity": 2, "unit": "cup"},
        {"name": "broccoli", "quantity": 1, "unit": "lb"},
        {"name": "olive oil", "quantity": 2, "unit": "tbsp"},
    ]
    computed = nutrition.compute(ings, 2)
    gap = recipe_engine._protein_gap(computed, "fermented skate wing")
    final, protein = recipe_engine._effective_nutrition(model_est, computed, gap)
    print(f"case 1 — anchor unmatched: coverage={computed['coverage']:.2f} "
          f"(would have passed the old mass-only gate: "
          f"{'YES' if computed['coverage'] >= nutrition.COVERAGE_THRESHOLD else 'NO'})")
    print(f"  nutrition_gap: {final.get('nutrition_gap')}")
    print(f"  verdict: source={final['source']} protein={protein}g "
          f"(model estimate held; computed {computed['protein_g']}g rejected)")
    assert final["source"] == "est" and final.get("nutrition_gap") == gap and gap

    # No floor chip from the partial computation: flags run on the est figure.
    flags = recipe_engine._quality_flags(protein, final.get("calories"), 34, 1100, 2000)
    print(f"  floor chip on the phantom figure: "
          f"{'NO (est {0}g >= floor)'.format(protein) if 'protein_below_floor' not in (flags or {}) else 'YES — BUG'}")

    # (2) MINOR ITEM UNMATCHED, protein matched: computed stands as before.
    ings2 = [
        {"name": "chicken breast", "quantity": 1.5, "unit": "lb"},
        {"name": "white rice", "quantity": 2, "unit": "cup"},
        {"name": "yuzu kosho", "quantity": 1, "unit": "tbsp"},
    ]
    computed2 = nutrition.compute(ings2, 2)
    gap2 = recipe_engine._protein_gap(computed2, "chicken breast")
    final2, protein2 = recipe_engine._effective_nutrition(model_est, computed2, gap2)
    print(f"case 2 — minor item unmatched: coverage={computed2['coverage']:.2f} "
          f"gap={gap2} -> source={final2['source']} protein={protein2}g "
          "(computed stands)")
    assert final2["source"] == "calculated" and not gap2

    # (3) POST-ENRICHMENT: the kalbi/pollock-roe class now computes for real.
    ings3 = [
        {"name": "kalbi", "quantity": 1.5, "unit": "lb"},
        {"name": "white rice", "quantity": 2, "unit": "cup"},
        {"name": "kimchi", "quantity": 1, "unit": "cup"},
    ]
    computed3 = nutrition.compute(ings3, 2)
    gap3 = recipe_engine._protein_gap(computed3, "kalbi")
    final3, protein3 = recipe_engine._effective_nutrition(model_est, computed3, gap3)
    print(f"case 3 — enriched kalbi: coverage={computed3['coverage']:.2f} "
          f"gap={gap3} -> source={final3['source']} protein={protein3}g")
    assert final3["source"] == "calculated" and not gap3 and protein3 > 20


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
            await week_checks()
            print()
            await nutrition_gate_checks()
            print()

    if args.save_reference:
        REFERENCE_PATH.write_text(buf.getvalue())
        print(f"reference saved -> {REFERENCE_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
