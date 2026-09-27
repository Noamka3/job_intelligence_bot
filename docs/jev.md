# Jev

TypeSafe's Jev is a decision model, not a chat model: it is sent some
text ("state") and typed questions, and it answers with probabilities
and a confidence score instead of generating text. That shape fits two
places in this pipeline where the code already makes narrow, structured
decisions with hand-written rules. This document says what is asked,
where the answers go, how much of the score it takes, and what it costs.

## What is asked

**Of every posting** (`app/services/jev/reading.py`), once per text -
the content hash is stored with the answers, so a re-read only happens
when the posting changes:

| Question | Type | Maps onto |
|---|---|---|
| The kind of work (software development / QA / data / DevOps-IT / hardware / product / other) | Choice | the role gate, which today is keyword lists |
| The level it is written for (student / junior / mid / senior / lead) | Choice | `seniority.py`'s title and years rules |
| Open only to students | Noul (0-1) | the "משרת סטודנט" tag |
| Requires more than a year of prior experience | Noul (0-1) | the "דורש ניסיון" tag |

**Of every match above the role gate** (`app/services/jev/fit.py`),
once per (match, posting text). TypeSafe's own composite-scoring recipe
is resume screening, and this is that recipe: the CV and the posting go
in one state, side by side.

| Question | Type |
|---|---|
| A recruiter would consider this candidate a plausible applicant | Noul (0-1) |
| How many of the required skills the CV demonstrably has | Score, 0-4 rubric |
| The candidate's experience relative to what is asked (below / matches / above) | Choice |

Postings that did not pass the role gate (`role_fit < 0.5`) are not
judged: the score already says they are not the role, and skipping them
keeps the calls in the hundreds rather than thousands.

## Where the answers go

`job_postings.jev_reading` and `job_matches.jev_fit`, both nullable
JSONB, each carrying the model version and the content hash. The
dashboard shows them on the card and on the job page; the scorer reads
the judgement (below).

## How it ranks

`JEV_WEIGHT` is the share of a judged match's quality score that Jev's
"a recruiter would shortlist" probability takes: `quality = (1 - w) x
rules + w x jev_fit`. The role gate applies either way, and only
matches above it are judged. At 0 Jev is shown and never ranked on; the
owner runs it at 0.35 - about a third - on the strength of the reads
below, with the feedback given in the dashboard as the thing that will
move it up or down.

## What was checked before giving it weight

Two concerns, both measurable:

1. **Hebrew.** 40% of the postings are in Hebrew. TypeSafe says English
   is the primary training language and that other languages should be
   tested before deployment. The rule-based readers were built for
   Hebrew from the start.
2. **No independent benchmark exists** for the model; the accuracy
   figures are the vendor's own.

Measured on 40 Hebrew and 40 English postings against the rule-based
reads: level agreement 19/24 (Hebrew) and 22/34 (English) where the
rules had decided; students-only 40/40 in both languages; experience
required 34/40 and 31/40. Where Jev disagreed on Hebrew it was mostly
right ("ראש מחלקה" is a lead, "מנהל חשבונות" is a bookkeeper, not a
manager); on English it tends to call three years "senior" in
non-technical roles. Its reads stay visible next to the rule-based
ones on every job page, and the feedback given on cards is what the
weight will be tuned against.

## Cost and privacy

$0.042 per million input tokens, output free. A posting read is about
1,000 tokens, a fit judgement about 2,500: reading the whole database
once is around 25 cents, judging every match above the gate around 6,
and the daily flow after that is under a cent. `jev-backfill` prints
the tokens it used.

With a key set, posting text and **the CV text** are sent to TypeSafe,
hosted in the United States. Their policy: input is not used to train
models and is not shared beyond their service providers. With the key
empty - the default, and how the tests run - nothing is sent anywhere.

## Known limits that shaped the questions

From TypeSafe's own list for jev-1.13: it reads literally, cannot count
or compare dates, and loses accuracy as the state fills with unrelated
text. So the questions never ask for a number of years (the rules keep
doing that), the levels are described in words, and the state is the
title plus the first 8,000 characters of the posting, not the page.

## Running it

```bash
# .env
TYPESAFE_API_KEY=...          # from console.typesafe.ai/keys
JEV_MODEL=jev-1.13.0          # pinned; jev-latest moves without notice

uv run python -m app.cli jev-backfill   # read what is stored, judge what passed the gate
```

New postings are read as they are crawled and judged as they are scored.
