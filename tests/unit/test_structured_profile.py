from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import ollama
import pytest

from app.core.config import Settings
from app.schemas.candidate import StructuredCandidateProfile
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
        programming_languages=["Python", "JavaScript"], seniority="junior"
    )
    fake_parse = _FakeParse(result=expected)
    monkeypatch.setattr(
        structured_profile, "OpenAI", lambda **kwargs: _FakeOpenAIClient(fake_parse, **kwargs)
    )

    result = _extract_via_openai("some resume text", api_key="sk-test", model="gpt-test")

    assert result == expected
    assert fake_parse.captured_kwargs["response_format"] is StructuredCandidateProfile


def test_openai_falls_back_when_unparsed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_parse = _FakeParse(result=None)
    monkeypatch.setattr(
        structured_profile, "OpenAI", lambda **kwargs: _FakeOpenAIClient(fake_parse, **kwargs)
    )

    result = _extract_via_openai("some resume text", api_key="sk-test", model="gpt-test")

    assert result == StructuredCandidateProfile()


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
    def __init__(self, content: str | None = None, error: Exception | None = None) -> None:
        self._content = content
        self._error = error
        self.captured_kwargs: dict[str, Any] = {}

    def chat(self, **kwargs: Any) -> _FakeOllamaResponse:
        self.captured_kwargs.update(kwargs)
        if self._error is not None:
            raise self._error
        return _FakeOllamaResponse(message=_FakeOllamaMessage(content=self._content))


def test_ollama_returns_parsed_result(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = StructuredCandidateProfile(programming_languages=["Go"], seniority="senior")
    fake_client = _FakeOllamaClient(content=expected.model_dump_json())
    monkeypatch.setattr(ollama, "Client", lambda **_kwargs: fake_client)

    result = _extract_via_ollama(
        "some resume text",
        base_url="http://localhost:11434",
        model="test-model",
        timeout_seconds=30,
    )

    assert result == expected
    assert fake_client.captured_kwargs["format"] == StructuredCandidateProfile.model_json_schema()


def test_ollama_falls_back_when_content_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeOllamaClient(content=None)
    monkeypatch.setattr(ollama, "Client", lambda **_kwargs: fake_client)

    result = _extract_via_ollama(
        "some resume text",
        base_url="http://localhost:11434",
        model="test-model",
        timeout_seconds=30,
    )

    assert result == StructuredCandidateProfile()


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
