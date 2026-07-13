"""Probe deals sources for seeded chains (Prompt 24 A2 + Prompt 38 A probe v2).

v1 (default): for every active chain, light-probe the known aggregator URL
patterns (weeklyadnextweek.com/{slug} variants, theweeklyad.com/{slug}). The
first URL whose page actually contains flyer-page images wins and is recorded
onto supported_chains (source_url, source_type='aggregator',
deals_status='active').

v2 (--fingerprint, P38 A): for every PENDING chain, discover its weekly-ad
page (recorded source_url, else homepage guess + on-page weekly-ad link) and
fingerprint the serving platform from script srcs, iframe hosts, and DOM
markers (Flipp, Quotient/ShopLocal, Webstop, Freshop/Mercatus, RedPepper,
VTEX, direct PDF, static images, unknown-JS). platform + evidence land on
supported_chains, a strategy hint lands on source_type, and the run ends with
the chains-per-platform histogram — the build-priority map.

Light GET only — index pages, never the flyer pages themselves.

Run from backend/:
    .venv/bin/python scripts/probe_circular_sources.py                 # v1 all
    .venv/bin/python scripts/probe_circular_sources.py shoprite        # subset
    .venv/bin/python scripts/probe_circular_sources.py --fingerprint   # v2 all
    .venv/bin/python scripts/probe_circular_sources.py --fingerprint h_mart
"""

import asyncio
import pathlib
import re
import sys
from collections import Counter

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.store import SupportedChain
from app.services import circular_probe

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


async def fingerprint_main(slugs: list[str], limit: int | None) -> None:
    """Probe v2 (P38 A): fingerprint every pending chain's flyer platform."""
    async with AsyncSessionLocal() as db:
        await circular_probe.ensure_default_profiles(db)
        query = (
            select(SupportedChain)
            .where(
                SupportedChain.is_active.is_(True),
                SupportedChain.deals_status != "active",
            )
            .order_by(SupportedChain.id)
        )
        if slugs:
            query = select(SupportedChain).where(
                SupportedChain.chain_slug.in_(slugs)
            )
        chains = (await db.execute(query)).scalars().all()
        if limit:
            chains = chains[:limit]

        sem = asyncio.Semaphore(_CONCURRENCY)
        async with httpx.AsyncClient(
            headers={"User-Agent": circular_probe.UA},
            follow_redirects=True, timeout=_TIMEOUT,
        ) as client:
            async def _one(ch):
                async with sem:
                    try:
                        return ch, await circular_probe.discover_and_fingerprint(
                            client, ch
                        )
                    except Exception as exc:  # noqa: BLE001 — isolate per chain
                        return ch, (None, "unknown", f"probe error: {exc}")

            results = await asyncio.gather(*(_one(ch) for ch in chains))

        hist: Counter = Counter()
        for ch, (url, platform, evidence) in results:
            ch.platform = platform
            ch.platform_evidence = evidence[:2000]
            # Pending chains only run here — a freshly-resolved weekly-ad URL
            # beats a stale recorded one (H Mart's recorded /weeklyad 404s;
            # the fingerprint resolves the live /weekly-ads).
            if url:
                ch.source_url = url
            strategy = circular_probe.STRATEGY_FOR_PLATFORM.get(platform)
            if strategy and ch.source_type in (None, "", "chain_site"):
                ch.source_type = strategy
            hist[platform] += 1
        await db.commit()

    total = len(chains)
    print(f"\n=== Probe v2 — platform fingerprints ({total} pending chains) ===")
    print("chains per platform (build-priority map):")
    for platform, n in hist.most_common():
        strategy = circular_probe.STRATEGY_FOR_PLATFORM.get(platform, "—")
        print(f"  {n:>4}  {platform:<20} -> strategy: {strategy}")
    print("\nsample evidence:")
    shown: set = set()
    for ch, (url, platform, evidence) in results:
        if platform in shown or platform == "unknown":
            continue
        shown.add(platform)
        print(f"  [{platform}] {ch.chain_slug}: {evidence[:120]}")


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    limit = None
    for a in sys.argv[1:]:
        if a.startswith("--limit"):
            limit = int(a.split("=")[-1]) if "=" in a else int(sys.argv[sys.argv.index(a) + 1])
    if "--fingerprint" in sys.argv:
        await fingerprint_main(args, limit)
        return

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
