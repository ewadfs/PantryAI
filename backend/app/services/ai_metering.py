"""Central AI cost metering (Prompt 27).

Every Claude call records a usage event — model, token counts (including
prompt-cache reads/writes), and computed USD cost — tagged with a category so
we can prove where spend goes and whether caching / lazy details / the Batch
API actually move the needle.

Usage
-----
Wrap a logical operation in a metering scope, make the Claude calls inside it,
then persist the accumulated events with your DB session::

    with metering("generation", user_id=uid) as events:
        ... calls that invoke record_usage(...) ...
    await persist_events(db, events, batch_at=generated_at)

The event sink is a plain list held in a ContextVar. asyncio.gather copies the
context into each child task but the *list object* is shared, so events from
parallel detail calls all land in the one sink.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("app.ai_metering")

# --------------------------------------------------------------------------- #
# Pricing — USD per 1,000,000 tokens.
# cache_write is the 5-minute write price (1.25x input); cache_read is 0.1x
# input. Batch API applies a flat 50% discount on top (handled per-event).
# --------------------------------------------------------------------------- #
_PER_M = 1_000_000

_PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.10},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50},
    "claude-sonnet-5": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-opus-4-7": {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50},
}
# Fall back to Sonnet pricing for an unknown model rather than under-counting.
_DEFAULT_PRICE = _PRICES["claude-sonnet-4-6"]

CATEGORIES = ("generation", "pre-generation", "scan", "circular", "critic")


@dataclass
class _Meta:
    category: str
    user_id: int | None = None
    batch_at: datetime | None = None
    circular_fetch_id: int | None = None
    batch_api: bool = False
    events: list[dict] = field(default_factory=list)


_current: contextvars.ContextVar[_Meta | None] = contextvars.ContextVar(
    "ai_metering_current", default=None
)


def cost_usd(model: str, usage: Any, *, batch_api: bool = False) -> float:
    """Compute the USD cost of one call from an Anthropic usage block."""
    p = _PRICES.get(model, _DEFAULT_PRICE)
    inp = int(getattr(usage, "input_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    total = (
        inp * p["input"]
        + out * p["output"]
        + cache_read * p["cache_read"]
        + cache_write * p["cache_write"]
    ) / _PER_M
    if batch_api:
        total *= 0.5
    return total


def record_usage(
    model: str,
    usage: Any,
    *,
    category: str | None = None,
    batch_api: bool | None = None,
    stage: str | None = None,
) -> dict | None:
    """Record one call's usage into the active metering scope (if any).

    ``category`` / ``batch_api`` override the scope defaults for a single call
    (e.g. the critic call inside a generation scope). ``stage`` attributes the
    call to a pipeline stage (concepts / critic / concept_fix / details /
    detail_fix) so the ledger can answer "which model served Stage 1?" exactly.
    """
    meta = _current.get()
    if meta is None:
        return None
    use_batch = meta.batch_api if batch_api is None else batch_api
    inp = int(getattr(usage, "input_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    event = {
        "category": category or meta.category,
        "stage": stage,
        "model": model,
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "batch_api": use_batch,
        "cost_usd": cost_usd(model, usage, batch_api=use_batch),
        "user_id": meta.user_id,
        "batch_at": meta.batch_at,
        "circular_fetch_id": meta.circular_fetch_id,
    }
    meta.events.append(event)
    return event


@contextlib.contextmanager
def metering(
    category: str,
    *,
    user_id: int | None = None,
    batch_at: datetime | None = None,
    circular_fetch_id: int | None = None,
    batch_api: bool = False,
):
    """Open a metering scope. Yields the shared event-sink list."""
    meta = _Meta(
        category=category,
        user_id=user_id,
        batch_at=batch_at,
        circular_fetch_id=circular_fetch_id,
        batch_api=batch_api,
    )
    token = _current.set(meta)
    try:
        yield meta.events
    finally:
        _current.reset(token)


def set_batch_at(batch_at: datetime) -> None:
    """Backfill batch_at onto the active scope + its already-recorded events.

    generate_concepts doesn't know the batch timestamp until the rows flush, so
    it stamps the scope afterward.
    """
    meta = _current.get()
    if meta is None:
        return
    meta.batch_at = batch_at
    for e in meta.events:
        if e.get("batch_at") is None:
            e["batch_at"] = batch_at


async def persist_events(db: AsyncSession, events: list[dict]) -> None:
    """Bulk-insert accumulated cost events. Never raises into the caller."""
    if not events:
        return
    from app.models.ai_cost import AICostEvent  # local import to avoid cycles

    try:
        db.add_all([AICostEvent(**e) for e in events])
        await db.flush()
    except Exception:  # noqa: BLE001 - metering must never break the feature
        logger.exception("Failed to persist %d AI cost events", len(events))
