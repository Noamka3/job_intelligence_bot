"""One-time structured extraction from a normalized CV, via the OpenAI
chat completions structured-output API. Runs once per resume upload (a
rare event), never in a hot path - see spec §45 cost control.
"""

from __future__ import annotations

from openai import OpenAI

from app.core.config import get_settings
from app.schemas.candidate import StructuredCandidateProfile

_SYSTEM_PROMPT = (
    "You extract a structured profile from a candidate's CV/resume text. "
    "Only include information that is actually present or strongly implied "
    "in the text - never invent skills, employers, or experience. "
    "years_of_experience should be your best numeric estimate of total "
    "professional (non-academic) experience in years, or null if it cannot "
    "be reasonably estimated. seniority should be one short word such as "
    "'intern', 'junior', 'mid', 'senior' based on the candidate's own "
    "experience level, or null if unclear."
)


def extract_structured_profile(normalized_text: str) -> StructuredCandidateProfile:
    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key)

    completion = client.chat.completions.parse(
        model=settings.openai_chat_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": normalized_text},
        ],
        response_format=StructuredCandidateProfile,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        return StructuredCandidateProfile()
    return parsed
