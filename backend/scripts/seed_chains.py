"""Seed supported_chains from Wikipedia (Prompt 24). Idempotent upsert on slug.

Fetches and parses
https://en.wikipedia.org/wiki/List_of_supermarket_chains_in_the_United_States
at run time as the canonical source, seeds banner-level rows with a curated
parent-company map + special-source hints, then supplements with a guaranteed
list (club stores + explicitly-named specialty/international banners) so the
catalog is complete regardless of the page's exact structure.

Run from backend/:
    .venv/Scripts/python.exe scripts/seed_chains.py
"""

import asyncio
import pathlib
import re
import sys
import unicodedata

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.database import AsyncSessionLocal
from app.models.store import SupportedChain

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_supermarket_chains_in_the_United_States"
# Wikipedia blocks generic UAs; a descriptive UA per their policy is required.
_UA = "PantryAI/1.0 (https://pantryai.app; contact@pantryai.app) circular-seed"

# Section heading -> catalog category. Sections NOT listed are excluded
# (cooperatives, wholesalers, defunct, see-also, references).
_SECTION_CATEGORY = {
    "National chains": "national",
    "Regional chains": "regional",
    "Notable local chains": "local",
    "Deep-discount and limited-assortment chains": "discount",
    "Ethnic chains": "international",
    "Specialty and natural foods": "natural",
}
_INCLUDED_H2 = set(_SECTION_CATEGORY)
# h2 sections whose list items are consumer banners we never want.
_EXCLUDED_H2 = {
    "Retailers' cooperatives",
    "Wholesalers",
    "Defunct chains",
    "See also",
    "References",
    "Contents",
}

# Names to drop even if they appear in an included section.
_EXCLUDE_NAME_RE = re.compile(
    r"\b(dollar general|family dollar|dollar tree|big lots|five below|"
    r"99 cents only|variety wholesalers|unfi|topco|c&s wholesale|"
    r"associated wholesale|spartannash|awg)\b",
    re.I,
)
# li text markers that mean the banner is gone.
_DEFUNCT_RE = re.compile(
    r"\b(defunct|now merged|merged (in)?to|now part of|acquired by|"
    r"rebranded|renamed to|converted to|closed in|ceased|out of business)\b",
    re.I,
)

_DASHES = "–—"  # en / em dash

# Curated parent-company map (banner slug -> parent). Best-effort for the majors.
_PARENT = {
    # Albertsons
    "albertsons": "Albertsons", "safeway": "Albertsons", "vons": "Albertsons",
    "acme_markets": "Albertsons", "jewel_osco": "Albertsons", "shaws": "Albertsons",
    "star_market": "Albertsons", "randalls": "Albertsons", "tom_thumb": "Albertsons",
    "pavilions": "Albertsons", "haggen": "Albertsons", "carrs": "Albertsons",
    "united_supermarkets": "Albertsons", "market_street": "Albertsons",
    # Kroger
    "kroger": "Kroger", "ralphs": "Kroger", "fred_meyer": "Kroger",
    "king_soopers": "Kroger", "smiths": "Kroger", "qfc": "Kroger",
    "harris_teeter": "Kroger", "food_4_less": "Kroger", "dillons": "Kroger",
    "city_market": "Kroger", "marianos": "Kroger", "pick_n_save": "Kroger",
    "frys_food_and_drug": "Kroger", "frys": "Kroger", "ruler_foods": "Kroger",
    # Ahold Delhaize
    "stop_and_shop": "Ahold Delhaize", "giant_food": "Ahold Delhaize",
    "giant": "Ahold Delhaize", "food_lion": "Ahold Delhaize",
    "hannaford": "Ahold Delhaize", "the_giant_company": "Ahold Delhaize",
    # Wakefern
    "shoprite": "Wakefern", "the_fresh_grocer": "Wakefern", "price_rite": "Wakefern",
    "fairway_market": "Wakefern",
    # Amazon
    "whole_foods_market": "Amazon", "whole_foods": "Amazon", "amazon_fresh": "Amazon",
    # Others
    "sams_club": "Walmart", "walmart": "Walmart", "walmart_neighborhood_market": "Walmart",
    "food_city": "K-VA-T", "meijer": "Meijer", "heb": "H-E-B", "publix": "Publix",
    "wegmans": "Wegmans", "aldi": "Aldi", "lidl": "Lidl Stiftung",
}

# Special deals-source hints for the probe (Prompt 24 A1 notes).
_SOURCE_HINT = {
    "whole_foods_market": "structured",
    "whole_foods": "structured",
    "h_mart": "chain_site",
    "patel_brothers": "chain_site",
    "99_ranch_market": "chain_site",
    "mitsuwa_marketplace": "chain_site",
}

# Guaranteed banners (club + explicitly-named specialty/international/natural/
# discount) so they exist with correct category/source even if parsing misses
# them. Each: (name, slug, category, parent, source_type|None).
_GUARANTEE = [
    # Existing launch chains — ensure categorized (deals state preserved in DB).
    ("ShopRite", "shoprite", "regional", "Wakefern", None),
    ("Stop & Shop", "stop_and_shop", "regional", "Ahold Delhaize", None),
    ("Vons", "vons", "national", "Albertsons", None),
    ("Costco", "costco", "club", "Costco", None),
    ("BJ's Wholesale Club", "bjs", "club", "BJ's", None),
    ("Sam's Club", "sams_club", "club", "Walmart", None),
    ("H Mart", "h_mart", "international", None, "chain_site"),
    ("99 Ranch Market", "99_ranch_market", "international", None, "chain_site"),
    ("Mitsuwa Marketplace", "mitsuwa_marketplace", "international", None, "chain_site"),
    ("Seafood City", "seafood_city", "international", None, "chain_site"),
    ("Patel Brothers", "patel_brothers", "international", None, "chain_site"),
    ("Fiesta Mart", "fiesta_mart", "international", None, None),
    ("Cardenas Markets", "cardenas_markets", "international", None, None),
    ("Vallarta Supermarkets", "vallarta_supermarkets", "international", None, None),
    ("Sedano's", "sedanos", "international", None, None),
    ("El Super", "el_super", "international", None, None),
    ("Northgate Market", "northgate_market", "international", None, None),
    ("Whole Foods Market", "whole_foods_market", "natural", "Amazon", "structured"),
    ("Sprouts Farmers Market", "sprouts_farmers_market", "natural", None, None),
    ("The Fresh Market", "the_fresh_market", "natural", None, None),
    ("Fresh Thyme Market", "fresh_thyme_market", "natural", None, None),
    ("Natural Grocers", "natural_grocers", "natural", None, None),
    ("Aldi", "aldi", "discount", "Aldi", None),
    ("Grocery Outlet", "grocery_outlet", "discount", None, None),
    ("Save A Lot", "save_a_lot", "discount", None, None),
    ("WinCo Foods", "winco_foods", "discount", None, None),
]


def _clean(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").replace("​", "").strip()


def _slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"&", " and ", s.lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def _parse_banner(li_text: str) -> tuple[str, list[str]] | None:
    """(name, areas_served) from a list item, or None if it isn't a banner."""
    t = _clean(li_text)
    if not t or _DEFUNCT_RE.search(t) or _EXCLUDE_NAME_RE.search(t):
        return None
    # areas_served = contents of the first parenthetical, if any.
    areas: list[str] = []
    m = re.search(r"\(([^)]{2,200})\)", t)
    if m:
        areas = [
            a.strip()
            for a in re.split(r",|;|/| and ", m.group(1))
            if 2 <= len(a.strip()) <= 40
        ][:20]
    # name = leading text before the first '(', dash-description, or comma.
    name = re.split(rf"\s*[\(\{_DASHES}]", t)[0]
    name = name.split(",")[0]
    name = _clean(name).strip(" -" + _DASHES + "•*")
    # Plausibility: 2-60 chars, has a letter, not all-lowercase prose.
    if not (2 <= len(name) <= 60) or not re.search(r"[A-Za-z]", name):
        return None
    if name.lower() in {"see also", "references", "notes", "list"}:
        return None
    return name, areas


def _disambiguate(slug: str, name: str, areas: list[str], seen: set[str]) -> str:
    if slug not in seen:
        return slug
    # Known collision: the two unrelated Price Choppers.
    if name.lower() == "price chopper":
        joined = " ".join(areas).lower()
        return "price_chopper_kc" if ("kansas" in joined or "missouri" in joined) \
            else "price_chopper_northeast"
    n = 2
    while f"{slug}_{n}" in seen:
        n += 1
    return f"{slug}_{n}"


def _harvest(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []
    seen: set[str] = set()
    current_cat: str | None = None
    included = False
    for el in soup.find_all(["h2", "h3", "li"]):
        if el.name in ("h2", "h3"):
            title = _clean(el.get_text()).replace("[edit]", "").strip()
            if el.name == "h2":
                if title in _INCLUDED_H2:
                    included, current_cat = True, _SECTION_CATEGORY[title]
                elif title in _EXCLUDED_H2:
                    included, current_cat = False, None
                else:
                    included, current_cat = False, None
            # h3 keeps the parent h2 category (regional East/West, ethnic subsecs)
            continue
        if not included or current_cat is None:
            continue
        parsed = _parse_banner(el.get_text(" ", strip=True))
        if parsed is None:
            continue
        name, areas = parsed
        slug = _slugify(name)
        if not slug:
            continue
        slug = _disambiguate(slug, name, areas, seen)
        seen.add(slug)
        rows.append(
            {
                "chain_name": name,
                "chain_slug": slug,
                "category": current_cat,
                "areas_served": areas or None,
                "parent_company": _PARENT.get(slug),
                "source_type_hint": _SOURCE_HINT.get(slug),
            }
        )
    return rows


def _merge_guarantee(rows: list[dict]) -> list[dict]:
    by_slug = {r["chain_slug"]: r for r in rows}
    for name, slug, cat, parent, stype in _GUARANTEE:
        r = by_slug.get(slug)
        if r is None:
            r = {
                "chain_name": name, "chain_slug": slug, "category": cat,
                "areas_served": None, "parent_company": parent,
                "source_type_hint": stype,
            }
            rows.append(r)
            by_slug[slug] = r
        else:
            # Guarantee is curated/authoritative for its entries; keep the
            # parsed areas_served but override category/parent/source hint.
            r["category"] = cat
            r["parent_company"] = parent or r.get("parent_company")
            r["source_type_hint"] = stype or r.get("source_type_hint")
    return rows


async def main() -> None:
    print(f"Fetching {WIKI_URL} ...")
    resp = httpx.get(
        WIKI_URL, headers={"User-Agent": _UA}, follow_redirects=True, timeout=30
    )
    resp.raise_for_status()
    rows = _merge_guarantee(_harvest(resp.text))
    print(f"Parsed {len(rows)} banners from Wikipedia (+ guaranteed supplement).")

    async with AsyncSessionLocal() as session:
        for r in rows:
            values = {
                "chain_name": r["chain_name"],
                "chain_slug": r["chain_slug"],
                "category": r["category"],
                "areas_served": r["areas_served"],
                "parent_company": r["parent_company"],
                "google_places_query": r["chain_name"],
                "has_weekly_circular": True,
                "circular_refresh_day": "friday",
                "is_active": True,
            }
            if r["source_type_hint"]:
                values["source_type"] = r["source_type_hint"]
            stmt = insert(SupportedChain).values(**values)
            # Preserve probe/live state: don't clobber deals_status/source_url;
            # keep any existing source_type (probe result) via COALESCE.
            update_cols = {
                "chain_name": stmt.excluded.chain_name,
                "category": stmt.excluded.category,
                "areas_served": stmt.excluded.areas_served,
                "parent_company": func.coalesce(
                    SupportedChain.parent_company, stmt.excluded.parent_company
                ),
                "google_places_query": func.coalesce(
                    SupportedChain.google_places_query, stmt.excluded.google_places_query
                ),
                "source_type": func.coalesce(
                    SupportedChain.source_type, stmt.excluded.source_type
                ),
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["chain_slug"], set_=update_cols
            )
            await session.execute(stmt)
        await session.commit()

        by_cat = (
            await session.execute(
                select(SupportedChain.category, func.count())
                .group_by(SupportedChain.category)
                .order_by(func.count().desc())
            )
        ).all()
        total = await session.scalar(select(func.count()).select_from(SupportedChain))

    print("\nSeeded catalog by category:")
    for cat, n in by_cat:
        print(f"  {cat or '(uncategorized)':16} {n}")
    print(f"  {'TOTAL':16} {total}")


if __name__ == "__main__":
    asyncio.run(main())
