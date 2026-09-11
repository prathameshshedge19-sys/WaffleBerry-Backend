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
    # Optional release-time switch for the bundled Capacitor client. Keeping it
    # unset means Phase B code does not change the deployed production allowlist.
    android_app_origin: Literal["https://localhost"] | None = None

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=30, ge=5, le=1440)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=30)
    remembered_refresh_token_expire_days: int = Field(default=30, ge=1, le=90)
    auth_code_expire_minutes: int = Field(default=10, ge=1, le=60)
    access_invite_expire_days: int = Field(default=7, ge=1, le=30)
    frontend_base_url: str = "http://localhost:5600"

    # Phase 2 cannot enforce quotas. Enable only after the additive migration.
    plans_tracking_enabled: bool = False
    plans_enforcement_enabled: bool = False

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

    # L16 Media & Sources. Production must explicitly select an encrypted
    # S3-compatible backend; local storage is only the debug/test adapter.
    media_enabled: bool = False
    media_storage_backend: Literal["local", "s3"] = "local"
    media_local_storage_path: str = "./media-storage"
    media_s3_endpoint_url: str | None = None
    media_s3_bucket: str | None = None
    media_s3_region: str | None = None
    media_s3_access_key_id: str | None = None
    media_s3_secret_access_key: str | None = None
    media_s3_sse_customer_key: str | None = None
    media_s3_sse_customer_key_id: str | None = None
    media_max_photo_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    media_max_document_bytes: int = Field(default=50 * 1024 * 1024, ge=1024, le=200 * 1024 * 1024)
    media_max_audio_bytes: int = Field(default=100 * 1024 * 1024, ge=1024, le=500 * 1024 * 1024)
    media_max_video_bytes: int = Field(default=100 * 1024 * 1024, ge=1024, le=500 * 1024 * 1024)
    media_upload_expire_seconds: int = Field(default=3600, ge=300, le=86400)
    media_intelligence_model: str = "gpt-5.5"

    # L19 presentation only. Purge must not depend on either enablement flag.
    visual_presence_enabled: bool = False
    visual_preparation_enabled: bool = False
    visual_worker_python: str | None = None
    visual_model_path: str | None = None
    visual_native_library_dir: str | None = None

    # L15 Phase B is opt-in infrastructure, with no product audio endpoint.
    realtime_enabled: bool = False
    realtime_model: str = "gpt-realtime-2.1"
    realtime_transcription_model: str = "gpt-live-transcribe"
    realtime_ticket_seconds: int = Field(default=30, ge=5, le=60)
    realtime_session_seconds: int = Field(default=1800, ge=60, le=2700)
    realtime_lease_seconds: int = Field(default=15, ge=5, le=60)
    realtime_reconnect_seconds: int = Field(default=10, ge=1, le=30)
    realtime_auth_timeout_seconds: int = Field(default=5, ge=1, le=15)
    realtime_io_timeout_seconds: int = Field(default=10, ge=1, le=30)
    realtime_idle_seconds: int = Field(default=30, ge=5, le=90)
    realtime_frame_bytes: int = Field(default=4800, ge=960, le=24000)
    realtime_message_bytes: int = Field(default=8192, ge=512, le=65536)
    realtime_messages_per_second: int = Field(default=60, ge=10, le=120)
    realtime_creations_per_minute: int = Field(default=6, ge=1, le=30)
    realtime_queue_depth: int = Field(default=16, ge=1, le=64)
    realtime_max_sessions_per_user: Literal[1] = 1

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
        if self.android_app_origin and self.android_app_origin not in origins:
            origins.append(self.android_app_origin)
        return origins

    @model_validator(mode="after")
    def validate_production_database(self):
        if not self.legarya_debug and self.database_url.lower().startswith("sqlite"):
            raise ValueError("Production requires an explicit PostgreSQL DATABASE_URL.")
        if not self.legarya_debug and self.media_enabled:
            if self.media_storage_backend != "s3":
                raise ValueError("Production media requires an explicit S3-compatible storage backend.")
            if not all((self.media_s3_endpoint_url, self.media_s3_bucket, self.media_s3_access_key_id,
                        self.media_s3_secret_access_key, self.media_s3_sse_customer_key,
                        self.media_s3_sse_customer_key_id)):
                raise ValueError("Production media requires private S3 credentials and SSE-C configuration.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
