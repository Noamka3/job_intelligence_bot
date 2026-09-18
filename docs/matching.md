# Matching (Phase 5)

## Pipeline

```
Hard Constraints (none yet - see "Not yet built" below)
        v
Title / Role Classification  (role_score)
        v
Semantic Similarity          (candidate_semantic_score, intent_semantic_score)
        v
Skill Matching                (skill_score)
        v
Seniority Fit                 (seniority_score)
        v
Location Fit                  (location_score)
        v
Recency                       (recency_score)
        v
Optional LLM Reranking (not yet built - see below)
        v
Final Score (0-100)
```

`app/services/matching/scoring.py:compute_match()` is the single entry
point: given one `CandidateProfile`, one `TargetRole`, and one
`JobPosting`, it returns a `MatchResult` with all seven component scores,
the weighted `final_score`, and `reasons`/`concerns` (short strings
explaining the score - not embedding similarity numbers).

## Component scores

| Component | Module | What it measures |
|---|---|---|
| `candidate_semantic_score` | `semantic.py` | Cosine similarity: candidate CV embedding vs. job embedding |
| `intent_semantic_score` | `semantic.py` | Cosine similarity: target role's *own* embedding vs. job embedding - kept separate from the CV per spec §7, since a resume can span domains (e.g. data science + web dev) even when the current search is narrowly scoped |
| `skill_score` | `skills.py` | Fraction of the job's mentioned skills (extracted via a canonical alias vocabulary on word boundaries, not substring search) the candidate demonstrably has. Aliases that are also ordinary words ("Go", "React", "Spring") only count in a technical context - a list item, "Go developer", "experience with Go" - after a real posting's "go-to-market", "send your CV", "the rest of the team" and "jobs@company.net" produced four bogus skills and a "go experience requested" concern |
| `role_score` | `role_score.py` | Title/department match against the target role's canonical name, aliases, and positive/negative keywords - whole-word ("java" doesn't credit "JavaScript"), and negatives are checked *before* aliases, since "Senior Software Engineer" contains the alias "Software Engineer" too |
| `seniority_score` | `seniority.py` | Title-based seniority detection first (most reliable; English and Hebrew tokens, "Engineer I"/"Engineer 1", "Student"), years-of-experience text second; body text alone (e.g. "work closely with senior engineers") never triggers a senior flag. Years are read from the requirements section only when the description has a recognizable one ("Requirements", "What you'll bring", "דרישות"...), so "with over 15 years in cybersecurity, Acme..." in the intro doesn't count; with several requirements the largest stated minimum wins ("5+ years backend, 1-2 years Kubernetes" -> 5), and anything above 15 is treated as a company blurb |
| `location_score` | `location_score.py` | Israel vs. confidently-elsewhere vs. unrecognized (reuses `app/services/jobs/location.py`), remote positions always score well |
| `recency_score` | `recency.py` | Decays from 1.0 (< 24h) down to a 0.1 floor (> 30 days) - never zero, so an old-but-great match still surfaces |

`reasons`/`concerns` are built from whichever components cleared a
threshold (>= 0.7 for reasons, <= 0.3 for concerns) plus every matched/
missing skill - not raw embedding scores, matching spec §8's explicit
"do not use embedding similarity itself as the user-facing percentage."

## Matching v2: the role gate and calibrated similarity

The first version summed seven weighted components. Measured on the real
database (3,100 jobs, the user's real CV) that had two failure modes the
user noticed immediately:

1. **A perfect job couldn't score above ~79.** Raw cosine similarity from
   the local MiniLM model spans only 0.15-0.55 for the CV (5th-99th
   percentile over every match) and 0.10-0.50 for the role intent - even a
   "Junior Software Engineer" posting sits around 0.43/0.26. At a combined
   32% weight those components silently cost every job ~20 points.
2. **Jobs in the wrong field scored 65-72** ("Payment Operations Analyst",
   "Support Engineer"): junior-friendly, in Israel, fresh, mentioning SQL -
   the seniority/location/recency/skills points were "free", and role fit
   was only 15% of the score.

Now:

```
final     = 100 x role_gate x quality
role_gate = 0.2 + 0.8 x role_fit
role_fit  = max(title/keyword role score, 0.6 x calibrated intent similarity)
            - or just the title/keyword score when the title contains an
              excluded term
quality   = 0.45 seniority + 0.30 skills + 0.12 candidate_sim + 0.08 intent_sim
            + 0.03 location + 0.02 recency
```

- The **gate** makes "not the role you're looking for" dominate: with no
  title/keyword/intent match a job keeps at most 20% of its quality, so
  the payments-analyst opening lands around 35-40 instead of 67.
- **Calibration** maps raw similarity linearly onto [0, 1] between the
  measured floor and ceiling (`SEMANTIC_*_FLOOR/CEILING`), so a typical
  good match reads ~0.7 rather than ~0.4. The stored component scores
  are the calibrated ones - what the dashboard bars show.
- **Intent as a role backstop, capped**: a title the keyword lists never
  anticipated ("Platform Wizard") still passes the gate at up to ~2/3
  strength when its embedding is close to the target role's; the reason
  shown is "reads like the target role, even though the title doesn't
  say so". It is capped at 0.6 because, measured on the live database,
  the local model's intent similarity barely separates fields: a "Junior
  Customer Support" posting calibrated to 0.80 while a perfect "Junior
  Software Engineer" read 0.67, and at the original 0.85 factor almost
  anything "read like the role" (that support job scored 57%). The
  user's **excluded terms veto the backstop** entirely: "Software Project
  Manager" reads like a software role to an embedding, which is exactly
  why "manager" is excluded.
- **Titles in Hebrew** only match through aliases/keywords in Hebrew - the
  matcher is whole-word and language-agnostic, so a target role that
  should catch "מהנדס/ת BackEnd" or "מפתח/ת Full Stack" needs aliases
  such as מפתח, מפתחת, מתכנת, מתכנתת, מהנדס תוכנה. Without them such
  titles only get the capped semantic backstop.

## Experience requirements, in Hebrew and English

`seniority.py` reads the years a posting asks for from its requirements
section (heading detection covers "Requirements", "Qualifications",
"דרישות", "כישורים נדרשים" and the like). Found live: the patterns were
English-only, so "לפחות 4 שנות ניסיון" read as "no clear seniority
signal", a neutral 0.5 that let the job through at 61% to a candidate
with no experience. Now:

- Digits and words in both languages: "5+ years", "at least three
  years", "3 שנות ניסיון", "לפחות 4 שנים", "ניסיון של 5 שנים", "חמש שנות
  ניסיון", "ניסיון של שנתיים", "שנה ניסיון".
- Ranges contribute their lower bound and are removed before the
  single-number patterns run, so "3-5 years of experience" is 3, not 5
  (it used to be 5).
- A Hebrew number-of-years only counts with "ניסיון" nearby: "תואר
  ראשון, 3 שנות לימוד" is the length of a degree.
- A posting that says experience isn't needed ("ללא ניסיון", "ניסיון לא
  חובה", "no prior experience required", "fresh graduates") reads as
  junior with 0 years - unless an explicit years requirement contradicts
  it, or the phrase is negated ("לא יתקבלו מועמדים ללא ניסיון").

The read is stored on the posting at ingest (`JobPosting.seniority`,
`experience_min_years`; `reassess-seniority` backfills) and the API
derives a **seniority fit** per match relative to the target role's
`max_expected_years`: `fit` (entry-level title, or stated years within
the ceiling), `experienced` (senior-level title, or years above it),
`unknown` (nothing readable). The dashboard shows it as a tag on every
card and filters on it; the default view hides `experienced`.
- **Better embeddings underneath**: single-blob descriptions now lead
  with their recognizable requirements section (the same heading
  detection seniority uses), and the local provider embeds long texts in
  ~220-word chunks and averages the vectors, so page two of a CV and the
  requirements at the end of a posting count. `python -m app.cli reembed`
  recomputes every stored vector and rescores after such a change.

## Weights - and why they differ from the spec's suggested starting point

The spec suggested 30/20/20/10/10/5/5
(candidate-semantic/intent-semantic/skills/role/seniority/location/
recency) as "a reasonable initial configuration" while explicitly saying
not to use it blindly if analysis suggests otherwise. Verified against
real embeddings in `tests/integration/test_matching_scenarios.py` (the
spec's own §43 examples): the local embedding model's raw cosine
similarity barely distinguishes "Junior Backend Engineer" from "Senior
Backend Engineer" for the *same* tech stack - both landed around
0.43-0.54. That's exactly the failure mode spec §8 itself warns about
("semantic similarity... can rank Senior Backend Engineer very highly...
because the technology and responsibilities are semantically similar").

At a 10% weight, seniority's own signal - which fires correctly and
confidently (1.0 for a junior title, 0.05 for a senior one) - couldn't
overcome that. The weights actually in use:

| Component | Spec's suggestion | In use (v2) | Why |
|---|---|---|---|
| role | 10% | the **gate** (x0.2 … x1.0) | "Is this the role at all" isn't one signal among seven - it decides whether the others count |
| seniority | 10% | 45% of quality | The component that actually solves spec §43's examples |
| skills | 20% | 30% of quality | A real, reliable signal |
| candidate_semantic | 30% | 12% of quality, calibrated | Raw similarity spans only 0.15-0.55 and doesn't discriminate on seniority |
| intent_semantic | 20% | 8% of quality, calibrated (also feeds the gate) | Same limitation; useful as a backstop for unlisted titles |
| location | 5% | 3% | Israel-only filtering already happens at the API layer, so this is a tiebreaker |
| recency | 5% | 2% | Per spec §27, must never dominate relevance |

The six quality weights sum to 1.0 (enforced by `InvalidWeightsError` in
`scoring.py`), and every weight, the gate floor and the calibration
bounds are env vars - the spec's "make weights configurable"
requirement, not a hardcoded choice bolted on for these test cases.

## When scores are (re)computed

- Every new or meaningfully changed job is scored right after ingestion
  (`crawl_source` -> `score_job`), whether the crawl was triggered by
  Celery Beat, `crawl-now`, or `crawl-company`.
- Uploading/activating a CV, and creating/patching a target role,
  rescore every active job (`score_all_active_jobs`) - a new profile id
  or a changed alias list would otherwise leave `/matches/top` empty or
  stale until a crawl happened to touch each job. `rebuild-profile` does
  the same. `score-all` remains as the manual backfill.
- `JobMatch` rows are kept when a job closes (history); `/matches/top`
  filters on `JobPosting.status = ACTIVE` so a closed job's old 95 never
  ranks first.

## Embedding length

The local model reads ~512 tokens per call. `LocalEmbeddingProvider.
embed_one` therefore splits longer text into ~220-word chunks, embeds
each and averages (re-normalized) - the standard long-document approach
for sentence-transformer models - so a multi-page CV and a long posting
are represented whole. `build_embedding_text` additionally leads with a
posting's requirements section when the description is one blob, so the
part that matters most also gets the most weight. Changing either means
running `python -m app.cli reembed`.

## Not yet built (later phases)

- **Hard constraints** (spec's pipeline diagram shows this as the first
  stage) - not implemented as a separate pre-filter yet; low scores serve
  that purpose today (e.g. a confidently-senior job still gets a
  `JobMatch` row, just a low one). A future pass could exclude egregious
  mismatches outright rather than merely scoring them low.
- **Optional LLM reranking** (spec §26) - deliberately not built. Given
  the local-LLM performance findings during Phase 2 (CPU-only inference
  took minutes per call and once destabilized the whole machine - see
  README's "Local LLM performance" entry), reranking every
  newly-discovered job would be far too expensive to run unattended; if
  it's ever added it needs a strict threshold/opt-in gate.
