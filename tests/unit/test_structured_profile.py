from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import ollama
import pytest

from app.core.config import Settings
from app.schemas.candidate import CandidateProfileLists, StructuredCandidateProfile
from app.services.candidate import structured_profile
from app.services.candidate.structured_profile import (
    OllamaTimeoutError,
    OllamaUnavailableError,
    OpenAINotConfiguredError,
    _extract_via_ollama,
    _extract_via_openai,
)

# --- OpenAI path -----------------------------------------------------------


@dataclass
class _FakeMessage:
    parsed: StructuredCandidateProfile | None


@dataclass
class _FakeChoice:
    message: _FakeMessage


@dataclass
class _FakeCompletion:
    choices: list[_FakeChoice]


@dataclass
class _FakeParse:
    result: StructuredCandidateProfile | None
    captured_kwargs: dict[str, Any] = field(default_factory=dict)

    def __call__(self, **kwargs: Any) -> _FakeCompletion:
        self.captured_kwargs.update(kwargs)
        return _FakeCompletion(choices=[_FakeChoice(message=_FakeMessage(parsed=self.result))])


class _FakeCompletions:
    def __init__(self, parse: _FakeParse) -> None:
        self.parse = parse


class _FakeChat:
    def __init__(self, parse: _FakeParse) -> None:
        self.completions = _FakeCompletions(parse)


class _FakeOpenAIClient:
    def __init__(self, parse: _FakeParse, **_kwargs: Any) -> None:
        self.chat = _FakeChat(parse)


def test_openai_returns_parsed_result(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = StructuredCandidateProfile(
        programming_languages=["Python", "JavaScript"], seniority="senior", years_of_experience=8
    )
    fake_parse = _FakeParse(result=expected)
    monkeypatch.setattr(
        structured_profile, "OpenAI", lambda **kwargs: _FakeOpenAIClient(fake_parse, **kwargs)
    )

    result = _extract_via_openai("EXPERIENCE\nAcme, 2016-2024", api_key="sk-test", model="gpt-test")

    assert result == expected
    assert fake_parse.captured_kwargs["response_format"] is StructuredCandidateProfile


def test_openai_falls_back_when_unparsed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_parse = _FakeParse(result=None)
    monkeypatch.setattr(
        structured_profile, "OpenAI", lambda **kwargs: _FakeOpenAIClient(fake_parse, **kwargs)
    )

    result = _extract_via_openai("some resume text", api_key="sk-test", model="gpt-test")

    assert result == StructuredCandidateProfile(years_of_experience=0.0, seniority="junior")


def test_openai_raises_when_key_missing() -> None:
    with pytest.raises(OpenAINotConfiguredError):
        _extract_via_openai("some resume text", api_key="", model="gpt-test")


# --- Ollama path -------------------------------------------------------


@dataclass
class _FakeOllamaMessage:
    content: str | None


@dataclass
class _FakeOllamaResponse:
    message: _FakeOllamaMessage


class _FakeOllamaClient:
    """Answers each chat() call with the next content in `contents`."""

    def __init__(self, *contents: str | None, error: Exception | None = None) -> None:
        self._contents = list(contents)
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def chat(self, **kwargs: Any) -> _FakeOllamaResponse:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        content = self._contents.pop(0) if self._contents else None
        return _FakeOllamaResponse(message=_FakeOllamaMessage(content=content))

    @property
    def captured_kwargs(self) -> dict[str, Any]:
        return self.calls[-1]


def test_ollama_copies_the_lists_and_a_cv_without_a_work_section_is_a_junior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lists come from the model; the experience level does not - the
    3B model called a fresh graduate 'mid, 4 years' on the real CV."""
    lists = CandidateProfileLists(
        programming_languages=["Go"], frameworks=["Gin"], skills=["REST APIs"]
    )
    fake_client = _FakeOllamaClient(lists.model_dump_json())
    monkeypatch.setattr(ollama, "Client", lambda **_kwargs: fake_client)

    result = _extract_via_ollama(
        "SUMMARY\nGraduate.\nEDUCATION\nB.Sc. 2022-2026\nPROJECTS\nSomething in Go and Gin",
        base_url="http://localhost:11434",
        model="test-model",
        timeout_seconds=30,
    )

    assert result.programming_languages == ["Go"]
    assert result.frameworks == ["Gin"]
    assert result.skills == ["REST APIs"]
    assert result.years_of_experience == 0.0
    assert result.seniority == "junior"
    assert len(fake_client.calls) == 1  # no years estimate without a work section
    assert fake_client.calls[0]["format"] == CandidateProfileLists.model_json_schema()
    assert "programming_languages" in fake_client.calls[0]["messages"][0]["content"]


def test_ollama_estimates_years_only_when_the_cv_has_a_work_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lists = CandidateProfileLists(programming_languages=["Go"])
    fake_client = _FakeOllamaClient(lists.model_dump_json(), '{"years_of_experience": 3}')
    monkeypatch.setattr(ollama, "Client", lambda **_kwargs: fake_client)

    result = _extract_via_ollama(
        "PROFESSIONAL EXPERIENCE\nAcme Ltd, Backend Developer, 2021-2024",
        base_url="http://localhost:11434",
        model="test-model",
        timeout_seconds=30,
    )

    assert len(fake_client.calls) == 2
    assert result.years_of_experience == 3.0
    assert result.seniority == "mid"


def test_ollama_fills_skills_from_the_vocabulary_when_the_model_leaves_them_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lists = CandidateProfileLists(programming_languages=["Python"])
    fake_client = _FakeOllamaClient(lists.model_dump_json())
    monkeypatch.setattr(ollama, "Client", lambda **_kwargs: fake_client)

    result = _extract_via_ollama(
        "SKILLS\nLanguages: Python\nTools: Docker, Git, Linux",
        base_url="http://localhost:11434",
        model="test-model",
        timeout_seconds=30,
    )

    assert "docker" in result.skills
    assert "python" not in result.skills  # already listed under programming_languages


def test_list_fields_accept_objects_and_bare_strings_from_a_sloppy_model() -> None:
    """Seen live: education came back as objects, not strings."""
    profile = StructuredCandidateProfile.model_validate(
        {
            "education": [{"degree": "B.Sc.", "institution": "SCE", "years": "2022-2026"}],
            "projects": "DocuGuard",
            "skills": None,
        }
    )
    assert profile.education == ["B.Sc., SCE, 2022-2026"]
    assert profile.projects == ["DocuGuard"]
    assert profile.skills == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("EXPERIENCE\nAcme", True),
        ("Work Experience:\nAcme", True),
        ("ניסיון תעסוקתי\nחברה", True),
        ("SUMMARY\nhands-on experience in backend development\nPROJECTS", False),
        ("EDUCATION\nB.Sc.\nMILITARY SERVICE\nIDF", False),
    ],
)
def test_work_experience_section_detection(text: str, expected: bool) -> None:
    assert structured_profile.has_work_experience_section(text) is expected


@pytest.mark.parametrize(
    ("years", "expected"),
    [(None, None), (0, "junior"), (1, "junior"), (2, "mid"), (4.5, "mid"), (5, "senior")],
)
def test_seniority_from_years(years: float | None, expected: str | None) -> None:
    assert structured_profile.seniority_from_years(years) == expected


def test_ollama_falls_back_when_content_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeOllamaClient(None)
    monkeypatch.setattr(ollama, "Client", lambda **_kwargs: fake_client)

    result = _extract_via_ollama(
        "some resume text",
        base_url="http://localhost:11434",
        model="test-model",
        timeout_seconds=30,
    )

    # Nothing from the model; the rule still decides the experience level.
    assert result == StructuredCandidateProfile(years_of_experience=0.0, seniority="junior")


def test_ollama_wraps_connection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeOllamaClient(error=ConnectionError("refused"))
    monkeypatch.setattr(ollama, "Client", lambda **_kwargs: fake_client)

    with pytest.raises(OllamaUnavailableError):
        _extract_via_ollama(
            "some resume text",
            base_url="http://localhost:11434",
            model="test-model",
            timeout_seconds=30,
        )


def test_ollama_wraps_timeout_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeOllamaClient(error=httpx.ReadTimeout("timed out"))
    monkeypatch.setattr(ollama, "Client", lambda **_kwargs: fake_client)

    with pytest.raises(OllamaTimeoutError):
        _extract_via_ollama(
            "some resume text",
            base_url="http://localhost:11434",
            model="test-model",
            timeout_seconds=30,
        )


# --- dispatch ------------------------------------------------------------


def test_extract_structured_profile_dispatches_by_settings_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _fake_openai(text: str, api_key: str, model: str) -> StructuredCandidateProfile:
        calls.append("openai")
        return StructuredCandidateProfile()

    def _fake_ollama(
        text: str, base_url: str, model: str, timeout_seconds: int
    ) -> StructuredCandidateProfile:
        calls.append("ollama")
        return StructuredCandidateProfile()

    monkeypatch.setattr(structured_profile, "_extract_via_openai", _fake_openai)
    monkeypatch.setattr(structured_profile, "_extract_via_ollama", _fake_ollama)

    monkeypatch.setattr(
        structured_profile,
        "get_settings",
        lambda: Settings(_env_file=None, llm_provider="openai", openai_api_key="sk-test"),
    )
    structured_profile.extract_structured_profile("text")
    assert calls == ["openai"]

    calls.clear()
    monkeypatch.setattr(
        structured_profile, "get_settings", lambda: Settings(_env_file=None, llm_provider="ollama")
    )
    structured_profile.extract_structured_profile("text")
    assert calls == ["ollama"]
