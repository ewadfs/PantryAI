"""Refresh weekly-circular deals for supported chains (Railway cron entrypoint).

Usage (from backend/):
    .venv/Scripts/python.exe scripts/refresh_circulars.py            # all chains
    .venv/Scripts/python.exe scripts/refresh_circulars.py shoprite   # one chain

Exits 0 when every processed chain succeeded (success/partial/skipped), 1 if any
chain failed or errored (so the cron run is flagged).
"""

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.database import AsyncSessionLocal
from app.services.vision import CircularExtractor

_OK_STATUSES = {"success", "partial", "skipped"}


async def _run(slugs: list[str] | None) -> list[dict]:
    async with AsyncSessionLocal() as db:
        results = await CircularExtractor().run_pipeline(db, slugs)
        await db.commit()
    return results


def main() -> int:
    slugs = sys.argv[1:] or None
    results = asyncio.run(_run(slugs))

    if not results:
        print("No chains matched the request.")
        return 1

    print(f"\n{'chain':<16}{'status':<10}{'pages':>6}{'deals':>7}"
          f"{'matched':>9}{'w/reg':>7}")
    print("-" * 55)
    tot_deals = tot_matched = tot_reg = 0
    for r in results:
        reg = r.get("regular_price") or 0
        tot_deals += r["deals"]
        tot_matched += r["matched"]
        tot_reg += reg
        print(f"{r['chain']:<16}{r['status']:<10}{r['pages']:>6}"
              f"{r['deals']:>7}{r['matched']:>9}{reg:>7}")
        if r.get("error"):
            print(f"    error: {r['error']}")

    print("-" * 55)
    print(f"\nGate-1: {tot_deals} deals extracted, {tot_matched} matched to "
          f"ingredients, {tot_reg} with regular_price present")

    ok = all(r["status"] in _OK_STATUSES for r in results)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
