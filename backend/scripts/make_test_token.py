"""Mint a valid HS256 test JWT signed with SUPABASE_JWT_SECRET.

Lets you exercise the authenticated endpoints with curl before a frontend exists.

Usage (from the backend/ directory):
    TOKEN=$(.venv/Scripts/python.exe scripts/make_test_token.py)
    curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/me
"""

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from jose import jwt

from app.config import settings

DEFAULT_SUB = "test-user-1"
DEFAULT_EMAIL = "test@pantryai.dev"


def make_token(
    sub: str = DEFAULT_SUB,
    email: str = DEFAULT_EMAIL,
    ttl_seconds: int = 3600,
) -> str:
    if not settings.supabase_jwt_secret:
        raise SystemExit(
            "SUPABASE_JWT_SECRET is not set; cannot mint an HS256 test token."
        )
    now = int(time.time())
    claims = {
        "sub": sub,
        "email": email,
        "aud": "authenticated",
        "role": "authenticated",
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(claims, settings.supabase_jwt_secret, algorithm="HS256")


if __name__ == "__main__":
    print(make_token())
