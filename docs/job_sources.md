# Job sources

Adapters are implemented starting Phase 4. This document records the
verified facts gathered during research so they don't need to be
re-derived later, and the real distribution found in the current company
spreadsheet (240 rows) so adapter priority is grounded in reality rather
than guesswork.

## What's actually in the spreadsheet today

| source_type | Count (approx.) | Examples |
|---|---|---|
| `comeet` | ~10 | Cymotive, Tango, Buyme, Better, Israel Discount Bank, Classiq, Pango, LiveU, TriEye, Natural Intelligence |
| `greenhouse` | 1 | Torq |
| `workday` | 3 | Intel, Flex, Medtronic |
| Oracle Taleo | 1 | Radware |
| `dueto.io` (shared Israeli ATS, no dedicated adapter yet — see below) | 3 | Yad2, Capow, Dig |
| `adamtotal.co.il` (shared Israeli ATS, no dedicated adapter yet) | 2 | CBC Israel, Harel |
| `topmatch.co.il` (shared Israeli ATS, no dedicated adapter yet) | 1 | Altshuler Shaham |
| LinkedIn (`unsupported`, never scraped) | ~30 | mostly recruiter profile links (`linkedin.com/in/...`), a few `linkedin.com/company/.../jobs/` |
| Broken/missing URL (`unsupported`) | a few | Meta and NICE have no URL; NVIDIA's row points at Earnix's careers page |
| Everything else | ~190 | custom company career pages — routed through JSON-LD detection first, then generic HTML, then Playwright as a last resort |

No Lever or Ashby examples exist in the sheet today, but both adapters are
still built (spec priority order + future-proofing for companies added
later).

## Why LinkedIn is never scraped

LinkedIn's User Agreement (§8.2) explicitly prohibits automated
scraping/crawling. `hiQ Labs v. LinkedIn` established that scraping public
data isn't federal computer-fraud, but LinkedIn still won on contract
grounds — hiQ was fined $500k and permanently banned in 2022 for violating
the User Agreement. A `CareerSource` whose URL resolves to LinkedIn is
marked `source_type=unsupported` with `unsupported_reason` set, and is
simply not crawled.

## Verified adapter API formats

### Comeet
- `GET https://api.comeet.co/positions` or the older
  `GET https://www.comeet.co/careers-api/2.0/company/{company_uid}/positions?token={token}&details=false`
- Set `details=true` to include description/requirements text (excluded by default).
- Response: JSON with `uid`, `name`, `status`, `department`, `employment_type`, `location`, URLs.
- Use a 60s timeout — large boards can be slow to return.
- No authentication required for public boards.

### Greenhouse
- `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs` (list), `.../jobs/{job_id}` (single).
- `content=true` includes the HTML job description; `department_id`/`office_id` filter.
- No auth for reads (POSTing an application requires HTTP Basic with a Job Board API key — not needed here).
- Response: `{jobs: [...], meta: {total}}`; each job has `id`, `internal_job_id`, `title`, `location.name`, `updated_at`, `absolute_url`.
- No published rate limit, but poll on a schedule (not on every page view) to avoid being blocked.

### Lever
- `GET https://api.lever.co/v0/postings/{client}?mode=json` (JSON) or `?mode=xml`.
- Public, no authentication.
- Filter params: `team`, `department`, `location`, `commitment`, `level`, `skip`, `limit`.

### Ashby
- `GET https://api.ashbyhq.com/posting-api/job-board/{client}?includeCompensation=true`
- Public, documented, no authentication. No filtering/search support — always returns the full board.

### JSON-LD (`schema.org/JobPosting`)
- Look for `<script type="application/ld+json">` blocks containing a `JobPosting` object before falling back to generic HTML scraping.
- Fields to read: `title`, `description`, `datePosted`, `validThrough`, `employmentType`, `hiringOrganization`, `jobLocation`, `applicantLocationRequirements`, `skills`.
- Many modern company career sites include this for Google for Jobs SEO even when they have no public ATS API — this is expected to cover a meaningful chunk of the ~190 "custom" companies for free, before resorting to generic HTML/Playwright.

### Workday, Taleo, and the Israeli shared platforms (dueto.io, adamtotal.co.il, topmatch.co.il)
Not yet verified against live documentation — will be confirmed against
real responses when their adapters are built (Phase 8), not invented from
memory, per the project's working rules.
