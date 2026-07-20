"""P42 verification: plan-week endpoint + ritual surface, in-process.

Stage 1/2 model calls ride the golden-batch stub; the router logic (This Week
rows, week_planned/week_list_built events, set-wide estimate, ritual card
numbers + 48h window) runs for real against the local DB.

Run: SUPABASE_JWT_SECRET=... ENVIRONMENT=production PYTHONPATH=. \
     .venv/bin/python scripts/verify_p42.py
"""

import asyncio
import logging
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
from jose import jwt
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.services import recipe_engine

logging.disable(logging.INFO)

SUB = str(uuid.uuid4())


def mint() -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": SUB, "email": f"p42-{SUB[:8]}@example.com", "aud": "authenticated",
         "role": "authenticated", "iat": now, "exp": now + 3600},
        settings.supabase_jwt_secret, algorithm="HS256",
    )


async def main() -> None:
    from scripts.golden_batch import StubAnthropic

    from app.database import AsyncSessionLocal
    from app.models.deal import CircularFetch, DealCache
    from app.models.event import Event
    from app.models.store import StoreLocation

    stub = StubAnthropic()
    recipe_engine.AsyncAnthropic = lambda api_key=None: stub

    headers = {"Authorization": f"Bearer {mint()}"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=headers, timeout=120
    ) as c:
        # Clean up synthetic fetches left by earlier verify scripts so the
        # "collapsed" baseline is real.
        from sqlalchemy import delete as _delete
        async with AsyncSessionLocal() as db:
            stale = (await db.execute(
                select(CircularFetch.id).where(
                    CircularFetch.source_url.in_(("test", "t"))))).scalars().all()
            if stale:
                await db.execute(_delete(DealCache).where(
                    DealCache.fetch_id.in_(stale)))
                await db.execute(_delete(CircularFetch).where(
                    CircularFetch.id.in_(stale)))
                await db.commit()

        me = (await c.get("/api/v1/me")).json()
        stores = (await c.get("/api/v1/stores/discover", params={"zip": "11803"})).json()["stores"]
        pick = next(s for s in stores if s["chain_slug"] == "lidl")
        r = await c.put("/api/v1/stores/mine", json={
            "store_location_ids": [pick["id"]], "default_store_id": pick["id"]})
        assert r.status_code == 200
        print(f"user {me['id']} @ {pick['store_name']}")

        # Give the test store an isolated region so the golden fixture's
        # region-less fetches can't bleed into the ritual query.
        REGION = "lidl:P42-TEST"
        async with AsyncSessionLocal() as db:
            loc = await db.get(StoreLocation, pick["id"])
            loc.region_key = REGION
            await db.commit()

        # --- OLD fetch (3 days back): the card must stay collapsed ---
        async with AsyncSessionLocal() as db:
            old = CircularFetch(
                chain_id=pick["chain_id"], region_key=REGION, source_url="t",
                status="success", valid_from=date.today() - timedelta(days=3),
                valid_to=date.today() + timedelta(days=3),
            )
            db.add(old)
            await db.flush()
            old.fetched_at = datetime.now(timezone.utc) - timedelta(days=3)
            db.add(DealCache(
                chain_id=pick["chain_id"], fetch_id=old.id,
                product_name="stale item", sale_price=1, region_key=REGION,
                valid_from=old.valid_from, valid_to=old.valid_to))
            await db.commit()
        r = await c.get("/api/v1/deals/ritual")
        assert r.status_code == 200
        rit = r.json()
        print(f"ritual (flyer fetched 3d ago): is_flip_day={rit['is_flip_day']} "
              f"(48h window enforced)")
        assert rit["is_flip_day"] is False

        # --- simulate a cron completion NOW: fresh fetch + deals ---
        async with AsyncSessionLocal() as db:
            fetch = CircularFetch(
                chain_id=pick["chain_id"], region_key=REGION, source_url="t",
                status="success", valid_from=date.today(),
                valid_to=date.today() + timedelta(days=6),
            )
            db.add(fetch)
            await db.flush()
            for name, price in [("chicken breast", 1.99), ("broccoli", 1.49)]:
                db.add(DealCache(
                    chain_id=pick["chain_id"], fetch_id=fetch.id,
                    product_name=name, sale_price=price, region_key=REGION,
                    valid_from=fetch.valid_from, valid_to=fetch.valid_to))
            await db.commit()

        r = await c.get("/api/v1/deals/ritual")
        rit = r.json()
        print(f"ritual (fresh flyer): is_flip_day={rit['is_flip_day']} "
              f"store={rit['store_name']} deals={rit['deal_count']} "
              f"matches={rit['pantry_matches']} expiring={rit['expiring_count']}")
        assert rit["is_flip_day"] is True and rit["deal_count"] == 2

        # --- plan the week ---
        r = await c.post("/api/v1/recipes/plan-week", json={"dinners": 3})
        assert r.status_code == 200, r.text
        plan = r.json()
        assert len(plan["recipes"]) == 3
        est = plan["estimate"]
        print(f"planned 3 dinners: {[x['title'] for x in plan['recipes']]}")
        print(f"estimate: known=${est['known_cost']} savings=${est['deal_savings']} "
              f"shared={[(s['name'], len(s['used_in'])) for s in est['shared_purchases']]}")

        # Saved straight to This Week.
        week_start = plan["week_start"]
        r = await c.get(f"/api/v1/week/{week_start}")
        assert r.status_code == 200
        week = r.json()
        assert len(week["recipes"]) == 3, len(week["recipes"])
        print(f"This Week has {len(week['recipes'])} saved (skipped Discover)")

        # Discover /latest does NOT show the week batch.
        r = await c.get("/api/v1/recipes/latest")
        latest = r.json()
        week_ids = {x["recipe"]["id"] for x in week["recipes"]}
        latest_ids = {x["id"] for x in latest["recipes"]}
        assert not (week_ids & latest_ids), "week batch leaked into Discover!"
        print(f"Discover /latest untouched ({len(latest['recipes'])} recipes, "
              "no overlap with the week set)")

        # --- build the list: aggregation credits shared items once ---
        r = await c.post("/api/v1/lists/build", json={"week_start": week_start})
        assert r.status_code == 200, r.text
        sl = r.json()
        items = sl.get("items") or []
        print(f"list built: {sl.get('item_count')} items, "
              f"total=${sl.get('total_known_cost')} savings=${sl.get('deal_savings')}")
        from collections import Counter
        name_counts = Counter(
            str(i.get("display_name") or "").lower() for i in items
        )
        dupes = [n for n, ct in name_counts.items() if n and ct > 1]
        assert not dupes, f"shared items duplicated on the list: {dupes}"
        multi = [
            (i.get("display_name"), i.get("from_recipes"))
            for i in items
            if isinstance(i.get("from_recipes"), list) and len(i["from_recipes"]) > 1
        ]
        for nm, srcs in multi:
            print(f"  consolidated once, feeds {len(srcs)} recipes: {nm} <- {srcs}")

        # --- events ---
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(Event.event).where(Event.user_id == me["id"])
                .order_by(Event.ts, Event.id))).scalars().all()
        print(f"events: {rows}")
        assert "week_planned" in rows and "week_list_built" in rows
        print("\nALL P42 LOCAL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
