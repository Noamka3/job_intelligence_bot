"""What a posting is, as Jev reads it: the kind of work, the level it is
written for, and two yes/no checks that map straight onto the
dashboard's tags. Asked once per posting text - the content hash is
stored with the answers - and kept beside the rule-based reads in
seniority.py, not instead of them, until the two have been compared on
real postings (docs/jev.md).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from typesafe_sdk import Choice, ChoiceAnswer, Noul, NoulAnswer

from app.models.job_posting import JobPosting
from app.services.jev.client import MAX_STATE_CHARS, Question, ask, is_enabled

ROLE_FAMILIES = {
    "software_development": "Writing application software: backend, frontend, "
    "full stack, mobile, embedded",
    "qa_automation": "Testing software, manual or automated",
    "data": "Data science, data engineering, analytics, machine learning",
    "devops_it": "DevOps, SRE, cloud infrastructure, IT support, networking",
    "hardware": "Electrical, chip design and verification, mechanical, physical engineering",
    "product_management": "Product or project management",
    "other": "Sales, marketing, HR, finance, operations, or anything else",
}
SENIORITY_LEVELS = {
    "student": "Open only to people currently studying: a student position or an internship",
    "junior": "Entry level: no or up to about one year of professional experience",
    "mid": "Two to four years of professional experience",
    "senior": "Five or more years, or a senior, staff, principal or architect title",
    "lead": "Leads people: team lead, manager, head of, director, VP",
}
_QUESTIONS: dict[str, Question] = {
    "role_family": Choice(
        instructions="The kind of work this job posting is for", criteria=ROLE_FAMILIES
    ),
    "seniority": Choice(
        instructions="The experience level this posting is written for", criteria=SENIORITY_LEVELS
    ),
    "students_only": Noul(
        instructions="The posting is open only to people who are currently students"
    ),
    "experience_required": Noul(
        instructions="The posting requires more than one year of prior professional "
        "experience in its field"
    ),
}


@dataclass(frozen=True)
class PostingReading:
    model: str
    content_hash: str
    role_family: str
    role_family_confidence: float
    seniority: str
    seniority_confidence: float
    students_only: float
    experience_required: float


def needs_reading(job: JobPosting) -> bool:
    """True when Jev is on and has not read this text of the posting."""
    if not is_enabled():
        return False
    stored = job.jev_reading or {}
    return stored.get("content_hash") != job.content_hash


def read_posting(job: JobPosting) -> dict[str, Any] | None:
    """Jev's reading of `job`, as the dict stored in JobPosting.jev_reading,
    or None when Jev is off or the call failed."""
    response = ask(_state(job), _QUESTIONS)
    if response is None:
        return None
    answers = response.answers
    role_family, seniority = answers["role_family"], answers["seniority"]
    students_only, experience_required = answers["students_only"], answers["experience_required"]
    assert isinstance(role_family, ChoiceAnswer) and isinstance(seniority, ChoiceAnswer)
    assert isinstance(students_only, NoulAnswer) and isinstance(experience_required, NoulAnswer)
    return asdict(
        PostingReading(
            model=response.model,
            content_hash=job.content_hash,
            role_family=role_family.choice,
            role_family_confidence=role_family.confidence,
            seniority=seniority.choice,
            seniority_confidence=seniority.confidence,
            students_only=students_only.noul,
            experience_required=experience_required.noul,
        )
    )


def _state(job: JobPosting) -> dict[str, str]:
    text = job.normalized_description or job.description or ""
    state = {
        "title": job.title,
        "department": job.department,
        "location": job.location_text,
        "description": text[:MAX_STATE_CHARS],
    }
    return {key: value for key, value in state.items() if value}
