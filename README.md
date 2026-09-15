# Job Intelligence Bot

A personal job-monitoring system: syncs companies from a Google Sheet,
discovers new job postings on their career pages, matches them against a
CV and configurable target roles using hybrid (semantic + rule-based)
scoring, and notifies via WhatsApp. See `docs/architecture.md` for the
full design and `docs/job_sources.md` for verified ATS API formats.

**Status: Phase 6 (automatic scheduling) complete and verified end-to-end**,
on top of Phases 1-5. All 240 real companies from the sheet are imported;
Greenhouse/Comeet/JSON-LD adapters discover jobs incrementally (new jobs
get fetched/embedded, unchanged jobs skipped cheaply, missing jobs closed
after several consecutive crawls, never deleted). The matching engine
(`app/services/matching/`) combines semantic similarity, skill matching,
title/role matching, seniority detection, location, and recency into one
0-100 score with human-readable reasons/concerns, and now runs
automatically right after ingestion (`app/services/jobs/ingestion.py`
calls `score_job` for every new/changed job - no separate manual step) -
verified against a real uploaded CV + a real "Junior Software Engineer"
target role scored against all 170 real discovered jobs. See
`docs/matching.md` for the design, including a real empirical finding:
the default weights had to be rebalanced well away from the spec's
suggested starting point, because raw semantic similarity barely
distinguishes "Junior" from "Senior" versions of the same role.

A Celery worker + beat scheduler (`app/tasks/`, running in Docker - see
"Running the scheduler" below) now dispatch every due `CareerSource`
automatically on a 5-minute timer (`DEFAULT_POLL_MINUTES`), the same code
path as `crawl-now`/`POST /operations/crawl-now`. Verified live: manually
triggered a real dispatch of all 210 then-due sources against the running
worker container and inspected the resulting `CrawlRun` rows in Postgres -
found and fixed a real bug in the process, see the "Celery worker runs out
of memory" troubleshooting entry below.

Embeddings default to a **free local model** (`EMBEDDING_PROVIDER=local`,
see `app/services/embeddings/local_provider.py`) - no API key needed for
job ingestion to work end-to-end. Structured CV extraction (Phase 2)
defaults to a **free local LLM via Ollama** (`LLM_PROVIDER=ollama`) too -
OpenAI is only used if you explicitly switch either provider to `openai`.
**Honest caveat, found during live testing on this dev machine:**
CPU-only Ollama inference was extremely slow and once destabilized the
whole machine badly enough to crash Docker Desktop (see the
"Local LLM performance" troubleshooting entry below) - a 120s timeout
(`OLLAMA_TIMEOUT_SECONDS`) now bounds the damage, and a resume upload
always succeeds regardless (raw text + embedding are saved even if
structured extraction fails or times out - see
`app/services/candidate/profile_service.py`), but extraction quality on
the default small model (`llama3.2:3b`) was noticeably weak on a real
13-page CV. If this matters to you, `LLM_PROVIDER=openai` is more
reliable for this one feature.

Israel-only job filtering (`GET /jobs` defaults to `israel_only=true`) is
in per user request - see `app/services/jobs/location.py`.

No WhatsApp notifications yet (Phase 7) - new high-scoring matches sit in
`JobMatch`/`GET /matches/top` until you check.

## Prerequisites

- Python 3.12+
- Docker Desktop
- A Google Cloud service account with Sheets API read access (Phase 3) -
  **or** skip it and use `app.cli import-excel <path>` instead, see below
- [Ollama](https://ollama.com) installed and running (`ollama serve`),
  with a model pulled (`ollama pull llama3.2:3b`) - free, used by default
  for resume structured extraction (Phase 2). See the performance caveat
  above; an OpenAI key is a more reliable alternative for this feature.
- An OpenAI API key - **optional**: only if you switch `LLM_PROVIDER` or
  `EMBEDDING_PROVIDER` to `openai`. Job ingestion (Phase 4) works fully
  without one either way - it defaults to a free local embedding model.
- A Twilio account with WhatsApp enabled (Phase 7)

## Setup

```bash
# 1. Create and activate a virtualenv, then install the project
uv venv --python 3.12 .venv          # or: python -m venv .venv
uv pip install -e ".[dev]" -p .venv/Scripts/python.exe   # or: pip install -e ".[dev]"

# 2. Copy env config
cp .env.example .env
# Defaults work as-is for job ingestion (Phase 4) - EMBEDDING_PROVIDER is
# "local" (free, no key). Set OPENAI_API_KEY only if you want resume
# upload (Phase 2) or switch EMBEDDING_PROVIDER to "openai". Set
# GOOGLE_APPLICATION_CREDENTIALS to use the sheet sync (Phase 3, see
# "Google Sheets setup" below). Twilio isn't needed until Phase 7.

# 3. Start Postgres + Redis
docker compose up -d postgres redis

# 4. Apply migrations
.venv/Scripts/python.exe -m alembic upgrade head
# First real embedding call downloads the local model (~220MB, one-time,
# cached under ~/.cache/fastembed/ afterward).

# 5. Run the API
.venv/Scripts/python.exe -m uvicorn app.main:app --reload
# -> GET http://127.0.0.1:8000/health should return {"status": "ok", ...}
```

### Note on ports

`docker-compose.yml` publishes Postgres on **5544** (not 5432) and Redis on
**6380** (not 6379). This is not the norm for a fresh machine — it's
because this particular dev machine already has a native Postgres service
and another project's containers on the standard ports. If you're setting
this up on a clean machine, feel free to change both back to the standard
ports in `docker-compose.yml` and `.env`.

### Note for editors/IDEs

Point your editor's Python interpreter at `.venv/Scripts/python.exe` (or
run `uv sync` equivalent) so import resolution and type checking work
in-editor, not just from the CLI.

### Google Sheets setup (Phase 3)

The company sync reads `GOOGLE_SHEET_ID` + `GOOGLE_SHEET_GID` (already
defaulted to the real sheet in `.env.example`) via a Google service
account - never your own Google login.

1. In Google Cloud Console, create (or reuse) a project and enable the
   **Google Sheets API**.
2. Create a **Service Account** (IAM & Admin -> Service Accounts), then
   create a JSON key for it and download it.
3. Save that file somewhere outside the repo (e.g. `./secrets/google-service-account.json`
   - already gitignored) and point `GOOGLE_APPLICATION_CREDENTIALS` at it in `.env`.
4. **Share the actual Google Sheet** with the service account's
   `client_email` (found inside the JSON key file) - Viewer access is
   enough. Without this step every sync call fails with a permissions
   error, since the service account has no access of its own.

## Running the scheduler (Phase 6)

The worker + beat scheduler run in Docker (Celery's prefork pool doesn't
work on native Windows - see the troubleshooting entry below), sharing the
same image as each other (`Dockerfile`) but not the FastAPI app, which
still runs directly on the Windows host during development.

```bash
docker compose up -d --build worker beat
# beat fires dispatch_due_sources every DEFAULT_POLL_MINUTES (5 by
# default); it fans out crawl_one_source per due CareerSource, which is
# the same crawl_source()+score_job() call crawl-now/POST
# /operations/crawl-now make - just dispatched automatically instead of
# triggered manually.

docker logs -f job_bot_worker   # watch crawls happen live
docker logs -f job_bot_beat     # watch the 5-minute dispatch tick
```

If you're on a machine with a TLS-inspecting antivirus (see the pip/SSL
troubleshooting entries below), building `worker`/`beat` needs the same
CA cert workaround as the host - see `certs/README.md`.

## Running tests

Integration tests need Postgres/Redis running (`docker compose up -d
postgres redis`) — they exercise real constraints (unique indexes, FKs)
rather than mocking the database.

```bash
.venv/Scripts/python.exe -m pytest
```

## Code quality

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m mypy app
.venv/Scripts/python.exe -m mypy tests
```

## Database migrations

```bash
# After changing a model in app/models/:
.venv/Scripts/python.exe -m alembic revision --autogenerate -m "describe the change"
# Review the generated file in alembic/versions/ before applying -
# autogenerate does not know about the vector extension or intentional
# renames, and won't add `CREATE EXTENSION IF NOT EXISTS vector` itself.
.venv/Scripts/python.exe -m alembic upgrade head
```

## Troubleshooting

**First embedding call fails with an ONNX `bad allocation` / model load
error.** Seen once during development, right after the local model
finished downloading - a retry immediately succeeded (looked like the
antivirus scanning the freshly-written model file was holding a lock on
it). If it happens, just re-run the same command.

**`pip install` fails with `CERTIFICATE_VERIFY_FAILED`.** This machine's
antivirus (Avast) does TLS inspection with its own root certificate, which
the plain Python `certifi` bundle doesn't trust. Use `uv` instead of pip
(`uv pip install ...`) — it already respects the Windows system
certificate store (`UV_SYSTEM_CERTS=true` is set in this environment).

**`alembic upgrade head` fails with a password/auth error against
`localhost`.** Something else on the machine is already listening on the
port you think Postgres/Redis use. Check with (PowerShell):
`Get-NetTCPConnection -LocalPort 5432` (or whichever port). This project
deliberately uses 5544/6380 for exactly this reason - see above.

**Local LLM performance (Ollama, Phase 2's structured CV extraction).**
On this dev machine, CPU-only inference was severe: a single short-text
extraction call took 5-9 minutes, and running a couple of test calls
concurrently pushed the machine into resource exhaustion bad enough to
crash Docker Desktop's engine outright (`docker ps` failed with
`dockerDesktopLinuxEngine` unreachable; fixed by killing and relaunching
`Docker Desktop.exe`, data was intact afterward - Postgres's normal WAL
crash recovery handled it). Mitigations now in place: `OLLAMA_TIMEOUT_SECONDS`
(default 120s) bounds a single request, and resume upload never blocks on
this step failing - see `_extract_structured_profile_best_effort` in
`app/services/candidate/profile_service.py`. Still, avoid running more
than one Ollama call at a time on constrained hardware, and don't be
surprised if it's slow. A real end-to-end upload of a 13-page PDF
completed in 66s on a "quiet" system (no other load) - much better than
the worst case, but still slow, and the extraction quality was weak
(mostly empty fields) even then.

**Celery on Windows (Phase 6+).** Celery's default worker pool doesn't
work on native Windows. The worker and beat scheduler are meant to run
inside Docker (Linux) containers, not directly on the Windows host -
Postgres and Redis already run this way, so this is consistent rather than
an extra step.

**`docker compose build worker`/`beat` fails with `CERTIFICATE_VERIFY_FAILED`
during `pip install`.** Same root cause as the host-side pip issue below -
Docker Desktop's container network traffic goes through the same
TLS-inspecting antivirus. Fixed via `certs/` (see `certs/README.md`) -
the Dockerfile installs whatever CA certs are dropped there into the
image's trust store and points `PIP_CERT`/`SSL_CERT_FILE` at it, since pip
vendors its own certifi bundle and ignores the system store otherwise.

**Celery worker runs out of memory and SIGKILLs its own child processes
under a large batch of due sources.** Found during Phase 6 live
verification: manually dispatching all 210 then-due `CareerSource` rows
at once against the default worker concurrency (one prefork process per
CPU core - 8 on this dev machine) produced 250+ `SIGKILL`/
`WorkerLostError` events and `OperationalError: ... Temporary failure in
name resolution` connecting to Redis, because each worker process loads
its *own* independent copy of the local embedding model
(`app/services/embeddings` caches it per-process via `@lru_cache`, not
shared across forks) - 8 concurrent model loads exceeded the ~3.75GB total
RAM of this machine's Docker Desktop/WSL2 VM. `docker-compose.yml` now
pins the worker to `--concurrency=2`, which was reverified clean (0
SIGKILLs, ~360MB peak) against a smaller real due batch. This was a
one-time bootstrap-scale event, not a permanent steady-state problem (most
of those 210 sources are `generic_html`/`workday`/`taleo`, which have no
adapter yet - Phase 8 - and back off exponentially after each failed
attempt, capped at 24h), but a fresh company sync or a new adapter could
reintroduce a large due batch, so the concurrency cap stays as a
permanent safeguard rather than a one-off fix.

**Any real HTTPS call crashes the process with `OPENSSL_Uplink(...): no
OPENSSL_Applink`** (e.g. running tests that hit `httpx`, or the Google
Sheets sync). This machine's antivirus (Avast) sets the environment
variable `SSLKEYLOGFILE` to one of its own internal named pipes
(`\\.\aswMonFltProxy\...`) for TLS inspection; Python's `ssl` module
crashes trying to open that path whenever it creates an SSL context. This
is already worked around in `app/core/config.py` (which unsets the
variable before anything else in the process can create an SSL context) -
if you ever see this crash again, something is importing before
`app.core.config`, or a *new* entrypoint (e.g. a fresh script run directly)
needs the same fix.

## API (Phase 2)

```bash
# Upload a resume (becomes the active profile)
curl -F "file=@/path/to/cv.pdf" http://127.0.0.1:8000/candidate/resume

# See the active profile / all versions
curl http://127.0.0.1:8000/candidate/active
curl http://127.0.0.1:8000/candidate/profiles

# Reactivate an older version
curl -X POST http://127.0.0.1:8000/candidate/profiles/1/activate

# Create a target role
curl -X POST http://127.0.0.1:8000/target-roles \
  -H "Content-Type: application/json" \
  -d '{"canonical_name": "Junior Software Engineer", "aliases": ["Software Engineer I"], "positive_keywords": ["python", "rest api"]}'

curl http://127.0.0.1:8000/target-roles
```

## API (Phase 3)

```bash
# Sync companies + career sources from the configured Google Sheet
curl -X POST http://127.0.0.1:8000/sync/google-sheet

# List companies / sources (optionally filtered)
curl http://127.0.0.1:8000/companies
curl "http://127.0.0.1:8000/companies?enabled=true"
curl "http://127.0.0.1:8000/sources?source_type=comeet"
```

## API (Phase 4)

```bash
# Crawl every due CareerSource now (blocks until done - see caveat below)
curl -X POST http://127.0.0.1:8000/operations/crawl-now

# List / inspect discovered jobs (israel_only defaults to true)
curl http://127.0.0.1:8000/jobs
curl "http://127.0.0.1:8000/jobs?status=active&company_id=1"
curl "http://127.0.0.1:8000/jobs?title=junior&israel_only=false"
curl http://127.0.0.1:8000/jobs/1
```

`POST /operations/crawl-now` runs every due source synchronously inside
the request - fine for a handful of sources during development, but it
will be slow with many. Since Phase 6, Celery Beat dispatches the same
`crawl_source()` call to a worker automatically every 5 minutes (see
"Running the scheduler" above) - `crawl-now`/`POST /operations/crawl-now`
are still there for an on-demand manual trigger (e.g. right after a fresh
company sync).

## API (Phase 5)

```bash
# Score every ACTIVE job against the active CV + every enabled target role
.venv/Scripts/python.exe -m app.cli score-all

# Top matches for the active candidate profile, best first
curl "http://127.0.0.1:8000/matches/top?limit=20"
curl "http://127.0.0.1:8000/matches/top?min_score=70"

# Leave feedback on a job (spec §29 - collected for future ranking tuning,
# no learning happens on it yet)
curl -X POST http://127.0.0.1:8000/jobs/1/feedback \
  -H "Content-Type: application/json" -d '{"action": "interested"}'
```

Since Phase 6, every new/changed job is scored automatically right after
ingestion - `score-all` is now mainly useful after uploading a *new* CV or
adding a target role, to backfill scores against jobs that were already in
the database. See `docs/matching.md` for how the seven component scores
combine, and why the weights differ from the spec's suggested starting
point. Known
limitation found during live testing: a job in a clearly different field
(e.g. "Junior Customer Support") can still rank surprisingly high purely
from a matching seniority signal + a couple of generic skill overlaps
(SQL, testing, ...) - tightening a `TargetRole`'s `negative_keywords` is
the intended lever for this, not something the scorer should special-case.

## CLI

```bash
.venv/Scripts/python.exe -m app.cli rebuild-profile
.venv/Scripts/python.exe -m app.cli sync-sheet
.venv/Scripts/python.exe -m app.cli import-excel "C:\path\to\companies.xlsx"
.venv/Scripts/python.exe -m app.cli crawl-now
.venv/Scripts/python.exe -m app.cli crawl-company "Torq"
.venv/Scripts/python.exe -m app.cli score-all
```

`rebuild-profile` re-runs structured extraction + embedding for the active
CV in place (useful after tweaking the extraction prompt or switching
embedding models), without re-uploading the file. `sync-sheet` runs the
same company sync as `POST /sync/google-sheet`; `import-excel` does the
same upsert/disable logic from a local .xlsx export instead (company name
in column A, URL in column B) - no Google credentials needed, useful
before setting those up or for a one-off import. `crawl-now` crawls every
due `CareerSource`; `crawl-company` crawls just one company's sources, by
name - useful for testing a single adapter without waiting on a full
sync. `score-all` scores every `ACTIVE` job against the active CV and
every enabled target role (see API (Phase 5) above). More commands
(`send-digest`, ...) are added alongside the phase that makes them real.
