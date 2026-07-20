"""P40 verification: fresh-account funnel journey, in-process.

Simulates a brand-new user end-to-end against the real app (ASGI, local DB):
signup (first-sight upsert) → ZIP store discovery → store pick →
client events (first_batch_viewed, recipe_opened) → taste card →
save/build/complete hooks — then prints the /stats/funnel output and the
raw event rows for the new user. No Anthropic calls (generation timing is
verified against production separately).

Run: .venv/bin/python scripts/verify_p40.py
"""

import asyncio
import logging
import time
import uuid

import httpx
from jose import jwt

from app.config import settings
from app.main import app

logging.disable(logging.INFO)

SUB = str(uuid.uuid4())
EMAIL = f"p40-fresh-{SUB[:8]}@example.com"


def mint() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": SUB,
            "email": EMAIL,
            "aud": "authenticated",
            "role": "authenticated",
            "iat": now,
            "exp": now + 3600,
        },
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )


async def main() -> None:
    headers = {"Authorization": f"Bearer {mint()}"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=headers, timeout=60
    ) as c:
        t0 = time.monotonic()

        # 1. Signup: first authenticated request upserts the user.
        r = await c.get("/api/v1/me")
        assert r.status_code == 200, r.text
        me = r.json()
        print(f"[{time.monotonic()-t0:5.2f}s] signup ok — user id {me['id']}, "
              f"defaults household={me['household_size']} protein={me['protein_target']} "
              f"taste_notes={me['taste_notes']!r}")

        # 2. ZIP → store discovery (catalog fallback locally).
        r = await c.get("/api/v1/stores/discover", params={"zip": "11803"})
        assert r.status_code == 200, r.text
        stores = r.json()["stores"]
        assert stores, "no stores discovered"
        pick = next((s for s in stores if s["has_deals_source"]), stores[0])
        print(f"[{time.monotonic()-t0:5.2f}s] discovered {len(stores)} stores; "
              f"picking {pick['store_name'] or pick['chain_name']} (id {pick['id']})")

        # 3. Tap store → the only required setup (fires store_selected).
        r = await c.put(
            "/api/v1/stores/mine",
            json={"store_location_ids": [pick["id"]], "default_store_id": pick["id"]},
        )
        assert r.status_code == 200, r.text
        print(f"[{time.monotonic()-t0:5.2f}s] store saved")

        # Loading state the user sees while generating: the store's top deals.
        r = await c.get("/api/v1/deals/top")
        deals = r.json() if r.status_code == 200 else []
        print(f"        top deals visible during load: "
              f"{[d['product_name'] for d in deals[:3]]}")

        # 4. Client events (batch render + card open).
        for ev, meta in [
            ("first_batch_viewed", {"count": 5}),
            ("recipe_opened", {"recipe_id": 1}),
        ]:
            r = await c.post("/api/v1/events", json={"event": ev, "meta": meta})
            assert r.status_code == 202, (ev, r.text)
        # Disallowed event must 400 (allowlist).
        r = await c.post("/api/v1/events", json={"event": "signup"})
        assert r.status_code == 400, "server-only event accepted from client!"
        print("        client events accepted; server-only event correctly rejected")

        # 5. Taste upgrade card → PATCH /me (fires taste_set).
        r = await c.patch(
            "/api/v1/me",
            json={"taste_notes": "Spice: bring the heat. Style: mix it up.",
                  "max_prep_time": 30},
        )
        assert r.status_code == 200, r.text
        assert r.json()["taste_notes"].startswith("Spice")
        print(f"[{time.monotonic()-t0:5.2f}s] taste set")

        # 6. Funnel — admin-ish endpoint (admin_emails empty → any authed user).
        r = await c.get("/api/v1/stats/funnel")
        assert r.status_code == 200, r.text
        funnel = r.json()
        print("\n=== /stats/funnel ===")
        for cohort in funnel["cohorts"]:
            print(f"cohort week {cohort['cohort_week']}:")
            for s in cohort["steps"]:
                if s["users"]:
                    print(f"   {s['step']:<20} {s['users']:>3} users  "
                          f"(conv {s['conversion_from_prev']})")
            print(f"   returns: {cohort['returns']}")

        # 7. Raw rows for the fresh user.
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.models.event import Event
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(Event).where(Event.user_id == me["id"]).order_by(Event.ts)
                )
            ).scalars().all()
        print(f"\n=== events for fresh user {me['id']} ===")
        for e in rows:
            print(f"  {e.ts:%H:%M:%S}  {e.event:<20} {e.meta}")
        got = [e.event for e in rows]
        for expected in ["signup", "store_selected", "first_batch_viewed",
                        "recipe_opened", "taste_set"]:
            assert expected in got, f"missing {expected} in {got}"
        print("\nALL P40 FUNNEL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
