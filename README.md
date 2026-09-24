# Job Intelligence Bot

I was looking for my first developer job and got tired of job boards.
Postings reach them days late, the same job shows up three times, and the
"junior" filter means nothing when a posting titled *Full Stack
Developer* asks for "2-3 years, mandatory".

So I built this instead. It watches the career pages of the 242 companies
on my own list, notices a new posting within minutes, reads it against my
CV, and shows me the few that are actually worth an application.

It runs unattended: a FastAPI service, a Celery worker and scheduler,
Postgres with `pgvector`, and a React dashboard, all in Docker. The
embeddings and the LLM run on my own machine, so there is no API bill and
my CV never leaves it.

![Architecture](docs/architecture.svg)

<details>
<summary>The same flow in one line</summary>

```
Google Sheet -> company sync -> source resolver -> 11 source adapters
                                                          |
                            incremental crawl <-----------+
                                   |
            normalize -> dedupe -> embed -> store (Postgres + pgvector)
                                   |
                          hybrid matching engine
                                   |
                   REST API -> React dashboard -> (WhatsApp, planned)
```

</details>

> **Status:** phases 1-9 are done and the bot runs every day. WhatsApp
> alerts (phase 7) and moving it onto a small VPS (phase 10) are what is
> left. See [Roadmap](#roadmap).

---

## Reading 208 different career pages

Every company's careers page is its own problem, so there is no single
scraper. A resolver classifies each URL once, and the cheapest adapter
that can actually read it gets the job. The counts are from my real list:

| Tier | How the jobs are read | Sources | Cost |
|---|---|---|---|
| ATS API | Greenhouse, Lever, Ashby, Comeet, Workable, SmartRecruiters, Workday, Taleo | 50 | 1-6 s |
| Site feed | the JSON the company's own page fetches (Elbit, IAI, Amazon) | 3 | ~8 s |
| WordPress REST | jobs stored as a custom post type | 13 | ~10 s |
| JSON-LD | `schema.org/JobPosting` markup, there for Google for Jobs | 1 | ~3 s |
| Plain HTML | the page's own structure, no hardcoded selectors | 138 | 2-5 s |
| Headless browser | Playwright, only after everything above found nothing | escalated | ~20 s |

Two parts of this I am happy with.

**About 45 of the "custom" pages turned out to be a known ATS in
disguise.** The resolver reads the page and recognises the real board
behind it: a `COMEET.init({...})` call, a Greenhouse embed script, a
`myworkdayjobs.com` link, or an API that the page's own JavaScript calls.
It then crawls that board with an adapter I already have. A guessed board
token is never stored until the ATS's public API answers for it, because
one company still had a dead Greenhouse embed sitting next to the Ashby
board it had migrated to, and only the live one should be kept.

**Nothing is configured as "needs a browser".** A plain page that returns
no job links in three successful crawls in a row, or answers 403, is
handed to the Playwright adapter by itself and polled hourly from then
on. The slow tier is entered on evidence, not on a hunch.

A rendered page is also watched for what it *fetches*: when the HTML has
no links to follow, the JSON its own scripts pull becomes the job list.
Picking the right captured document is harder than it sounds, because
field names lie. One site's *product cards* carry a `description` while
its real postings keep theirs under `AboutTheRole`, so my first rule
cheerfully imported "Bank Hapoalim" as a job title. What works is
measuring whether the titles read like job titles. The rule I settled on,
and the two wrong ones before it, are written down in
[`docs/job_sources.md`](docs/job_sources.md).

---

## The crawl used to take two and a half hours

The first version downloaded every job page on every crawl. Profiling
said plain career sites were 85% of all crawl time, which is why they
could only be polled every 30 minutes.

Now a job page is downloaded only when the link is new, when the listing
reports a newer timestamp, or when the stored copy is older than
`JOB_DETAILS_REFRESH_HOURS`. Everything else is confirmed present from
the listing alone.

| Same sites, before and after | Average | Median | Worst |
|---|---|---|---|
| Downloading every page | 5.5 s | 3.2 s | 350 s |
| Listing diff | 2.1 s | 1.7 s | 6 s |

That bought the polling interval: plain sites went from 30 minutes to 10,
and a new posting now shows up in about 12 minutes instead of 35.

---

## Deciding what is worth applying to

The score is deliberately not one cosine similarity:

```
final = 100 x role_gate x quality
role_gate = 0.2 + 0.8 x role_fit
quality   = 0.45*seniority + 0.30*skills + 0.12*cv_similarity
          + 0.08*intent + 0.03*location + 0.02*recency
```

The role gate is the part that matters. A Check Point posting titled
*Agentic AI Solutions Engineer* scores 0.98 on raw CV similarity, because
the text is full of AI, LLM, cloud and API. It is also a customer-facing
pre-sales role that wants firewall industry experience. The gate reads
the title and the keywords, multiplies everything else by how much the
job really is the role I am looking for, and leaves it at 30%. Plain
semantic similarity would have put it near the top of my list.

Other decisions the real data forced on me:

- The local embedding model's raw similarity sits between 0.15 and 0.55
  even for a perfect match, and it barely separates junior from senior.
  So it is calibrated onto that measured range and weighted lightly, and
  seniority plus skill overlap carry the score. That is why the weights
  in `.env` are nowhere near the textbook defaults.
- Seniority is read in Hebrew and in English, from the requirements
  section rather than the whole description. Ranges (`3-5 שנות ניסיון`)
  resolve to their lower bound, Hebrew numerals and spelled-out numbers
  are handled, and "no experience required" phrasings are recognised
  together with their negations (`לא יתקבלו מועמדים ללא ניסיון`).
- Stated years beat the title. A posting that reads junior but asks for
  "2-3 years, mandatory" is tagged *requires experience* and hidden. The
  bug that made that obvious is now a test.
- Student positions get their own tag. I finished my degree, so
  "Software Development Student" is not a job I can take however well it
  matches. Those postings are labelled *משרת סטודנט* and kept out of the
  default view instead of filling the top of it.
- Every match carries **reasons** and **concerns** in plain language, so
  a score I disagree with can be argued with instead of guessed at.

---

## The scheduler

Celery Beat dispatches due sources every 5 minutes. Three things I got
wrong first and now document in the code:

- The dispatcher runs on its own queue with its own worker. On the shared
  queue it waited behind hundreds of queued crawls after any pause, and
  the "next refresh" countdown on the dashboard was an hour off.
- A source is leased, meaning its next check is pushed forward, *before*
  its task is queued. Otherwise a slow cycle enqueues the same source
  twice and two workers collide on the uniqueness constraint.
- Sources with a real API are dispatched before slow ones, so a new job
  at a company with a proper ATS never waits behind a 250-page career
  site.

`source_published_at` (what the ATS says) and `first_seen_at` (when my
bot noticed) are kept apart and never substituted for each other in the
UI. A job that disappears from a listing is not deleted and not closed
right away. It has to be missing from several consecutive *successful*
crawls first, because one crawl can drop a job through a pagination
hiccup. Closed jobs keep their history.

---

## What is in it right now

Numbers from the live database, not from a sample:

| | |
|---|---|
| Companies on the list | 242, of which 208 have a career page that can be read (LinkedIn URLs are never scraped) |
| Companies currently producing jobs | 134 |
| Active postings stored | ~5,500, about 2,400 confirmed to be in Israel |
| Crawl runs so far | 22,000+ |
| Tests | 397 |

Scoring behaviour against my own CV, measured over those postings:
developer titles that read as junior score a median of 76, senior
developer titles 14, and roles in other fields (sales, finance, HR) 10.

---

## Running it

You need Docker Desktop, Python 3.12 with [uv](https://docs.astral.sh/uv/),
Node 20 for the dashboard, and [Ollama](https://ollama.com) if you want
the LLM to do the CV extraction. There is a deterministic fallback for
when it is not there.

```bash
git clone <this repo> && cd Bot_career
cp .env.example .env            # the defaults are right for local development

docker compose up -d            # Postgres + Redis + Celery worker + scheduler
uv sync                         # Python dependencies
uv run alembic upgrade head     # schema

cd frontend && npm install && npm run build && cd ..
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open **http://127.0.0.1:8000/app/**, upload a CV on the profile
page, and the crawler fills the dashboard as it goes.

The Celery worker and the scheduler run in Linux containers on purpose:
Celery's prefork pool does not work on native Windows, and the browser
fallback needs a Linux Chromium. Only the API process runs on the host
while developing.

### Commands I actually use

```bash
uv run python -m app.cli sync-sheet          # re-import the companies
uv run python -m app.cli crawl-now           # crawl everything due, right now
uv run python -m app.cli score-all           # rescore every active job
uv run python -m app.cli reassess-seniority  # re-read experience requirements
uv run pytest                                # 397 tests
uv run ruff check . && uv run mypy app tests # lint and types
```

[`docs/operations.md`](docs/operations.md) is the full operator's manual:
starting it up, what the scheduler does, the API surface, migrations, and
the environment problems worth knowing about before they cost an
afternoon. One of them silently stops every single crawl on a machine
with a TLS-inspecting antivirus, and it has its own page in
[`certs/README.md`](certs/README.md).

---

## How it is built

- **397 tests**, unit and integration, the integration ones against a
  real Postgres with `pgvector`. Every bug described above has a test
  named after it.
- **`mypy --strict`** over the app and the tests, `ruff` for lint and
  formatting, both clean.
- **Alembic migrations** for every schema change, no `create_all` outside
  the tests.
- **Structured JSON logs** that carry identifiers and not payloads, so
  they stay greppable and hold no secrets.
- Comments explain decisions, not syntax. Where a constant looks
  arbitrary, the comment says what I measured to pick it.
- Third-party API formats are checked against live responses and written
  down in `docs/job_sources.md`, including the undocumented Workday and
  Taleo request shapes.

---

## Security

The threat model here is specific: one user, bound to localhost, with a
crawler that reads untrusted third-party HTML all day long. I went over
input handling, injection, SSRF, XSS, secret handling, the container
setup and the dependencies.

| Control | How |
|---|---|
| SQL injection | SQLAlchemy Core/ORM everywhere, no SQL built from strings. The one raw statement is a literal `SELECT 1` health probe. |
| Stored XSS | Crawled URLs become links in the dashboard. Anything that is not `http(s)` (`javascript:`, `data:`) is dropped when the posting is ingested, and filtered again before it is rendered as an `href`. |
| SSRF | The crawler refuses a URL whose host is a loopback, private or link-local address, including `169.254.169.254`, the cloud metadata endpoint. |
| Browser isolation | The Playwright fallback renders hostile pages with Chromium's sandbox on, as a non-root user, with images, media and fonts blocked. |
| Uploads | A CV is size-capped while streaming (10 MB), the filename is reduced to its last path component and length-checked, and only PDF and DOCX are parsed. |
| Secrets | `.env`, the service-account key and the certificates are git-ignored and have never been committed. Logs record error *types*, not payloads. |
| Response headers | `X-Content-Type-Options`, `X-Frame-Options` and `Referrer-Policy` on every response. No CORS middleware, because the dashboard is same-origin and no other site should be able to call the API. |
| Exposure | Postgres and Redis publish their ports on `127.0.0.1` only, so a weak local password is not reachable from the network. |
| Dependencies | `pip-audit` reports no known vulnerabilities in the app's dependencies. |
| Authentication | Off by default, which is the right answer for a service on `127.0.0.1`. Setting `DASHBOARD_PASSWORD` turns on HTTP Basic for everything except `/health`, and that has to be on before this is exposed anywhere, behind HTTPS or Tailscale. |

LinkedIn is never scraped. Its User Agreement forbids automated access
and `hiQ Labs v. LinkedIn` was decided on contract grounds, so a row on
my sheet pointing at LinkedIn is marked unsupported with a reason instead
of being crawled quietly. The crawling itself is polite: bounded
timeouts, retries with exponential backoff and jitter on transient
failures only, and no retry storms against a site answering 4xx.

---

## What it does not do

These are choices, not things I forgot:

- **It only runs while the machine is awake.** A laptop asleep overnight
  scans nothing. That is what phase 10, a small VPS, is for.
- **Some sites cannot be read at all.** Enterprise bot management
  (Akamai, Imperva) turns away the headless browser too. Those sources
  fail visibly on the status page instead of quietly returning nothing.
- **About a quarter of postings say nothing readable about experience.**
  They get a grey "unspecified" tag rather than a guess.
- **The local embedding model is weaker than a hosted one.** That is the
  price of keeping my CV on my own machine, and the weights are
  calibrated around it instead of pretending otherwise.

---

## Roadmap

| Phase | Status |
|---|---|
| 1-6, schema, CV pipeline, company sync, ingestion, matching, scheduling | done |
| 8, the remaining adapters, embedded-board detection, browser fallback | done |
| 9, React dashboard, applications pipeline, live status | done |
| Security review | done |
| 7, WhatsApp alerts for strong new matches | next |
| 10, VPS deployment, CI/CD, backups, rate limiting | planned |

---

## Layout

```
app/
  api/routes/      FastAPI endpoints
  core/            config, logging, security, timezone
  db/              engine and session
  models/          SQLAlchemy models
  schemas/         Pydantic request/response types
  services/
    candidate/     CV parsing and structured extraction
    jobs/          ingestion, normalization, location
    matching/      scoring, seniority, skills, queries
    sheets/        company sync
  ingestion/
    resolver.py    URL -> source type classification
    adapters/      one module per job source
  tasks/           Celery app, scheduler, crawl tasks
frontend/          React + TypeScript dashboard (Vite)
tests/             unit and integration tests
docs/              architecture, job sources, matching, dashboard
alembic/           migrations
```

More detail: [`docs/architecture.md`](docs/architecture.md) for the
design, [`docs/job_sources.md`](docs/job_sources.md) for the verified ATS
formats and what each real site needed,
[`docs/matching.md`](docs/matching.md) for the scoring model,
[`docs/dashboard.md`](docs/dashboard.md) for the UI, and
[`docs/operations.md`](docs/operations.md) for running it.
