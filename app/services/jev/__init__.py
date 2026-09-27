"""Jev, TypeSafe's decision model, as a second reader beside the rules.

Two narrow jobs, both stored on the row that asked and shown in the
dashboard, neither ranking anything yet: reading.py says what a posting
is (kind of work, level, student-only, experience required) and fit.py
says whether a recruiter would shortlist the CV for it. docs/jev.md has
the reasoning, the cost and the plan for letting it into the score.
"""

from app.services.jev.client import is_enabled
from app.services.jev.fit import JUDGED_ABOVE_ROLE_FIT, judge_fit, needs_judgement
from app.services.jev.reading import needs_reading, read_posting

__all__ = [
    "JUDGED_ABOVE_ROLE_FIT",
    "is_enabled",
    "judge_fit",
    "needs_judgement",
    "needs_reading",
    "read_posting",
]
