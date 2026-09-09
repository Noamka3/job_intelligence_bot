"""Application configuration, loaded from environment variables / .env.

A single Settings instance (get_settings()) is the only place that reads
os.environ. Every other module receives values through it rather than
calling os.getenv directly, so all configuration stays discoverable here.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database ---
    # Default port 5544 (not 5432) matches docker-compose.yml: this dev
    # machine already has a native Postgres install / other project's
    # container on 5432/5433.
    database_url: str = "postgresql+psycopg://job_bot:job_bot@localhost:5544/job_bot"

    # --- Redis / Celery (used from Phase 6 onward) ---
    # Default port 6380 (not 6379) for the same reason - avoids an existing
    # Redis container on this machine.
    redis_url: str = "redis://localhost:6380/0"

    # --- Google Sheets ---
    google_sheet_id: str = ""
    google_sheet_gid: int = 168609393
    google_application_credentials: str = "./secrets/google-service-account.json"

    # --- Embeddings ---
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-large"
    embedding_dimensions: int = 1024

    # --- LLM (structured CV extraction now; optional reranking from Phase 5) ---
    openai_chat_model: str = "gpt-5.4-mini"

    # --- Display / scheduling defaults ---
    default_timezone: str = "Asia/Jerusalem"
    default_poll_minutes: int = 5
    job_missing_threshold: int = 3

    # --- Matching / notifications ---
    notification_score_threshold: int = Field(default=80, ge=0, le=100)

    # --- Twilio WhatsApp (Phase 7) ---
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""
    whatsapp_to: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
