from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.store import StoreLocation, SupportedChain, UserStore
from app.models.user import User
from app.schemas.store import (
    StoreLocationRead,
    StoreSelectionUpdate,
    UserStoreRead,
)
from app.services import recipe_engine
from app.services.auth import get_current_user

router = APIRouter(prefix="/stores", tags=["stores"])

MAX_STORES = 5


def _to_location_read(loc: StoreLocation, chain: SupportedChain) -> StoreLocationRead:
    return StoreLocationRead(
        id=loc.id,
        store_name=loc.store_name,
        address=loc.address,
        city=loc.city,
        state=loc.state,
        zip_code=loc.zip_code,
        latitude=loc.latitude,
        longitude=loc.longitude,
        is_active=loc.is_active,
        chain_id=chain.id,
        chain_name=chain.chain_name,
        chain_slug=chain.chain_slug,
    )


@router.get("", response_model=list[StoreLocationRead])
async def list_stores(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[StoreLocationRead]:
    """All active store locations with chain info (static catalog)."""
    rows = (
        await db.execute(
            select(StoreLocation, SupportedChain)
            .join(SupportedChain, StoreLocation.chain_id == SupportedChain.id)
            .where(StoreLocation.is_active.is_(True))
            .order_by(SupportedChain.chain_name, StoreLocation.store_name)
        )
    ).all()
    return [_to_location_read(loc, chain) for loc, chain in rows]


@router.get("/mine", response_model=list[UserStoreRead])
async def list_my_stores(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[UserStoreRead]:
    """The current user's saved stores, default flagged."""
    rows = (
        await db.execute(
            select(UserStore, StoreLocation, SupportedChain)
            .join(StoreLocation, UserStore.store_location_id == StoreLocation.id)
            .join(SupportedChain, StoreLocation.chain_id == SupportedChain.id)
            .where(UserStore.user_id == current_user.id)
            .order_by(UserStore.is_default.desc(), StoreLocation.store_name)
        )
    ).all()
    return [
        UserStoreRead(
            is_default=us.is_default,
            store=_to_location_read(loc, chain),
        )
        for us, loc, chain in rows
    ]


@router.put("/mine/default/{store_location_id}", response_model=list[UserStoreRead])
async def set_default_store(
    store_location_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[UserStoreRead]:
    """Switch the user's default store to one of their saved stores.

    Everything (deals, recipe generation, list pricing) is already anchored to
    the default store, so this is the whole "this week's store" switch. Fires a
    fresh background recipe batch anchored to the new store.
    """
    saved = (
        (
            await db.execute(
                select(UserStore).where(UserStore.user_id == current_user.id)
            )
        )
        .scalars()
        .all()
    )
    if not any(s.store_location_id == store_location_id for s in saved):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That store is not one of your saved stores.",
        )
    for s in saved:
        s.is_default = s.store_location_id == store_location_id
    await db.flush()

    background_tasks.add_task(recipe_engine.warm_generate, current_user.id)
    return await list_my_stores(current_user=current_user, db=db)


@router.put("/mine", response_model=list[UserStoreRead])
async def replace_my_stores(
    payload: StoreSelectionUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[UserStoreRead]:
    """Replace the user's saved store set. Validates the default is in the set."""
    ids = payload.store_location_ids
    if len(ids) > MAX_STORES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {MAX_STORES} stores may be saved.",
        )
    unique_ids = list(dict.fromkeys(ids))  # de-dupe, preserve order
    if len(unique_ids) != len(ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate store ids in selection.",
        )

    if (
        payload.default_store_id is not None
        and payload.default_store_id not in unique_ids
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="default_store_id must be one of store_location_ids.",
        )

    if unique_ids:
        valid = set(
            (
                await db.execute(
                    select(StoreLocation.id).where(
                        StoreLocation.id.in_(unique_ids),
                        StoreLocation.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        missing = [sid for sid in unique_ids if sid not in valid]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown or inactive store ids: {missing}",
            )

    # Replace the whole set.
    await db.execute(delete(UserStore).where(UserStore.user_id == current_user.id))
    for sid in unique_ids:
        db.add(
            UserStore(
                user_id=current_user.id,
                store_location_id=sid,
                is_default=(sid == payload.default_store_id),
            )
        )
    await db.flush()

    return await list_my_stores(current_user=current_user, db=db)
