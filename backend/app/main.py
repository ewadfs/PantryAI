import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.routers import auth, deals, pantry, prices, recipes, shopping, stats, stores

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    yield
    # Shutdown: dispose of the connection pool.
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


@app.get("/")
async def root() -> dict[str, str]:
    return {"app": "PantryAI API", "status": "ok", "docs": "/docs"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
