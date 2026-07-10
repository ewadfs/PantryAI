# PantryAI Backend

FastAPI backend for **PantryAI**, an AI meal-planning app.

## Stack

- **FastAPI** + **Uvicorn**
- **SQLAlchemy 2.0** (async, `asyncpg`)
- **Alembic** (async-configured migrations)
- **Pydantic v2** / **pydantic-settings**
- **Anthropic** SDK for AI features
- **boto3** for S3 image storage

## Setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # then fill in values
```

## Run

```bash
uvicorn app.main:app --reload
```

Then check the health endpoint:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

> Note: the lifespan handler opens a database connection on startup. To boot
> without a reachable database, point `DATABASE_URL` at a running Postgres or
> temporarily comment out the connection check in `app/main.py`.

## Migrations

```bash
alembic revision --autogenerate -m "message"
alembic upgrade head
```

## Project layout

```
app/
  main.py        FastAPI app, CORS, lifespan, /health
  config.py      pydantic-settings BaseSettings
  database.py    async engine, sessionmaker, get_db dependency
  models/        SQLAlchemy ORM models
  schemas/       Pydantic v2 schemas
  routers/       API routers mounted under /api/v1
  services/      business-logic services
  utils/
alembic/         async migration environment
scripts/
```

## API

All routers are mounted under `/api/v1`:

- `/api/v1/auth`
- `/api/v1/pantry`
- `/api/v1/stores`
- `/api/v1/deals`
- `/api/v1/recipes`
- `/api/v1/shopping`
