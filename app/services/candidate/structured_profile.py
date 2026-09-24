"""One-time structured extraction from a normalized CV. Runs once per
resume upload (a rare event), never in a hot path.

Two providers, selected by Settings.llm_provider:
- ollama (default): free, runs on this machine, no API key.
- openai: needs OPENAI_API_KEY.
Both use JSON-schema-constrained structured output so the result always
matches the requested shape.

What a small local model is good at, measured on a real CV during
development (llama3.2:3b on CPU): copying lists out of the text -
languages, frameworks, databases, projects, education - once the prompt
names every field and says what goes in it. With a vaguer prompt it
returned two fields and nothing else. What it is bad at: judging the
experience level - it called a fresh graduate with no employer "mid, 4
years". So the lists come from the model, and the experience level from a
rule: a CV with no work-experience section is a junior with 0 years;
otherwise the model estimates the years in a second, tiny call and the
seniority word is derived from that number.
"""

from __future__ import annotations

import re

import httpx
import ollama
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.schemas.candidate import CandidateProfileLists, StructuredCandidateProfile
from app.services.matching.skills import extract_skills_from_text

_LIST_PROMPT = (
    "You are a strict CV parser. Read the CV below and fill EVERY field of the JSON object:\n"
    "- programming_languages: programming languages listed (e.g. Python, Java).\n"
    "- frameworks: frameworks and libraries (e.g. React, FastAPI, PyTorch).\n"
    "- databases: databases (e.g. PostgreSQL, MongoDB).\n"
    "- cloud: cloud platforms and services (e.g. AWS, Firebase).\n"
    "- devops: DevOps and deployment tools (e.g. Docker, GitHub Actions).\n"
    "- skills: other technical skills, tools and competencies (e.g. OOP, REST APIs, Git, "
    "Linux).\n"
    "- domains: fields the candidate worked in (e.g. computer vision, web development).\n"
    "- keywords: 5-15 short keywords that describe this candidate.\n"
    "- projects: one short line per project (name + what it is).\n"
    "- education: one line per degree or program, with institution and years.\n"
    "Copy names exactly as written in the CV. Never invent anything. Output JSON only."
)

_YEARS_PROMPT = (
    "From the CV below, estimate the candidate's total PAID professional work experience "
    "in years, as a number. Count only employment at companies or organizations (part-time "
    "jobs and internships at companies count). Studies, academic projects, courses, "
    "bootcamps and military service do NOT count. Output JSON only."
)

# The OpenAI path fills the whole profile in one call; the rule below
# still decides the experience level for a CV without a work section.
_FULL_PROMPT = (
    _LIST_PROMPT
    + "\n- years_of_experience: total PAID professional work experience in years, as a "
    "number; studies, academic projects, courses, bootcamps and military service do NOT "
    "count.\n- seniority: 'intern', 'junior', 'mid' or 'senior'."
)

# A line that is only a section heading for work experience, in English
# or Hebrew. Line-anchored on purpose: "hands-on experience in backend
# development" inside a summary must not count.
_EXPERIENCE_HEADING_RE = re.compile(
    r"^(?:(?:work|professional|employment|relevant|industry|practical)\s+)?"
    r"(?:experience|employment(?:\s+history)?|career\s+history|work\s+history)\s*:?\s*$"
    r"|^ניסיון(?:\s+(?:תעסוקתי|מקצועי|בעבודה|רלוונטי))?\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


class _ExperienceEstimate(BaseModel):
    years_of_experience: float | None = None


class OpenAINotConfiguredError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "OPENAI_API_KEY is not set. Set LLM_PROVIDER=ollama (the default) to use the "
            "free local model instead, or set OPENAI_API_KEY - see README.md."
        )


class OllamaUnavailableError(RuntimeError):
    def __init__(self, base_url: str, cause: Exception) -> None:
        super().__init__(
            f"Could not reach Ollama at {base_url}. Is `ollama serve` running, and has "
            f"the model been pulled (`ollama pull <model>`)? Original error: {cause}"
        )


class OpenAIExtractionError(RuntimeError):
    def __init__(self, cause: Exception) -> None:
        super().__init__(f"OpenAI structured extraction failed: {cause}")


class OllamaTimeoutError(RuntimeError):
    def __init__(self, timeout_seconds: int) -> None:
        super().__init__(
            f"Ollama did not respond within {timeout_seconds}s. CPU-only local-LLM inference "
            "can be very slow on some hardware - try a smaller model (OLLAMA_CHAT_MODEL), "
            "raise OLLAMA_TIMEOUT_SECONDS, or set LLM_PROVIDER=openai instead."
        )


def has_work_experience_section(text: str) -> bool:
    return _EXPERIENCE_HEADING_RE.search(text) is not None


def seniority_from_years(years: float | None) -> str | None:
    if years is None:
        return None
    if years < 1.5:
        return "junior"
    if years < 5:
        return "mid"
    return "senior"


def extract_structured_profile(normalized_text: str) -> StructuredCandidateProfile:
    settings = get_settings()
    if settings.llm_provider == "openai":
        return _extract_via_openai(
            normalized_text, settings.openai_api_key, settings.openai_chat_model
        )
    return _extract_via_ollama(
        normalized_text,
        settings.ollama_base_url,
        settings.ollama_chat_model,
        settings.ollama_timeout_seconds,
    )


def _extract_via_openai(
    normalized_text: str, api_key: str, model: str
) -> StructuredCandidateProfile:
    if not api_key:
        raise OpenAINotConfiguredError()
    client = OpenAI(api_key=api_key)

    try:
        completion = client.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": _FULL_PROMPT},
                {"role": "user", "content": normalized_text},
            ],
            response_format=StructuredCandidateProfile,
        )
    except (OpenAIError, ValidationError) as exc:
        # Bad key, rate limit, network, or a response that didn't fit the
        # schema - all secondary to the upload itself.
        raise OpenAIExtractionError(exc) from exc
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        parsed = StructuredCandidateProfile()
    return apply_deterministic_rules(parsed, normalized_text)


def _extract_via_ollama(
    normalized_text: str, base_url: str, model: str, timeout_seconds: int
) -> StructuredCandidateProfile:
    client = ollama.Client(host=base_url, timeout=timeout_seconds)
    lists = _ollama_json(
        client,
        model,
        _LIST_PROMPT,
        normalized_text,
        CandidateProfileLists,
        base_url,
        timeout_seconds,
    )
    profile = StructuredCandidateProfile(**lists.model_dump())
    if has_work_experience_section(normalized_text):
        estimate = _ollama_json(
            client,
            model,
            _YEARS_PROMPT,
            normalized_text,
            _ExperienceEstimate,
            base_url,
            timeout_seconds,
        )
        profile.years_of_experience = estimate.years_of_experience
    return apply_deterministic_rules(profile, normalized_text)


def _ollama_json[T: BaseModel](
    client: ollama.Client,
    model: str,
    system_prompt: str,
    user_text: str,
    schema: type[T],
    base_url: str,
    timeout_seconds: int,
) -> T:
    try:
        response = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            format=schema.model_json_schema(),
            options={"temperature": 0},
        )
    except httpx.TimeoutException as exc:
        raise OllamaTimeoutError(timeout_seconds) from exc
    except Exception as exc:  # noqa: BLE001 - translate any connection/runtime error uniformly
        raise OllamaUnavailableError(base_url, exc) from exc

    content = response.message.content
    if not content:
        return schema()
    try:
        return schema.model_validate_json(content)
    except ValidationError as exc:
        # Schema-constrained output is best effort on the model's side: a
        # small local model can still emit a wrong type or truncated JSON.
        # This step is secondary and must never sink the upload.
        raise OllamaUnavailableError(base_url, exc) from exc


def apply_deterministic_rules(
    profile: StructuredCandidateProfile, normalized_text: str
) -> StructuredCandidateProfile:
    """The part of the profile that never depends on a model - also what
    a CV gets when the local model is down or times out (see
    profile_service), so the dashboard still shows the level and skills.

    Experience level is decided here, not by the model (see the module
    docstring). Also guarantees a non-empty skills list: when the model
    leaves it out, the skill vocabulary the matcher uses fills it from the
    text, minus anything already listed under a more specific field."""
    if not has_work_experience_section(normalized_text):
        profile.years_of_experience = 0.0
        profile.seniority = "junior"
    else:
        profile.seniority = seniority_from_years(profile.years_of_experience) or profile.seniority
    if not profile.skills:
        listed = {
            item.casefold()
            for key in ("programming_languages", "frameworks", "databases", "cloud", "devops")
            for item in getattr(profile, key)
        }
        profile.skills = sorted(
            skill
            for skill in extract_skills_from_text(normalized_text)
            if skill.casefold() not in listed
        )
    return profile
