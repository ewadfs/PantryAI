"""Deterministic (no-LLM) quantity parsing + sufficiency math.

Parses quantity strings from pantry items ("1 lb", "~2 lbs", "3-4 count",
"half gallon") and recipe ingredients (1.5 + "lbs") into a normalized value and
a unit family, then compares a pantry holding against a recipe's demand. Never
guesses: an unparseable or cross-family comparison is UNVERIFIABLE, not a match.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Unit -> factor into the family's base unit.
_WEIGHT = {  # base: ounce
    "oz": 1.0, "ounce": 1.0, "ounces": 1.0,
    "lb": 16.0, "lbs": 16.0, "pound": 16.0, "pounds": 16.0, "#": 16.0,
    "g": 0.035274, "gram": 0.035274, "grams": 0.035274, "gr": 0.035274,
    "kg": 35.274, "kilogram": 35.274, "kilograms": 35.274,
}
_VOLUME = {  # base: fluid ounce
    "floz": 1.0, "oz-fl": 1.0,
    "cup": 8.0, "cups": 8.0, "c": 8.0,
    "pint": 16.0, "pints": 16.0, "pt": 16.0,
    "quart": 32.0, "quarts": 32.0, "qt": 32.0,
    "gallon": 128.0, "gallons": 128.0, "gal": 128.0,
    "tbsp": 0.5, "tablespoon": 0.5, "tablespoons": 0.5, "tbs": 0.5,
    "tsp": 0.166667, "teaspoon": 0.166667, "teaspoons": 0.166667,
    "ml": 0.033814, "milliliter": 0.033814, "milliliters": 0.033814,
    "l": 33.814, "liter": 33.814, "liters": 33.814, "litre": 33.814,
}
_CONTAINER = {
    "can", "cans", "jar", "jars", "bottle", "bottles", "box", "boxes",
    "bag", "bags", "packet", "packets", "pack", "packs", "container",
    "containers", "package", "packages", "pkg", "tub", "tubs", "carton",
    "cartons", "case", "cases", "stick", "sticks", "loaf", "loaves",
}
_COUNT = {
    "count", "ct", "each", "ea", "piece", "pieces", "pcs", "pc",
    "clove", "cloves", "head", "heads", "bunch", "bunches", "stalk",
    "stalks", "egg", "eggs", "slice", "slices", "fillet", "fillets",
    "breast", "breasts", "thigh", "thighs", "link", "links", "ear", "ears",
}
# Count tokens that carry no specific shape — freely comparable with each other.
_GENERIC_COUNT = {"", "count", "ct", "each", "ea", "piece", "pieces", "pcs", "pc", "x"}

# Canonical token per weight/volume alias — keeps rounding + display consistent.
_CANON = {
    "oz": "oz", "ounce": "oz", "ounces": "oz",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb", "#": "lb",
    "g": "g", "gram": "g", "grams": "g", "gr": "g",
    "kg": "kg", "kilogram": "kg", "kilograms": "kg",
    "floz": "floz", "oz-fl": "floz",
    "cup": "cup", "cups": "cup", "c": "cup",
    "pint": "pint", "pints": "pint", "pt": "pint",
    "quart": "quart", "quarts": "quart", "qt": "quart",
    "gallon": "gallon", "gallons": "gallon", "gal": "gallon",
    "tbsp": "tbsp", "tablespoon": "tbsp", "tablespoons": "tbsp", "tbs": "tbsp",
    "tsp": "tsp", "teaspoon": "tsp", "teaspoons": "tsp",
    "ml": "ml", "milliliter": "ml", "milliliters": "ml",
    "l": "l", "liter": "l", "liters": "l", "litre": "l",
}
_PLURAL = re.compile(r"(.+?)(?:es|s)$")


def _singular(token: str) -> str:
    """Canonical count/container token (weight/volume use _CANON instead)."""
    if token.endswith("ss"):
        return token
    m = _PLURAL.match(token)
    return m.group(1) if m else token


@dataclass
class Qty:
    value: float        # normalized into the family base unit
    family: str         # 'weight' | 'volume' | 'container' | 'count'
    unit: str           # canonical (singular) unit token, "" for a bare number
    factor: float       # base-per-unit, to convert back to `unit`


def _extract_value(text: str) -> float | None:
    t = text.replace("~", " ").replace("½", " 1/2").strip()
    # mixed number "1 1/2"
    m = re.search(r"(\d+)\s+(\d+)\s*/\s*(\d+)", t)
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    # simple fraction "1/2"
    m = re.search(r"(\d+)\s*/\s*(\d+)", t)
    if m:
        return int(m.group(1)) / int(m.group(2))
    # range "3-4" / "3 – 4" -> LOWER bound
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)", t)
    if m:
        return float(m.group(1))
    # first plain number
    m = re.search(r"\d+(?:\.\d+)?", t)
    if m:
        return float(m.group())
    if "half" in t:
        return 0.5
    if re.search(r"\b(an?|one)\b", t):
        return 1.0
    return None


def _match_unit(text: str, unit: str | None) -> tuple[str | None, float, str]:
    candidates: list[str] = []
    if unit:
        candidates.append(str(unit).strip().lower())
    candidates += re.findall(r"[a-z#]+", text.lower())
    for c in candidates:
        c = c.rstrip(".")
        s = _singular(c)
        if c in _WEIGHT:
            return "weight", _WEIGHT[c], _CANON.get(c, c)
        if c in _VOLUME:
            return "volume", _VOLUME[c], _CANON.get(c, c)
        if c in _CONTAINER or s in _CONTAINER:
            return "container", 1.0, s
        if c in _COUNT or s in _COUNT:
            return "count", 1.0, s
    return None, 1.0, ""


def parse(quantity: object, unit: object = None) -> Qty | None:
    """Parse a quantity (+ optional unit) into a normalized :class:`Qty`.

    Returns None when no numeric magnitude is present (never guesses).
    """
    qpart = "" if quantity is None else str(quantity)
    upart = "" if unit is None else str(unit)
    text = f"{qpart} {upart}".strip().lower()
    if not text:
        return None
    value = _extract_value(text)
    if value is None:
        return None
    family, factor, token = _match_unit(text, upart or None)
    if family is None:
        # A bare number with no recognizable unit is treated as a count.
        return Qty(value=value, family="count", unit="", factor=1.0)
    return Qty(value=value * factor, family=family, unit=token, factor=factor)


def _tokens_compatible(a: str, b: str, family: str) -> bool:
    if family == "weight" or family == "volume":
        return True  # freely convertible within the family
    if family == "count":
        if a in _GENERIC_COUNT and b in _GENERIC_COUNT:
            return True
        return a == b
    # container: only the same container shape is comparable
    return a == b


def sufficiency(pantry: Qty | None, need: Qty | None) -> tuple[str, float]:
    """Return (state, shortfall_base) where state is
    'sufficient' | 'partial' | 'unverifiable' and shortfall_base is in the
    NEED's base unit (0.0 unless partial)."""
    if pantry is None or need is None:
        return "unverifiable", 0.0
    if pantry.family != need.family:
        return "unverifiable", 0.0
    if not _tokens_compatible(pantry.unit, need.unit, need.family):
        return "unverifiable", 0.0
    if pantry.value + 1e-9 >= need.value:
        return "sufficient", 0.0
    return "partial", need.value - pantry.value


# Round-up increments keyed on CANONICAL units, for a sane purchasable amount.
_INCREMENT = {
    "weight": {"lb": 0.25, "oz": 1.0, "g": 25.0, "kg": 0.25},
    "volume": {"gallon": 0.25, "quart": 0.5, "pint": 0.5, "cup": 0.5,
               "floz": 1.0, "tbsp": 1.0, "tsp": 1.0, "ml": 50.0, "l": 0.25},
}


def _round_up(value: float, increment: float) -> float:
    if increment <= 0:
        return value
    return math.ceil(value / increment - 1e-9) * increment


def _fmt_num(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def format_amount(base_value: float, ref: Qty) -> tuple[float, str]:
    """Express a base-unit amount in ``ref``'s unit, rounded UP to a
    purchasable increment. Returns (rounded_value_in_unit, display_string)."""
    raw = base_value / ref.factor if ref.factor else base_value
    if ref.family in ("count", "container"):
        rounded = max(1.0, math.ceil(raw - 1e-9))
    else:
        inc = _INCREMENT.get(ref.family, {}).get(ref.unit, 0.0)
        rounded = _round_up(raw, inc) if inc else raw
    unit = ref.unit
    disp = f"{_fmt_num(rounded)}{(' ' + unit) if unit else ''}".strip()
    return rounded, disp


def describe(q: Qty | None) -> str:
    """Human string for a parsed quantity, e.g. '1 lb', '3 cans', '6'."""
    if q is None:
        return "?"
    raw = q.value / q.factor if q.factor else q.value
    unit = q.unit
    return f"{_fmt_num(raw)}{(' ' + unit) if unit else ''}".strip()
