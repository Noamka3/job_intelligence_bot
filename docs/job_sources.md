# Job sources

Phase 4 implemented adapters for Greenhouse, Lever, Ashby, Comeet, and a
basic JSON-LD parser; Phase 8 added Workday, Workable, SmartRecruiters,
Taleo and a generic HTML adapter, plus detection of boards *embedded* in
company pages (`app/ingestion/adapters/`, `app/ingestion/resolver.py`).
This document records the verified facts gathered during research so they
don't need to be re-derived later. Every adapter below was exercised
against at least one real company's live board during development; the
Phase 8 ones against the sheet's own companies (see each section).
The company distribution below is the real breakdown from the current
company spreadsheet (240 rows), which is why adapter priority here differs
slightly from the spec's abstract ranking (Comeet, with ~10 real
companies, was built alongside Greenhouse/Lever/Ashby rather than after
JSON-LD as originally ranked).

## What's actually in the spreadsheet today

| source_type | Count (approx.) | Examples |
|---|---|---|
| `comeet` | ~10 | Cymotive, Tango, Buyme, Better, Israel Discount Bank, Classiq, Pango, LiveU, TriEye, Natural Intelligence |
| `greenhouse` | 1 | Torq |
| `workday` | 3 by hostname + ~5 embedded on company pages | Intel, Flex, Medtronic; Unity, Samsung, Mastercard, Ribbon, Leidos |
| Oracle Taleo | 1 | Radware |
| `comeet` embedded on the company's own page (Phase 8) | ~30 | eToro, Checkmarx, Atera, Cyera, Buildots, Kaltura, Gett, hibob, ... |
| `greenhouse`/`ashby`/`workable` embedded on the company's own page (Phase 8) | ~7 | SimilarWeb, Nexxen, JFrog, AppsFlyer, Crusoe, Humanz, Anzu |
| `dueto.io` / `topmatch.co.il` | 4 | dead links (404) - Yad2, Capow, Dig, Altshuler Shaham |
| `adamtotal.co.il` | 2 | CBC Israel, Harel - server-rendered, handled by generic HTML |
| LinkedIn (`unsupported`, never scraped) | ~30 | mostly recruiter profile links (`linkedin.com/in/...`), a few `linkedin.com/company/.../jobs/` |
| Broken/missing URL (`unsupported`) | a few | Meta and NICE have no URL; NVIDIA's row points at Earnix's careers page |
| Everything else (`generic_html`) | ~130 | custom company career pages: ~60 render job links server-side (generic HTML adapter), ~25 are JS-rendered and ~8 sit behind a WAF (need the browser fallback, not built) |

No Lever or Ashby examples exist in the sheet today, but both adapters are
still built (spec priority order + future-proofing for companies added
later).

## CareerSource dedup by identifier, not exact URL

Found live during Phase 4 verification: the real sheet's Torq and Tango
rows carry query-string variants (`?offices%5B%5D=...`,
`?location=Tel%20Aviv`) of URLs that were already present from earlier
manual testing. Matching `CareerSource` by exact `source_url` treated
these as two different sources, and each independently crawled and stored
the same real jobs - doubling them (Torq showed 60 stored jobs for a
30-job board). Fixed in `company_sync._ensure_career_source`: when the
resolver returns an `external_identifier` (true for every known ATS -
Comeet's `company_uid`, Greenhouse's board token, ...), dedup matches on
`(source_type, external_identifier)` instead of the URL string. Only
falls back to exact-URL matching for `generic_html`/`jsonld`/`unsupported`
sources, where there's no such identifier to key on.

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

**Verified live** against real companies from the sheet (Cymotive - 0 open
roles right now; Tango - 19 real open roles, fetched and parsed
successfully) during Phase 4 development. One important fact a generic
search summary did not surface:

- The positions API (`GET https://www.comeet.com/careers-api/2.0/company/{company_uid}/positions?token={token}&details=false`)
  needs a `token` that is **not present anywhere in the public job board
  URL**. It's embedded in a `var COMPANY_DATA = {...};` JS blob inside the
  HTML of the public careers page (`https://www.comeet.com/jobs/{slug}/{company_uid}`).
  `ComeetAdapter` fetches that page once per source and extracts
  `company_uid` + `token` from `COMPANY_DATA` via regex + `json.loads` -
  it does not trust the URL's slug segment for this (see
  `app/ingestion/adapters/comeet.py`).
- `company_uid` **does** match the URL's last path segment (e.g. `F1.008`
  in `/jobs/cymotive/F1.008`) - confirmed by comparing the URL against the
  `company_uid` found in `COMPANY_DATA` for two different companies.
- List call (`details=false`): each item has `uid`, `name`, `department`,
  `location` (`name`, `country`, `city`, `is_remote`), `employment_type`
  (free text like `"Full-time"`), `experience_level` (free text like
  `"Senior"` - a real seniority signal Comeet provides directly, not yet
  used by this project; Phase 5's seniority detection is title/requirements
  based per spec §9 rather than trusting each ATS's own inconsistent
  labels, but this is worth revisiting), `time_updated` (ISO-8601),
  `url_active_page`.
- Detail call (`details=true`) adds a `details` array of
  `{"name": "Description"|"Responsibilities"|"Requirements"|..., "value": "<html>"}`
  objects - Comeet already segments the job text into the sections spec
  §11 asks for, unlike most other ATSes which return one HTML blob.
- No authentication beyond the token; no published rate limit encountered.

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

### Workday (Phase 8) - verified live against Intel, Flex, Medtronic
- `POST https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` with
  `{"appliedFacets": {...}, "limit": 20, "offset": 0, "searchText": ""}` ->
  `{total, jobPostings: [{title, externalPath, locationsText, postedOn, bulletFields}], facets}`.
  `limit` caps at 20. No auth. Not a documented API - it is what the site's
  own JS calls; unchanged for years.
- `GET .../wday/cxs/{tenant}/{site}{externalPath}` -> `jobPostingInfo`:
  `title`, `jobDescription` (HTML), `location`, `additionalLocations`,
  `startDate` (the only real date - `postedOn` is "Posted Yesterday"),
  `timeType`, `remoteType`, `externalUrl`, `jobReqId`, `country.descriptor`.
- **The country facet is tenant-specific**: Medtronic exposes
  `locationCountry`, Flex `Location_Country` (both with Israel =
  `084562884af243748dad7c84c304d89a`, a Workday-wide reference id), Intel
  has no country facet at all - only city-level `locations` values
  ("Israel, Haifa"), nested under a `locationMainGroup` facet. Sending a
  facet parameter a tenant doesn't have is a 400. The adapter therefore
  discovers the right parameter/ids from the first response's `facets`
  (country-level preferred, city-level otherwise, `searchText` as the last
  fallback) instead of assuming one. Result: Intel 594 -> 22 jobs, Flex
  1610 -> 10, Medtronic 1095 -> 25, fetched instead of ~3,300.
- Site URLs come in several shapes, all seen in the sheet or on company
  pages: `/External/page/<id>` (Intel), `/he-IL/MedtronicCareers` (locale
  prefix), `/Unity/job/...` (deep link), `//sec.wd3.myworkdayjobs.com/...`
  (protocol-relative, Samsung), `/External/login` (Leidos). The resolver
  stores `tenant/site` as the identifier and the site root as the URL.

### Workable (Phase 8) - verified live (Humanz) and against workable.readme.io
- `GET https://apply.workable.com/api/v1/widget/accounts/{subdomain}` ->
  `{name, description, jobs: [{shortcode, title, city, country, state,
  department, url, application_url, published_on, created_at,
  employment_type, telecommuting, locations}]}`. No auth, no pagination
  (one payload). **Emits one row per location** for multi-location jobs
  (71 rows for 57 jobs on a real account) - dedupe by `shortcode`.
- `GET https://apply.workable.com/api/v2/accounts/{subdomain}/jobs/{shortcode}` ->
  `description`, `requirements`, `benefits` (HTML), `location`, `department`
  (list), `remote`, `workplace` (`on_site`/`hybrid`/`remote`), `published`.
  Undocumented but what apply.workable.com itself uses.
- `POST .../api/v3/accounts/{subdomain}/jobs` (paginated via `nextPage` ->
  `token`) also works; not needed at this volume.

### SmartRecruiters (Phase 8) - verified against the official OpenAPI spec + a live call
- `GET https://api.smartrecruiters.com/v1/companies/{companyIdentifier}/postings?limit=100&offset=0`
  -> `{offset, limit, totalFound, content: [...]}`; `limit` caps at 100,
  paginate by offset. `GET .../postings/{id}` adds
  `jobAd.sections.{companyDescription,jobDescription,qualifications,additionalInformation}.text`
  (HTML), `applyUrl`, `postingUrl`. `security: []` in the spec - no auth.
  Only `releasedDate`, no updated-at. No company in the sheet uses it
  today; built because the spec lists it and it verified cheaply.

### Oracle Taleo careersection (Phase 8) - verified live against radware.taleo.net
- `POST https://{host}/careersection/rest/jobboard/searchjobs?portal=101430233&lang=en`
  needs `Content-Type: application/json` **and** a `tz`/`tzname` header
  (500 without), and the full filter body the page's JS sends (trimmed
  arrays 500 too). `portal=101430233` is not in the page HTML - it is the
  Taleo-wide default external portal, confirmed on two unrelated hosts.
  -> `requisitionList[{jobId, contestNo, column, linkedColumn,
  locationsColumns}]`, `pagingData{pageSize: 25, totalCount}`,
  `facetResults` (LOCATION facet ids -> server-side Israel filter: 40 -> 10).
- Detail `GET .../careersection/{section}/jobdetail.ftl?job={contestNo}&lang=en`:
  the description is not server-rendered HTML; it sits in an inline
  `api.fillList('requisitionDescriptionInterface', 'descRequisition', [...])`
  JS string array - `[9]` title, `[10]` contestNo, `[11]` description,
  `[12]` qualifications (both `!*!`-prefixed, URL-encoded HTML), `[13]`
  location ("IL-IL-Tel Aviv"). No RSS (off by default in Taleo).

### Embedded boards on company pages (Phase 8) - the big one
A survey of every "custom" page in the sheet (197 fetched) found that
**~45 of them just wrap a known ATS in the company's own domain** - the
hostname-based resolver had filed all of them as `generic_html`. Real
embed shapes now recognized, each verified on the named page:

| Shape | Seen on | Resolves to |
|---|---|---|
| `COMEET.init({"token": ..., "company-uid": "41.009"})` inline JS | eToro, Checkmarx | `comeet`, crawl the company page itself (the adapter reads both values from `COMEET.init`) |
| Links to `comeet.com/jobs/{slug}/{uid}/...` | Atera, Cyera | `comeet`, board `https://www.comeet.com/jobs/{slug}/{uid}` |
| Comeet WordPress plugin: positions under the company domain as `/careers/{position-uid}/`, no credentials on the listing | Buildots, Kaltura | follow one position page for `company-uid`, then the public board `comeet.com/jobs/{domain label}/{uid}` - kept only if it serves `COMPANY_DATA` |
| `boards.greenhouse.io/embed/job_board?for=X` / `job-boards.greenhouse.io/X/jobs/...` (incl. JSON-escaped `\/`) | Nexxen, SimilarWeb | `greenhouse` |
| Board loaded purely from JS, only `?gh_department=` hints | JFrog, AppsFlyer | `greenhouse` with token = domain label, **only** after `boards-api.greenhouse.io/v1/boards/{token}/jobs` answers 200 (same rule for Lever/Ashby hints) |
| `jobs.ashbyhq.com/X/embed`, `api.ashbyhq.com/posting-api/job-board/X` | Nexxen, Crusoe | `ashby` |
| `apply.workable.com/X/`, `apply.workable.com/api/v1/widget/accounts/X` | Humanz, Anzu | `workable` |
| Any `{tenant}.{wdN}.myworkdayjobs.com/...` link | Unity, Samsung, Mastercard, Ribbon, Leidos | `workday` |
| `boards-api.greenhouse.io/v1/boards/X/jobs` fetched by the page's own JS | VIA | `greenhouse` |
| Links into `jobs.eu.lever.co/X/...` (Lever's EU region, own API host) | Mobileye | `lever`, read from `api.eu.lever.co` |
| Comeet JS API loaded, company uid only in the site's own script (`careers-api/2.0/company/{uid}/positions`) | Plus500 | `comeet`, public board verified as for the plugin |
| `COMEET.init({ token: '...', 'company-uid': ... })` with bare keys | Alice | `comeet` (the adapter accepts unquoted keys) |
| Two boards on one page, one dead (a Greenhouse embed left behind after moving to Ashby) | Nexxen | the first whose public API answers - every Greenhouse/Lever/Ashby/Workable reference is checked that way before it is stored |

Still `generic_html` after all that (verified): pages whose list is
injected by JS with no board reference at all (hibob, Team8, TriEye's
WordPress plugin page, which carries a token but no company uid), and
pages behind a WAF that 403s non-browser clients (Nayax, Check Point,
Fiverr, ...). Those are the browser-fallback's job (not built). Ribbon's
Workday tenant (`vhr-genband`) answers 422 to every CXS request, even a
browser-like one, so it is in the same bucket despite the hostname.

### Generic HTML (Phase 8)
No API and no board: the listing page's job links are found from its own
structure (the largest group of same-shaped same-site anchors whose
links look like job pages), titles from the anchor's heading or the
card's heading when the anchor is a "View Details" button; a job page is
read via its JSON-LD JobPosting when present (Kaltura, Wolt), else
`<h1>` + the main content block. Verified on Buildots, Island, Wolt,
Kaltura, Moveo, GotFriends. Known limits: recruiting agencies whose
"jobs" are category pages (GotFriends) come through as such; JS-rendered
job pages give a title but no description (Buildots detail pages - which
is why the Comeet-plugin resolution above matters).

### Israeli shared platforms (dueto.io, adamtotal.co.il, topmatch.co.il)
All three sheet URLs were checked live: dueto.io and topmatch.co.il
return 404 (dead links in the sheet), adamtotal serves a 23MB
server-rendered ASP.NET page that the generic adapter handles. No
dedicated adapters.

### Site feeds and WordPress REST (coverage step 2)
A survey of the 94 sources that were crawled without ever yielding a job
found three JS-rendered sites that publish the JSON their own page
fetches; `adapters/site_feed.py` reads them (source type `site_feed`,
keyed by hostname in the resolver):

| Site | Feed | Notes |
|---|---|---|
| Elbit | `elbitsystemscareer.com/cron/jobs.json` | 586 positions, HTML-escaped text, area, open/update times; a job opens at `/jobs/?jobId={jobId}` |
| IAI | `jobs.iai.co.il/wp-content/themes/tyco-wp/assets/json/jobs.json` | 517 positions, terse keys (`tl` title, `dc` text, `ct` Hebrew city, `tp` type); job page `/job/{id}/` |
| Amazon | `amazon.jobs/en/search.json?country=ISR` | 171 in Israel, full text in the list, 100 per page |

Microsoft's `gcsservices.careers.microsoft.com` API serves a certificate
that is not valid for its hostname, so it is not read.

Of the 28 WordPress career sites in the sheet, 4 expose their jobs as
posts of a custom type through the REST API (Comblack `careers` 315, One
`job` 157, OMC `career` 14, Tap `awsm_job_openings` 5). The resolver
finds the type in `/wp-json/wp/v2/types` (source type `wordpress`,
identifier = REST base) and `adapters/wordpress.py` lists it, reading a
post's own page when the API returns no text (One). The other 24 keep
their jobs in a plugin the API does not show and stay `generic_html`.

### Browser fallback (coverage step 3)
`adapters/browser.py` (source type `playwright`) renders a page in
headless Chromium - installed in the worker image, `Dockerfile` - and
then reads it exactly like a plain page (`extract_job_links`,
`details_from_html`). Nothing is resolved to it up front: the crawler
hands a `generic_html` source over after the plain reader saw no jobs
three successful crawls in a row (`_EMPTY_CRAWLS_BEFORE_BROWSER`) or was
refused with 403, and a sheet sync never downgrades it back. It polls
hourly, renders one page at a time per worker process with images,
media and fonts blocked, and fetches a job page plainly first - many
JS-listed sites still serve the posting itself as HTML. Sites behind
Akamai/Imperva bot management usually refuse the headless browser too;
those keep failing at the hourly cadence.
