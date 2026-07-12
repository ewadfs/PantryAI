"""Probe deals sources for seeded chains (Prompt 24 A2).

For every active chain, light-probe the known aggregator URL patterns
(weeklyadnextweek.com/{slug} variants, theweeklyad.com/{slug}). The first URL
whose page actually contains flyer-page images wins and is recorded onto
supported_chains (source_url, source_type='aggregator', deals_status='active').
Chains that already carry a special source hint (chain_site / structured) keep
it and stay 'pending_source' until their bespoke fetcher is wired. Everything
else is 'pending_source'.

Light GET only — we read the aggregator *index* page (to see whether flyer
images exist) but never download the flyer pages themselves.

Run from backend/:
    .venv/Scripts/python.exe scripts/probe_circular_sources.py            # all
    .venv/Scripts/python.exe scripts/probe_circular_sources.py shoprite   # subset
    .venv/Scripts/python.exe scripts/probe_circular_sources.py --limit 40
"""

import asyncio
import pathlib
import re
import sys

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.store import SupportedChain

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_TIMEOUT = 8.0
_CONCURRENCY = 12

# Flyer-page image pattern used by the live fetcher (weeklyadnextweek gallery).
_FLYER_IMG_RE = re.compile(
    r"weeklyadpreview\.com/images/ADs/[^/\"']+/\d+\.(?:webp|jpe?g|png)", re.I
)
# theweeklyad.com renders Flipp-hosted flyer tiles.
_FLIPP_IMG_RE = re.compile(r"(f3\.flippenterprise\.net|flipp\.com)/[^\"']+", re.I)


def _slug_variants(slug: str) -> list[str]:
    return list(dict.fromkeys([slug, slug.replace("_", ""), slug.replace("_", "-")]))


def _candidates(slug: str) -> list[tuple[str, re.Pattern]]:
    urls: list[tuple[str, re.Pattern]] = []
    for v in _slug_variants(slug):
        urls.append((f"https://weeklyadnextweek.com/{v}", _FLYER_IMG_RE))
    for v in _slug_variants(slug):
        urls.append((f"https://www.theweeklyad.com/{v}", _FLIPP_IMG_RE))
    return urls


async def _probe_one(
    client: httpx.AsyncClient, chain: SupportedChain
) -> tuple[str, str | None, str]:
    """Return (chain_slug, source_url|None, source_type)."""
    for url, pattern in _candidates(chain.chain_slug):
        try:
            r = await client.get(url)
        except httpx.HTTPError:
            continue
        if r.status_code != 200 or not r.text:
            continue
        if pattern.search(r.text):
            return chain.chain_slug, url, "aggregator"
    return chain.chain_slug, None, chain.source_type or ""


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    limit = None
    for a in sys.argv[1:]:
        if a.startswith("--limit"):
            limit = int(a.split("=")[-1]) if "=" in a else int(sys.argv[sys.argv.index(a) + 1])

    async with AsyncSessionLocal() as db:
        query = select(SupportedChain).where(SupportedChain.is_active.is_(True))
        if args:
            query = query.where(SupportedChain.chain_slug.in_(args))
        query = query.order_by(SupportedChain.id)
        chains = (await db.execute(query)).scalars().all()
        if limit:
            chains = chains[:limit]

        sem = asyncio.Semaphore(_CONCURRENCY)
        async with httpx.AsyncClient(
            headers={"User-Agent": _UA}, follow_redirects=True, timeout=_TIMEOUT
        ) as client:
            async def _guarded(ch):
                async with sem:
                    return await _probe_one(client, ch)

            results = await asyncio.gather(*(_guarded(ch) for ch in chains))

        by_slug = {c.chain_slug: c for c in chains}
        active = pending_hint = pending = 0
        found_rows: list[str] = []
        for slug, url, stype in results:
            ch = by_slug[slug]
            if url:
                ch.source_url = url
                ch.source_type = stype
                ch.deals_status = "active"
                active += 1
                found_rows.append(f"{slug} -> {url}")
            elif ch.source_type in ("chain_site", "structured"):
                # Known bespoke strategy; not aggregator-activated yet.
                ch.deals_status = "pending_source"
                pending_hint += 1
            else:
                ch.deals_status = "pending_source"
                pending += 1
        await db.commit()

    total = len(chains)
    print("\n=== Circular source coverage ===")
    print(f"  probed:              {total} chains")
    print(f"  ACTIVE (aggregator): {active}")
    print(f"  pending w/ strategy: {pending_hint}  (chain_site/structured hints)")
    print(f"  pending (no source): {pending}")
    print(f"  --> {active}/{total} chains have a working source, "
          f"{total - active} pending")
    if found_rows:
        print("\n  working sources found:")
        for row in sorted(found_rows):
            print(f"    {row}")


if __name__ == "__main__":
    asyncio.run(main())
