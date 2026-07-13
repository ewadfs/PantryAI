"""Circular viewer endpoint verification (Prompt 37 B).

Exercises GET /deals/circular through the real FastAPI app (auth overridden
to the golden fixture user) across its four states:

  ready      — page images (presigned URLs) + per-page deal strips
  no_images  — structured-source chain: grouped deal list + note
  expired    — no valid fetch: "new circular loads {refresh_day}"
  flag off   — EXPOSE_CIRCULAR_VIEWER=0 hides the endpoint (404) and
               /deals/state reports circular_viewer=false

Run from backend/ AFTER scripts/golden_batch.py has seeded the fixture:
    .venv/bin/python scripts/verify_circular.py
"""

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# Local-signing R2 creds (presigning never talks to the network) + no
# background refresher during the test.
os.environ.setdefault("R2_ACCOUNT_ID", "https://example.r2.cloudflarestorage.com")
os.environ.setdefault("R2_ACCESS_KEY_ID", "verify-local")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "verify-local")
os.environ.setdefault("R2_BUCKET", "verify")
os.environ.setdefault("DEALS_REFRESH_ENABLED", "0")
os.environ.setdefault("ENVIRONMENT", "production")

import httpx  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import AsyncSessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.deal import CircularFetch  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth import get_current_user  # noqa: E402

LIDL_REGION = "lidl:GOLDEN"
SNS_REGION = "stop_and_shop:GOLDEN"


async def _fixture_user() -> User:
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(
                select(User).where(User.supabase_user_id == "golden-fixture-lidl")
            )
        ).scalar_one()


app.dependency_overrides[get_current_user] = _fixture_user


async def _attach_page_images() -> None:
    """Ensure the fixture flyers exist (the golden starvation check wipes
    them), then give the Lidl fetch flyer pages (keys only; presigning is a
    local signature, no storage round-trip)."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import golden_batch as gb  # noqa: E402

    from app.services import ingredient_matcher

    async with AsyncSessionLocal() as db:
        await ingredient_matcher.preload(db)
        fetch = (
            await db.execute(
                select(CircularFetch)
                .where(CircularFetch.region_key == LIDL_REGION)
                .order_by(CircularFetch.fetched_at.desc())
            )
        ).scalars().first()
        if fetch is None:
            for slug, rows in (("lidl", gb.LIDL_DEALS),
                               ("stop_and_shop", gb.SNS_DEALS)):
                await gb._seed_deals(db, slug, rows)
            await db.commit()
            fetch = (
                await db.execute(
                    select(CircularFetch)
                    .where(CircularFetch.region_key == LIDL_REGION)
                    .order_by(CircularFetch.fetched_at.desc())
                )
            ).scalars().first()
        assert fetch is not None, "run scripts/golden_batch.py first"
        fetch.image_keys = [
            f"circulars/lidl/golden/page_{n}.jpg" for n in (1, 2, 3)
        ]
        await db.commit()


async def _drop_fetches() -> None:
    from app.models.deal import DealCache

    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(DealCache).where(
                DealCache.region_key.in_([LIDL_REGION, SNS_REGION])
            )
        )
        await db.execute(
            delete(CircularFetch).where(
                CircularFetch.region_key.in_([LIDL_REGION, SNS_REGION])
            )
        )
        await db.commit()


async def main_async() -> bool:
    await _attach_page_images()
    ok = True
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://verify"
    ) as client:
        # 1. ready — pages with presigned URLs + per-page deal strips
        r = (await client.get("/api/v1/deals/circular")).json()
        pages = r.get("pages", [])
        page1_deals = pages[0]["deals"] if pages else []
        presigned = all(
            "X-Amz-Signature" in p["image_url"] or "Signature" in p["image_url"]
            for p in pages
        )
        print(f"ready:      state={r['state']} chain={r['chain_name']} "
              f"valid_to={r['valid_to']} pages={len(pages)} "
              f"presigned={presigned} page1_deals={len(page1_deals)}")
        ok &= r["state"] == "ready" and len(pages) == 3 and presigned
        ok &= all(d.get("page_number") == 1 for d in page1_deals)

        # 2. no_images — the S&S fixture fetch has no page images
        r = (await client.get("/api/v1/deals/circular?chain=stop_and_shop")).json()
        print(f"no_images:  state={r['state']} chain={r['chain_name']} "
              f"grouped_deals={len(r.get('deals', []))}")
        ok &= r["state"] == "no_images" and len(r.get("deals", [])) > 0

        # 3. unknown chain — 404, saved stores only
        code = (await client.get("/api/v1/deals/circular?chain=aldi")).status_code
        print(f"not saved:  chain=aldi -> {code}")
        ok &= code == 404

        # 4. expired — fetches removed
        await _drop_fetches()
        r = (await client.get("/api/v1/deals/circular")).json()
        print(f"expired:    state={r['state']} refresh_day={r['refresh_day']}")
        ok &= r["state"] == "expired"

        # 5. flag off — endpoint 404s, state reports the flag
        settings.expose_circular_viewer = False
        code = (await client.get("/api/v1/deals/circular")).status_code
        st = (await client.get("/api/v1/deals/state")).json()
        print(f"flag off:   /deals/circular -> {code}; "
              f"state.circular_viewer={st['circular_viewer']}")
        ok &= code == 404 and st["circular_viewer"] is False
        settings.expose_circular_viewer = True
    return ok


def main() -> None:
    import asyncio

    ok = asyncio.run(main_async())
    print("RESULT:", "ALL PASS" if ok else "FAILURES ABOVE")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
