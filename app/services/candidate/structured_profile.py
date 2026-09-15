"""One-time structured extraction from a normalized CV. Runs once per
resume upload (a rare event), never in a hot path - see spec §45 cost
control.

Two providers, selected by Settings.llm_provider:
- ollama (default): free, runs on this machine, no API key.
- openai: needs OPENAI_API_KEY.
Both use JSON-schema-constrained structured output so the result always
matches StructuredCandidateProfile's shape.
"""

from __future__ import annotations

import httpx
import ollama
from openai import OpenAI, OpenAIError
from pydantic import ValidationError

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
    "experience level, or null if unclear. Respond with JSON only."
)


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
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": normalized_text},
            ],
            response_format=StructuredCandidateProfile,
        )
    except (OpenAIError, ValidationError) as exc:
        # Bad key, rate limit, network, or a response that didn't fit the
        # schema - all secondary to the upload itself (spec §6).
        raise OpenAIExtractionError(exc) from exc
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        return StructuredCandidateProfile()
    return parsed


def _extract_via_ollama(
    normalized_text: str, base_url: str, model: str, timeout_seconds: int
) -> StructuredCandidateProfile:
    client = ollama.Client(host=base_url, timeout=timeout_seconds)
    try:
        response = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": normalized_text},
            ],
            format=StructuredCandidateProfile.model_json_schema(),
            options={"temperature": 0},
        )
    except httpx.TimeoutException as exc:
        raise OllamaTimeoutError(timeout_seconds) from exc
    except Exception as exc:  # noqa: BLE001 - translate any connection/runtime error uniformly
        raise OllamaUnavailableError(base_url, exc) from exc

    content = response.message.content
    if not content:
        return StructuredCandidateProfile()
    try:
        return StructuredCandidateProfile.model_validate_json(content)
    except ValidationError as exc:
        # Schema-constrained output is best effort on the model's side: a
        # small local model can still emit a wrong type or truncated JSON.
        # This step is secondary (spec §6) and must never sink the upload.
        raise OllamaUnavailableError(base_url, exc) from exc
