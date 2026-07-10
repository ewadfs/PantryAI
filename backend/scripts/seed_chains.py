"""Seed supported_chains. Idempotent: upserts on chain_slug.

Run from the backend/ directory:
    .venv/Scripts/python.exe scripts/seed_chains.py
"""

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.database import AsyncSessionLocal
from app.models.store import SupportedChain

CHAINS = [
    {
        "chain_name": "ShopRite",
        "chain_slug": "shoprite",
        "has_weekly_circular": True,
        "circular_refresh_day": "friday",
        "notes": "Regional circulars via Wakefern",
        "is_active": True,
    },
    {
        "chain_name": "Stop & Shop",
        "chain_slug": "stop_and_shop",
        "has_weekly_circular": True,
        "circular_refresh_day": "friday",
        "notes": "Ahold Delhaize; circulars vary by region",
        "is_active": True,
    },
    {
        "chain_name": "Lidl",
        "chain_slug": "lidl",
        "has_weekly_circular": True,
        "circular_refresh_day": "wednesday",
        "notes": "Weekly ad; mostly national with minor regional variation",
        "is_active": True,
    },
]


async def main() -> None:
    async with AsyncSessionLocal() as session:
        for row in CHAINS:
            stmt = insert(SupportedChain).values(**row)
            update_cols = {k: stmt.excluded[k] for k in row if k != "chain_slug"}
            stmt = stmt.on_conflict_do_update(
                index_elements=["chain_slug"], set_=update_cols
            )
            await session.execute(stmt)
        await session.commit()

        count = await session.scalar(
            select(func.count()).select_from(SupportedChain)
        )
    print(f"chains={count}")


if __name__ == "__main__":
    asyncio.run(main())
