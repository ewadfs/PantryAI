"""P41 verification: web-push caps + shareable recipes, in-process.

Push sends are stubbed at the pywebpush boundary (no real push service in the
sandbox) — everything else (subscription rows, cap logic, once-per-flip,
404-deletes-subscription, share slugs, public page, OG image, unshare-404s,
event rows) runs for real against the local DB.

Run: SUPABASE_JWT_SECRET=... VAPID_PUBLIC_KEY=test VAPID_PRIVATE_KEY=test \
     PYTHONPATH=. .venv/bin/python scripts/verify_p41.py
"""

import asyncio
import logging
import time
import uuid

import httpx
from jose import jwt
from sqlalchemy import select

from app.config import settings
from app.main import app

logging.disable(logging.INFO)

SUB = str(uuid.uuid4())
EMAIL = f"p41-{SUB[:8]}@example.com"


def mint(sub: str, email: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "email": email, "aud": "authenticated",
         "role": "authenticated", "iat": now, "exp": now + 3600},
        settings.supabase_jwt_secret, algorithm="HS256",
    )


SENT: list[tuple[str, str]] = []  # (endpoint, payload)
FAIL_WITH: dict[str, int] = {}  # endpoint -> status to simulate


def fake_send(sub_info: dict, payload: str) -> int | None:
    ep = sub_info["endpoint"]
    if ep in FAIL_WITH:
        return FAIL_WITH[ep]
    SENT.append((ep, payload))
    return 201


async def main() -> None:
    from app.database import AsyncSessionLocal
    from app.models.deal import CircularFetch, DealCache
    from app.models.event import Event
    from app.models.push import PushSend, PushSubscription
    from app.models.recipe import Recipe
    from app.services import push as push_service

    push_service._send_webpush_sync = fake_send  # stub the network edge

    headers = {"Authorization": f"Bearer {mint(SUB, EMAIL)}"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=headers, timeout=60
    ) as c:
        # --- setup: fresh user with a default store on a chain with deals ---
        me = (await c.get("/api/v1/me")).json()
        stores = (await c.get("/api/v1/stores/discover", params={"zip": "11803"})).json()["stores"]
        pick = next(s for s in stores if s["chain_slug"] in ("lidl", "stop_and_shop"))
        r = await c.put("/api/v1/stores/mine", json={
            "store_location_ids": [pick["id"]], "default_store_id": pick["id"]})
        assert r.status_code == 200
        print(f"user {me['id']} @ {pick['store_name']}")

        # --- A: subscribe ---
        r = await c.post("/api/v1/push/subscribe", json={
            "endpoint": "https://push.example/ep1",
            "keys": {"p256dh": "k", "auth": "a"}})
        assert r.status_code == 201, r.text
        r = await c.get("/api/v1/push/status")
        assert r.json()["subscribed_endpoints"] == ["https://push.example/ep1"]
        print("A1 subscribe: OK (push_subscribed logged)")

        async with AsyncSessionLocal() as db:
            from datetime import date, timedelta

            from app.models.store import StoreLocation

            region = await db.scalar(
                select(StoreLocation.region_key).where(
                    StoreLocation.id == pick["id"])
            )
            # Synthesize a flyer flip for the picked chain×region (local DB
            # has no live fetch for this combo).
            fetch = CircularFetch(
                chain_id=pick["chain_id"], region_key=region,
                source_url="test", status="success",
                valid_from=date.today(),
                valid_to=date.today() + timedelta(days=6))
            db.add(fetch)
            await db.flush()
            db.add(DealCache(
                chain_id=pick["chain_id"], fetch_id=fetch.id,
                product_name="Verify chicken thighs", sale_price=2.49,
                region_key=region, valid_from=fetch.valid_from,
                valid_to=fetch.valid_to))
            await db.flush()

            # --- A: flyer-flip trigger ---
            n = await push_service.notify_flyer_flip(
                db, fetch.chain_id, fetch.region_key, fetch.id)
            await db.commit()
            assert n == 1 and len(SENT) == 1, (n, SENT)
            import json as _json
            payload = _json.loads(SENT[0][1])
            print(f"A2 flyer-flip push sent: {payload['title']!r} url={payload['url']}")
            assert payload["title"].startswith("📰 New ")

            # Same flip again → once-per-flip guard.
            n = await push_service.notify_flyer_flip(
                db, fetch.chain_id, fetch.region_key, fetch.id)
            await db.commit()
            assert n == 0 and len(SENT) == 1
            print("A3 same-flip re-trigger: correctly suppressed (cap test part 1)")

            # A second flip in the same window → allowed (2/week)...
            f2 = CircularFetch(
                chain_id=fetch.chain_id, region_key=fetch.region_key,
                source_url="test", status="success",
                valid_from=fetch.valid_from, valid_to=fetch.valid_to)
            db.add(f2)
            await db.flush()
            db.add(DealCache(
                chain_id=fetch.chain_id, fetch_id=f2.id, product_name="Test deal",
                sale_price=1, region_key=fetch.region_key,
                valid_from=fetch.valid_from, valid_to=fetch.valid_to))
            await db.flush()
            n = await push_service.notify_flyer_flip(
                db, fetch.chain_id, fetch.region_key, f2.id)
            assert n == 1 and len(SENT) == 2
            # ...and a THIRD flip → weekly cap blocks.
            f3 = CircularFetch(
                chain_id=fetch.chain_id, region_key=fetch.region_key,
                source_url="test", status="success",
                valid_from=fetch.valid_from, valid_to=fetch.valid_to)
            db.add(f3)
            await db.flush()
            db.add(DealCache(
                chain_id=fetch.chain_id, fetch_id=f3.id, product_name="Test deal 2",
                sale_price=1, region_key=fetch.region_key,
                valid_from=fetch.valid_from, valid_to=fetch.valid_to))
            await db.flush()
            n = await push_service.notify_flyer_flip(
                db, fetch.chain_id, fetch.region_key, f3.id)
            await db.commit()
            assert n == 0 and len(SENT) == 2
            print("A4 hard cap: 2 sends max per rolling week — third flip suppressed")

            # 410 from push service → subscription deleted server-side.
            FAIL_WITH["https://push.example/ep1"] = 410
            for row in (await db.execute(
                    select(PushSend).where(PushSend.user_id == me["id"]))).scalars():
                await db.delete(row)
            await db.flush()
            n = await push_service.notify_flyer_flip(
                db, fetch.chain_id, fetch.region_key, f3.id)
            await db.commit()
            gone = await db.scalar(select(PushSubscription).where(
                PushSubscription.user_id == me["id"]))
            assert n == 0 and gone is None
            print("A5 410 from push service: subscription deleted (server-honored opt-out)")

        # Explicit unsubscribe endpoint (fresh sub, then one-tap out).
        await c.post("/api/v1/push/subscribe", json={
            "endpoint": "https://push.example/ep2",
            "keys": {"p256dh": "k", "auth": "a"}})
        r = await c.post("/api/v1/push/unsubscribe",
                         json={"endpoint": "https://push.example/ep2"})
        assert r.status_code == 200
        assert (await c.get("/api/v1/push/status")).json()["subscribed_endpoints"] == []
        print("A6 one-tap unsubscribe: server row deleted")

        # --- B: shareable recipe ---
        async with AsyncSessionLocal() as db:
            recipe = Recipe(
                user_id=me["id"], status="ready", title="Verify Share Skillet",
                description="A test dinner.", total_time_min=30, servings=4,
                ingredients_json=[
                    {"name": "chicken thighs", "quantity": 1.5, "unit": "lb",
                     "in_pantry": False, "on_sale": True, "sale_price": 2.49,
                     "sale_store": "Lidl", "est_cost": 3.74},
                    {"name": "rice", "quantity": 1, "unit": "cup",
                     "in_pantry": True, "on_sale": False, "est_cost": 0},
                ],
                instructions_json=["Sear the chicken.", "Steam the rice."],
                nutrition_json={"calories": 620, "protein_g": 45,
                                "carbs_g": 50, "fat_g": 22},
                generated_store_name="Lidl — Commack",
                is_market_pick=True,
                market_anchor_json={"name": "chicken thighs", "sale_price": 2.49,
                                    "price_unit": "per lb", "store": "Lidl"},
            )
            db.add(recipe)
            await db.commit()
            rid = recipe.id

        r = await c.post(f"/api/v1/recipes/{rid}/share")
        assert r.status_code == 200, r.text
        slug, url = r.json()["slug"], r.json()["url"]
        print(f"B1 share created: {url}")

        # Public fetch — logged OUT (no auth header).
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=30
        ) as anon:
            r = await anon.get(f"/api/v1/public/r/{slug}")
            assert r.status_code == 200
            pub = r.json()
            assert pub["title"] == "Verify Share Skillet"
            flat = str(pub)
            assert "in_pantry" not in flat and "est_cost" not in flat, "pantry data leaked!"
            sale = [i for i in pub["ingredients"] if i["on_sale"]]
            assert sale and sale[0]["sale_price"] == 2.49
            print(f"B2 public page (logged out): OK — deal story "
                  f"{sale[0]['name']} @ ${sale[0]['sale_price']}, no pantry data")

            r = await anon.get(f"/api/v1/public/r/{slug}/og.png")
            assert r.status_code == 200
            assert r.headers["content-type"] == "image/png"
            assert r.content[:8] == b"\x89PNG\r\n\x1a\n" and len(r.content) > 5000
            print(f"B3 OG card renders: {len(r.content)} byte PNG")

            # Unshare → immediate 404.
            rr = await c.delete(f"/api/v1/recipes/{rid}/share")
            assert rr.status_code == 200
            r = await anon.get(f"/api/v1/public/r/{slug}")
            assert r.status_code == 404
            r = await anon.get(f"/api/v1/public/r/{slug}/og.png")
            assert r.status_code == 404
            print("B4 unshare: slug and OG both 404 immediately")

        # share_converted via client event (new-user welcome ?ref flow).
        r = await c.post("/api/v1/events",
                         json={"event": "share_converted", "meta": {"slug": slug}})
        assert r.status_code == 202
        r = await c.post("/api/v1/events", json={"event": "push_opened"})
        assert r.status_code == 202

        # --- event rows ---
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(Event.event).where(Event.user_id == me["id"])
                .order_by(Event.ts))).scalars().all()
        print(f"\nevents for user {me['id']}: {rows}")
        for expected in ("push_subscribed", "share_created", "share_visited",
                         "share_converted", "push_opened"):
            assert expected in rows, f"missing {expected}"
        print("\nALL P41 CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
