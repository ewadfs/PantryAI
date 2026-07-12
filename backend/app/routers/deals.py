"""Deals endpoints: trigger the circular pipeline and browse cached deals.

Single-store mode: a user sees only the deals for their *default* store's chain.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, nulls_last, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.deal import CircularFetch, DealCache
from app.models.store import StoreLocation, SupportedChain, UserStore
from app.models.user import User
from app.schemas.deal import (
    DealListResponse,
    DealRead,
    DealsStateResponse,
    RefreshRequest,
    RefreshResponse,
)
from app.services.auth import get_current_user
from app.services.vision import CircularExtractor

router = APIRouter(prefix="/deals", tags=["deals"])


async def _default_region(
    db: AsyncSession, user_id: int
) -> tuple[int | None, str | None]:
    """(chain_id, region_key) of the user's default store."""
    row = (
        await db.execute(
            select(StoreLocation.chain_id, StoreLocation.region_key)
            .join(UserStore, UserStore.store_location_id == StoreLocation.id)
            .where(UserStore.user_id == user_id, UserStore.is_default.is_(True))
        )
    ).first()
    return (row.chain_id, row.region_key) if row else (None, None)


def _current_valid(chain_id: int, region_key: str | None, today: date):
    """Filter clause: deals for this chain×region valid today."""
    clauses = [
        DealCache.valid_to >= today,
        or_(DealCache.valid_from <= today, DealCache.valid_from.is_(None)),
    ]
    # Region-scoped when the store has a region; fall back to chain for legacy.
    if region_key is not None:
        clauses.append(DealCache.region_key == region_key)
    else:
        clauses.append(DealCache.chain_id == chain_id)
    return tuple(clauses)


async def _region_state(
    db: AsyncSession, chain_id: int | None, region_key: str | None, today: date
) -> str:
    """'ready' | 'loading' | 'pending_source' | 'no_store' for the user's region."""
    if chain_id is None:
        return "no_store"
    chain = await db.get(SupportedChain, chain_id)
    if chain is not None and chain.deals_status != "active" and not chain.source_url:
        return "pending_source"
    have = await db.scalar(
        select(DealCache.id).where(*_current_valid(chain_id, region_key, today)).limit(1)
    )
    if have is not None:
        return "ready"
    # Active chain, no valid deals yet → activation is (or should be) in flight.
    return "loading"


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
    """Current-valid deals for the user's default store's chain×region."""
    chain_id, region_key = await _default_region(db, current_user.id)
    today = date.today()
    state = await _region_state(db, chain_id, region_key, today)
    if chain_id is None:
        return DealListResponse(
            count=0, page=page, per_page=per_page, state=state, deals=[]
        )

    filters = list(_current_valid(chain_id, region_key, today))
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
        state=state,
        deals=[DealRead.model_validate(r) for r in rows],
    )


@router.get("/state", response_model=DealsStateResponse)
async def deals_state(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DealsStateResponse:
    """Banner state for the Deals tab / Home (ready/loading/pending_source)."""
    chain_id, region_key = await _default_region(db, current_user.id)
    today = date.today()
    state = await _region_state(db, chain_id, region_key, today)
    chain_name = None
    if chain_id is not None:
        chain = await db.get(SupportedChain, chain_id)
        chain_name = chain.chain_name if chain else None
    return DealsStateResponse(
        state=state, chain_name=chain_name, region_key=region_key
    )


@router.get("/top", response_model=list[DealRead])
async def top_deals(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DealRead]:
    """Top 5 deals by savings for the home screen (default store's chain×region)."""
    chain_id, region_key = await _default_region(db, current_user.id)
    if chain_id is None:
        return []

    today = date.today()
    rows = (
        (
            await db.execute(
                select(DealCache)
                .where(*_current_valid(chain_id, region_key, today))
                .order_by(nulls_last(DealCache.savings_pct.desc()), DealCache.id)
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    return [DealRead.model_validate(r) for r in rows]
