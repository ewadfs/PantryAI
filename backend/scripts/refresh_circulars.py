"""Refresh weekly-circular deals for ACTIVE chain×region combos (cron entry).

Only chain×region combos with ≥1 saved user store are refreshed (Prompt 24 C3);
combos with no users are never touched. Combos whose chain has no working source
yet are demand-logged rather than fetched.

Usage (from backend/):
    .venv/Scripts/python.exe scripts/refresh_circulars.py             # all active combos
    .venv/Scripts/python.exe scripts/refresh_circulars.py shoprite    # one chain's combos
    .venv/Scripts/python.exe scripts/refresh_circulars.py --dry-run    # list, don't fetch

Exits 0 when every processed combo succeeded (success/partial/skipped/
pending_source), 1 if any failed or errored.
"""

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.database import AsyncSessionLocal
from app.services.vision import CircularExtractor

_OK_STATUSES = {"success", "partial", "skipped", "pending_source", "would_refresh"}


async def _run(slugs: list[str] | None, dry_run: bool) -> list[dict]:
    async with AsyncSessionLocal() as db:
        results = await CircularExtractor().run_pipeline(db, slugs, dry_run=dry_run)
        await db.commit()
    return results


def main() -> int:
    argv = sys.argv[1:]
    dry_run = "--dry-run" in argv
    slugs = [a for a in argv if not a.startswith("--")] or None
    results = asyncio.run(_run(slugs, dry_run))

    if not results:
        print("No active chain×region combos matched the request.")
        return 0

    print(f"\n{'chain':<16}{'region':<18}{'status':<14}{'pages':>6}{'deals':>7}{'matched':>9}")
    print("-" * 70)
    refreshed = skipped = pending = failed = 0
    tot_deals = tot_matched = 0
    for r in results:
        st = r.get("status", "")
        if st in ("success", "partial"):
            refreshed += 1
        elif st == "skipped":
            skipped += 1
        elif st == "pending_source":
            pending += 1
        elif st in ("failed", "error"):
            failed += 1
        tot_deals += r.get("deals", 0)
        tot_matched += r.get("matched", 0)
        print(f"{r.get('chain',''):<16}{r.get('region',''):<18}{st:<14}"
              f"{r.get('pages',0):>6}{r.get('deals',0):>7}{r.get('matched',0):>9}")
        if r.get("error"):
            print(f"    error: {r['error']}")

    print("-" * 70)
    if dry_run:
        print(f"\nDry run: {len(results)} active combo(s) would be refreshed.")
        return 0
    print(f"\nCombos: {refreshed} refreshed, {skipped} skipped (still valid), "
          f"{pending} pending-source (demand logged), {failed} failed.")
    print(f"Totals: {tot_deals} deals, {tot_matched} matched to ingredients.")

    ok = all(r.get("status") in _OK_STATUSES for r in results)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
