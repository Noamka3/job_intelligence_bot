"""The hybrid matching engine (spec §8): combines seven independent
component scores into one calibrated 0-100 final score, with an
explanation (reasons/concerns) for why - never embedding similarity
alone, and never a black box.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings, get_settings
from app.models.candidate_profile import CandidateProfile
from app.models.job_posting import JobPosting
from app.models.target_role import TargetRole
from app.services.matching.location_score import score_location
from app.services.matching.recency import score_recency
from app.services.matching.role_score import score_role
from app.services.matching.semantic import cosine_similarity
from app.services.matching.seniority import assess_seniority, score_seniority
from app.services.matching.skills import extract_skills_from_text, normalize_skill, score_skills

_WEIGHT_SUM_TOLERANCE = 0.01


class InvalidWeightsError(ValueError):
    def __init__(self, total: float) -> None:
        super().__init__(
            f"Match score weights must sum to 1.0, got {total:.4f}. Check the WEIGHT_* "
            "settings in .env."
        )


@dataclass(frozen=True)
class MatchResult:
    candidate_semantic_score: float
    intent_semantic_score: float
    skill_score: float
    role_score: float
    seniority_score: float
    location_score: float
    recency_score: float
    final_score: float
    reasons: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)


def _candidate_skill_set(candidate: CandidateProfile) -> set[str]:
    skills: set[str] = set()
    structured = candidate.structured_profile or {}
    for key in ("skills", "programming_languages", "frameworks", "databases", "cloud", "devops"):
        for item in structured.get(key) or []:
            skills.add(normalize_skill(str(item)))
    # Structured extraction is best-effort (spec §6) and can be empty even
    # for a real CV (observed with local-LLM extraction on constrained
    # hardware) - the raw resume text is the reliable fallback.
    skills |= extract_skills_from_text(candidate.normalized_text)
    return skills


def _job_skill_set(job: JobPosting) -> set[str]:
    skills: set[str] = {
        normalize_skill(item) for item in (*job.required_skills, *job.preferred_skills)
    }
    text = " ".join(
        part
        for part in (job.normalized_description, job.qualifications, job.responsibilities)
        if part
    )
    skills |= extract_skills_from_text(text)
    return skills


def _validate_weights(settings: Settings) -> None:
    total = (
        settings.weight_candidate_semantic
        + settings.weight_intent_semantic
        + settings.weight_skills
        + settings.weight_role
        + settings.weight_seniority
        + settings.weight_location
        + settings.weight_recency
    )
    if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
        raise InvalidWeightsError(total)


def compute_match(
    candidate: CandidateProfile, target_role: TargetRole, job: JobPosting
) -> MatchResult:
    settings = get_settings()
    _validate_weights(settings)

    reasons: list[str] = []
    concerns: list[str] = []

    candidate_semantic_score = (
        cosine_similarity(candidate.embedding, job.embedding)
        if candidate.embedding is not None and job.embedding is not None
        else 0.5
    )
    intent_semantic_score = (
        cosine_similarity(target_role.embedding, job.embedding)
        if target_role.embedding is not None and job.embedding is not None
        else 0.5
    )

    candidate_skills = _candidate_skill_set(candidate)
    job_skills = _job_skill_set(job)
    skill_score, matched_skills, missing_skills = score_skills(candidate_skills, job_skills)
    reasons.extend(matched_skills[:6])
    concerns.extend(f"{skill} experience requested" for skill in missing_skills[:4])

    role_score, role_explanation = score_role(job.title, job.department, target_role)
    if role_score >= 0.7:
        reasons.append(role_explanation)
    elif role_score <= 0.3:
        concerns.append(role_explanation)

    requirements_text = job.qualifications or job.normalized_description or job.description or ""
    assessment = assess_seniority(job.title, requirements_text)
    seniority_score, seniority_explanation = score_seniority(
        assessment, target_role.max_expected_years
    )
    if seniority_score >= 0.7:
        reasons.append(seniority_explanation)
    elif seniority_score <= 0.3:
        concerns.append(seniority_explanation)

    location_score, location_explanation = score_location(
        job.country, job.location_text, job.remote_type
    )
    if location_score >= 0.7:
        reasons.append(location_explanation)
    elif location_score <= 0.3:
        concerns.append(location_explanation)

    recency_score, recency_explanation = score_recency(job.source_published_at or job.first_seen_at)
    if recency_score >= 0.85:
        reasons.append(recency_explanation)

    final_score = round(
        100
        * (
            settings.weight_candidate_semantic * candidate_semantic_score
            + settings.weight_intent_semantic * intent_semantic_score
            + settings.weight_skills * skill_score
            + settings.weight_role * role_score
            + settings.weight_seniority * seniority_score
            + settings.weight_location * location_score
            + settings.weight_recency * recency_score
        ),
        1,
    )

    return MatchResult(
        candidate_semantic_score=candidate_semantic_score,
        intent_semantic_score=intent_semantic_score,
        skill_score=skill_score,
        role_score=role_score,
        seniority_score=seniority_score,
        location_score=location_score,
        recency_score=recency_score,
        final_score=final_score,
        reasons=reasons,
        concerns=concerns,
    )
