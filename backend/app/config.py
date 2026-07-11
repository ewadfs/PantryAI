from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Origins always permitted, even when CORS_ORIGINS is unset in the environment
# (e.g. on Railway). Includes local dev and the deployed frontend. A wildcard
# is deliberately NOT used here because it is invalid alongside credentials.
_DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:3001",
    "https://zesty-liberation-production-71c3.up.railway.app",
]


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file.

    Optional integration credentials default to empty strings so the app can
    boot without them (e.g. SUPABASE_JWT_SECRET during early development).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    environment: str = "development"
    port: int = 8000

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/pantryai"

    # Anthropic
    anthropic_api_key: str = ""

    # Supabase (auth)
    supabase_url: str = ""
    supabase_jwt_secret: str = ""
    supabase_jwks_url: str = ""

    # Cloudflare R2 (S3-compatible image storage).
    # R2_ACCOUNT_ID holds the full R2 endpoint URL, e.g.
    # https://<account>.r2.cloudflarestorage.com/<bucket>.
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""

    # Anthropic model selection
    vision_model: str = "claude-sonnet-4-6"
    recipe_model: str = "claude-sonnet-4-6"  # Stage 1: recipe concepts
    # Stage 2: full recipe details. Defaults to the same model but is
    # env-overridable (DETAIL_MODEL) so we can A/B a cheaper model later.
    detail_model: str = "claude-sonnet-4-6"

    # CORS — comma-separated *extra* origins, merged with _DEFAULT_CORS_ORIGINS.
    # A bare "*" is ignored because it cannot be combined with credentials.
    cors_origins: str = ""
    # Any origin matching this regex is also allowed (covers Railway frontends).
    cors_origin_regex: str = r"https://.*\.up\.railway\.app"

    @property
    def cors_origins_list(self) -> list[str]:
        extra = [
            o.strip()
            for o in self.cors_origins.split(",")
            if o.strip() and o.strip() != "*"
        ]
        # De-duplicate while preserving order (defaults first).
        return list(dict.fromkeys([*_DEFAULT_CORS_ORIGINS, *extra]))


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
