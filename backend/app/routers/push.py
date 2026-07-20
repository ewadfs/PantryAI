"""Web Push subscription management (P41 A). Opt-in, one-tap out."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.push import PushSubscription
from app.models.user import User
from app.services import events
from app.services.auth import get_current_user
from app.services.push import push_enabled

router = APIRouter(prefix="/push", tags=["push"])


class SubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class SubscriptionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    endpoint: str
    keys: SubscriptionKeys


class UnsubscribeIn(BaseModel):
    endpoint: str


@router.get("/public-key")
async def public_key(
    current_user: User = Depends(get_current_user),
) -> dict:
    """VAPID application server key for pushManager.subscribe()."""
    if not push_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Push is not configured.",
        )
    return {"key": settings.vapid_public_key}


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
async def subscribe(
    payload: SubscriptionIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not push_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Push is not configured.",
        )
    # Upsert on endpoint: a browser re-subscribing (or a device changing
    # hands between accounts) simply re-points the endpoint.
    stmt = pg_insert(PushSubscription).values(
        user_id=current_user.id,
        endpoint=payload.endpoint,
        p256dh=payload.keys.p256dh,
        auth=payload.keys.auth,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["endpoint"],
        set_={
            "user_id": current_user.id,
            "p256dh": payload.keys.p256dh,
            "auth": payload.keys.auth,
        },
    )
    await db.execute(stmt)
    events.log(db, current_user.id, "push_subscribed")
    await db.flush()
    return {"status": "subscribed"}


@router.post("/unsubscribe", status_code=status.HTTP_200_OK)
async def unsubscribe(
    payload: UnsubscribeIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """One-tap out — deletes the endpoint server-side, no questions asked."""
    await db.execute(
        delete(PushSubscription).where(
            PushSubscription.user_id == current_user.id,
            PushSubscription.endpoint == payload.endpoint,
        )
    )
    await db.flush()
    return {"status": "unsubscribed"}


@router.get("/status")
async def push_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Whether push is configured and this user has any subscription."""
    subs = (
        (
            await db.execute(
                select(PushSubscription.endpoint).where(
                    PushSubscription.user_id == current_user.id
                )
            )
        )
        .scalars()
        .all()
    )
    return {"enabled": push_enabled(), "subscribed_endpoints": subs}
