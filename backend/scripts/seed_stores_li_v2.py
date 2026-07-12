"""Prompt 24-lite v2 — Brandon's other Long Island stores.

Seeds store_locations (with region_key) for Aldi, Whole Foods, Stop & Shop
(Deer Park), H Mart, and Patel Brothers; consolidates the duplicate Whole Foods
catalog rows; and records each chain's deal-source strategy in notes.

Addresses web-verified July 2026 (see per-row comments). Idempotent: locations
upsert on (store_name, zip_code); chain metadata is set explicitly each run.

Run from backend/:
    .venv/Scripts/python.exe scripts/seed_stores_li_v2.py
"""

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select

from app.database import AsyncSessionLocal
from app.models.store import StoreLocation, SupportedChain
from app.services import regions

# chain_slug, store_name, address, city, state, zip_code
# Verified via web lookup (ALDI/WFM/S&S/H Mart/Patel store locators, Jul 2026).
STORES = [
    # ALDI — no store in Commack proper; nearest to 11746 is East Northport,
    # next-nearest to the south is Bethpage. (Substitution noted in report.)
    ("aldi", "ALDI — East Northport", "4000 Jericho Tpke", "East Northport", "NY", "11731"),
    ("aldi", "ALDI — Bethpage", "3988 Hempstead Tpke", "Bethpage", "NY", "11714"),
    # Whole Foods Market
    ("whole_foods_market", "Whole Foods Market — Jericho", "429 N Broadway", "Jericho", "NY", "11753"),
    ("whole_foods_market", "Whole Foods Market — Lake Grove", "120 New Moriches Rd", "Lake Grove", "NY", "11755"),
    ("whole_foods_market", "Whole Foods Market — Manhasset", "2101 Northern Blvd", "Manhasset", "NY", "11030"),
    # Stop & Shop — the requested "Deer Park" location EXISTS (421 Commack Rd).
    ("stop_and_shop", "Stop & Shop — Deer Park", "421 Commack Rd", "Deer Park", "NY", "11729"),
    # H Mart
    ("h_mart", "H Mart — Williston Park", "400 Hillside Ave", "Williston Park", "NY", "11596"),
    # Patel Brothers
    ("patel_brothers", "Patel Brothers — Hicksville", "415 S Broadway", "Hicksville", "NY", "11801"),
]

# Duplicate Whole Foods catalog rows to fold into `whole_foods_market` (they have
# zero locations/deals). Keeps the store picker from showing three "Whole Foods".
WF_CANONICAL = "whole_foods_market"
WF_DUP_SLUGS = ["whole_foods", "amazon_whole_foods_market"]

# chain_slug -> (source_type, source_url, notes). Documents the sourcing strategy
# decided for this pass (Prompt 24-lite v2 Section B).
CHAIN_META = {
    "whole_foods_market": (
        "structured",
        "https://www.wholefoodsmarket.com/sales",
        "Deal source: intended STRUCTURED HTML parse of wholefoodsmarket.com/sales. "
        "PROBED Jul 2026: the /sales page is a JS single-page app — NO server-"
        "rendered deals, no JSON-LD / __NEXT_DATA__ / preloaded state, prices load "
        "client-side. A pure-HTML parser is not viable; a working structured path "
        "needs their internal promotions API (store-scoped) or a headless render. "
        "Deferred: not wiring a fragile scraper. Interim option = Vision on the "
        "printable weekly flyer.",
    ),
    "h_mart": (
        "chain_site",
        "https://www.hmart.com/weeklyad",
        "Deal source: NY/LI regional weekly flyer IMAGES from hmart.com → existing "
        "Vision extraction. Live Vision run deferred to a follow-up pass.",
    ),
    "patel_brothers": (
        "chain_site",
        "https://www.patelbros.com/",
        "Deal source: specials flyer images from patelbros.com → Vision. Image "
        "chain is JS-walled; documented fallback is the manual circular-image "
        "upload path (no new source code wired this pass).",
    ),
}


async def main() -> None:
    async with AsyncSessionLocal() as db:
        # --- consolidate duplicate Whole Foods chains (0 locations/deals) ------
        canon = (
            await db.execute(
                select(SupportedChain).where(SupportedChain.chain_slug == WF_CANONICAL)
            )
        ).scalar_one_or_none()
        if canon is None:
            raise SystemExit(f"Canonical chain {WF_CANONICAL!r} missing — run seed_chains.py first.")
        for dup_slug in WF_DUP_SLUGS:
            dup = (
                await db.execute(
                    select(SupportedChain).where(SupportedChain.chain_slug == dup_slug)
                )
            ).scalar_one_or_none()
            if dup is None:
                continue
            nloc = await db.scalar(
                select(func.count()).select_from(StoreLocation).where(
                    StoreLocation.chain_id == dup.id
                )
            )
            if nloc:
                print(f"  ! {dup_slug} has {nloc} locations — NOT deleting; skipping.")
                continue
            await db.execute(delete(SupportedChain).where(SupportedChain.id == dup.id))
            print(f"  consolidated Whole Foods dup: removed chain {dup_slug!r} (id {dup.id})")

        # --- chain source metadata --------------------------------------------
        for slug, (stype, url, notes) in CHAIN_META.items():
            c = (
                await db.execute(select(SupportedChain).where(SupportedChain.chain_slug == slug))
            ).scalar_one_or_none()
            if c is None:
                print(f"  ! chain {slug!r} missing — skipping metadata")
                continue
            c.source_type = stype
            c.source_url = url
            c.notes = notes
            c.has_weekly_circular = True

        # --- store locations (with region_key) --------------------------------
        chain_ids = dict(
            (await db.execute(select(SupportedChain.chain_slug, SupportedChain.id))).all()
        )
        missing = {s[0] for s in STORES} - set(chain_ids)
        if missing:
            raise SystemExit(f"Missing chains {sorted(missing)} — run seed_chains.py first.")

        for slug, name, address, city, state, zip_code in STORES:
            rk = regions.region_key(slug, state)
            existing = await db.scalar(
                select(StoreLocation).where(
                    StoreLocation.store_name == name,
                    StoreLocation.zip_code == zip_code,
                )
            )
            if existing is None:
                db.add(
                    StoreLocation(
                        chain_id=chain_ids[slug],
                        store_name=name, address=address, city=city, state=state,
                        zip_code=zip_code, region_key=rk, is_active=True,
                    )
                )
            else:
                existing.chain_id = chain_ids[slug]
                existing.address = address
                existing.city = city
                existing.state = state
                existing.region_key = rk
                existing.is_active = True

        await db.commit()

        total = await db.scalar(select(func.count()).select_from(StoreLocation))
        print(f"\nseeded {len(STORES)} target locations; store_locations total={total}")
        for slug in ("aldi", "whole_foods_market", "stop_and_shop", "h_mart", "patel_brothers"):
            c = (await db.execute(select(SupportedChain).where(SupportedChain.chain_slug == slug))).scalar_one()
            locs = (await db.execute(select(StoreLocation).where(StoreLocation.chain_id == c.id))).scalars().all()
            print(f"  {slug}: status={c.deals_status} src={c.source_type} "
                  f"locs={[l.store_name for l in locs]}")


if __name__ == "__main__":
    asyncio.run(main())
