"""Re-run P39 post-compute enforcement over a user's latest batch.

For already-shipped recipes (the live stew/skillet/bake cases): re-runs the
deterministic annotation pass — honesty chips from the stored computed panel,
prose-nutrition sync, market-badge integrity, co-protein disclosure — and
persists the corrections. No model calls; slot regeneration only happens in
the live detail pipeline.

Usage (from backend/):
    .venv/bin/python scripts/reenforce_batch.py --email brandon@example.com
    .venv/bin/python scripts/reenforce_batch.py --sub p38-live-check --dry-run
"""

import argparse
import asyncio
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.recipe import Recipe
from app.models.user import User
from app.services import ingredient_matcher, recipe_engine


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--email")
    ap.add_argument("--sub", help="supabase_user_id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not (args.email or args.sub):
        raise SystemExit("Pass --email or --sub.")

    async with AsyncSessionLocal() as db:
        q = select(User)
        q = q.where(User.email == args.email) if args.email else q.where(
            User.supabase_user_id == args.sub
        )
        user = (await db.execute(q)).scalar_one()
        await ingredient_matcher.preload(db)

        newest = await db.scalar(
            select(Recipe.generated_at)
            .where(Recipe.user_id == user.id)
            .order_by(Recipe.generated_at.desc())
            .limit(1)
        )
        if newest is None:
            print("No batches for this user.")
            return
        rows = (
            (
                await db.execute(
                    select(Recipe).where(
                        Recipe.user_id == user.id, Recipe.generated_at == newest
                    ).order_by(Recipe.id)
                )
            )
            .scalars()
            .all()
        )
        floor = (
            math.ceil(user.protein_target / 3) if user.protein_target else 0
        )
        cap = round(user.calorie_target * 0.55) if user.calorie_target else 0

        print(f"Re-enforcing batch of {len(rows)} ({newest:%Y-%m-%d %H:%M}) "
              f"for {user.email} — floor {floor}g, cap {cap} cal")
        for r in rows:
            if r.status != "ready":
                print(f"  #{r.id} {r.title!r}: concept-only, skipped")
                continue
            nut = r.nutrition_json if isinstance(r.nutrition_json, dict) else {}
            summary = recipe_engine.enforce_computed(
                r, r.ingredients_json or [], nut.get("protein_g"),
                nut.get("calories"), floor, cap, user.calorie_target or 0,
            )
            r.quality_flags_json = summary["flags"]
            fired = {k: v for k, v in summary.items() if k != "flags" and v}
            print(f"  #{r.id} {r.title!r}: flags={summary['flags']} "
                  f"{('fired: ' + str(fired)) if fired else 'clean'}")
        if args.dry_run:
            await db.rollback()
            print("(dry run — rolled back)")
        else:
            await db.commit()
            print("Persisted.")


if __name__ == "__main__":
    asyncio.run(main())
