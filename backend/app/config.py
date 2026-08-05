from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
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
    cors_origins: str = (
        "http://127.0.0.1:4173,http://localhost:4173,"
        "http://127.0.0.1:5500,http://localhost:5500"
    )

    @property
    def allowed_cors_origins(self) -> list[str]:
        """Return explicit browser origins configured for CORS."""
        origins = [
            origin.strip().rstrip("/")
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]
        if "*" in origins:
            raise ValueError("CORS_ORIGINS must contain explicit origins, not '*'.")
        return origins

    # JWT settings
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # AI settings
    ai_provider: str = "openai"
    ai_model: str = ""
    audio_transcription_model: str = "gpt-4o-mini-transcribe"
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "alloy"
    openai_tts_male_voice: str = "cedar"
    openai_tts_female_voice: str = "marin"
    default_standard_voice_profile: str = "standard_female"
    openai_tts_format: str = "mp3"
    tts_max_text_characters: int = Field(default=4096, ge=1, le=4096)
    tts_timeout_seconds: float = Field(default=60.0, gt=0)
    message_speech_engine: str = "tts"
    openai_realtime_model: str = "gpt-realtime-2.1"
    openai_realtime_timeout_seconds: float = Field(default=60.0, gt=0)
    openai_realtime_max_audio_bytes: int = Field(
        default=25 * 1024 * 1024,
        ge=1,
    )
    openai_realtime_output_format: str = "audio/pcm"
    realtime_fallback_to_tts: bool = True
    sarvam_api_key: str | None = None
    sarvam_model: str = "bulbul:v3"
    sarvam_speaker_male: str = "shubh"
    sarvam_speaker_female: str = "priya"
    sarvam_output_format: str = "wav"
    sarvam_timeout_seconds: float = Field(default=60.0, gt=0)
    sarvam_max_text_characters: int = Field(default=2500, ge=1, le=2500)
    sarvam_max_audio_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    sarvam_pace: float = Field(default=0.92, ge=0.5, le=2.0)
    sarvam_temperature: float = Field(default=0.6, ge=0.01, le=2.0)
    sarvam_pronunciation_dictionary_id: str | None = None
    sarvam_pronunciation_dictionary_required: bool = False
    speech_emotion_enabled: bool = True
    speech_nonverbal_cues_enabled: bool = False
    speech_discourse_markers_enabled: bool = False
    openai_api_key: str | None = None
    ai_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    ai_read_timeout_seconds: float = Field(default=90.0, gt=0)
    ai_retry_max_retries: int = Field(default=2, ge=0)
    ai_retry_base_delay_seconds: float = Field(default=0.25, ge=0)
    ai_retry_max_delay_seconds: float = Field(default=2.0, gt=0)
    ai_retry_jitter_seconds: float = Field(default=0.15, ge=0)
    ai_max_context_messages: int = Field(default=24, ge=2)

    # Companion approved-memory grounding budget
    memory_grounding_max_memories: int = Field(default=8, ge=1, le=100)
    memory_grounding_max_estimated_tokens: int = Field(
        default=1500,
        ge=1,
    )
    memory_grounding_max_characters: int = Field(default=6000, ge=1)

    @model_validator(mode="after")
    def reject_production_sqlite_fallback(self):
        """Production must explicitly select PostgreSQL persistence."""
        if not self.debug and self.database_url.lower().startswith("sqlite"):
            raise ValueError(
                "DATABASE_URL must use PostgreSQL when DEBUG is false."
            )
        return self


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
