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
| `skill_score` | `skills.py` | Fraction of the job's mentioned skills (extracted via a canonical alias vocabulary, not substring search) the candidate demonstrably has |
| `role_score` | `role_score.py` | Title/department match against the target role's canonical name, aliases, and positive/negative keywords |
| `seniority_score` | `seniority.py` | Title-based seniority detection first (most reliable), years-of-experience text second; body text alone (e.g. "work closely with senior engineers") never triggers a senior flag |
| `location_score` | `location_score.py` | Israel vs. confidently-elsewhere vs. unrecognized (reuses `app/services/jobs/location.py`), remote positions always score well |
| `recency_score` | `recency.py` | Decays from 1.0 (< 24h) down to a 0.1 floor (> 30 days) - never zero, so an old-but-great match still surfaces |

`reasons`/`concerns` are built from whichever components cleared a
threshold (>= 0.7 for reasons, <= 0.3 for concerns) plus every matched/
missing skill - not raw embedding scores, matching spec §8's explicit
"do not use embedding similarity itself as the user-facing percentage."

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

| Component | Spec's suggestion | In use | Why |
|---|---|---|---|
| candidate_semantic | 30% | 20% | Doesn't discriminate on seniority - see above |
| intent_semantic | 20% | 12% | Same limitation |
| skills | 20% | 20% | Unchanged - a real, reliable signal |
| role | 10% | 15% | Reliable rule-based signal (title/keyword match) - raised |
| seniority | 10% | 28% | The component that actually solves spec §43's examples - raised substantially |
| location | 5% | 3% | Still matters, but Israel-only filtering already happens at the API layer (`GET /jobs?israel_only=true`), so this is a smaller tiebreaker here |
| recency | 5% | 2% | Per spec §27, must never dominate relevance - kept deliberately small |

All seven still sum to 1.0 (enforced by `InvalidWeightsError` in
`scoring.py`), and every weight is a `WEIGHT_*` env var - the spec's
"make weights configurable" requirement, not a hardcoded choice bolted on
for these specific test cases.

## Not yet built (later phases)

- **Hard constraints** (spec's pipeline diagram shows this as the first
  stage) - not implemented as a separate pre-filter yet; low scores serve
  that purpose today (e.g. a confidently-senior job still gets a
  `JobMatch` row, just a low one). A future pass could exclude egregious
  mismatches outright rather than merely scoring them low.
- **Optional LLM reranking** (spec §26) - deliberately not built this
  phase. Given the local-LLM performance findings during Phase 2
  (CPU-only inference took minutes per call and once destabilized the
  whole machine - see README's "Local LLM performance" entry), reranking
  every newly-discovered job would be far too expensive to run
  unattended. Phase 6's scheduler is the more appropriate place to design
  this with a strict threshold/opt-in gate.
- **Wiring into the crawl pipeline** - `score-all` (CLI) and
  `score_all_active_jobs()` currently need to be run explicitly. Phase 6
  is the natural point to score a job automatically right after it's
  discovered/updated, alongside the 5-minute dispatcher.
