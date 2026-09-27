"""Title/role component score: how well a job's title/department
matches the TargetRole being scored against, independent of the semantic
embedding comparison - a cheap, explainable signal that catches obvious
matches/mismatches embeddings can sometimes blur.
"""

from __future__ import annotations

import re

from app.models.target_role import TargetRole


def _mentions(haystack: str, term: str) -> bool:
    # Whole-word, so a "java" keyword doesn't credit "JavaScript Developer"
    # and the alias "Software Engineer" doesn't match "Software
    # Engineering Manager".
    term = term.strip()
    if not term:
        return False
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", haystack, re.IGNORECASE) is not None


def negative_keyword_hits(job_title: str, target_role: TargetRole) -> list[str]:
    """The target role's excluded terms found in the title. Checked before
    anything else here, and in scoring.py it also vetoes the semantic
    "reads like the role" rescue: a "Software Project Manager" reads a lot
    like a software role to an embedding model, which is exactly why the
    user listed "manager"."""
    return [kw for kw in target_role.negative_keywords if _mentions(job_title, kw)]


def score_role(
    job_title: str, job_department: str | None, target_role: TargetRole
) -> tuple[float, str]:
    # Exclusions first: "Senior Software Engineer" contains the alias
    # "Software Engineer" too, and the excluded term is what the role
    # actually cares about there.
    negative_hits = negative_keyword_hits(job_title, target_role)
    if negative_hits:
        return 0.1, f"title contains excluded term(s): {', '.join(negative_hits)}"

    names = [target_role.canonical_name, *target_role.aliases]
    if any(_mentions(job_title, name) for name in names):
        return 1.0, "title directly matches the target role"

    haystack = job_title + " " + (job_department or "")
    positive_hits = [kw for kw in target_role.positive_keywords if _mentions(haystack, kw)]
    if positive_hits:
        # One of the role's own keywords in the title ("backend" in "Junior
        # Backend Developer") is strong evidence on its own; two settle it.
        score = min(1.0, 0.6 + 0.2 * len(positive_hits))
        return score, f"title/department matches keyword(s): {', '.join(positive_hits)}"

    return 0.3, "no direct title/keyword match to the target role"
