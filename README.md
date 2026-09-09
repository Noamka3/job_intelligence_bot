# Job Intelligence Bot

A personal job-monitoring system: syncs companies from a Google Sheet,
discovers new job postings on their career pages, matches them against a
CV and configurable target roles using hybrid (semantic + rule-based)
scoring, and notifies via WhatsApp. See `docs/architecture.md` for the
full design and `docs/job_sources.md` for verified ATS API formats.

**Status: Phase 4 (job ingestion) complete**, on top of Phases 1-3. Real
adapters for Greenhouse, Lever, Ashby, Comeet, and a basic JSON-LD parser
discover jobs incrementally (new jobs get fetched/embedded, unchanged jobs
are skipped cheaply, jobs missing for several consecutive crawls get
closed rather than deleted). Comeet's adapter was verified live against
real companies from the sheet during development - see
`docs/job_sources.md`. **You will not see real job rows yet without an
OpenAI API key** - job ingestion generates an embedding for every new/
changed job, which requires `OPENAI_API_KEY` to be set for real (see
Setup below); everything up to that step works without one. No matching/
ranking yet (Phase 5).

## Prerequisites

- Python 3.12+
- Docker Desktop
- A Google Cloud service account with Sheets API read access (Phase 3)
- An OpenAI API key (Phase 2+)
- A Twilio account with WhatsApp enabled (Phase 7)

## Setup

```bash
# 1. Create and activate a virtualenv, then install the project
uv venv --python 3.12 .venv          # or: python -m venv .venv
uv pip install -e ".[dev]" -p .venv/Scripts/python.exe   # or: pip install -e ".[dev]"

# 2. Copy env config
cp .env.example .env
# Set OPENAI_API_KEY to use resume upload / target roles (Phase 2).
# Set GOOGLE_APPLICATION_CREDENTIALS to use the sheet sync (Phase 3, see
# "Google Sheets setup" below). Twilio isn't needed until Phase 7.

# 3. Start Postgres + Redis
docker compose up -d postgres redis

# 4. Apply migrations
.venv/Scripts/python.exe -m alembic upgrade head

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

# List / inspect discovered jobs
curl http://127.0.0.1:8000/jobs
curl "http://127.0.0.1:8000/jobs?status=active&company_id=1"
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
.venv/Scripts/python.exe -m app.cli crawl-now
.venv/Scripts/python.exe -m app.cli crawl-company "Torq"
```

`rebuild-profile` re-runs structured extraction + embedding for the active
CV in place (useful after tweaking the extraction prompt or switching
embedding models), without re-uploading the file. `sync-sheet` runs the
same company sync as `POST /sync/google-sheet`. `crawl-now` crawls every
due `CareerSource`; `crawl-company` crawls just one company's sources, by
name - useful for testing a single adapter without waiting on a full
sync. More commands (`score-all`, `send-digest`, ...) are added alongside
the phase that makes them real.
