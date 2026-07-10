from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    recipe_model: str = "claude-sonnet-4-6"

    # CORS
    cors_origins: str = "*"

    @property
    def cors_origins_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
