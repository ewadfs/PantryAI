"""Recipe generation, rating, and the This Week list."""

import logging
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.pantry import PantryItem
from app.models.recipe import Recipe, WeekRecipe
from app.models.user import User
from app.schemas.recipe import (
    GenerateRequest,
    GenerateResponse,
    LatestResponse,
    PlanWeekRequest,
    PlanWeekResponse,
    RateRequest,
    RecipeRead,
    SaveToWeekRequest,
    WeekPlanEstimate,
    WeekRecipeRead,
    WeekResponse,
)
from app.services import events, ingredient_matcher, recipe_engine
from app.services.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["recipes"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _owned_recipe(db: AsyncSession, recipe_id: int, user_id: int) -> Recipe:
    recipe = await db.scalar(
        select(Recipe).where(Recipe.id == recipe_id, Recipe.user_id == user_id)
    )
    if recipe is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found."
        )
    return recipe


# --------------------------------------------------------------------------- #
# Recipes
# --------------------------------------------------------------------------- #
@router.post("/recipes/generate", response_model=GenerateResponse)
async def generate(
    background_tasks: BackgroundTasks,
    payload: GenerateRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GenerateResponse:
    """Stage 1: return N recipe concepts fast; Stage 2 details run in background.

    Optional ``pinned_pantry_item_ids`` forces every recipe to feature those
    (up to 3) pantry items.
    """
    pinned = payload.pinned_pantry_item_ids if payload else []
    pinned_deals = payload.pinned_deal_ids if payload else []
    direction = payload.direction if payload else None
    difficulties = payload.difficulties if payload else []
    pantry_mode = payload.pantry_mode if payload else False
    try:
        recipes = await recipe_engine.generate_concepts(
            db, current_user, pinned, direction, difficulties,
            pinned_deal_ids=pinned_deals, pantry_mode=pantry_mode,
        )
    except ValueError as e:
        # User-facing validation error (e.g. bad pinned ids) — keep the 400.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as exc:  # noqa: BLE001 — any pipeline failure (incl. a dropped
        # DB connection mid-generation) becomes a structured 500 with an error id
        # the client can surface, instead of a silent connection drop (Prompt 31).
        error_id = uuid.uuid4().hex[:8]
        logger.exception(
            "generate failed [error_id=%s] user=%s: %s",
            error_id, current_user.id, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Recipe generation failed. Please try again.",
                "error_id": error_id,
            },
        )
    reads = [
        RecipeRead.model_validate(recipe_engine.recipe_to_read(r)) for r in recipes
    ]
    # Lazy details: eagerly write only the top-3 critic-scored concepts now; the
    # rest fill in on the user's first tap or save.
    eager_ids = recipe_engine._eager_detail_ids(recipes)
    if eager_ids:
        background_tasks.add_task(
            recipe_engine.run_details_bg, current_user.id, eager_ids
        )
    return GenerateResponse(recipes=reads)


@router.post("/recipes/plan-week", response_model=PlanWeekResponse)
async def plan_week(
    payload: PlanWeekRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PlanWeekResponse:
    """One coordinated WEEK-PLANNING generation (P42 A): N dinners as a set —
    shared purchases, deal stacking, easy-weighted order — saved straight to
    This Week. The batch never enters the Discover feed."""
    try:
        recipes = await recipe_engine.generate_concepts(
            db, current_user,
            payload.pinned_pantry_item_ids, None, payload.difficulties,
            pinned_deal_ids=payload.pinned_deal_ids,
            pantry_mode=payload.pantry_mode,
            week_plan=payload.dinners,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as exc:  # noqa: BLE001 — structured 500 with an error id
        error_id = uuid.uuid4().hex[:8]
        logger.exception(
            "plan_week failed [error_id=%s] user=%s: %s",
            error_id, current_user.id, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Week planning failed. Please try again.",
                "error_id": error_id,
            },
        )

    # A4: the set saves straight to This Week (skipping Discover).
    week_start = recipe_engine.week_start_for(date.today())
    for r in recipes:
        db.add(
            WeekRecipe(
                user_id=current_user.id, recipe_id=r.id, week_start=week_start
            )
        )
    events.log(db, current_user.id, "week_planned", dinners=payload.dinners)
    await db.flush()

    # Details: same lazy/eager split as the daily batch.
    eager_ids = recipe_engine._eager_detail_ids(recipes)
    if eager_ids:
        background_tasks.add_task(
            recipe_engine.run_details_bg, current_user.id, eager_ids
        )

    summary = recipe_engine.week_plan_summary(recipes)
    return PlanWeekResponse(
        recipes=[
            RecipeRead.model_validate(recipe_engine.recipe_to_read(r))
            for r in recipes
        ],
        week_start=week_start,
        estimate=WeekPlanEstimate(**summary),
    )


_PREGEN_STALE_HOURS = 12


@router.get("/recipes/latest", response_model=LatestResponse)
async def latest(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LatestResponse:
    """The user's most recent generated batch (any status) for a warm tab load."""
    # Week-plan batches (P42) save straight to This Week — they never become
    # the Discover feed's "latest batch".
    newest = await db.scalar(
        select(Recipe.generated_at)
        .where(
            Recipe.user_id == current_user.id,
            Recipe.week_plan.is_(False),
        )
        .order_by(Recipe.generated_at.desc())
        .limit(1)
    )

    # Pre-gen discipline (Prompt 27): on the first app-open of the day — when the
    # newest batch is >12h old (or absent) — kick off one background batch. The
    # last_pregen_at debounce keeps /latest polling from spawning duplicates.
    cutoff = _now() - timedelta(hours=_PREGEN_STALE_HOURS)
    batch_stale = newest is None or newest < cutoff
    pregen_idle = current_user.last_pregen_at is None or current_user.last_pregen_at < cutoff
    if batch_stale and pregen_idle:
        current_user.last_pregen_at = _now()
        await db.flush()
        background_tasks.add_task(recipe_engine.warm_generate, current_user.id)
    if newest is None:
        return LatestResponse(
            generated_at=None, store_name=None, direction=None,
            difficulties=[], recipes=[],
        )
    rows = (
        (
            await db.execute(
                select(Recipe)
                .where(
                    Recipe.user_id == current_user.id,
                    Recipe.generated_at == newest,
                )
                .order_by(Recipe.id)
            )
        )
        .scalars()
        .all()
    )
    store_name = rows[0].generated_store_name if rows else None
    # Batch chip labels: deal pins render priced + attributed ("salmon $8.99 —
    # your pick", P37 C9); pantry pins stay bare names.
    pinned = []
    for p in (rows[0].pinned_items_json or []) if rows else []:
        if not (isinstance(p, dict) and p.get("name")):
            continue
        if p.get("deal"):
            price = f" ${p['sale_price']}" if p.get("sale_price") else ""
            unit = f"/{p['price_unit']}" if p.get("price_unit") else ""
            pinned.append(f"{p['name']}{price}{unit} — your pick")
        else:
            pinned.append(p.get("name"))
    direction = rows[0].direction if rows else None
    difficulties = (rows[0].difficulties or []) if rows else []
    batch_pantry_mode = bool(rows and rows[0].pantry_mode)
    return LatestResponse(
        generated_at=newest,
        store_name=store_name,
        pinned=pinned,
        direction=direction,
        difficulties=difficulties,
        pantry_mode=batch_pantry_mode,
        recipes=[RecipeRead.model_validate(recipe_engine.recipe_to_read(r)) for r in rows],
    )


@router.get("/recipes/{recipe_id}", response_model=RecipeRead)
async def get_recipe(
    recipe_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RecipeRead:
    recipe = await _owned_recipe(db, recipe_id, current_user.id)
    # Lazy details: tapping a not-yet-detailed concept kicks off its detail
    # generation; the client polls this endpoint until status flips to 'ready'.
    if recipe.status == "concept":
        background_tasks.add_task(
            recipe_engine.run_details_bg, current_user.id, [recipe_id]
        )
    return RecipeRead.model_validate(recipe_engine.recipe_to_read(recipe))


@router.post("/recipes/{recipe_id}/rate", response_model=RecipeRead)
async def rate_recipe(
    recipe_id: int,
    payload: RateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RecipeRead:
    """Thumbs up (1) or down (-1) — feeds future personalization."""
    recipe = await _owned_recipe(db, recipe_id, current_user.id)
    recipe.rating = payload.rating
    await db.flush()
    return RecipeRead.model_validate(recipe_engine.recipe_to_read(recipe))


@router.post("/recipes/{recipe_id}/save-to-week", response_model=WeekRecipeRead)
async def save_to_week(
    recipe_id: int,
    payload: SaveToWeekRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WeekRecipeRead:
    """Add a recipe to a week (default: the most recent Sunday). Idempotent."""
    recipe = await _owned_recipe(db, recipe_id, current_user.id)
    week_start = payload.week_start or recipe_engine.week_start_for(date.today())
    # Saving a still-concept recipe triggers its detail generation immediately so
    # a later shopping-list build is never blocked on a concept-status recipe.
    if recipe.status == "concept":
        background_tasks.add_task(
            recipe_engine.run_details_bg, current_user.id, [recipe_id]
        )

    wr = await db.scalar(
        select(WeekRecipe).where(
            WeekRecipe.user_id == current_user.id,
            WeekRecipe.recipe_id == recipe_id,
            WeekRecipe.week_start == week_start,
        )
    )
    if wr is None:
        wr = WeekRecipe(
            user_id=current_user.id, recipe_id=recipe_id, week_start=week_start
        )
        db.add(wr)
        events.log(db, current_user.id, "save_to_week", recipe_id=recipe_id)
        await db.flush()

    return WeekRecipeRead(
        week_start=wr.week_start,
        is_cooked=wr.is_cooked,
        cooked_at=wr.cooked_at,
        recipe=RecipeRead.model_validate(recipe_engine.recipe_to_read(recipe)),
    )


# --------------------------------------------------------------------------- #
# Public sharing (P41 B): opt-in per recipe, revocable, no pantry data.
# --------------------------------------------------------------------------- #
@router.post("/recipes/{recipe_id}/share")
async def share_recipe(
    recipe_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Mint (or return) the public read-only slug for one of my recipes."""
    from app.config import settings

    recipe = await _owned_recipe(db, recipe_id, current_user.id)
    if recipe.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Open the recipe once so its details finish before sharing.",
        )
    if not recipe.share_slug:
        recipe.share_slug = secrets.token_urlsafe(9)  # 12 URL chars
        recipe.shared_at = _now()
        events.log(db, current_user.id, "share_created", recipe_id=recipe_id)
        await db.flush()
    return {
        "slug": recipe.share_slug,
        "url": f"{settings.frontend_origin}/r/{recipe.share_slug}",
    }


@router.delete("/recipes/{recipe_id}/share")
async def unshare_recipe(
    recipe_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Revoke the public link — the old slug 404s immediately."""
    recipe = await _owned_recipe(db, recipe_id, current_user.id)
    recipe.share_slug = None
    recipe.shared_at = None
    await db.flush()
    return {"status": "unshared"}


# --------------------------------------------------------------------------- #
# This Week list
# --------------------------------------------------------------------------- #
@router.get("/week/{week_start}", response_model=WeekResponse)
async def get_week(
    week_start: date,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WeekResponse:
    """Saved recipes for a week, with their is_cooked flags."""
    rows = (
        await db.execute(
            select(WeekRecipe, Recipe)
            .join(Recipe, Recipe.id == WeekRecipe.recipe_id)
            .where(
                WeekRecipe.user_id == current_user.id,
                WeekRecipe.week_start == week_start,
            )
            .order_by(WeekRecipe.added_at)
        )
    ).all()

    return WeekResponse(
        week_start=week_start,
        recipes=[
            WeekRecipeRead(
                week_start=wr.week_start,
                is_cooked=wr.is_cooked,
                cooked_at=wr.cooked_at,
                recipe=RecipeRead.model_validate(recipe_engine.recipe_to_read(rec)),
            )
            for wr, rec in rows
        ],
    )


@router.delete("/week/{week_start}/recipes/{recipe_id}", status_code=status.HTTP_200_OK)
async def remove_from_week(
    week_start: date,
    recipe_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int | str]:
    """Remove a recipe from a week."""
    wr = await db.scalar(
        select(WeekRecipe).where(
            WeekRecipe.user_id == current_user.id,
            WeekRecipe.recipe_id == recipe_id,
            WeekRecipe.week_start == week_start,
        )
    )
    if wr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recipe not on this week's list.",
        )
    await db.delete(wr)
    await db.flush()
    return {"status": "removed", "recipe_id": recipe_id}


@router.post("/week/{week_start}/recipes/{recipe_id}/cooked")
async def mark_cooked(
    week_start: date,
    recipe_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Mark a week recipe cooked and (MVP) consume its in-pantry, non-staple items."""
    wr = await db.scalar(
        select(WeekRecipe).where(
            WeekRecipe.user_id == current_user.id,
            WeekRecipe.recipe_id == recipe_id,
            WeekRecipe.week_start == week_start,
        )
    )
    if wr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recipe not on this week's list.",
        )
    recipe = await _owned_recipe(db, recipe_id, current_user.id)

    now = _now()
    wr.is_cooked = True
    wr.cooked_at = now

    # Soft-delete the pantry items this recipe used up (skip staples).
    wanted = {
        ingredient_matcher._norm(ing.get("name", ""))
        for ing in (recipe.ingredients_json or [])
        if isinstance(ing, dict) and ing.get("in_pantry")
    }
    wanted.discard("")
    consumed: list[str] = []
    if wanted:
        active = (
            (
                await db.execute(
                    select(PantryItem).where(
                        PantryItem.user_id == current_user.id,
                        PantryItem.is_active.is_(True),
                        PantryItem.is_staple.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        for item in active:
            if ingredient_matcher._norm(item.name or "") in wanted:
                item.is_active = False
                item.consumed_at = now
                consumed.append(item.name)

    await db.flush()
    return {
        "week_start": week_start.isoformat(),
        "recipe_id": recipe_id,
        "is_cooked": True,
        "pantry_items_consumed": consumed,
    }
