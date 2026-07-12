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

    # Google Places (store discovery). When empty, /stores/discover falls back
    # to the seeded store catalog (Prompt 24 B).
    google_places_api_key: str = ""

    # USDA FoodData Central (deterministic nutrition, Prompt 28 B). Used only by
    # the offline bulk-seed script; runtime nutrition compute reads local macros
    # off ingredient_master and never calls USDA. Empty → bulk script no-ops.
    usda_fdc_api_key: str = ""

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
    # Stage 2: full recipe details. Haiku by default (Prompt 27 cost work):
    # details are constrained, formulaic writing that Haiku handles well at
    # ~1/3 the input and 1/3 the output price of Sonnet. Override via
    # DETAIL_MODEL to A/B a stronger model.
    detail_model: str = "claude-haiku-4-5"
    # Stage 1.5 critic. Haiku by default: measured >5s on Sonnet for 3 concepts,
    # over the latency budget, and scoring is a cheap task. Override via CRITIC_MODEL
    # (empty string → falls back to detail_model).
    critic_model: str = "claude-haiku-4-5"

    @property
    def critic_model_id(self) -> str:
        return self.critic_model or self.detail_model

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
