"""Deals endpoints: trigger the circular pipeline and browse cached deals.

Single-store mode: a user sees only the deals for their *default* store's chain.
"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, nulls_last, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.deal import CircularFetch, DealCache
from app.models.store import StoreLocation, SupportedChain, UserStore
from app.models.user import User
from app.schemas.deal import (
    CircularPage,
    CircularResponse,
    DealListResponse,
    DealRead,
    DealsStateResponse,
    RefreshRequest,
    RefreshResponse,
)
from app.services import storage
from app.services.auth import get_current_user
from app.services.vision import CircularExtractor

logger = logging.getLogger(__name__)

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
        state=state, chain_name=chain_name, region_key=region_key,
        circular_viewer=settings.expose_circular_viewer,
    )


# --------------------------------------------------------------------------- #
# Circular viewer (Prompt 37 B)
# --------------------------------------------------------------------------- #
_PRESIGN_TTL_SECONDS = 600  # 10 minutes, matching the P20 crop URLs


@router.get("/circular", response_model=CircularResponse)
async def circular(
    chain: str | None = Query(default=None, description="chain_slug of one of "
                              "the user's saved stores; default store if omitted"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CircularResponse:
    """The current flyer for one of the user's saved stores: swipeable page
    images (short-lived presigned URLs) with each page's extracted deals.
    Structured-source chains (no page images) fall back to the grouped deal
    list; an expired/missing fetch reports when the new circular lands."""
    if not settings.expose_circular_viewer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Circular viewer is not enabled.",
        )
    # Resolve the requested store among the user's SAVED stores only.
    rows = (
        await db.execute(
            select(
                UserStore.is_default,
                StoreLocation.chain_id,
                StoreLocation.store_name,
                StoreLocation.region_key,
                SupportedChain.chain_name,
                SupportedChain.chain_slug,
                SupportedChain.circular_refresh_day,
            )
            .join(StoreLocation, StoreLocation.id == UserStore.store_location_id)
            .join(SupportedChain, SupportedChain.id == StoreLocation.chain_id)
            .where(UserStore.user_id == current_user.id)
        )
    ).all()
    if not rows:
        return CircularResponse(state="no_store")
    row = None
    if chain:
        row = next((r for r in rows if r.chain_slug == chain), None)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="That store isn't in your saved stores.",
            )
    else:
        row = next((r for r in rows if r.is_default), rows[0])

    today = date.today()
    fetch_scope = (
        CircularFetch.region_key == row.region_key
        if row.region_key is not None
        else CircularFetch.chain_id == row.chain_id
    )
    fetch = (
        await db.execute(
            select(CircularFetch)
            .where(
                fetch_scope,
                CircularFetch.status.in_(["success", "partial"]),
                CircularFetch.valid_to >= today,
            )
            .order_by(CircularFetch.fetched_at.desc())
            .limit(1)
        )
    ).scalars().first()

    base = dict(
        chain_name=row.chain_name, chain_slug=row.chain_slug,
        store_name=row.store_name, refresh_day=row.circular_refresh_day,
    )
    if fetch is None:
        return CircularResponse(state="expired", **base)

    deal_rows = (
        (
            await db.execute(
                select(DealCache)
                .where(DealCache.fetch_id == fetch.id)
                .order_by(
                    DealCache.page_number,
                    nulls_last(DealCache.savings_pct.desc()),
                    DealCache.id,
                )
            )
        )
        .scalars()
        .all()
    )
    all_deals = [DealRead.model_validate(d) for d in deal_rows]

    pages: list[CircularPage] = []
    for n, key in enumerate(fetch.image_keys or [], start=1):
        try:
            url = await storage.presign_get(key, _PRESIGN_TTL_SECONDS)
        except Exception:  # noqa: BLE001 — storage misconfig → list fallback
            logger.exception("Presign failed for circular page %s", key)
            pages = []
            break
        pages.append(CircularPage(
            page_number=n,
            image_url=url,
            deals=[d for d in all_deals if d.page_number == n],
        ))

    if not pages:
        # Structured-source chain (no page images) or unreadable storage:
        # the grouped deal list stands in for the flyer.
        return CircularResponse(
            state="no_images", valid_from=fetch.valid_from,
            valid_to=fetch.valid_to, deals=all_deals, **base,
        )
    return CircularResponse(
        state="ready", valid_from=fetch.valid_from, valid_to=fetch.valid_to,
        pages=pages, **base,
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
