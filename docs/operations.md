# Operations

Everything needed to run, inspect and debug this project day to day. The
[README](../README.md) covers what the system is and why it is built this
way; this file is the operator's manual, including the environment
quirks that cost real time to diagnose on the development machine.

## Prerequisites

- Docker Desktop (Postgres, Redis, the Celery worker and scheduler)
- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node 20 (the dashboard build)
- [Ollama](https://ollama.com) with `llama3.2:3b` pulled, optional — used
  for structured CV extraction, with a deterministic fallback when it is
  unavailable or times out

## Starting it

Three steps, in this order, every time:

1. **Open Docker Desktop** and wait until it says *Engine running*. Until
   it does, `docker compose` fails with `error during connect` or
   `unable to get image` — which means nothing started, not that
   something is wrong with the project.
2. **Start the containers** (Postgres, Redis, worker, scheduler):
   ```powershell
   cd C:\Users\97254\Desktop\Bot_career
   docker compose up -d
   ```
   Expect four lines saying `Started` or `Running`.
3. **Start the API and dashboard**:
   ```powershell
   uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```
   Then open **http://127.0.0.1:8000/app/** — not `localhost`, which
   costs 2 seconds per request while Windows tries IPv6 first.

Startup takes ~20 s (the embedding model loads), then a first pass over
all sources completes within about 10 minutes: up to 5 for the next
dispatcher tick, ~5 for the pass itself.

`[Errno 10048] only one usage of each socket address` means the server is
**already running** — just open the link. To take it over:
`Get-Process python | Stop-Process -Force`, then repeat step 3.

Turning on *Start Docker Desktop when you sign in* makes step 1 automatic,
so crawling resumes on every boot without you doing anything.

### First-time setup

```bash
cp .env.example .env         # defaults are correct for local development
uv sync                      # Python dependencies into .venv
uv run alembic upgrade head  # schema
cd frontend && npm install && npm run build && cd ..
```

`http://127.0.0.1:8000/app/` is the dashboard; `GET /health` reports
Postgres and Redis. The first embedding call downloads the local model
(~240 MB, once, cached under `FASTEMBED_CACHE_PATH`).

Point your editor's interpreter at `.venv/Scripts/python.exe` so imports
and type checking resolve in-editor as well as from the CLI.

### Ports

`docker-compose.yml` publishes Postgres on **5544** and Redis on **6380**,
not the standard ports, because the development machine already has a
native Postgres service and another project's containers on 5432/6379.
On a clean machine both can go back to the defaults in
`docker-compose.yml` and `.env`. Both are bound to `127.0.0.1` on
purpose — see the security section of the README.

### Google Sheets access

The company sync reads `GOOGLE_SHEET_ID` + `GOOGLE_SHEET_GID` through a
Google **service account**, never a personal login.

1. In Google Cloud Console, enable the **Google Sheets API**.
2. Create a service account, create a JSON key, download it.
3. Save it outside the repo (`./secrets/google-service-account.json` is
   git-ignored) and point `GOOGLE_APPLICATION_CREDENTIALS` at it.
4. **Share the sheet** with the service account's `client_email` (inside
   the key file); Viewer is enough. Skipping this makes every sync fail
   with a permissions error — the account has no access of its own.

Without credentials, `import-excel` does the same upsert from a local
`.xlsx` export (company name in column A, URL in column B).

## The scheduler

The worker and Beat run in Linux containers, sharing the app image; the
FastAPI process runs on the host during development.

```bash
docker compose up -d --build worker beat
docker logs -f job_bot_worker   # crawls as they happen
docker logs -f job_bot_beat     # the 5-minute dispatch tick
```

What each tick does:

- `dispatch_due_sources` selects enabled sources of enabled companies
  whose `next_check_at` has passed **and** whose type has an adapter
  registered (`app/ingestion/registry.py`), so a type that gains an
  adapter later is picked up on the next tick instead of sitting in a
  24-hour backoff.
- Each selected source is **leased** for one poll interval before being
  enqueued, so a long queue cannot be re-dispatched by the following
  tick and have two workers collide on the uniqueness constraint.
- The queue is topped up to `CRAWL_QUEUE_TARGET` (250, room for every
  source); API-backed sources go first, longest-overdue first within a
  class.
- Workday and Taleo are asked for `TARGET_COUNTRY` server-side, so a
  global board like Intel's (594 postings) costs ~22 fetches, not ~600.
- `crawl_one_source` runs the same `crawl_source()` as `crawl-now`. A job
  whose detail fetch fails is skipped and counted in
  `CrawlRun.jobs_failed`; the run still succeeds unless *every* fetch
  failed, which fails the run and backs that source off.

Poll intervals: API sources every ~5 minutes (interval 3, i.e. every
tick), Workday/Taleo and plain sites every 10, the browser fallback every
60. A crawl downloads a job page only when the link is new, the listing
reports a newer timestamp, or the stored copy is older than
`JOB_DETAILS_REFRESH_HOURS` (24). Pages that do need fetching are read
`CRAWL_FETCH_CONCURRENCY` (4) at a time.

**All of this stops when the machine sleeps.** After a long sleep the
next tick dispatches the whole backlog and takes 10–15 minutes to work
through it; the status page shows the queue depth and the countdown.

The image runs as a non-root user and the model cache lives in the
`fastembed_cache` volume at `/home/app/.cache/fastembed`. If you change
either, recreate the volume (`docker volume rm bot_career_fastembed_cache`)
— a volume first mounted by an older image is root-owned and the first
download fails with `EACCES`.

## Tests, linting, migrations

Integration tests use a real Postgres with `pgvector` (they exercise real
constraints rather than mocking the database). Stop the worker first: the
suite shares the development database, and long transactions cause lock
convoys.

```bash
docker compose stop worker beat
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app tests
docker compose up -d worker beat
```

```bash
# after changing a model in app/models/
uv run alembic revision --autogenerate -m "describe the change"
# review the generated file: autogenerate does not know about the vector
# extension or intentional renames, and will not add
# `CREATE EXTENSION IF NOT EXISTS vector` itself
uv run alembic upgrade head
```

## CLI

```bash
uv run python -m app.cli sync-sheet            # re-import companies from the sheet
uv run python -m app.cli import-excel FILE     # ...or from a local .xlsx
uv run python -m app.cli reresolve-sources     # re-classify stored companies
uv run python -m app.cli crawl-now             # crawl everything due, synchronously
uv run python -m app.cli crawl-company "Torq"  # one company
uv run python -m app.cli score-all             # rescore every active job
uv run python -m app.cli reassess-seniority    # re-read experience requirements, then rescore
uv run python -m app.cli reclassify-locations  # recompute country/region
uv run python -m app.cli apply-poll-intervals  # re-apply per-type intervals
uv run python -m app.cli rebuild-profile       # re-extract + re-embed the active CV
uv run python -m app.cli reembed               # re-embed jobs and roles
```

`reresolve-sources` is the one to run after a resolver change: it re-runs
classification for every company from the URL it was imported with, no
sheet read, updating sources in place rather than duplicating them.

## API

```bash
# candidate
curl -F "file=@cv.pdf" http://127.0.0.1:8000/candidate/resume
curl http://127.0.0.1:8000/candidate/active
curl -X POST http://127.0.0.1:8000/candidate/profiles/1/activate

# target roles
curl -X POST http://127.0.0.1:8000/target-roles -H "Content-Type: application/json" \
  -d '{"canonical_name": "Junior Software Engineer", "positive_keywords": ["python"]}'

# companies and sources
curl -X POST http://127.0.0.1:8000/sync/google-sheet
curl "http://127.0.0.1:8000/sources?source_type=comeet"

# jobs and matches
curl -X POST http://127.0.0.1:8000/operations/crawl-now
curl "http://127.0.0.1:8000/jobs?title=junior&israel_only=false"
curl "http://127.0.0.1:8000/matches/top?min_score=60&days=3"
curl -X POST http://127.0.0.1:8000/jobs/1/feedback \
  -H "Content-Type: application/json" -d '{"action": "interested"}'

# what the scheduler is doing
curl http://127.0.0.1:8000/dashboard/stats
```

`POST /operations/crawl-now` crawls every due source **synchronously in
the request**, which is fine on demand (right after a sheet sync) and
slow with many sources. The scheduler does the same work automatically.

Feedback is stored for future ranking work; nothing learns from it yet.

## Troubleshooting

These are machine-specific problems that were diagnosed the hard way.
Most stem from a TLS-inspecting antivirus (Avast) on the development
machine.

**Everything looks healthy but no jobs are ever found.** The single most
likely cause on this machine: Avast rotated its TLS-inspection root
certificate, so the copy baked into the worker image no longer matches
and every crawl fails with `CERTIFICATE_VERIFY_FAILED` while the
dashboard, the scheduler and the containers all look fine. Happened on
2026-09-24 (151 failures in 8 minutes, 0 jobs). Diagnosis and the exact
refresh-and-rebuild steps are in **[`certs/README.md`](../certs/README.md)** —
start there before suspecting anything else. Quick check:

```powershell
docker compose exec -T postgres psql -U job_bot -d job_bot -c "select status, count(*) from crawl_runs where started_at > now() - interval '15 minutes' group by 1"
```

**Any HTTPS call crashes with `OPENSSL_Uplink(...): no OPENSSL_Applink`.**
Avast sets `SSLKEYLOGFILE` to one of its internal named pipes
(`\\.\aswMonFltProxy\...`); Python's `ssl` module crashes trying to open
that path whenever it creates an SSL context. `app/core/config.py` unsets
it before anything else in the process can create one. If it reappears,
something is importing before `app.core.config`, or a new standalone
script needs `Remove-Item env:SSLKEYLOGFILE` first.

**`pip install` fails with `CERTIFICATE_VERIFY_FAILED`.** Same antivirus,
different layer: its root certificate is not in the `certifi` bundle pip
uses. Use `uv`, which respects the Windows certificate store.

**`docker compose build worker` fails the same way.** Container traffic
goes through the same inspection. [`certs/`](../certs/README.md) holds
the CA certificates the build installs into the image's trust store, with
`PIP_CERT`/`SSL_CERT_FILE` pointed at it. The browser fallback needs two
more: `NODE_EXTRA_CA_CERTS` for Playwright's Node downloader, and the
certificate registered in Chromium's own NSS store, or every rendered
page fails with `ERR_CERT_AUTHORITY_INVALID`. Both are in the Dockerfile.
Refreshing the certificate and rebuilding covers all three.

**`alembic upgrade head` fails with an auth error against localhost.**
Something else is listening on the port. Check with
`Get-NetTCPConnection -LocalPort 5544`; this project uses non-standard
ports for exactly this reason.

**The Celery worker SIGKILLs its own children under a large batch.**
Found during live verification: dispatching all 210 due sources at the
default concurrency (one process per core, 8 here) produced 250+
`WorkerLostError` events, because each worker process loads its **own**
copy of the local embedding model — it is cached per process via
`@lru_cache`, not shared across forks — and 8 copies exceeded the WSL2
VM's ~3.75 GB. `--concurrency=2` is now pinned in `docker-compose.yml`
and stays as a permanent safeguard.

**Local LLM extraction is slow.** CPU-only inference on this machine took
5–9 minutes for a short extraction, and running two at once was enough to
crash Docker Desktop's engine. `OLLAMA_TIMEOUT_SECONDS` bounds a single
request, and a résumé upload never blocks on this step failing — see
`_extract_structured_profile_best_effort` in
`app/services/candidate/profile_service.py`. Run one call at a time on
constrained hardware.

**The first embedding call fails with an ONNX `bad allocation` error.**
Seen once, immediately after the model finished downloading; the
antivirus appeared to be holding a lock on the fresh file. Re-running the
same command succeeded.

**A restart says the port is already in use.** The API server from an
earlier session is still running: `Get-Process python | Stop-Process
-Force`, then start it again.
