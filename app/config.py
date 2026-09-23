"""Application settings, read from environment variables (and a local .env file)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the API server.

    Vapi setup-script variables (VAPI_API_KEY, VAPI_PHONE_NUMBER_ID, PUBLIC_BASE_URL)
    are intentionally absent: the running server never needs them.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = ""
    api_key: str = ""
    vapi_webhook_secret: str = ""
    seed_on_startup: bool = False
    log_level: str = "INFO"
    clinic_timezone: str = "America/New_York"


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
