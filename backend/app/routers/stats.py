"""Savings scoreboard — cumulative deal savings from COMPLETED shopping trips.

Honest math throughout: a checked item contributes to deal_savings only when
both its sale price and regular price are known; unknowns are never estimated
into a total.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.ai_cost import AICostEvent
from app.models.recipe import WeekRecipe
from app.models.shopping import ShoppingList, ShoppingListItem
from app.models.user import User
from app.schemas.stats import LastTrip, SavingsBucket, SavingsResponse
from app.services.auth import get_current_user

router = APIRouter(tags=["stats"])

_Z = Decimal("0.00")


def _bucket(lists: list[ShoppingList], items_by_list: dict[int, list[ShoppingListItem]]) -> SavingsBucket:
    savings = Decimal("0")
    pantry_value = Decimal("0")
    item_count = 0
    for sl in lists:
        pantry_value += sl.pantry_value_used or Decimal("0")
        for it in items_by_list.get(sl.id, []):
            if not it.is_checked:
                continue
            item_count += 1
            if it.price is not None and it.regular_price is not None:
                diff = it.regular_price - it.price
                if diff > 0:
                    savings += diff
    return SavingsBucket(
        deal_savings=savings.quantize(_Z),
        pantry_value_used=pantry_value.quantize(_Z),
        trips=len(lists),
        items=item_count,
    )


@router.get("/stats/savings", response_model=SavingsResponse)
async def savings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SavingsResponse:
    completed = (
        (
            await db.execute(
                select(ShoppingList)
                .where(
                    ShoppingList.user_id == current_user.id,
                    ShoppingList.status == "completed",
                )
                .order_by(ShoppingList.completed_at.desc())
            )
        )
        .scalars()
        .all()
    )

    # Checked items for those lists, grouped by list id.
    items_by_list: dict[int, list[ShoppingListItem]] = {}
    if completed:
        list_ids = [sl.id for sl in completed]
        rows = (
            (
                await db.execute(
                    select(ShoppingListItem).where(
                        ShoppingListItem.list_id.in_(list_ids),
                        ShoppingListItem.is_checked.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        for it in rows:
            items_by_list.setdefault(it.list_id, []).append(it)

    now = datetime.now(timezone.utc)
    this_month_lists = [
        sl
        for sl in completed
        if sl.completed_at is not None
        and sl.completed_at.year == now.year
        and sl.completed_at.month == now.month
    ]

    all_time = _bucket(completed, items_by_list)
    this_month = _bucket(this_month_lists, items_by_list)

    last_trip: LastTrip | None = None
    if completed:
        sl = completed[0]  # ordered by completed_at desc
        trip_savings = Decimal("0")
        trip_known = Decimal("0")
        for it in items_by_list.get(sl.id, []):
            if it.price is not None:
                trip_known += it.price
                if it.regular_price is not None and it.regular_price - it.price > 0:
                    trip_savings += it.regular_price - it.price
        last_trip = LastTrip(
            date=sl.completed_at,
            store=sl.priced_store_name,
            deal_savings=trip_savings.quantize(_Z),
            known_cost=trip_known.quantize(_Z),
        )

    cooked = (
        await db.scalar(
            select(func.count())
            .select_from(WeekRecipe)
            .where(
                WeekRecipe.user_id == current_user.id,
                WeekRecipe.is_cooked.is_(True),
            )
        )
    ) or 0

    return SavingsResponse(
        all_time=all_time,
        this_month=this_month,
        last_trip=last_trip,
        cooked_recipe_count=cooked,
    )


# --------------------------------------------------------------------------- #
# AI cost observability (Prompt 27)
# --------------------------------------------------------------------------- #
class AICostCategory(BaseModel):
    category: str
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    # Fraction of would-be input tokens served from cache (0..1).
    cache_hit_rate: float


class AICostWindow(BaseModel):
    days: int
    total_cost_usd: float
    by_category: list[AICostCategory]


class AICostResponse(BaseModel):
    windows: list[AICostWindow]


async def _cost_window(db: AsyncSession, days: int) -> AICostWindow:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        await db.execute(
            select(
                AICostEvent.category,
                func.count().label("calls"),
                func.coalesce(func.sum(AICostEvent.input_tokens), 0),
                func.coalesce(func.sum(AICostEvent.output_tokens), 0),
                func.coalesce(func.sum(AICostEvent.cache_read_tokens), 0),
                func.coalesce(func.sum(AICostEvent.cache_write_tokens), 0),
                func.coalesce(func.sum(AICostEvent.cost_usd), 0),
            )
            .where(AICostEvent.created_at >= since)
            .group_by(AICostEvent.category)
            .order_by(func.sum(AICostEvent.cost_usd).desc())
        )
    ).all()

    cats: list[AICostCategory] = []
    total = 0.0
    for cat, calls, inp, out, cread, cwrite, cost in rows:
        inp, out, cread, cwrite = int(inp), int(out), int(cread), int(cwrite)
        cost = float(cost)
        total += cost
        billed_input = inp + cread  # cache reads stand in for input tokens
        hit = (cread / billed_input) if billed_input else 0.0
        cats.append(
            AICostCategory(
                category=cat,
                calls=int(calls),
                input_tokens=inp,
                output_tokens=out,
                cache_read_tokens=cread,
                cache_write_tokens=cwrite,
                cost_usd=round(cost, 4),
                cache_hit_rate=round(hit, 3),
            )
        )
    return AICostWindow(days=days, total_cost_usd=round(total, 4), by_category=cats)


@router.get("/stats/ai-costs", response_model=AICostResponse)
async def ai_costs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AICostResponse:
    """AI spend over the last 7 and 30 days, broken down by category
    (generation, pre-generation, scan, circular, critic)."""
    windows = [await _cost_window(db, 7), await _cost_window(db, 30)]
    return AICostResponse(windows=windows)


# --------------------------------------------------------------------------- #
# Funnel (P40 C7) — counts + step conversion by signup-cohort week.
# --------------------------------------------------------------------------- #
@router.get("/stats/funnel")
async def funnel(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Distinct users per funnel step, split by signup-cohort week, with
    step-over-step conversion and computed D1/D7 return. Admin-gated when
    ADMIN_EMAILS is configured; any authenticated user otherwise (pre-beta)."""
    from app.config import settings as _settings
    from app.models.event import Event
    from app.services.events import FUNNEL_STEPS

    admins = [
        e.strip().lower() for e in (_settings.admin_emails or "").split(",")
        if e.strip()
    ]
    if admins and (current_user.email or "").lower() not in admins:
        from fastapi import HTTPException, status as _status
        raise HTTPException(status_code=_status.HTTP_403_FORBIDDEN,
                            detail="Admin only.")

    cohort = func.date_trunc("week", User.created_at).label("cohort")
    # Distinct users per (cohort, event).
    rows = (
        await db.execute(
            select(
                cohort,
                Event.event,
                func.count(func.distinct(Event.user_id)),
            )
            .join(User, User.id == Event.user_id)
            .where(Event.event.in_(FUNNEL_STEPS))
            .group_by(cohort, Event.event)
        )
    ).all()
    by_cohort: dict[str, dict[str, int]] = {}
    for c, ev, n in rows:
        by_cohort.setdefault(c.date().isoformat(), {})[ev] = int(n)

    # D1/D7 return: any event on [signup+1d, +2d) / [signup+7d, +8d).
    ret_rows = (
        await db.execute(
            select(
                cohort,
                func.count(func.distinct(Event.user_id)).filter(
                    Event.ts >= User.created_at + func.make_interval(0, 0, 0, 1),
                    Event.ts < User.created_at + func.make_interval(0, 0, 0, 2),
                ).label("d1"),
                func.count(func.distinct(Event.user_id)).filter(
                    Event.ts >= User.created_at + func.make_interval(0, 0, 0, 7),
                    Event.ts < User.created_at + func.make_interval(0, 0, 0, 8),
                ).label("d7"),
            )
            .join(User, User.id == Event.user_id)
            .group_by(cohort)
        )
    ).all()
    returns = {c.date().isoformat(): {"d1": int(d1), "d7": int(d7)}
               for c, d1, d7 in ret_rows}

    out = []
    for week in sorted(by_cohort):
        counts = by_cohort[week]
        steps = []
        prev = None
        for step in FUNNEL_STEPS:
            n = counts.get(step, 0)
            conv = round(n / prev, 3) if prev else None
            steps.append({"step": step, "users": n,
                          "conversion_from_prev": conv})
            if n:
                prev = n
        out.append({
            "cohort_week": week,
            "steps": steps,
            "returns": returns.get(week, {"d1": 0, "d7": 0}),
        })
    return {"cohorts": out}


@router.post("/stats/recompute-nutrition")
async def recompute_nutrition(
    payload: dict | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """P43 B5 ops tool: re-run the deterministic nutrition computation over a
    user's current-week READY recipes (post-enrichment) under the
    protein-aware gate, refresh the honesty chips, and report before/after.
    Admin-gated like /stats/funnel; targets the CALLER unless an admin passes
    {"sub": ...}. No model calls — deterministic and cheap."""
    import math

    from fastapi import HTTPException, status as _status

    from app.config import settings as _settings
    from app.models.recipe import Recipe
    from app.services import ingredient_matcher, nutrition, recipe_engine

    admins = [
        e.strip().lower() for e in (_settings.admin_emails or "").split(",")
        if e.strip()
    ]
    if admins and (current_user.email or "").lower() not in admins:
        raise HTTPException(status_code=_status.HTTP_403_FORBIDDEN,
                            detail="Admin only.")

    target = current_user
    sub = (payload or {}).get("sub")
    if sub:
        target = (
            await db.execute(select(User).where(User.supabase_user_id == sub))
        ).scalar_one_or_none() or current_user

    await ingredient_matcher.preload(db)
    await nutrition.preload(db)

    week_start = recipe_engine.week_start_for(datetime.now(timezone.utc).date())
    rows = (
        (
            await db.execute(
                select(Recipe)
                .join(WeekRecipe, WeekRecipe.recipe_id == Recipe.id)
                .where(
                    WeekRecipe.user_id == target.id,
                    WeekRecipe.week_start == week_start,
                )
                .order_by(Recipe.id)
            )
        )
        .scalars()
        .all()
    )
    floor = math.ceil(target.protein_target / 3) if target.protein_target else 0
    cap = round(target.calorie_target * 0.55) if target.calorie_target else 0
    report = []
    for r in rows:
        if r.status != "ready" or not r.ingredients_json:
            report.append({"id": r.id, "title": r.title, "skipped": r.status})
            continue
        before = dict(r.nutrition_json or {})
        before_flags = dict(r.quality_flags_json or {})
        anchor = (
            (r.signature_json or {}).get("anchor_ingredient")
            if isinstance(r.signature_json, dict) else None
        )
        computed = nutrition.compute(r.ingredients_json, r.servings)
        gap = recipe_engine._protein_gap(computed, anchor)
        model_nut = before if before.get("source") == "est" else None
        if model_nut is not None:
            model_nut = {
                k: v for k, v in model_nut.items()
                if k in ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g")
            } | {"source": "est"}
        final, protein = recipe_engine._effective_nutrition(model_nut, computed, gap)
        if final is not None:
            r.nutrition_json = final
        partial_only = bool(
            isinstance(final, dict) and final.get("nutrition_gap")
            and final.get("source") == "est" and "coverage" in final
        )
        calories = (final or {}).get("calories")
        enf = recipe_engine.enforce_computed(
            r, r.ingredients_json or [],
            None if partial_only else protein,
            None if partial_only else calories,
            floor, cap, target.calorie_target or 0,
        )
        r.quality_flags_json = enf["flags"] or None
        report.append({
            "id": r.id,
            "title": r.title,
            "anchor": anchor,
            "before": {
                "protein_g": before.get("protein_g"),
                "source": before.get("source"),
                "flags": sorted(before_flags),
            },
            "after": {
                "protein_g": (final or {}).get("protein_g"),
                "source": (final or {}).get("source"),
                "coverage": (computed or {}).get("coverage"),
                "nutrition_gap": (final or {}).get("nutrition_gap"),
                "flags": sorted(enf["flags"] or {}),
            },
        })
    await db.flush()
    return {"week_start": week_start.isoformat(), "recipes": report}
