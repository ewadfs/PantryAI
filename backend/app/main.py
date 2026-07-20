import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.database import AsyncSessionLocal, engine
from app.routers import (
    auth,
    deals,
    events,
    pantry,
    prices,
    recipes,
    shopping,
    stats,
    stores,
)

logger = logging.getLogger(__name__)

# Advisory-lock key so only ONE replica runs a refresh cycle at a time.
_DEALS_REFRESH_LOCK = 0x0DEA15


async def _deals_refresh_loop() -> None:
    """Keep weekly flyers fresh: run the circular pipeline every
    DEALS_REFRESH_HOURS. run_pipeline skips chain×regions whose fetch is
    still valid, so a quiet cycle costs one cheap query per active combo.
    A Postgres advisory lock keeps multi-replica deploys from double-fetching."""
    from app.services.vision import CircularExtractor  # deferred: heavy import

    while True:
        try:
            async with engine.connect() as lock_conn:
                got = (
                    await lock_conn.execute(
                        text("SELECT pg_try_advisory_lock(:k)"),
                        {"k": _DEALS_REFRESH_LOCK},
                    )
                ).scalar()
                if got:
                    try:
                        async with AsyncSessionLocal() as db:
                            # Finish extractions parked on slow Batches-API
                            # jobs FIRST — they're already paid for.
                            collected = await CircularExtractor().collect_pending_batches(db)
                            if collected:
                                logger.info("Parked-batch sweep: %s", collected)
                            results = await CircularExtractor().run_pipeline(db)
                            await db.commit()
                        by_status: dict[str, int] = {}
                        for r in results:
                            by_status[r.get("status", "?")] = (
                                by_status.get(r.get("status", "?"), 0) + 1
                            )
                        logger.info("Deals refresh cycle: %s combos — %s",
                                    len(results), by_status or "none active")
                        pending_remain = any(
                            c.get("status") == "still_processing" for c in collected
                        ) or any(r.get("pending_batch") for r in results)
                    finally:
                        await lock_conn.execute(
                            text("SELECT pg_advisory_unlock(:k)"),
                            {"k": _DEALS_REFRESH_LOCK},
                        )
                else:
                    # Replica overlap during a redeploy: the outgoing replica
                    # can hold the lock at our first cycle. Retry SOON — a
                    # 6-hour sleep here stranded parked batches for hours
                    # after every deploy (observed live).
                    logger.info(
                        "Deals refresh: another replica holds the lock; "
                        "retrying in 15 minutes."
                    )
                    pending_remain = True
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the loop must survive bad cycles
            logger.exception("Deals refresh cycle failed; retrying next cycle.")
            pending_remain = True
        # Parked batches (and lock-skipped or failed cycles) get a fast 15-min
        # cadence; quiet cycles keep the configured hours.
        await asyncio.sleep(
            900 if pending_remain
            else max(1, settings.deals_refresh_hours) * 3600
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # App loggers must actually reach stdout: uvicorn configures only its own
    # loggers, so without this the engine's INFO telemetry (prompt audits,
    # market-pool starvation, scheduler cycles) was silently dropped.
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    # Startup: verify the database connection is reachable.
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    # Model-selection guard (Prompt 32 A1): a stray RECIPE_MODEL env override to
    # a Haiku-class model silently regressed Stage 1 concept quality in prod.
    # Stage 1 is the creative step — it needs a Sonnet-class model or better.
    if "haiku" in settings.recipe_model.lower():
        logger.warning(
            "RECIPE_MODEL=%s is a Haiku-class model. Stage 1 (concepts) quality "
            "regresses on Haiku (clone anchors, incoherent dishes, sub-floor "
            "macros — Prompt 32 audit). Set RECIPE_MODEL=claude-sonnet-4-6; "
            "only Stage 2 details (DETAIL_MODEL) and the critic (CRITIC_MODEL) "
            "are Haiku-safe.",
            settings.recipe_model,
        )
    refresh_task: asyncio.Task | None = None
    if settings.deals_refresh_enabled:
        refresh_task = asyncio.create_task(_deals_refresh_loop())
        logger.info("Deals refresh scheduler started (every %dh).",
                    settings.deals_refresh_hours)
    yield
    # Shutdown: stop the scheduler, then dispose of the connection pool.
    if refresh_task is not None:
        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass
    await engine.dispose()


app = FastAPI(
    title="PantryAI",
    description="AI meal-planning app backend",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    # Explicit so multipart uploads (pantry scan) preflight cleanly.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin"],
    max_age=600,
)

API_PREFIX = "/api/v1"
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(pantry.router, prefix=API_PREFIX)
app.include_router(stores.router, prefix=API_PREFIX)
app.include_router(deals.router, prefix=API_PREFIX)
app.include_router(recipes.router, prefix=API_PREFIX)
app.include_router(shopping.router, prefix=API_PREFIX)
app.include_router(prices.router, prefix=API_PREFIX)
app.include_router(stats.router, prefix=API_PREFIX)
app.include_router(events.router, prefix=API_PREFIX)


# The commit serving this process — Railway injects RAILWAY_GIT_COMMIT_SHA at
# build/deploy time, so the production URL itself can answer "what's live?".
_SERVING_COMMIT = (
    os.environ.get("RAILWAY_GIT_COMMIT_SHA")
    or os.environ.get("GIT_COMMIT_SHA")
    or None
)


@app.get("/")
async def root() -> dict[str, str | None]:
    return {
        "app": "PantryAI API", "status": "ok", "docs": "/docs",
        "commit": _SERVING_COMMIT,
    }


@app.get("/health")
async def health() -> dict[str, str | None]:
    return {"status": "ok", "commit": _SERVING_COMMIT}
