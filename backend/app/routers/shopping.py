"""Consolidated shopping list: build from the week, manage items, complete to pantry."""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.ingredient import IngredientMaster
from app.models.pantry import PantryItem
from app.models.shopping import ShoppingList, ShoppingListItem
from app.models.user import User
from app.schemas.shopping import (
    AlsoOnSale,
    BuildRequest,
    CategoryGroup,
    CheckRequest,
    CompleteResponse,
    CurrentListResponse,
    ManualAddRequest,
    ShoppingItemRead,
    ShoppingListRead,
)
from app.services import events, ingredient_matcher, list_builder
from app.services.auth import get_current_user

router = APIRouter(tags=["shopping"])

_ALSO_ON_SALE_LIMIT = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _owned_list(db: AsyncSession, list_id: int, user_id: int) -> ShoppingList:
    sl = await db.scalar(
        select(ShoppingList).where(
            ShoppingList.id == list_id, ShoppingList.user_id == user_id
        )
    )
    if sl is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Shopping list not found."
        )
    return sl


async def _list_items(db: AsyncSession, list_id: int) -> list[ShoppingListItem]:
    return (
        (
            await db.execute(
                select(ShoppingListItem)
                .where(ShoppingListItem.list_id == list_id)
                .order_by(ShoppingListItem.id)
            )
        )
        .scalars()
        .all()
    )


async def _recompute(db: AsyncSession, sl: ShoppingList) -> list[ShoppingListItem]:
    items = await _list_items(db, sl.id)
    total, savings, count = list_builder.compute_totals(items)
    sl.total_known_cost = total
    sl.deal_savings = savings
    sl.item_count = count
    await db.flush()
    return items


def _read(item: ShoppingListItem) -> ShoppingItemRead:
    return ShoppingItemRead.model_validate(list_builder.serialize_item(item))


# --------------------------------------------------------------------------- #
@router.post("/lists/build", response_model=ShoppingListRead)
async def build(
    payload: BuildRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShoppingListRead:
    """Consolidate the week's saved recipes into one honest, de-duplicated buy list."""
    try:
        sl, items = await list_builder.build_list(db, current_user, payload.week_start)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    events.log(db, current_user.id, "list_built", list_id=sl.id, items=len(items))
    await db.flush()
    return ShoppingListRead.model_validate(list_builder.serialize_list(sl, items))


@router.get("/lists/current", response_model=CurrentListResponse)
async def current_list(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CurrentListResponse:
    """Latest active list, grouped by category, plus nearby deals not on the list."""
    sl = await db.scalar(
        select(ShoppingList)
        .where(
            ShoppingList.user_id == current_user.id,
            ShoppingList.status == "active",
        )
        .order_by(ShoppingList.created_at.desc(), ShoppingList.id.desc())
    )
    if sl is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No active shopping list."
        )
    items = await _list_items(db, sl.id)

    # Group items by category (staples never made it onto the list).
    grouped: dict[str, list[ShoppingItemRead]] = {}
    for it in items:
        grouped.setdefault(it.category or "other", []).append(_read(it))
    categories = [
        CategoryGroup(category=cat, items=grouped[cat]) for cat in sorted(grouped)
    ]

    # also_on_sale: top current deals at the default chain not already on the list.
    on_list_deal_ids = {it.deal_id for it in items if it.deal_id is not None}
    on_list_ingredient_ids = {
        it.ingredient_id for it in items if it.ingredient_id is not None
    }
    chain_id, _name, _store, region_key = await list_builder.default_chain(
        db, current_user.id
    )
    also: list[AlsoOnSale] = []
    if chain_id is not None:
        for d in await list_builder.current_deals(
            db, chain_id, date.today(), region_key
        ):
            if d.id in on_list_deal_ids:
                continue
            if (
                d.matched_ingredient_id is not None
                and d.matched_ingredient_id in on_list_ingredient_ids
            ):
                continue
            also.append(
                AlsoOnSale(
                    deal_id=d.id,
                    product_name=d.product_name,
                    sale_price=d.sale_price,
                    regular_price=d.regular_price,
                    savings_pct=d.savings_pct,
                    price_unit=d.price_unit,
                )
            )
            if len(also) >= _ALSO_ON_SALE_LIMIT:
                break

    return CurrentListResponse(
        id=sl.id,
        week_start=sl.week_start,
        status=sl.status,
        store_name=sl.priced_store_name,
        total_known_cost=sl.total_known_cost,
        deal_savings=sl.deal_savings,
        item_count=sl.item_count,
        categories=categories,
        also_on_sale=also,
    )


@router.post("/lists/{list_id}/items", response_model=ShoppingItemRead,
             status_code=status.HTTP_201_CREATED)
async def add_item(
    list_id: int,
    payload: ManualAddRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShoppingItemRead:
    """Manually add a line to the list."""
    sl = await _owned_list(db, list_id, current_user.id)
    await ingredient_matcher.preload(db)
    iid, _conf = ingredient_matcher.match_ingredient(payload.display_name)
    category = None
    if iid is not None:
        category = await db.scalar(
            select(IngredientMaster.category).where(IngredientMaster.id == iid)
        )

    item = ShoppingListItem(
        list_id=sl.id,
        ingredient_id=iid,
        display_name=payload.display_name,
        buy_quantity=payload.quantity,
        unit=payload.unit,
        category=category,
        notes=payload.notes,
        is_manual_add=True,
    )
    db.add(item)
    await db.flush()
    await _recompute(db, sl)
    await db.refresh(item)
    return _read(item)


@router.patch("/lists/{list_id}/items/{item_id}", response_model=ShoppingItemRead)
async def toggle_item(
    list_id: int,
    item_id: int,
    payload: CheckRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShoppingItemRead:
    """Check / uncheck an item."""
    await _owned_list(db, list_id, current_user.id)
    item = await db.scalar(
        select(ShoppingListItem).where(
            ShoppingListItem.id == item_id, ShoppingListItem.list_id == list_id
        )
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Item not found."
        )
    item.is_checked = payload.is_checked
    item.checked_at = _now() if payload.is_checked else None
    await db.flush()
    return _read(item)


@router.delete("/lists/{list_id}/items/{item_id}", status_code=status.HTTP_200_OK)
async def delete_item(
    list_id: int,
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int | str]:
    """Remove a line from the list."""
    sl = await _owned_list(db, list_id, current_user.id)
    item = await db.scalar(
        select(ShoppingListItem).where(
            ShoppingListItem.id == item_id, ShoppingListItem.list_id == list_id
        )
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Item not found."
        )
    await db.delete(item)
    await db.flush()
    await _recompute(db, sl)
    return {"status": "deleted", "id": item_id}


@router.post("/lists/{list_id}/complete", response_model=CompleteResponse)
async def complete_list(
    list_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CompleteResponse:
    """Mark the list completed and add every checked item to the pantry."""
    sl = await _owned_list(db, list_id, current_user.id)
    now = _now()
    sl.status = "completed"
    sl.completed_at = now
    events.log(db, current_user.id, "list_completed", list_id=list_id)

    checked = [it for it in await _list_items(db, sl.id) if it.is_checked]
    if not checked:
        return CompleteResponse(items_added_to_pantry=0)

    await ingredient_matcher.preload(db)
    shelf_life = dict(
        (
            await db.execute(
                select(IngredientMaster.id, IngredientMaster.shelf_life_days)
            )
        ).all()
    )
    today = date.today()

    active = (
        (
            await db.execute(
                select(PantryItem).where(
                    PantryItem.user_id == current_user.id,
                    PantryItem.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    by_iid = {i.ingredient_id: i for i in active if i.ingredient_id is not None}
    by_norm = {ingredient_matcher._norm(i.name or ""): i for i in active}

    added = 0
    for it in checked:
        name = it.display_name or ""
        iid = it.ingredient_id
        if iid is None:
            iid, _conf = ingredient_matcher.match_ingredient(name)
        expiry = None
        if iid is not None and shelf_life.get(iid):
            expiry = today + timedelta(days=shelf_life[iid])

        existing = (by_iid.get(iid) if iid is not None else None) or by_norm.get(
            ingredient_matcher._norm(name)
        )
        if existing is not None:
            existing.quantity_estimate = it.buy_quantity
            existing.unit = it.unit
            existing.category = it.category or existing.category
            existing.ingredient_id = iid
            existing.estimated_expiry = expiry
            existing.source = "shopping"
            existing.is_active = True
            existing.consumed_at = None
            existing.last_confirmed_at = now
        else:
            db.add(
                PantryItem(
                    user_id=current_user.id,
                    ingredient_id=iid,
                    name=name,
                    quantity_estimate=it.buy_quantity,
                    unit=it.unit,
                    category=it.category,
                    source="shopping",
                    estimated_expiry=expiry,
                    last_confirmed_at=now,
                )
            )
        added += 1

    await db.flush()
    return CompleteResponse(items_added_to_pantry=added)
