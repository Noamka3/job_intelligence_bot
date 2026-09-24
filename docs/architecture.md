# Architecture

## Pipeline

```
Google Sheet -> company sync -> source resolver -> source adapter ->
incremental crawl -> normalize + dedupe -> embed -> Postgres (pgvector) ->
hybrid matching -> API -> React dashboard -> (WhatsApp, planned)
```

The system is one Python service (FastAPI for the API and the dashboard,
Celery for the recurring crawl and match work) backed by one Postgres
database with pgvector for the embeddings, and Redis as the Celery broker.
There is deliberately no microservice split: this is a personal-scale
tool, and what makes it extensible are the interfaces that vary by
provider (`JobSourceAdapter`, `EmbeddingProvider`, the LLM dispatch), not
process boundaries. The README walks through each stage of the pipeline.

## Why sync SQLAlchemy, not async

Route handlers and Celery task bodies use plain synchronous SQLAlchemy
sessions (`app/db/session.py`), not the async engine. Traffic is
single-user; async DB access would add real complexity (async sessions,
async task bodies, async Alembic env) for no measurable benefit at this
scale. FastAPI runs sync dependencies in a threadpool, so the event loop
is not blocked.

## Entities

| Table | Purpose |
|---|---|
| `companies` | One row per company from the Google Sheet. |
| `career_sources` | A company's recruiting source(s): an ATS board or a career page. Classified by `source_type`, which decides the adapter. `UNSUPPORTED` covers LinkedIn-only entries and rows the resolver could not classify - recorded with a reason, never silently dropped. |
| `candidate_profiles` | Versioned CV snapshots. Exactly one `is_active=True` row at a time, enforced by a partial unique index rather than application logic. Older versions are kept. |
| `target_roles` | The roles to match against (e.g. "Junior Software Engineer"), each with its own embedding, separate from the CV's. |
| `job_postings` | Discovered jobs. `source_published_at`/`source_updated_at` (from the ATS, when it reports them) are kept strictly apart from `first_seen_at`/`last_seen_at` (ours); the API never presents one as the other. |
| `job_matches` | The hybrid score for one (candidate profile, target role, job) triple, with every component score plus `reasons`/`concerns`. Unique per triple, recomputed in place. |
| `job_feedback` | Decisions on a job (interested / applied / not relevant / too senior / ...). Dismissing feedback hides the job from the default view. |
| `applications` | Where an application stands (applied, screening, interview, offer, ...), opened automatically by "applied" feedback. |
| `crawl_runs` | One observability record per source-check attempt: status, counts, error type. |
| `notification_logs` | Reserved for the WhatsApp phase: one row per notification sent, keyed by a dedup key. Nothing writes it yet. |

All timestamps are stored in UTC. Conversion to the display timezone
(`DEFAULT_TIMEZONE`, `Asia/Jerusalem`) happens only at the presentation
layer, never in storage or in business-logic comparisons.

## The interfaces that vary by provider

- `JobSourceAdapter` (`app/ingestion/adapters/base.py`): `list_jobs`
  returns cheap `JobStub`s from a listing, `fetch_job` turns one into
  `JobDetails`. Dispatch is a plain `CareerSourceType -> adapter` lookup in
  `app/ingestion/registry.py`, so there is no `can_handle`; and
  normalization lives once in `app/services/jobs/normalization.py`, not
  in each adapter. The rest of the system never knows whether a job came
  from Greenhouse, Workday or a plain HTML page.
- `EmbeddingProvider` (`app/services/embeddings/`): `embed_one` /
  `embed_many`. The default is `LocalEmbeddingProvider` (fastembed/ONNX,
  `paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensions): free, no API
  key, chosen over OpenAI so that nothing costs money per embedding and
  the CV stays on the machine. `OpenAIEmbeddingProvider` exists behind
  `EMBEDDING_PROVIDER=openai`; switching means re-embedding everything.
- Structured CV extraction (`app/services/candidate/structured_profile.py`):
  dispatched by `LLM_PROVIDER` to Ollama (default, local, `llama3.2:3b`)
  or OpenAI, bounded by `OLLAMA_TIMEOUT_SECONDS`. A failure here never
  blocks a CV from being saved, because the raw text and its embedding are
  what matching actually uses.

## Local development ports

Postgres and Redis are published on **5544** and **6380** rather than
5432/6379 because the development machine already has a native Postgres
service and another project's containers on the standard ports. See
`docker-compose.yml` and `.env.example`.
