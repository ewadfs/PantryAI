"""Quality-regression diagnostics (Prompt 32 A).

Three audits against the database DATABASE_URL points at:

  A1  MODEL AUDIT — which model actually served Stage 1 (concepts), the
      critic, and Stage 2 (details) for the last 5 recipe batches, from the
      ai_cost_events ledger. Events written after Prompt 32 carry an exact
      ``stage`` tag; older events are attributed by position (the first
      generation-category event of a batch is the Stage 1 concepts call).

  A2  PROMPT AUDIT — assemble the Stage 1 prompt for a user exactly as a live
      generation would (same code path, no API call) and verify the post-27
      cache restructure still carries: taste_notes, LOVED/PASSED history,
      RECENTLY SHOWN signatures, direction, and pins. ``--full-prompt`` dumps
      the assembled layers.

  A3  MARKET CANDIDATE POOL AUDIT — per saved store: total current deals,
      ingredient-matched count, matched PROTEIN count, and the top 10
      UNMATCHED deals whose name/category indicates meat/seafood. Also
      re-runs matching with the Prompt 32 flyer-name normalizer and reports
      matched-protein counts before/after. ``--rematch`` persists the improved
      matches onto deal_cache.

Run from backend/:
    .venv/bin/python scripts/diagnose_quality.py --user 1 [--full-prompt] [--rematch]
"""

import argparse
import asyncio
import pathlib
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.ai_cost import AICostEvent
from app.models.deal import DealCache
from app.models.user import User
from app.services import ingredient_matcher, recipe_engine

_PROTEIN_CATS = {"meat", "seafood"}
_MEAT_HINTS = (
    "chicken", "beef", "steak", "pork", "turkey", "lamb", "salmon", "shrimp",
    "cod", "tilapia", "fish", "sausage", "ribs", "roast", "chops", "tenderloin",
    "fillet", "filet", "strip", "brisket", "ground", "drumstick", "thigh",
    "breast", "wings", "scallop", "crab", "lobster",
)


def _hr(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


# --------------------------------------------------------------------------- #
# A1 — model audit from the ai_cost_events ledger
# --------------------------------------------------------------------------- #
async def audit_models(db, batches: int = 5) -> None:
    _hr("A1. MODEL AUDIT — last %d recipe batches (ai_cost_events)" % batches)
    batch_ats = (
        (
            await db.execute(
                select(AICostEvent.batch_at)
                .where(
                    AICostEvent.batch_at.isnot(None),
                    AICostEvent.category.in_(
                        ["generation", "pre-generation", "critic"]
                    ),
                )
                .group_by(AICostEvent.batch_at)
                .order_by(AICostEvent.batch_at.desc())
                .limit(batches)
            )
        )
        .scalars()
        .all()
    )
    if not batch_ats:
        print("No recipe-batch cost events in this database.")
        print(f"Configured now: RECIPE_MODEL={settings.recipe_model}  "
              f"DETAIL_MODEL={settings.detail_model}  "
              f"CRITIC_MODEL={settings.critic_model_id}")
        return

    stage1_models: set[str] = set()
    for at in batch_ats:
        events = (
            (
                await db.execute(
                    select(AICostEvent)
                    .where(AICostEvent.batch_at == at)
                    .order_by(AICostEvent.id)
                )
            )
            .scalars()
            .all()
        )
        by_stage: dict[str, set[str]] = {}
        for e in events:
            stage = e.stage
            if stage is None:  # pre-P32 events: attribute by position/category
                if e.category == "critic":
                    stage = "critic"
                elif e is events[0]:
                    stage = "concepts (inferred: first call of batch)"
                else:
                    stage = "details/fixes (inferred)"
            by_stage.setdefault(stage, set()).add(e.model)
        print(f"\nbatch {at:%Y-%m-%d %H:%M:%S}:")
        for stage, models in sorted(by_stage.items()):
            print(f"  {stage:<38} {', '.join(sorted(models))}")
        for stage, models in by_stage.items():
            if stage.startswith("concepts"):
                stage1_models |= models

    print(f"\nConfigured now: RECIPE_MODEL={settings.recipe_model}  "
          f"DETAIL_MODEL={settings.detail_model}  "
          f"CRITIC_MODEL={settings.critic_model_id}")
    haiku_stage1 = any("haiku" in m for m in stage1_models)
    print(
        "VERDICT: Stage 1 ran on a Haiku-class model — PRIMARY REGRESSION. "
        "Restore RECIPE_MODEL=claude-sonnet-4-6."
        if haiku_stage1
        else "VERDICT: Stage 1 served by " + (", ".join(sorted(stage1_models)) or "?")
        + " — no Haiku regression in these batches."
    )


# --------------------------------------------------------------------------- #
# A2 — assembled Stage 1 prompt audit
# --------------------------------------------------------------------------- #
async def audit_prompt(db, user_id: int, full: bool) -> None:
    _hr("A2. STAGE 1 PROMPT AUDIT — assembled exactly as a live generation")
    user = await db.get(User, user_id)
    if user is None:
        print(f"User {user_id} not found.")
        return
    ctx = await recipe_engine._load_context(db, user)
    ctx.history_block, ctx.variety_block, _sigs = (
        await recipe_engine._build_taste_history(db, user.id)
    )
    l2 = (
        recipe_engine._concept_profile(user)
        + recipe_engine._protein_block(ctx.protein_floor)
        + ctx.taste_block
        + ctx.history_block
        + "\n\n"
        + ctx.context_text
    )
    # Per-press blocks live in the user message (the Prompt 27 cache split).
    user_msg_blocks = {
        "RECENTLY SHOWN signatures": ctx.variety_block,
        "direction (per-press when typed)": recipe_engine._direction_block(
            "example direction", False
        ),
        "pins (per-press when pinned)": recipe_engine._pin_block(
            [{"name": "example", "quantity": "1", "freshness": "good"}]
        ),
    }
    checks = {
        "taste_notes (L2 system)": ("THEIR TASTE" in l2, bool(user.taste_notes)),
        "LOVED/PASSED history (L2 system)": (
            "WHAT THEY THINK OF PAST RECIPES" in l2,
            bool(ctx.history_block),
        ),
        "pantry+deals context (L2 system)": ("THEIR KITCHEN" in l2, True),
        "protein floor (L2 system)": ("Protein is a CONSTRAINT" in l2, True),
    }
    for label, (present, applicable) in checks.items():
        mark = "PRESENT" if present else ("MISSING" if applicable else "n/a (no data)")
        print(f"  {label:<40} {mark}")
    for label, block in user_msg_blocks.items():
        print(f"  {label:<40} "
              f"{'wired (user msg)' if block else 'NOT WIRED'}")
    print(f"\n  L2 length: {len(l2)} chars; variety block: "
          f"{len(ctx.variety_block)} chars")
    print("  Live confirmation: every generation now logs a one-line block "
          "checklist at INFO; LOG_PROMPTS=1 dumps the full prompt.")
    if full:
        print("\n--- L1 (static rules) ---\n" + recipe_engine._CONCEPT_SYSTEM)
        print("\n--- L2 (profile/taste/history/pantry/deals) ---\n" + l2)
        print("\n--- USER-MSG blocks (variety shown; direction/pins per-press) ---\n"
              + ctx.variety_block)


# --------------------------------------------------------------------------- #
# A3 — market candidate pool audit per saved store
# --------------------------------------------------------------------------- #
async def audit_pool(db, user_id: int, rematch: bool) -> None:
    _hr("A3. MARKET CANDIDATE POOL AUDIT — per saved store")
    user = await db.get(User, user_id)
    if user is None:
        print(f"User {user_id} not found.")
        return
    await ingredient_matcher.preload(db)
    stores = await recipe_engine._saved_stores(db, user.id)
    if not stores:
        print("User has no saved stores.")
        return
    today = date.today()
    starving = False
    for s in stores:
        deals = await recipe_engine._all_current_deals(
            db, s.chain_id, today, s.region_key
        )
        matched = [d for d in deals if d.matched_ingredient_id is not None]
        matched_prot = [d for d in matched if (d.category or "").lower() in _PROTEIN_CATS]
        unmatched_meat = [
            d
            for d in deals
            if d.matched_ingredient_id is None
            and (
                (d.category or "").lower() in _PROTEIN_CATS
                or any(h in (d.product_name or "").lower() for h in _MEAT_HINTS)
            )
        ]
        unmatched_meat.sort(
            key=lambda d: -(float(d.savings_pct) if d.savings_pct is not None else 0.0)
        )

        # Before/after the flyer-name normalizer (raw matcher vs P32 matcher).
        raw_prot = flyer_prot = 0
        improved = []
        for d in deals:
            if (d.category or "").lower() not in _PROTEIN_CATS:
                continue
            raw_iid, _rc = ingredient_matcher.match_ingredient(d.product_name or "")
            fly_iid, fc = ingredient_matcher.match_flyer_name(
                d.product_name or "", d.brand
            )
            raw_prot += raw_iid is not None
            flyer_prot += fly_iid is not None
            if raw_iid is None and fly_iid is not None:
                improved.append((d, fly_iid, fc))

        print(f"\n{s.chain_name} ({s.store_name or '?'}; region={s.region_key or 'chain-wide'})")
        print(f"  total current deals:        {len(deals)}")
        print(f"  ingredient-matched:         {len(matched)}")
        print(f"  matched PROTEIN deals:      {len(matched_prot)}")
        print(f"  matched proteins raw→flyer-normalized: {raw_prot} → {flyer_prot}")
        if unmatched_meat:
            print(f"  top {min(10, len(unmatched_meat))} UNMATCHED meat/seafood deals:")
            for d in unmatched_meat[:10]:
                sav = f" ({d.savings_pct}% off)" if d.savings_pct is not None else ""
                print(f"    - {d.product_name}: ${d.sale_price}"
                      f"{'/' + d.price_unit if d.price_unit else ''}{sav}"
                      f" [cat={d.category}]")
        if len(matched_prot) == 0 and unmatched_meat:
            starving = True
        if rematch and improved:
            for d, iid, conf in improved:
                d.matched_ingredient_id = iid
                d.match_confidence = conf
            await db.flush()
            print(f"  --rematch: persisted {len(improved)} newly matched deals")
    print(
        "\nVERDICT: candidate starvation CONFIRMED — matched proteins ≈ 0 while "
        "unmatched meat deals exist; the selector could only anchor on the few "
        "matched items (the $2.49 cauliflower). Root cause of the repetition."
        if starving
        else "\nVERDICT: no candidate starvation at these stores right now "
        "(matched proteins exist, or no unmatched meats)."
    )


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", type=int, default=None, help="user id for A2/A3")
    ap.add_argument("--batches", type=int, default=5)
    ap.add_argument("--full-prompt", action="store_true")
    ap.add_argument("--rematch", action="store_true",
                    help="persist normalizer-improved deal matches (A3)")
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        await audit_models(db, args.batches)
        if args.user is not None:
            await audit_prompt(db, args.user, args.full_prompt)
            await audit_pool(db, args.user, args.rematch)
            if args.rematch:
                await db.commit()
        else:
            print("\n(pass --user <id> to run the A2 prompt audit and the A3 "
                  "candidate-pool audit)")


if __name__ == "__main__":
    asyncio.run(main())
