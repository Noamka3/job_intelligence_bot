"""Application configuration, loaded from environment variables / .env.

A single Settings instance (get_settings()) is the only place that reads
os.environ. Every other module receives values through it rather than
calling os.getenv directly, so all configuration stays discoverable here.
"""

from __future__ import annotations

import os

# On this machine, Avast's TLS-inspection hook sets SSLKEYLOGFILE to an
# internal named pipe (\\.\aswMonFltProxy\...). Python's ssl module tries
# to open that path when creating any SSL context (ssl.create_default_
# context(), used by httpx/google-auth/openai for every real HTTPS
# request) and crashes the whole process with "OPENSSL_Uplink ... no
# OPENSSL_Applink" - not a bug in this app. Unsetting it here, before
# anything else in the process can create an SSL context, fixes it; this
# is a no-op on any machine where the variable isn't set to begin with.
# This module is imported first by every entrypoint (models, services,
# main.py, cli.py, alembic/env.py, tests/conftest.py), so this is the one
# place that reliably runs before the first SSL context is created.
os.environ.pop("SSLKEYLOGFILE", None)

# Separately: Avast also installs its own root CA into the Windows
# certificate store to inspect HTTPS traffic. That root isn't in the
# plain `certifi` bundle ssl/httpx use by default, so every outbound
# HTTPS request (career pages, Google Sheets, OpenAI) fails with
# CERTIFICATE_VERIFY_FAILED. truststore delegates certificate
# verification to the OS trust store instead (the same fix `uv` already
# uses here via UV_SYSTEM_CERTS) - harmless, and correct, on any machine.
import truststore  # noqa: E402

truststore.inject_into_ssl()

from functools import lru_cache  # noqa: E402
from typing import Literal  # noqa: E402

from pydantic import Field  # noqa: E402
from pydantic_settings import BaseSettings, SettingsConfigDict  # noqa: E402


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
    # "local" (default): free, runs on this machine via fastembed/ONNX, no
    # API key needed. "openai": higher quality, costs a few cents/month at
    # this project's volume, needs OPENAI_API_KEY. Swapping providers
    # means re-embedding everything - the two are not numerically
    # comparable even at the same dimension count.
    embedding_provider: Literal["local", "openai"] = "local"
    local_embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-large"
    embedding_dimensions: int = 384

    # --- LLM (structured CV extraction now; optional reranking from Phase 5) ---
    # "ollama" (default): free, runs on this machine, no API key needed -
    # needs `ollama serve` running and the model pulled once
    # (`ollama pull llama3.2:3b`). "openai": needs OPENAI_API_KEY.
    # ollama_timeout_seconds bounds worst case: CPU-only inference on
    # underpowered/contended hardware can be extremely slow (minutes for
    # even a short input, observed during development) - a timeout turns
    # that into a fast, clear failure instead of hanging the request (and,
    # were it not offloaded to a threadpool, the whole server) instead.
    llm_provider: Literal["ollama", "openai"] = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "llama3.2:3b"
    ollama_timeout_seconds: int = 120
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
