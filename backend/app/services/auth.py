"""Authentication against Supabase-issued JWTs.

Verification strategy — routed by the token header:
- If the header carries a ``kid``, verify the asymmetric signature (RS256/ES256)
  using the matching key from the cached JWKS (``SUPABASE_JWKS_URL``). This is how
  Supabase's modern signing keys work.
- Otherwise (no ``kid``, i.e. a legacy symmetric token), verify HS256 with the
  shared ``SUPABASE_JWT_SECRET``.

Both paths validate audience ``"authenticated"`` and reject expired/invalid tokens
with 401. On success we upsert a local ``users`` row keyed on the token ``sub`` and
return the ORM user.
"""

import time
from typing import Any

import httpx
from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User

AUDIENCE = "authenticated"
_ASYMMETRIC_ALGS = ["RS256", "ES256"]
_JWKS_TTL_SECONDS = 3600

# Simple in-process JWKS cache: {"data": <jwks dict>, "fetched_at": <monotonic>}.
_jwks_cache: dict[str, Any] = {"data": None, "fetched_at": 0.0}

_unauthorized = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired token",
    headers={"WWW-Authenticate": "Bearer"},
)


async def _get_jwks() -> dict:
    """Fetch the Supabase JWKS, cached for one hour."""
    now = time.monotonic()
    if (
        _jwks_cache["data"] is None
        or now - _jwks_cache["fetched_at"] > _JWKS_TTL_SECONDS
    ):
        if not settings.supabase_jwks_url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No JWT secret or JWKS URL configured.",
            )
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(settings.supabase_jwks_url)
            resp.raise_for_status()
            _jwks_cache["data"] = resp.json()
            _jwks_cache["fetched_at"] = now
    return _jwks_cache["data"]


async def _decode_token(token: str) -> dict:
    """Verify and decode the JWT, returning its claims. Raises 401 on failure.

    Routes by the token header: a ``kid`` selects the asymmetric JWKS path,
    otherwise we fall back to the HS256 shared secret.
    """
    try:
        kid = jwt.get_unverified_header(token).get("kid")

        if kid:
            jwks = await _get_jwks()
            key = next(
                (k for k in jwks.get("keys", []) if k.get("kid") == kid),
                None,
            )
            if key is None:
                raise JWTError("No matching JWKS key for token kid.")
            return jwt.decode(
                token, key, algorithms=_ASYMMETRIC_ALGS, audience=AUDIENCE
            )

        if settings.supabase_jwt_secret:
            return jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience=AUDIENCE,
            )

        raise JWTError("Token has no kid and no HS256 secret is configured.")
    except JWTError as exc:
        raise _unauthorized from exc


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency: authenticate the request and return the local user."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized

    token = authorization.split(" ", 1)[1].strip()
    payload = await _decode_token(token)

    sub = payload.get("sub")
    if not sub:
        raise _unauthorized
    email = payload.get("email")

    # Upsert on supabase_user_id: create with column defaults on first sight,
    # refresh the email on subsequent logins. Atomic, so concurrent first-sight
    # requests can't collide on the unique constraint.
    stmt = pg_insert(User).values(supabase_user_id=sub, email=email)
    if email:
        stmt = stmt.on_conflict_do_update(
            index_elements=["supabase_user_id"], set_={"email": email}
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=["supabase_user_id"])
    await db.execute(stmt)

    user = await db.scalar(
        select(User).where(User.supabase_user_id == sub)
    )
    if user is None:  # pragma: no cover - should be unreachable after upsert
        raise _unauthorized
    return user
