from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Legarya Backend"
    legarya_debug: bool = True
    database_url: str = "sqlite:///./legarya.db"
    cors_origins: str = "http://127.0.0.1:5600,http://localhost:5600"

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=30, ge=5, le=1440)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=30)
    remembered_refresh_token_expire_days: int = Field(default=30, ge=1, le=90)
    auth_code_expire_minutes: int = Field(default=10, ge=1, le=60)
    access_invite_expire_days: int = Field(default=7, ge=1, le=30)
    frontend_base_url: str = "http://localhost:5600"

    openai_api_key: str | None = None
    ai_model: str = "gpt-5.6-luna"
    ai_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "low"
    ai_max_context_messages: int = Field(default=24, ge=2, le=100)
    memory_extraction_model: str = "gpt-5.6-luna"
    memory_extraction_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "low"
    memory_embedding_model: str = "text-embedding-3-small"
    memory_embedding_dimensions: int = Field(default=256, ge=128, le=3072)
    memory_embedding_version: str = "v1"
    memory_retrieval_top_k: int = Field(default=6, ge=1, le=20)
    memory_retrieval_threshold: float = Field(default=0.28, ge=-1, le=1)
    memory_duplicate_threshold: float = Field(default=0.94, ge=0.8, le=1)
    voice_transcription_model: str = "gpt-4o-transcribe"
    voice_tts_model: str = "gpt-4o-mini-tts"
    voice_max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=25 * 1024 * 1024)
    voice_max_recording_seconds: int = Field(default=300, ge=10, le=600)
    voice_max_tts_characters: int = Field(default=4096, ge=100, le=4096)

    google_web_client_id: str | None = None
    mail_server: str | None = None
    mail_port: int = 587
    mail_username: str | None = None
    mail_password: str | None = None
    mail_from: str | None = None
    mail_starttls: bool = True
    mail_ssl_tls: bool = False

    @property
    def allowed_cors_origins(self) -> list[str]:
        origins = [value.strip().rstrip("/") for value in self.cors_origins.split(",") if value.strip()]
        if "*" in origins:
            raise ValueError("CORS_ORIGINS must use explicit origins.")
        return origins

    @model_validator(mode="after")
    def validate_production_database(self):
        if not self.legarya_debug and self.database_url.lower().startswith("sqlite"):
            raise ValueError("Production requires an explicit PostgreSQL DATABASE_URL.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
