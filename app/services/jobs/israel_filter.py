"""The one SQL predicate behind every `israel_only` switch (/jobs,
/matches/top, and the notifications to come), so they can never drift
from each other or from the Python classifier in location.py.

Keeps: anything classified as Israel at ingest, and anything whose
location wasn't recognized (never hide a real Israeli listing over an
unknown city name). Drops only an explicit non-Israel signal, matched on
word boundaries with the same vocabulary is_confidently_non_israeli uses.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, not_

from app.models.job_posting import JobPosting
from app.services.jobs.location import NON_ISRAEL_LOCATION_SQL_REGEX


def israel_only_clause() -> ColumnElement[bool]:
    return (
        (JobPosting.country == "Israel")
        | JobPosting.location_text.is_(None)
        | not_(JobPosting.location_text.regexp_match(NON_ISRAEL_LOCATION_SQL_REGEX, flags="i"))
    )
