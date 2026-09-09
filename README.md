# Job Intelligence Bot

A personal job-monitoring system: syncs companies from a Google Sheet,
discovers new job postings on their career pages, matches them against a
CV and configurable target roles using hybrid (semantic + rule-based)
scoring, and notifies via WhatsApp. See `docs/architecture.md` for the
full design and `docs/job_sources.md` for verified ATS API formats.

**Status: Phase 4 (job ingestion) complete and verified end-to-end**, on
top of Phases 1-3. Real adapters for Greenhouse, Lever, Ashby, Comeet, and
a basic JSON-LD parser discover jobs incrementally (new jobs get fetched/
embedded, unchanged jobs are skipped cheaply, jobs missing for several
consecutive crawls get closed rather than deleted). Verified live against
two real companies from the sheet: `python -m app.cli crawl-now` pulled
**30 real open roles from Torq (Greenhouse) and 19 from Tango (Comeet)**,
all persisted with real embeddings - see `docs/job_sources.md` for the
Comeet-specific findings from that verification.

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

No matching/ranking yet (Phase 5).

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
will be slow with many. Phase 6 replaces the "runs on a timer" part with
Celery Beat dispatching to a worker instead of blocking one HTTP call.

## CLI

```bash
.venv/Scripts/python.exe -m app.cli rebuild-profile
.venv/Scripts/python.exe -m app.cli sync-sheet
.venv/Scripts/python.exe -m app.cli import-excel "C:\path\to\companies.xlsx"
.venv/Scripts/python.exe -m app.cli crawl-now
.venv/Scripts/python.exe -m app.cli crawl-company "Torq"
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
sync. More commands (`score-all`, `send-digest`, ...) are added alongside
the phase that makes them real.
