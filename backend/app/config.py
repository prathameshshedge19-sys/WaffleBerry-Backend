from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIRECTORY = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIRECTORY / ".env"


class Settings(BaseSettings):
    """Application settings and configuration."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    # App settings
    app_name: str = "Waffle Berry Backend"
    debug: bool = True
    
    # Database settings
    database_url: str = "sqlite:///./waffle_berry.db"
    
    # API settings
    api_v1_prefix: str = "/api/v1"

    # JWT settings
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # AI settings
    ai_provider: str = "openai"
    ai_model: str = ""
    openai_api_key: str | None = None
    ai_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    ai_read_timeout_seconds: float = Field(default=90.0, gt=0)
    ai_retry_max_retries: int = Field(default=2, ge=0)
    ai_retry_base_delay_seconds: float = Field(default=0.25, ge=0)
    ai_retry_max_delay_seconds: float = Field(default=2.0, gt=0)
    ai_retry_jitter_seconds: float = Field(default=0.15, ge=0)


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
