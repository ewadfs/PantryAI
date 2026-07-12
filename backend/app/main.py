from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.routers import auth, deals, pantry, prices, recipes, shopping, stats, stores


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify the database connection is reachable.
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
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
