"""The hybrid matching engine: combines independent component
scores into one calibrated 0-100 final score, with an explanation
(reasons/concerns) for why - never embedding similarity alone, and never
a black box.

    final = 100 x role_gate x quality

    role_gate = floor + (1 - floor) x role_fit
    role_fit  = max(title/keyword role score, 0.6 x calibrated intent similarity)
                - or just the title/keyword score when the title contains
                one of the role's excluded terms
    quality   = weighted sum of seniority, skills, calibrated candidate
                similarity, calibrated intent similarity, location, recency
              - and, for a match Jev judged, (1 - w) x that + w x Jev's
                P(a recruiter would shortlist), w = JEV_WEIGHT

The gate is what makes "this isn't the role you're looking for" dominate:
a job can be junior-friendly, in Tel Aviv, fresh and mention a skill you
have, and still be a payments analyst opening. See docs/matching.md for
the measured distributions behind the calibration constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings, get_settings
from app.models.candidate_profile import CandidateProfile
from app.models.job_posting import JobPosting
from app.models.target_role import TargetRole
from app.services.matching.location_score import score_location
from app.services.matching.recency import score_recency
from app.services.matching.role_score import negative_keyword_hits, score_role
from app.services.matching.semantic import cosine_similarity
from app.services.matching.seniority import assess_seniority, score_seniority
from app.services.matching.skills import extract_skills_from_text, normalize_skill, score_skills

_WEIGHT_SUM_TOLERANCE = 0.01
# How much a strong semantic match with the role's *intent* can stand in
# for a title the keyword lists didn't anticipate. A backstop, not a
# substitute: measured on the live database the local model's intent
# similarity barely separates fields - a "Junior Customer Support"
# posting calibrated to 0.80 while a perfect "Junior Software Engineer"
# read 0.67 - so at 0.85 it let almost anything "read like the role".
# At 0.6 an unlisted title passes the gate at ~2/3 strength at most.
_INTENT_AS_ROLE_FIT = 0.6
_NEUTRAL_SEMANTIC = 0.5


class InvalidWeightsError(ValueError):
    def __init__(self, total: float) -> None:
        super().__init__(
            f"Match quality weights must sum to 1.0, got {total:.4f}. Check the WEIGHT_* "
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


def candidate_skill_set(candidate: CandidateProfile) -> set[str]:
    skills: set[str] = set()
    structured = candidate.structured_profile or {}
    for key in ("skills", "programming_languages", "frameworks", "databases", "cloud", "devops"):
        for item in structured.get(key) or []:
            skills.add(normalize_skill(str(item)))
    # Structured extraction is best-effort and can be empty even
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
        settings.weight_seniority
        + settings.weight_skills
        + settings.weight_candidate_semantic
        + settings.weight_intent_semantic
        + settings.weight_location
        + settings.weight_recency
    )
    if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
        raise InvalidWeightsError(total)


def calibrate(raw_similarity: float, floor: float, ceiling: float) -> float:
    """Raw cosine similarity -> [0, 1] over the range this model actually
    produces, so a typical good match reads as ~0.7, not ~0.4."""
    if ceiling <= floor:
        return max(0.0, min(1.0, raw_similarity))
    return max(0.0, min(1.0, (raw_similarity - floor) / (ceiling - floor)))


def compute_match(
    candidate: CandidateProfile,
    target_role: TargetRole,
    job: JobPosting,
    candidate_skills: set[str] | None = None,
    jev_fit: float | None = None,
) -> MatchResult:
    """`candidate_skills` lets a bulk rescore extract the CV's skills once
    instead of once per job (~3,000 regex passes over the same text).
    `jev_fit` is Jev's P(a recruiter would shortlist) for this pair, when
    the match has been judged (app/services/jev/fit.py)."""
    settings = get_settings()
    _validate_weights(settings)

    reasons: list[str] = []
    concerns: list[str] = []

    if candidate.embedding is not None and job.embedding is not None:
        candidate_semantic_score = calibrate(
            cosine_similarity(candidate.embedding, job.embedding),
            settings.semantic_candidate_floor,
            settings.semantic_candidate_ceiling,
        )
    else:
        candidate_semantic_score = _NEUTRAL_SEMANTIC
    if target_role.embedding is not None and job.embedding is not None:
        intent_semantic_score = calibrate(
            cosine_similarity(target_role.embedding, job.embedding),
            settings.semantic_intent_floor,
            settings.semantic_intent_ceiling,
        )
    else:
        intent_semantic_score = _NEUTRAL_SEMANTIC

    if candidate_skills is None:
        candidate_skills = candidate_skill_set(candidate)
    job_skills = _job_skill_set(job)
    skill_score, matched_skills, missing_skills = score_skills(candidate_skills, job_skills)
    reasons.extend(matched_skills[:6])
    concerns.extend(f"{skill} experience requested" for skill in missing_skills[:4])

    title_role_score, role_explanation = score_role(job.title, job.department, target_role)
    if negative_keyword_hits(job.title, target_role):
        # The role's veto: "Software Project Manager" reads a lot like a
        # software role to an embedding model, which is exactly why
        # "manager" is on their excluded list. No semantic rescue.
        role_fit = title_role_score
        concerns.append(role_explanation)
    else:
        role_fit = max(title_role_score, _INTENT_AS_ROLE_FIT * intent_semantic_score)
        if title_role_score >= 0.7:
            reasons.append(role_explanation)
        elif role_fit >= 0.5:
            reasons.append("reads like the target role, even though the title doesn't say so")
        elif role_fit <= 0.35:
            concerns.append("doesn't look like the role you're looking for")
        elif title_role_score <= 0.3:
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

    quality = (
        settings.weight_seniority * seniority_score
        + settings.weight_skills * skill_score
        + settings.weight_candidate_semantic * candidate_semantic_score
        + settings.weight_intent_semantic * intent_semantic_score
        + settings.weight_location * location_score
        + settings.weight_recency * recency_score
    )
    if jev_fit is not None and settings.jev_weight:
        quality = (1.0 - settings.jev_weight) * quality + settings.jev_weight * jev_fit
        if jev_fit >= 0.7:
            reasons.append(f"Jev: a recruiter would shortlist you ({jev_fit:.0%})")
        elif jev_fit <= 0.3:
            concerns.append(f"Jev: a recruiter would probably pass ({jev_fit:.0%})")
    role_gate = settings.role_gate_floor + (1.0 - settings.role_gate_floor) * role_fit
    final_score = round(100 * role_gate * quality, 1)

    return MatchResult(
        candidate_semantic_score=candidate_semantic_score,
        intent_semantic_score=intent_semantic_score,
        skill_score=skill_score,
        role_score=role_fit,
        seniority_score=seniority_score,
        location_score=location_score,
        recency_score=recency_score,
        final_score=final_score,
        reasons=reasons,
        concerns=concerns,
    )
