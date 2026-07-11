"""Recipe generation, rating, and the This Week list."""

from datetime import date, datetime, timezone

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
    RateRequest,
    RecipeRead,
    SaveToWeekRequest,
    WeekRecipeRead,
    WeekResponse,
)
from app.services import ingredient_matcher, recipe_engine
from app.services.auth import get_current_user

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
    """Stage 1: return 3 recipe concepts fast; Stage 2 details run in background.

    Optional ``pinned_pantry_item_ids`` forces every recipe to feature those
    (up to 3) pantry items.
    """
    pinned = payload.pinned_pantry_item_ids if payload else []
    try:
        recipes = await recipe_engine.generate_concepts(db, current_user, pinned)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    reads = [
        RecipeRead.model_validate(recipe_engine.recipe_to_read(r)) for r in recipes
    ]
    ids = [r.id for r in recipes]
    if ids:
        background_tasks.add_task(recipe_engine.run_details_bg, current_user.id, ids)
    return GenerateResponse(recipes=reads)


@router.get("/recipes/latest", response_model=LatestResponse)
async def latest(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LatestResponse:
    """The user's most recent generated batch (any status) for a warm tab load."""
    newest = await db.scalar(
        select(Recipe.generated_at)
        .where(Recipe.user_id == current_user.id)
        .order_by(Recipe.generated_at.desc())
        .limit(1)
    )
    if newest is None:
        return LatestResponse(generated_at=None, store_name=None, recipes=[])
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
    pinned = [
        p.get("name")
        for p in (rows[0].pinned_items_json or [])
        if isinstance(p, dict) and p.get("name")
    ] if rows else []
    return LatestResponse(
        generated_at=newest,
        store_name=store_name,
        pinned=pinned,
        recipes=[RecipeRead.model_validate(recipe_engine.recipe_to_read(r)) for r in rows],
    )


@router.get("/recipes/{recipe_id}", response_model=RecipeRead)
async def get_recipe(
    recipe_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RecipeRead:
    recipe = await _owned_recipe(db, recipe_id, current_user.id)
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WeekRecipeRead:
    """Add a recipe to a week (default: the most recent Sunday). Idempotent."""
    recipe = await _owned_recipe(db, recipe_id, current_user.id)
    week_start = payload.week_start or recipe_engine.week_start_for(date.today())

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
        await db.flush()

    return WeekRecipeRead(
        week_start=wr.week_start,
        is_cooked=wr.is_cooked,
        cooked_at=wr.cooked_at,
        recipe=RecipeRead.model_validate(recipe_engine.recipe_to_read(recipe)),
    )


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
