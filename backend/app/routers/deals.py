"""Deals endpoints: trigger the circular pipeline and browse cached deals.

Single-store mode: a user sees only the deals for their *default* store's chain.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, nulls_last, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.deal import DealCache
from app.models.store import StoreLocation, UserStore
from app.models.user import User
from app.schemas.deal import (
    DealListResponse,
    DealRead,
    RefreshRequest,
    RefreshResponse,
)
from app.services.auth import get_current_user
from app.services.vision import CircularExtractor

router = APIRouter(prefix="/deals", tags=["deals"])


async def _default_chain_id(db: AsyncSession, user_id: int) -> int | None:
    """The chain_id of the user's default store, or None if unset."""
    return await db.scalar(
        select(StoreLocation.chain_id)
        .join(UserStore, UserStore.store_location_id == StoreLocation.id)
        .where(UserStore.user_id == user_id, UserStore.is_default.is_(True))
    )


def _current_valid(chain_id: int, today: date):
    """Filter clause: deals for this chain valid today."""
    return (
        DealCache.chain_id == chain_id,
        DealCache.valid_to >= today,
        or_(DealCache.valid_from <= today, DealCache.valid_from.is_(None)),
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_deals(
    payload: RefreshRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RefreshResponse:
    """Run the circular pipeline (all chains, or the given ``chain_slugs``)."""
    slugs = payload.chain_slugs if payload else None
    results = await CircularExtractor().run_pipeline(db, slugs)
    return RefreshResponse(results=results)


@router.get("", response_model=DealListResponse)
async def list_deals(
    category: str | None = None,
    search: str | None = None,
    on_sale_only: bool = False,  # accepted but ignored: every cached row is a deal
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DealListResponse:
    """Current-valid deals for the user's default store's chain."""
    chain_id = await _default_chain_id(db, current_user.id)
    if chain_id is None:
        return DealListResponse(count=0, page=page, per_page=per_page, deals=[])

    today = date.today()
    filters = list(_current_valid(chain_id, today))
    if category:
        filters.append(DealCache.category == category)
    if search:
        filters.append(DealCache.product_name.ilike(f"%{search}%"))

    total = await db.scalar(
        select(func.count()).select_from(DealCache).where(*filters)
    )

    rows = (
        (
            await db.execute(
                select(DealCache)
                .where(*filters)
                .order_by(nulls_last(DealCache.savings_pct.desc()), DealCache.id)
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        )
        .scalars()
        .all()
    )

    return DealListResponse(
        count=total or 0,
        page=page,
        per_page=per_page,
        deals=[DealRead.model_validate(r) for r in rows],
    )


@router.get("/top", response_model=list[DealRead])
async def top_deals(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DealRead]:
    """Top 5 deals by savings for the home screen (default store's chain)."""
    chain_id = await _default_chain_id(db, current_user.id)
    if chain_id is None:
        return []

    today = date.today()
    rows = (
        (
            await db.execute(
                select(DealCache)
                .where(*_current_valid(chain_id, today))
                .order_by(nulls_last(DealCache.savings_pct.desc()), DealCache.id)
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    return [DealRead.model_validate(r) for r in rows]
