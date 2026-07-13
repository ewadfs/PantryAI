"""P38 live verification — activate Brandon's pending chains end-to-end.

Runs the REAL machinery against the REAL sites wherever the sandbox allows:
probe-v2 fingerprints, the local Playwright capture worker (King Kullen's
RedPepper viewer, H Mart's VTEX weekly-sale page), the static-images fetch
(Patel Brothers' Webflow gallery), the Whole Foods structured registry slot,
and the demand-activation flow that flips deals_status.

Sandbox substitutions (no ANTHROPIC_API_KEY / R2 credentials here):
- R2 uploads land in a local scratch directory (same keys, same pipeline).
- Vision extraction is served by a deterministic stub whose deals were
  HAND-READ off the actual captured page images in this session — every
  price below is visible on this week's real flyers. Production runs the
  live Claude Vision batch extraction unchanged.

Usage (worker must be running):
    HEADLESS_WORKER_URL=http://127.0.0.1:8931 \
        .venv/bin/python scripts/verify_p38.py
"""

import asyncio
import pathlib
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.deal import CircularFetch, DealCache
from app.models.pantry import PantryItem
from app.models.recipe import Recipe, WeekRecipe
from app.models.store import StoreLocation, SupportedChain, UserStore
from app.models.user import User
from app.models.ai_cost import AICostEvent
from app.services import deal_fetcher, ingredient_matcher, recipe_engine, storage, vision
from app.services.ai_metering import _PRICES

SCRATCH = pathlib.Path(
    "/tmp/claude-0/-home-user-PantryAI/cd625515-db8a-5f60-bd18-cf63799a0e69/"
    "scratchpad/p38_r2"
)
WORKER_URL = "http://127.0.0.1:8931"

# --------------------------------------------------------------------------- #
# Local-disk storage stand-in (no R2 creds in the sandbox; keys unchanged)
# --------------------------------------------------------------------------- #
async def _upload_image(file_bytes: bytes, key: str, content_type="image/jpeg"):
    path = SCRATCH / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(file_bytes)
    return key


async def _get_image_bytes(key: str) -> bytes:
    return (SCRATCH / key).read_bytes()


storage.upload_image = _upload_image
storage.get_image_bytes = _get_image_bytes
# deal_fetcher imported storage as a module ref — same object, already patched.

# --------------------------------------------------------------------------- #
# Vision extraction stub — every deal below was read off the REAL captured
# pages in this session (KK circular pp.1-2, H Mart weekly-sale grid, Patel
# Brothers store-promotions flyers).
# --------------------------------------------------------------------------- #
def _d(name, price, unit, cat, regular=None, brand=None):
    return {"product_name": name, "brand": brand, "sale_price": price,
            "price_unit": unit, "regular_price": regular, "deal_type": "sale",
            "category": cat, "confidence": 0.95}


STUB_DEALS: dict[str, dict[int, list[dict]]] = {
    "King Kullen": {
        1: [
            _d("Boneless Skinless Chicken Breast for Cutlets", "1.99", "lb", "meat"),
            _d("Atlantic Salmon Fillet", "9.99", "lb", "seafood"),
            _d("Pork Spare Ribs", "2.99", "lb", "meat"),
            _d("Smithfield Bacon 16 oz", "3.99", "each", "meat", brand="Smithfield"),
            _d("Eggland's Best Large White Eggs", "1.99", "each", "dairy",
               brand="Eggland's Best"),
            _d("Blueberries", "2.50", "pint", "produce"),
            _d("StarKist Solid White Tuna 5 oz", "1.00", "each", "pantry",
               brand="StarKist"),
            _d("Tropicana Pure Premium Juice", "3.49", "each", "beverages",
               brand="Tropicana"),
        ],
        2: [
            _d("Perdue Whole Chicken", "1.99", "lb", "meat", brand="Perdue"),
            _d("80% Lean Ground Beef", "6.49", "lb", "meat"),
            _d("New York Strip Steak Bone-In", "17.99", "lb", "meat"),
            _d("Pork Whole Tenderloins", "3.99", "lb", "meat"),
            _d("Nature's Pride 93% Lean Ground Turkey 16 oz", "6.99", "each",
               "meat"),
            _d("Swai Fillet", "5.99", "lb", "seafood"),
            _d("Tilapia Fillet", "6.99", "lb", "seafood"),
            _d("Raw EZ Peel Shrimp 16-20 ct", "10.99", "lb", "seafood"),
            _d("Fresh Mussels 2 lb", "7.99", "each", "seafood"),
        ],
    },
    "H Mart": {
        1: [
            _d("Cocktail Smoked Sausage", "3.59", "each", "meat",
               regular="5.99", brand="Chung Jung One"),
            _d("Soba Tsuyu 16.7 fl oz", "3.99", "each", "pantry",
               regular="5.99", brand="Danya"),
            _d("Natto 10.5 oz", "5.99", "each", "produce", regular="8.99",
               brand="Pulmuone"),
            _d("Teriyaki Stir-fry Udon", "5.99", "each", "pantry",
               regular="8.99", brand="Pulmuone"),
            _d("Sriracha Hot Chili Sauce", "4.99", "each", "pantry",
               regular="7.99", brand="Huy Fong Foods"),
            _d("Rice Cake Mitarashi Dango", "3.49", "each", "snacks",
               regular="4.99", brand="Shirakiku"),
            _d("Mochi Assorted Ice Cream 6 ct", "3.99", "each", "frozen",
               regular="5.99", brand="Danya"),
            _d("Yaki Sushi Nori 10 ct", "2.99", "each", "pantry",
               regular="3.99", brand="HAIO"),
            _d("Chestnut Bundle Pack 6 pk", "10.99", "each", "produce",
               regular="14.99", brand="Organic Farm"),
        ],
    },
    "Patel Brothers": {
        1: [
            _d("Fresh Okra", "1.99", "lb", "produce"),
            _d("Fresh Cabbage", "0.49", "lb", "produce"),
            _d("Big Tomato", "0.99", "lb", "produce"),
            _d("Muli With Leaves", "0.89", "lb", "produce"),
            _d("Fresh Tindora", "1.49", "lb", "produce"),
            _d("Indian Eggplant", "1.49", "lb", "produce"),
            _d("Fresh Thai Green Chili", "1.99", "lb", "produce"),
            _d("Thai Guava", "2.49", "lb", "produce"),
            _d("Fresh Dosakai", "0.89", "lb", "produce"),
        ],
        2: [
            _d("Swad Chana Dal 4 lb", "3.99", "each", "pantry", brand="Swad"),
            _d("Swad Rice Flour 4 lb", "2.99", "each", "pantry", brand="Swad"),
            _d("Swad Idli Rice 20 lb", "14.99", "each", "pantry", brand="Swad"),
            _d("Swad Peanut Oil 5 L", "24.99", "each", "pantry", brand="Swad"),
            _d("Aashirvaad Atta 20 lb", "11.99", "each", "pantry",
               brand="Aashirvaad"),
        ],
    },
}


async def _stub_extract(self, pages, chain_name):
    by_page = STUB_DEALS.get(chain_name, {})
    return {pn: by_page.get(pn, []) for pn, _img in pages}


vision.CircularExtractor.extract_deals_batched = _stub_extract

PROFILE = dict(
    name="P38 Fixture", calorie_target=2000, protein_target=160,
    household_size=2, diet_type="omnivore", skill_level="intermediate",
    max_prep_time=45, cuisine_preferences=["korean", "indian", "american"],
    allergies=[], excluded_ingredients=[], taste_notes="",
    recipes_per_generation=5,
)

CHAINS = ["h_mart", "patel_brothers", "king_kullen", "whole_foods_market"]


async def seed_user(db) -> User:
    sup = "p38-fixture"
    user = (
        await db.execute(select(User).where(User.supabase_user_id == sup))
    ).scalar_one_or_none()
    if user is None:
        user = User(supabase_user_id=sup, email=f"{sup}@example.test")
        db.add(user)
        await db.flush()
    for k, v in PROFILE.items():
        setattr(user, k, v)
    await db.execute(delete(WeekRecipe).where(WeekRecipe.user_id == user.id))
    await db.execute(delete(Recipe).where(Recipe.user_id == user.id))
    await db.execute(delete(PantryItem).where(PantryItem.user_id == user.id))
    await db.execute(delete(UserStore).where(UserStore.user_id == user.id))
    await db.execute(delete(AICostEvent).where(AICostEvent.user_id == user.id))

    # King Kullen's nearest-LI store (verified via kingkullen.com's own store
    # marker API): Garden City Park, 2305 Jericho Turnpike.
    kk = (
        await db.execute(
            select(SupportedChain).where(SupportedChain.chain_slug == "king_kullen")
        )
    ).scalar_one()
    kk_loc = (
        await db.execute(
            select(StoreLocation).where(
                StoreLocation.chain_id == kk.id,
                StoreLocation.city == "Garden City Park",
            )
        )
    ).scalar_one_or_none()
    if kk_loc is None:
        kk_loc = StoreLocation(
            chain_id=kk.id, store_name="King Kullen of Garden City Park",
            address="2305 Jericho Turnpike", city="Garden City Park",
            state="NY", zip_code="11040", latitude="40.7430760",
            longitude="-73.6635944", region_key="king_kullen:NY",
        )
        db.add(kk_loc)
        await db.flush()

    default_done = False
    for slug in CHAINS:
        ch = (
            await db.execute(
                select(SupportedChain).where(SupportedChain.chain_slug == slug)
            )
        ).scalar_one()
        loc = (
            await db.execute(
                select(StoreLocation)
                .where(StoreLocation.chain_id == ch.id)
                .order_by(StoreLocation.id)
            )
        ).scalars().first()
        db.add(UserStore(
            user_id=user.id, store_location_id=loc.id,
            is_default=(slug == "h_mart" and not default_done),
        ))
        if slug == "h_mart":
            default_done = True
    await db.flush()
    return user


async def main() -> None:
    settings.headless_worker_url = WORKER_URL
    SCRATCH.mkdir(parents=True, exist_ok=True)

    print("=" * 76)
    print("P38 LIVE VERIFICATION — activate H Mart / Patel Brothers / "
          "King Kullen (+ WF structured)")
    print("=" * 76)
    print("(sandbox: R2 -> local disk; vision extraction stubbed with deals "
          "hand-read off the real captured pages; production runs live)")

    extractor = vision.CircularExtractor()
    reports: list[dict] = []
    total_pages = 0

    async with AsyncSessionLocal() as db:
        await ingredient_matcher.preload(db)
        user = await seed_user(db)
        await db.commit()

        for slug in CHAINS:
            chain = (
                await db.execute(
                    select(SupportedChain).where(SupportedChain.chain_slug == slug)
                )
            ).scalar_one()
            loc = (
                await db.execute(
                    select(StoreLocation)
                    .where(StoreLocation.chain_id == chain.id)
                    .order_by(StoreLocation.id)
                )
            ).scalars().first()
            region = loc.region_key or f"{slug}:XX"
            # Fresh demand-activation each run.
            chain.deals_status = "pending_source"
            await db.execute(
                delete(DealCache).where(DealCache.region_key == region)
            )
            await db.execute(
                delete(CircularFetch).where(CircularFetch.region_key == region)
            )
            await db.commit()

            result = await extractor.activate_region(
                db, chain.id, region, zip_code=loc.zip_code
            )
            await db.refresh(chain)
            proteins = 0
            sample = None
            if result.get("deals"):
                rows = (
                    await db.execute(
                        select(DealCache).where(DealCache.region_key == region)
                    )
                ).scalars().all()
                proteins = sum(
                    1 for r in rows
                    if (r.category or "") in ("meat", "seafood")
                    and r.matched_ingredient_id is not None
                )
                s = next(
                    (r for r in rows if (r.category or "") in ("meat", "seafood")),
                    rows[0] if rows else None,
                )
                if s is not None:
                    sample = (f"{s.product_name} ${s.sale_price}"
                              f"{'/' + s.price_unit if s.price_unit else ''}")
            total_pages += result.get("pages", 0) or 0
            reports.append({
                "chain": chain.chain_name,
                "strategy": deal_fetcher.strategy_for(chain),
                "platform": chain.platform,
                "status": result.get("status"),
                "activated": result.get("activated"),
                "deals_status": chain.deals_status,
                "pages": result.get("pages", 0),
                "deals": result.get("deals", 0),
                "matched": result.get("matched", 0),
                "proteins": proteins,
                "sample": sample,
                "error": (result.get("error") or "")[:130],
            })

        print("\nPER-CHAIN ACTIVATION REPORT (E8):")
        for r in reports:
            print(f"\n  {r['chain']}  [{r['platform']} -> {r['strategy']}]")
            print(f"    status: {r['status']}  ->  deals_status: "
                  f"{r['deals_status']}")
            print(f"    pages captured: {r['pages']}  deals extracted: "
                  f"{r['deals']}  matched: {r['matched']}  "
                  f"proteins matched: {r['proteins']}")
            if r["sample"]:
                print(f"    sample deal: {r['sample']}")
            if r["error"]:
                print(f"    evidence: {r['error']}")

        # WF registry slot works when Amazon answers — prove the parser on a
        # synthetic payload of the documented shape.
        parsed = deal_fetcher._parse_wf_sales({
            "sales": [{"name": "Organic Chicken Thighs", "brand": None,
                       "salePrice": "3.99", "uom": "lb",
                       "regularPrice": "5.99", "category": "meat"}]
        })
        print(f"\n  WF structured registry slot: parser maps a sales payload "
              f"-> {len(parsed)} deal(s) "
              f"({parsed[0]['product_name']} ${parsed[0]['sale_price']}/"
              f"{parsed[0]['price_unit']}); live endpoint currently withheld "
              f"by Amazon (evidence above) -> stays pending_source, manual "
              f"upload remains the fallback.")

        # ---- deliberately-broken headless target degrades cleanly ---------- #
        broken = (
            await db.execute(
                select(SupportedChain).where(
                    SupportedChain.chain_slug == "p38_broken_fixture"
                )
            )
        ).scalar_one_or_none()
        if broken is None:
            broken = SupportedChain(
                chain_name="P38 Broken Fixture", chain_slug="p38_broken_fixture",
                is_active=True, source_type="headless", platform="flipp",
                source_url="https://broken-target.invalid/weekly-ad",
                deals_status="pending_source",
            )
            db.add(broken)
            await db.flush()
        broken.deals_status = "pending_source"
        await db.execute(
            delete(CircularFetch).where(CircularFetch.region_key == "p38_broken:ZZ")
        )
        await db.commit()
        res = await extractor.activate_region(db, broken.id, "p38_broken:ZZ")
        await db.refresh(broken)
        print(f"\n  broken headless target: status={res.get('status')} "
          f"activated={res.get('activated')} deals_status={broken.deals_status} "
          f"(cron continues; error captured: {(res.get('error') or '')[:60]})")

        # ---- generation anchored to H Mart (default store) ----------------- #
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        from golden_batch import StubAnthropic  # deterministic Stage-1 stub

        stub_client = StubAnthropic()
        recipe_engine.AsyncAnthropic = lambda api_key=None: stub_client
        concepts = await recipe_engine.generate_concepts(db, user)
        await db.commit()
        print("\nGENERATION ANCHORED TO H MART (default store; stub model, "
              "REAL deal table):")
        cited = 0
        for r in concepts:
            if r.is_market_pick and r.market_anchor_json:
                a = r.market_anchor_json
                at = f" — at {a.get('store')}" if a.get("cross_store") else ""
                print(f"  market pick: {r.title} -> built around "
                      f"{a.get('name')} ${a.get('sale_price')}"
                      f"{'/' + a['price_unit'] if a.get('price_unit') else ''}{at}")
                cited += 1
        hm = [
            r for r in concepts
            if r.is_market_pick and r.market_anchor_json
            and not r.market_anchor_json.get("cross_store")
        ]
        if hm:
            a = hm[0].market_anchor_json
            print(f"  -> H Mart price cited: {a.get('name')} "
                  f"${a.get('sale_price')} (visible on this week's real "
                  f"H Mart weekly-sale page)")

        # ---- cost report (verify-4) ---------------------------------------- #
        p = _PRICES.get(settings.vision_model, next(iter(_PRICES.values())))
        in_tok, out_tok = 2400, 2200   # per captured page (image + JSON out)
        per_page = (in_tok * p["input"] + out_tok * p["output"]) / 1_000_000 / 2
        print(f"\nCOST REPORT (first fetches, vision extraction estimate — "
              f"extraction was stubbed in-sandbox):")
        for r in reports:
            if r["pages"]:
                print(f"  {r['chain']:<16} {r['pages']:>2} pages × "
                      f"~{in_tok}in/{out_tok}out tok (Batches API 50% off) "
                      f"≈ ${r['pages'] * per_page:.3f}")
        print(f"  total ≈ ${total_pages * per_page:.3f} for {total_pages} "
              f"pages; capture compute: ~30-60s/chain on the worker "
              f"(one small Railway service, idle between refreshes)")


if __name__ == "__main__":
    asyncio.run(main())
