"""Turns a JobDetails DTO into the text actually used for embedding +
matching, plus a content hash used to decide whether a job's embedding
needs to be regenerated at all (spec §11/§45: never re-embed unchanged
content).
"""

from __future__ import annotations

import hashlib

from app.ingestion.adapters.base import JobDetails
from app.models.enums import EmploymentType
from app.services.matching.seniority import requirements_section
from app.services.text_normalization import normalize_whitespace

# Lines containing any of these (case-insensitive) are dropped before
# embedding - generic legal/marketing boilerplate, not job content.
_BOILERPLATE_MARKERS = (
    "equal opportunity employer",
    "equal employment opportunity",
    "does not discriminate",
    "committed to diversity",
    "reasonable accommodation",
    "privacy policy",
    "privacy notice",
    "cookie policy",
    "all rights reserved",
    "e-verify",
)


def strip_boilerplate(text: str) -> str:
    kept_lines = [
        line
        for line in text.split("\n")
        if not any(marker in line.lower() for marker in _BOILERPLATE_MARKERS)
    ]
    return "\n".join(kept_lines)


def build_normalized_description(details: JobDetails) -> str:
    return normalize_whitespace(strip_boilerplate(details.description or ""))


def build_embedding_text(details: JobDetails) -> str:
    parts = [details.title]
    if details.department:
        parts.append(f"Team: {details.department}")
    if details.location_text:
        parts.append(f"Location: {details.location_text}")
    if details.employment_type is not EmploymentType.UNKNOWN:
        parts.append(f"Employment type: {details.employment_type.value}")
    if details.required_skills:
        parts.append("Required skills: " + ", ".join(details.required_skills))
    if details.preferred_skills:
        parts.append("Preferred skills: " + ", ".join(details.preferred_skills))
    if details.responsibilities:
        parts.append(details.responsibilities)
    if details.qualifications:
        parts.append(details.qualifications)

    normalized_description = build_normalized_description(details)
    if normalized_description:
        # A single-blob description usually opens with "about us" and
        # buries the requirements at the end - past where the embedding
        # model stops reading. Lead with the requirements section when
        # one is recognizable, then the whole text.
        if not (details.responsibilities or details.qualifications):
            requirements = requirements_section(normalized_description)
            if requirements != normalized_description:
                parts.append(requirements)
        parts.append(normalized_description)

    return "\n".join(parts)


def content_hash_for(embedding_text: str) -> str:
    return hashlib.sha256(embedding_text.encode("utf-8")).hexdigest()


def normalize_job_title(title: str) -> str:
    return " ".join(title.strip().lower().split())


# Common Israeli city name variants (spec §28) collapsed to one canonical
# form, so "Tel Aviv-Yafo, Israel" and "Tel Aviv" fingerprint/filter
# identically. Applied as in-place replacements, longest variant first,
# since real location strings are rarely just the city ("Tel Aviv-Yafo,
# Tel Aviv District, Israel" is Greenhouse's standard form).
_LOCATION_ALIASES = {
    "tel aviv-yafo": "tel aviv",
    "tel aviv yafo": "tel aviv",
    "tel-aviv": "tel aviv",
    "be'er sheva": "beer sheva",
    "beer-sheva": "beer sheva",
    "petach tikva": "petah tikva",
    "petah-tikva": "petah tikva",
}
_LOCATION_ALIASES_LONGEST_FIRST = sorted(_LOCATION_ALIASES.items(), key=lambda kv: -len(kv[0]))


def normalize_location(location_text: str | None) -> str | None:
    if not location_text:
        return None
    normalized = " ".join(location_text.strip().lower().split())
    for variant, canonical in _LOCATION_ALIASES_LONGEST_FIRST:
        normalized = normalized.replace(variant, canonical)
    return normalized
