# Architecture

## Pipeline

```
Google Sheets -> Company Sync -> CareerSourceResolver -> ATS Adapter ->
Incremental Job Discovery -> Normalization -> Deduplication -> Postgres ->
Hybrid Matching Engine -> Ranking -> API/Dashboard -> WhatsApp
```

The system is a single Python service (FastAPI for the API/dashboard,
Celery for the recurring crawl/match/notify pipeline) backed by one
Postgres database (with pgvector for embeddings) and Redis (Celery broker).
There is deliberately no microservice split — this is a personal-scale
tool; the interfaces that vary by provider (`JobSourceAdapter`,
`EmbeddingProvider`, `NotificationProvider`) are what make it extensible,
not process boundaries.

## Why sync SQLAlchemy, not async

FastAPI route handlers and Celery task bodies use plain synchronous
SQLAlchemy sessions (`app/db/session.py`), not the async engine. Traffic is
single-user; async DB access would add real complexity (async sessions,
async Celery task bodies, async Alembic env) for no measurable benefit at
this scale. FastAPI runs sync dependencies in a threadpool, so this does
not block the event loop.

## Entities

| Table | Purpose |
|---|---|
| `companies` | One row per company from the Google Sheet. |
| `career_sources` | A company's recruiting source(s) (ATS board, career page). Classified by `source_type`; drives which `JobSourceAdapter` handles it. `UNSUPPORTED` covers LinkedIn-only entries and rows the resolver couldn't classify — recorded, never silently dropped. |
| `candidate_profiles` | Versioned CV snapshots. Exactly one `is_active=True` row at a time (enforced by a partial unique index, not just application logic). Previous versions are kept for history. |
| `target_roles` | Configurable roles to match against (e.g. "Junior Software Engineer"), each with its own embedding, separate from the candidate's. |
| `job_postings` | Discovered jobs. `source_published_at`/`source_updated_at` (from the ATS, when available) are kept strictly separate from `first_seen_at`/`last_seen_at` (ours) — the API must never present one as the other. |
| `job_matches` | The hybrid score for one (candidate profile, target role, job) triple, with every component score plus `reasons`/`concerns`. Unique per triple — recomputed in place, not appended. |
| `job_feedback` | User decisions (interested/applied/not relevant/...) for future ranking tuning. |
| `notification_logs` | Every notification actually sent, keyed by a dedup key, so a job never triggers the same channel twice. |
| `crawl_runs` | One observability record per source-check attempt. |

All timestamps are stored in UTC; conversion to the configurable display
timezone (default `Asia/Jerusalem`) happens only at the presentation layer
(`app/core/timezone.py`), never in storage or business-logic comparisons.

## Key interfaces (built where implementations genuinely vary)

- `JobSourceAdapter` (Phase 4+): `can_handle` / `list_jobs` / `fetch_job` / `normalize`, producing `JobStub`/`JobDetails` DTOs. The rest of the system never knows whether a job came from Greenhouse, Lever, Workday, or generic HTML.
- `EmbeddingProvider` (Phase 2+): default `OpenAIEmbeddingProvider` (`text-embedding-3-large`, `dimensions=1024`). Nothing outside this provider talks to the OpenAI SDK directly.
- `NotificationProvider` (Phase 7+): `ConsoleNotificationProvider`, `TwilioWhatsAppNotificationProvider`.
- `LLMReranker` (optional, Phase 5+): only invoked for jobs whose preliminary score already clears a threshold.

## Local development ports

Postgres and Redis are published on **5544** and **6380** (not the default
5432/6379) because this development machine already has a native Postgres
service and another project's containers bound to the standard ports. See
`docker-compose.yml` and `.env.example`.
