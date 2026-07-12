"""Cross-store price comparison endpoint."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.recipe import Recipe
from app.models.shopping import ShoppingList, ShoppingListItem
from app.models.user import User
from app.schemas.prices import PriceCompareResponse
from app.services import ingredient_matcher, pricing
from app.services.auth import get_current_user

router = APIRouter(tags=["prices"])


@router.get("/prices/compare", response_model=PriceCompareResponse)
async def compare_prices(
    recipe_id: int | None = Query(None),
    list_id: int | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PriceCompareResponse:
    """Compare the needed items' known flyer cost across the user's saved stores.

    Provide exactly one of ``recipe_id`` or ``list_id``. Pantry-covered items are
    excluded; only items the user still needs to buy are priced.
    """
    if (recipe_id is None) == (list_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide exactly one of recipe_id or list_id.",
        )

    await ingredient_matcher.preload(db)

    if recipe_id is not None:
        recipe = await db.scalar(
            select(Recipe).where(
                Recipe.id == recipe_id, Recipe.user_id == current_user.id
            )
        )
        if recipe is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Recipe not found.")
        specs = pricing.specs_from_recipe(recipe)
    else:
        sl = await db.scalar(
            select(ShoppingList).where(
                ShoppingList.id == list_id, ShoppingList.user_id == current_user.id
            )
        )
        if sl is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Shopping list not found.")
        items = (
            (
                await db.execute(
                    select(ShoppingListItem).where(
                        ShoppingListItem.list_id == list_id
                    )
                )
            )
            .scalars()
            .all()
        )
        specs = pricing.specs_from_list(items)

    stores = await pricing.compare(db, current_user.id, specs)
    return PriceCompareResponse(needed_count=len(specs), stores=stores)
