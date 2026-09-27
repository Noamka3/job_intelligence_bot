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
    # No default: the sheet is the owner's own list of companies, and its
    # id is all anyone needs to read it when the sheet is link-shared.
    # Set both in .env, which is never committed.
    google_sheet_id: str = ""
    google_sheet_gid: int = 0
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
    # 300: the field-by-field extraction prompt takes ~110s on this dev
    # machine's CPU, and a second call estimates years of experience.
    ollama_timeout_seconds: int = 300
    openai_chat_model: str = "gpt-5.4-mini"

    # --- Access control (app/core/security.py) ---
    # Empty password = no authentication, which is what a tool bound to
    # 127.0.0.1 needs. Set both before the dashboard is reachable from
    # anywhere else; Basic auth sends the password on every request, so
    # only over HTTPS or a private network (Tailscale).
    dashboard_user: str = "bot"
    dashboard_password: str = ""

    # --- Display / scheduling defaults ---
    default_timezone: str = "Asia/Jerusalem"
    default_poll_minutes: int = 5
    job_missing_threshold: int = 3
    # The dispatcher tops the crawl queue up to this many waiting crawls
    # per tick and leaves the rest due for the next one - a guard against
    # a queue that only grows (a pause, a fresh sheet sync), where sources
    # whose lease expired while still waiting were queued a second time.
    # Room for every enabled source (~210): since a plain career site
    # costs one listing fetch per crawl, two worker processes finish a
    # tick's crawls well within it. At 60, the ~57 API sources due every
    # tick came first and left three slots for 140 plain sites.
    crawl_queue_target: int = 250
    # Job pages of one source are fetched this many at a time (network-
    # bound; the embedding stays sequential). Measured: 164 of 208
    # sources are plain career sites fetched page by page, 85% of all
    # crawl time, one of them 23 minutes for 259 pages.
    crawl_fetch_concurrency: int = 4
    # A stored job's page is not downloaded again while it is younger than
    # this, unless the listing itself reports a newer "updated" timestamp
    # (the ATS APIs do; plain career sites don't). New links are always
    # fetched at once - this only bounds how long an edit to an existing
    # posting's text can go unnoticed.
    job_details_refresh_hours: int = 24
    # The dashboard's widest window, and how long a posting keeps its
    # text and vector: past this, retention archives or deletes it
    # (app/services/jobs/retention.py). The frontend's "10 days" option
    # is the same number.
    job_retention_days: int = 10

    # --- Ingestion ---
    # Country to ask a source for server-side, where its API supports a
    # location filter (Workday's facets today). Only a cost/volume
    # optimization - a global board like Intel's lists hundreds of jobs
    # elsewhere that would otherwise be fetched, embedded and then dropped
    # by the Israel-only filter anyway. Empty string = no filter. Not the
    # same thing as the Israel-only *display* filter in the API (the
    # pipeline itself stays country-agnostic).
    target_country: str = "Israel"

    # --- Matching / notifications ---
    notification_score_threshold: int = Field(default=80, ge=0, le=100)

    # Match score = 100 x role gate x quality (docs/matching.md). The role
    # gate scales everything by how much the job *is* the role being
    # looked for (0.2 + 0.8 x role fit): a junior-friendly, well-located
    # "Payment Operations Analyst" is not a software engineering job and
    # must not collect the seniority/location/skills points as if it were.
    # The quality weights below must sum to 1.0 - enforced in
    # app/services/matching/scoring.py, not here, so a bad .env value
    # fails loudly at scoring time with a clear message.
    #
    # Deliberately far from the 30/20/20/10/10/5/5 split this started with.
    # Measured on the real data (3,100 jobs, this CV): the local model's
    # raw cosine similarity ranges 0.15-0.55 for the CV and 0.10-0.50 for
    # the role intent even on perfect matches, and barely separates
    # "Junior" from "Senior" for the same stack - so the semantic scores
    # are calibrated to that range and weighted lightly, while seniority
    # detection and skill overlap - the components that actually separate
    # good from bad here - carry the score.
    role_gate_floor: float = 0.2
    weight_seniority: float = 0.45
    weight_skills: float = 0.30
    weight_candidate_semantic: float = 0.12
    weight_intent_semantic: float = 0.08
    weight_location: float = 0.03
    weight_recency: float = 0.02
    # Raw cosine similarity is mapped linearly onto [0, 1] between these
    # (the 5th/99th percentiles observed for this model); outside is clamped.
    semantic_candidate_floor: float = 0.15
    semantic_candidate_ceiling: float = 0.55
    semantic_intent_floor: float = 0.10
    semantic_intent_ceiling: float = 0.50

    # --- Jev, TypeSafe's decision model (app/services/jev/, docs/jev.md) ---
    # An empty key turns it off, which is how the tests and a fresh clone
    # run. With a key, every new posting is read once and every match
    # above the role gate is judged once; both are stored and shown, and
    # JEV_WEIGHT below says how much of the score the judgement takes.
    # Pinned to a version: "jev-latest" moves.
    typesafe_api_key: str = ""
    jev_model: str = "jev-1.13.0"
    # How much of a match's quality Jev's judgement takes, for the
    # matches it judged: quality = (1 - w) x rules + w x P(a recruiter
    # would shortlist). 0 keeps Jev in shadow mode - shown, never ranked
    # on. The role gate applies either way.
    jev_weight: float = Field(default=0.0, ge=0.0, le=1.0)

    # --- Twilio WhatsApp (Phase 7) ---
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""
    whatsapp_to: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
