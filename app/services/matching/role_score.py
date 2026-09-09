"""Title/role component score (spec §8): how well a job's title/department
matches the TargetRole being scored against, independent of the semantic
embedding comparison - a cheap, explainable signal that catches obvious
matches/mismatches embeddings can sometimes blur.
"""

from __future__ import annotations

from app.models.target_role import TargetRole


def score_role(
    job_title: str, job_department: str | None, target_role: TargetRole
) -> tuple[float, str]:
    title_lower = job_title.lower()

    names = [target_role.canonical_name, *target_role.aliases]
    if any(name.lower() in title_lower for name in names if name):
        return 1.0, "title directly matches the target role"

    negative_hits = [kw for kw in target_role.negative_keywords if kw.lower() in title_lower]
    if negative_hits:
        return 0.1, f"title contains excluded term(s): {', '.join(negative_hits)}"

    haystack = title_lower + " " + (job_department or "").lower()
    positive_hits = [kw for kw in target_role.positive_keywords if kw.lower() in haystack]
    if positive_hits:
        score = min(1.0, 0.5 + 0.15 * len(positive_hits))
        return score, f"title/department matches keyword(s): {', '.join(positive_hits)}"

    return 0.3, "no direct title/keyword match to the target role"
