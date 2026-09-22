# Dashboard (Phase 9)

A React single-page app in `frontend/` (Vite + TypeScript, React Router,
nothing else), served by the FastAPI process under `/app`. The JSON API
keeps its root paths (`/matches/top`, `/jobs/{id}`, ...) - `/app/jobs/5`
is a page, `/jobs/5` is JSON - which is why the SPA lives under a prefix.

## Running it

```bash
cd frontend
npm install          # once
npm run build        # -> frontend/dist, picked up by app/main.py on startup
```

Then open `http://127.0.0.1:8000/app/` (or just `/`, which redirects).
FastAPI serves `dist/` itself: hashed assets from `/app/assets`, every
other `/app/...` path gets `index.html` so deep links and refreshes work.
If `frontend/dist` is missing the API still runs and a warning is logged.

For UI work, `npm run dev` runs Vite on `http://127.0.0.1:5173/app/` with
hot reload and proxies every API prefix to uvicorn on `:8000`.

## Pages

| Route | What it shows |
|---|---|
| `/app/` | Matches for the active CV, **newest posting first** by default. A live line under the title says how many jobs the crawler found today and how many of them the default view shows, plus when the last crawl ran; it refreshes every 30 s and reloads the list the moment the found-today count changes. (or "התאמה קודם" to sort by score): a percentage ring, a colour-coded company tag (the hue is derived from the company name, so a company always looks the same), title, location, source, when it was posted/found, a **seniority tag** (מתאים לג'וניור / ותק לא צוין / דורש ניסיון - what the posting itself says about experience, see docs/matching.md; hover for the years), the reasons (green) and concerns (amber) the scorer produced, apply link, and one-tap feedback. Filters: search, **seniority** (only fits a junior / hide those requiring experience - the default / all), minimum match, found or published within (today / **3 days, the default** / all time - counted from local midnight N days back, so a job labelled "found 3 days ago" is inside "3 days"; a display filter, nothing is deleted), **region in Israel**, target role, Israel-only (default on), hide dismissed (default on). "Load more" pages through. |
| `/app/jobs/:id` | One job: the component scores as percentage bars with a plain-language label each, reasons/concerns, skills, responsibilities/requirements/description, apply + source links, feedback. |
| `/app/applications` | Every job marked "הגשתי", as a pipeline: a status per application (applied → screening → interview → assignment → offer / rejected / withdrawn), free-text notes saved on blur, counts per stage, "in process / finished / all" views. Nothing is ever deleted. |
| `/app/status` | Is the bot alive: a live **countdown to the next scheduled refresh** (from the scheduler's own heartbeat in Redis, turns amber if a tick is 2+ minutes late), how many sources are waiting in the crawl queue, last crawl, active jobs (classified Israel / unknown location / total), found in the last 24h, sources and jobs by type, sources failing repeatedly, the last 25 crawl runs. Refreshes every 30s. |
| `/app/profile` | Upload a CV (drag & drop, PDF/DOCX), see the active version, **what the scan does step by step and everything it extracted** (languages, frameworks, databases, education, projects, ...) plus the full raw text read from the file - so a missed skill is visible; switch back to an older version; target roles with enable/disable and a form to add one. Any change here rescores every job server-side before the request returns. |

**Search is hybrid.** Whatever is typed is matched as text against
title/company *and* embedded with the same local model the jobs were
embedded with; exact text hits rank first, then jobs whose embedding is
within cosine distance 0.75 of the query, by closeness. So "backend
developer" finds backend jobs, and "משהו עם AI וסטארטאפ קטן" finds jobs
that read like that.

Feedback semantics: "מעניין"/"הגשתי" mark a job; "לא רלוונטי"/"בכיר מדי"
(and the other dismissing actions) hide it from the default list - the
card animates out - and `hide_dismissed=false` shows it again with its
last feedback. Nothing is deleted; `JobFeedback` keeps the history.

## Design principles (the "Apple designer" brief)

- **System type, one accent.** The platform font stack, a 34/22/19/17/15/13
  size scale, `#0071e3` as the only accent. Meaning is carried by a
  handful of semantic colors: score tones (green ≥ 75, blue ≥ 65, amber ≥
  50, gray), green reasons vs. amber concerns, red only for errors/dismiss.
- **Surfaces, not lines.** White cards with 18px radius and a soft
  two-layer shadow on a `#f5f5f7` ground; no borders except the 1px
  hairline under the translucent (blurred) nav and filter bar.
- **Generous whitespace, tabular numbers, no decoration.** Nothing is on
  the page that doesn't carry information.
- **Automatic dark mode** via `prefers-color-scheme` - every color is a
  token in `src/styles/app.css`, redefined once for dark.
- **RTL first.** `<html lang="he" dir="rtl">`, logical CSS properties
  (`margin-inline-start`, `inset-inline-start`); job titles, company
  names and descriptions - mostly English - get `unicode-bidi: plaintext`
  (`.bidi`) so they read correctly inside the Hebrew UI.
- **Accessible by default.** Real buttons and links, `aria-pressed` on
  feedback, `role="radiogroup"` segmented controls, `role="meter"` score
  bars, a skip link, visible focus rings, AA contrast in both themes,
  `prefers-reduced-motion` honored, skeletons announce `aria-busy`.

## Code structure

```
frontend/src
  api/types.ts       the JSON shapes, mirrored by hand from app/schemas
  api/client.ts      typed fetch wrappers (one per endpoint), ApiError
  lib/format.ts      relative times (he), labels, score tones
  hooks/useAsync.ts  load-on-mount + reload, ignores stale responses
  components/        ui.tsx (ScoreBadge, Pill, Segmented, Toggle, ...),
                     MatchCard, FeedbackButtons
  pages/             Matches, Job, Status, Profile
  styles/app.css     the whole design system - tokens + components
```

No state library, no CSS framework, no component kit: the app is four
pages over a small API, and every abstraction here earns its place.
