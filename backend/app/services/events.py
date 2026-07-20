"""Product-event logging (P40 C) — best-effort, never breaks the request.

``log(db, ...)`` rides the caller's session/transaction (committed with the
request); ``log_bg(...)`` opens its own session for fire-and-forget spots.
Client-reported events go through the allowlisted POST /events endpoint.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.event import Event

logger = logging.getLogger(__name__)

# Events the CLIENT may report (server-side hooks log the rest directly).
CLIENT_EVENTS = {
    "first_batch_viewed",
    "recipe_opened",
    "push_opened",
    "share_visited",
    "share_converted",
}

FUNNEL_STEPS = [
    "signup",
    "store_selected",
    "first_batch_viewed",
    "recipe_opened",
    "scan_started",
    "scan_confirmed",
    "taste_set",
    "save_to_week",
    "list_built",
    "list_completed",
]


def log(db: AsyncSession, user_id: int, event: str, **meta) -> None:
    """Queue an event on the caller's session (flushes with the request)."""
    try:
        db.add(Event(user_id=user_id, event=event, meta=meta or None))
    except Exception:  # noqa: BLE001 — instrumentation must never break flow
        logger.exception("event log failed: %s", event)


async def log_bg(user_id: int, event: str, **meta) -> None:
    """Fire-and-forget with an independent session (background tasks)."""
    try:
        async with AsyncSessionLocal() as db:
            db.add(Event(user_id=user_id, event=event, meta=meta or None))
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("event log_bg failed: %s", event)
