"""Run the P43 international-staples nutrition addendum (idempotent).

Usage (from backend/): .venv/bin/python scripts/seed_international_nutrition.py
The same rows ship as alembic data migration b7c8d9e0f1a2 (the prod channel);
this script exists for local/dev runs and re-runs after edits.
"""

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.database import AsyncSessionLocal
from app.services.international_foods import ROWS, UPSERT_SQL, row_params


async def main() -> None:
    async with AsyncSessionLocal() as db:
        for row in ROWS:
            await db.execute(text(UPSERT_SQL), row_params(row))
        await db.commit()
        n = await db.scalar(text(
            "SELECT count(*) FROM ingredient_master WHERE kcal_per_100g IS NOT NULL"
        ))
    print(f"upserted {len(ROWS)} international rows; master now has {n} "
          "ingredients with macros")


if __name__ == "__main__":
    asyncio.run(main())
