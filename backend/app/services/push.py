"""Flyer-day Web Push (P41 A) — quiet by design.

One notification per flyer flip for a user's ACTIVE (default) store, hard cap
two per rolling week, nothing in between, no streaks or re-engagement. A
404/410 from the push service deletes the subscription — unsubscribe is
honored server-side even if the browser never told us.

``notify_flyer_flip`` is called from the two places a fresh flyer's deals
actually land (direct extraction + parked-batch collection). Sending is
best-effort and never fails the caller.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.deal import DealCache
from app.models.pantry import PantryItem
from app.models.push import PushSend, PushSubscription
from app.models.recipe import Recipe
from app.models.store import StoreLocation, UserStore

logger = logging.getLogger(__name__)

MAX_PER_WEEK = 2


def push_enabled() -> bool:
    return bool(settings.vapid_public_key and settings.vapid_private_key)


def _send_webpush_sync(sub: dict, payload: str) -> int | None:
    """Blocking pywebpush send. Returns an HTTP status to act on, or None."""
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info=sub,
            data=payload,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
            ttl=86400,
        )
        return 201
    except WebPushException as exc:
        return exc.response.status_code if exc.response is not None else None


async def notify_flyer_flip(
    db: AsyncSession,
    chain_id: int,
    region_key: str | None,
    fetch_id: int,
    chain_name: str | None = None,
) -> int:
    """Notify subscribed users whose DEFAULT store is this chain×region.

    Returns the number of notifications sent. Caps enforced per user:
    at most one per flyer flip (unique user+fetch) and MAX_PER_WEEK per
    rolling 7 days. Never raises.
    """
    if not push_enabled():
        return 0
    try:
        return await _notify(db, chain_id, region_key, fetch_id, chain_name)
    except Exception:  # noqa: BLE001 — notifications must never break refresh
        logger.exception("notify_flyer_flip failed for fetch %s", fetch_id)
        return 0


async def _notify(
    db: AsyncSession,
    chain_id: int,
    region_key: str | None,
    fetch_id: int,
    chain_name: str | None,
) -> int:
    if not chain_name:
        from app.models.store import SupportedChain

        chain_name = (
            await db.scalar(
                select(SupportedChain.chain_name).where(
                    SupportedChain.id == chain_id
                )
            )
        ) or "your store"
    # Users whose DEFAULT store sits on this chain×region AND who hold at
    # least one push subscription.
    q = (
        select(UserStore.user_id, StoreLocation.store_name)
        .join(StoreLocation, UserStore.store_location_id == StoreLocation.id)
        .where(
            UserStore.is_default.is_(True),
            StoreLocation.chain_id == chain_id,
        )
    )
    if region_key is not None:
        q = q.where(StoreLocation.region_key == region_key)
    candidates = (await db.execute(q)).all()
    if not candidates:
        return 0

    deal_count = (
        await db.scalar(
            select(func.count(DealCache.id)).where(DealCache.fetch_id == fetch_id)
        )
    ) or 0
    if deal_count == 0:
        return 0

    matched_ids = set(
        (
            await db.execute(
                select(DealCache.matched_ingredient_id).where(
                    DealCache.fetch_id == fetch_id,
                    DealCache.matched_ingredient_id.isnot(None),
                )
            )
        )
        .scalars()
        .all()
    )

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    sent = 0
    for user_id, store_name in candidates:
        subs = (
            (
                await db.execute(
                    select(PushSubscription).where(
                        PushSubscription.user_id == user_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if not subs:
            continue
        # Once per flip.
        already = await db.scalar(
            select(PushSend.id).where(
                PushSend.user_id == user_id, PushSend.fetch_id == fetch_id
            )
        )
        if already:
            continue
        # Hard cap: 2 per rolling week.
        recent = (
            await db.scalar(
                select(func.count(PushSend.id)).where(
                    PushSend.user_id == user_id, PushSend.sent_at >= week_ago
                )
            )
        ) or 0
        if recent >= MAX_PER_WEEK:
            continue

        # M = pantry items matching this flyer's deals.
        matches = 0
        if matched_ids:
            matches = (
                await db.scalar(
                    select(func.count(PantryItem.id)).where(
                        PantryItem.user_id == user_id,
                        PantryItem.is_active.is_(True),
                        PantryItem.ingredient_id.in_(matched_ids),
                    )
                )
            ) or 0

        # Warm-cache card title, when a current batch exists (one notification,
        # richer body — never a second notification).
        top_title = await db.scalar(
            select(Recipe.title)
            .where(
                Recipe.user_id == user_id,
                Recipe.generated_at >= datetime.now(timezone.utc) - timedelta(days=7),
            )
            .order_by(Recipe.generated_at.desc())
            .limit(1)
        )

        label = store_name or chain_name
        title = f"📰 New {label} flyer — {deal_count} deals. " + (
            f"{matches} match your pantry." if matches else "Fresh prices are in."
        )
        body = f"On deck: {top_title}" if top_title else ""
        # Tap opens Home; if nothing matches the pantry, open the circular
        # viewer instead so the flyer itself is the payoff.
        url = "/?push=1" if matches else "/circular/default?push=1"
        payload = json.dumps({"title": title, "body": body, "url": url})

        delivered = False
        for s in subs:
            status = await asyncio.to_thread(
                _send_webpush_sync,
                {"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}},
                payload,
            )
            if status in (404, 410):
                # Endpoint gone — the user unsubscribed at the browser level.
                await db.execute(
                    delete(PushSubscription).where(PushSubscription.id == s.id)
                )
            elif status and status < 300:
                delivered = True
        if delivered:
            db.add(
                PushSend(
                    user_id=user_id,
                    fetch_id=fetch_id,
                    chain_id=chain_id,
                    region_key=region_key,
                )
            )
            sent += 1
    await db.flush()
    if sent:
        logger.info(
            "Flyer-flip push: %d notification(s) for %s fetch %s",
            sent, chain_name, fetch_id,
        )
    return sent
