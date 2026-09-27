# Job Intelligence Bot

Job boards are late, repetitive, and their "junior" filter is useless
when a posting titled *Full Stack Developer* asks for "2-3 years,
mandatory".

So this watches the career pages of 242 Israeli tech companies directly,
finds new postings within minutes, and scores each one against my CV.

![Architecture](docs/architecture.svg)

> Running daily on my laptop. Next: WhatsApp alerts, then a small VPS.

---

## What it does

- Reads the company list from a Google Sheet I maintain.
- Works out how to read each career page by itself: 13 adapters, from a
  company's own ATS API down to a headless browser when nothing else works.
- Downloads a job page only when it's new or changed.
- Scores every posting against my CV with rules + embeddings, and asks
  [Jev](docs/jev.md) for a second opinion.
- Shows the result in a Hebrew dashboard with one-tap feedback.

Stack: FastAPI · Celery · PostgreSQL + pgvector · React · Docker.
Embeddings and CV parsing run locally, so there's no API bill.

---

## The three hard parts

### 1. Every career page is different

There's no universal scraper. A resolver classifies each URL once and the
cheapest adapter that works takes it:

| How the jobs are read | Sources |
|---|---|
| ATS API (Comeet, Workday, Greenhouse, Ashby, Lever, Workable, Taleo) | 52 |
| Plain HTML, read from the page's own structure | 71 |
| Headless browser, entered automatically after 3 empty crawls or a 403 | 68 |
| WordPress REST · site feeds · JSON-LD | 17 |

Two things I'm happy with: about 45 "custom" pages turned out to be a
known ATS in disguise, and the resolver finds them (27 Comeet boards
today, only ~10 of which say so in their URL). And nothing is configured
as "needs a browser" — sources escalate there on evidence, which is how a
third of them ended up needing one.

### 2. The score is not one cosine similarity

```
final = 100 × role_gate × quality
role_gate = 0.2 + 0.8 × role_fit
quality   = 0.45·seniority + 0.30·skills + 0.12·cv_similarity
          + 0.08·intent + 0.03·location + 0.02·recency
```

The gate is the point. A Check Point posting titled *Agentic AI Solutions
Engineer* scores 0.98 on raw CV similarity — the text is full of AI, LLM
and cloud — but it's a pre-sales role wanting firewall experience. The
gate reads the title, multiplies everything else by how much the job
*is* the role, and lands it at 30%.

Seniority is read in Hebrew and English, from the requirements section
only: `3-5 שנות ניסיון` resolves to 3, and stated years beat the title.
Student positions get their own tag: they require being enrolled, which
is not the same thing as junior.

Every match carries its reasons and concerns in plain language.
[`docs/matching.md`](docs/matching.md) has the measurements behind each weight.

### 3. Jev: a new kind of model, measured before it was trusted

[Jev](https://typesafe.ai) (September 2026) answers typed questions with
calibrated probabilities instead of generating text — exactly the shape
of the decisions this pipeline makes with hand-written rules.

It reads every posting (what kind of work, what level, students-only) and
judges the CV against the ones that pass the role gate: *would a recruiter
shortlist this candidate?* → 81%.

I measured it on 80 real postings first, half in Hebrew, because the
vendor flags non-English as weaker. It matched my rules and caught Hebrew
nuance they missed (`ראש מחלקה` is a lead; `מנהל חשבונות` is a
bookkeeper, not a manager). Only then did it get a third of the quality
score, behind `JEV_WEIGHT`. Whether it beats the rules long-term is still
open — I'm collecting feedback to find out. [`docs/jev.md`](docs/jev.md).

---

## Numbers

From the live database:

| | |
|---|---|
| Companies watched | 242, of which 208 can be read (LinkedIn postings not included) |
| Postings stored | 5,513 |
| Crawl runs | 36,000+ |
| Tests | 413 |

Median score by what a posting turns out to be:

| | Count | Median |
|---|---|---|
| Junior developer | 122 | 40 |
| Student position | 104 | 23 |
| Senior developer | 274 | 14 |
| A different field entirely | 190 | 12 |

The ordering is what matters. The absolute numbers came down when Jev
took its share; `JEV_WEIGHT=0` puts them back.

---

## How it's laid out

```
app/
  api/routes/     HTTP endpoints, one file per subject. Thin on purpose:
                  they call a service and return, so the CLI and the API
                  run the same code.
  core/           config (the only place that reads env vars), security,
                  logging, timezone.
  models/         the database tables (SQLAlchemy).
  schemas/        the JSON shapes the API speaks (Pydantic). Separate from
                  models so an internal change can't break the dashboard.
  services/       all the logic:
    sheets/         Google Sheet -> companies
    jobs/           crawling, normalizing, retention
    matching/       the score, component by component
    candidate/      CV parsing, versions, local LLM extraction
    embeddings/     the provider interface, local and OpenAI
    jev/            TypeSafe's model: reading, fit, agreement
  ingestion/
    resolver.py     a URL -> which adapter can read it
    registry.py     source type -> adapter
    adapters/       one per source: greenhouse, comeet, workday, wordpress,
                    site_feed, generic_html, browser, ...
  tasks/          Celery: the 5-minute dispatcher and the nightly prune.
                  No logic of its own, only when things run.
frontend/src/     React: pages/, components/, api/, styles/
tests/            unit (fast, pure) + integration (real Postgres)
alembic/versions/ every schema change, in order
docs/             the long version of everything above
```

Dependencies point one way: `api` → `services` → `models`. Nothing goes
back up, which is why every pipeline step is runnable from the CLI and
testable without HTTP.

---

## Running it

Needs Docker, Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20,
and optionally [Ollama](https://ollama.com) for CV parsing.

```bash
cp .env.example .env
docker compose up -d            # Postgres, Redis, worker, scheduler
uv sync && uv run alembic upgrade head
cd frontend && npm install && npm run build && cd ..
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/app/` and upload a CV.
[`docs/operations.md`](docs/operations.md) is the full operator's manual.

---

## How it's built

- **413 tests**, integration ones against a real Postgres with pgvector.
  Every bug described above has a test named after it.
- `mypy --strict` and `ruff` over app and tests, both clean.
- Alembic migrations for every schema change.
- Structured JSON logs carrying identifiers, never payloads.
- Comments explain decisions. Where a constant looks arbitrary, the
  comment says what I measured to pick it.

**Security.** The crawler reads untrusted HTML all day, so: SSRF guard on
every fetch, non-`http(s)` link schemes dropped twice, Chromium sandboxed
and non-root, uploads size-capped and type-checked, cross-site writes
refused, `pip-audit` clean. Auth is off while it's bound to localhost and
required before it goes anywhere else.

## Limits

It only runs while the laptop is awake (that's what the VPS is for).
Some companies don't serve automated clients at all; those sources fail
visibly on the status page instead of quietly returning nothing, and are
left alone. A quarter of postings say nothing readable about experience,
and get a grey tag rather than a guess.

---

## Docs

[architecture](docs/architecture.md) · [job sources](docs/job_sources.md) ·
[matching](docs/matching.md) · [Jev](docs/jev.md) ·
[dashboard](docs/dashboard.md) · [operations](docs/operations.md)
