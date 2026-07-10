"""Seed store_locations for the Long Island / ZIP 11746 area.

Idempotent: upserts on (store_name, zip_code). There is no DB unique constraint
on that pair, so we do a manual select-then-insert/update.

Addresses verified via web lookup (July 2026).

Run from the backend/ directory:
    .venv/Scripts/python.exe scripts/seed_stores.py
"""

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models.store import StoreLocation, SupportedChain

# chain_slug, store_name, address, city, state, zip_code
STORES = [
    ("shoprite", "ShopRite of Deer Park", "1960 Deer Park Ave", "Deer Park", "NY", "11729"),
    ("shoprite", "ShopRite of Commack", "1 Garet Pl", "Commack", "NY", "11725"),
    ("shoprite", "ShopRite of Hauppauge", "335 Nesconset Hwy", "Hauppauge", "NY", "11788"),
    ("stop_and_shop", "Stop & Shop — Huntington", "60 Wall St", "Huntington", "NY", "11743"),
    ("stop_and_shop", "Stop & Shop — Huntington Station", "953 New York Ave", "Huntington Station", "NY", "11746"),
    ("stop_and_shop", "Stop & Shop — Smithtown", "291 W Main St", "Smithtown", "NY", "11787"),
    ("lidl", "Lidl — Commack", "210 E Jericho Tpke", "Commack", "NY", "11725"),
    ("lidl", "Lidl — Plainview", "1054 Old Country Rd", "Plainview", "NY", "11803"),
]


async def main() -> None:
    async with AsyncSessionLocal() as session:
        # Resolve chain slugs -> ids (chains must be seeded first).
        chain_ids = dict(
            (await session.execute(
                select(SupportedChain.chain_slug, SupportedChain.id)
            )).all()
        )
        missing = {s[0] for s in STORES} - set(chain_ids)
        if missing:
            raise SystemExit(
                f"Missing chains {sorted(missing)} — run seed_chains.py first."
            )

        for slug, name, address, city, state, zip_code in STORES:
            existing = await session.scalar(
                select(StoreLocation).where(
                    StoreLocation.store_name == name,
                    StoreLocation.zip_code == zip_code,
                )
            )
            if existing is None:
                session.add(
                    StoreLocation(
                        chain_id=chain_ids[slug],
                        store_name=name,
                        address=address,
                        city=city,
                        state=state,
                        zip_code=zip_code,
                        is_active=True,
                    )
                )
            else:
                existing.chain_id = chain_ids[slug]
                existing.address = address
                existing.city = city
                existing.state = state
                existing.is_active = True

        await session.commit()

        count = await session.scalar(
            select(func.count()).select_from(StoreLocation)
        )
    print(f"stores={count}")


if __name__ == "__main__":
    asyncio.run(main())
