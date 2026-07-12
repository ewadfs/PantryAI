"""Deals-region helpers (Prompt 24).

A region groups a chain's stores that share one weekly circular. Default
granularity is ``{chain_slug}:{state}`` — coarse but correct for most chains,
which run one ad per state/DMA. Chains whose source accepts a store/zip param
can adopt finer keys later without a schema change.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession


def region_key(chain_slug: str, state: str | None) -> str:
    """Canonical region key for a chain + US state. Falls back to NY (the launch
    region) when the state is unknown so existing data stays consistent."""
    st = (state or "NY").strip().upper() or "NY"
    return f"{chain_slug}:{st}"


def split_region(region_key: str) -> tuple[str, str]:
    """(chain_slug, state) from a region key; state '' if malformed."""
    slug, _, state = (region_key or "").partition(":")
    return slug, state


async def log_store_request(
    db: AsyncSession,
    *,
    chain_id: int,
    chain_slug: str | None,
    region_key_val: str | None,
    zip_code: str | None = None,
) -> None:
    """Record demand for a chain×region we can't yet source deals for. Upserts on
    (chain_id, region_key), bumping the count + last_requested_at."""
    from app.models.store import StoreRequest

    stmt = insert(StoreRequest).values(
        chain_id=chain_id,
        chain_slug=chain_slug,
        region_key=region_key_val,
        zip_code=zip_code,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_store_request_combo",
        set_={
            "request_count": StoreRequest.request_count + 1,
            "last_requested_at": func.now(),
        },
    )
    await db.execute(stmt)
