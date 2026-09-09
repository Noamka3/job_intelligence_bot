from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.schemas.candidate import StructuredCandidateProfile
from app.services.candidate import structured_profile


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


def test_extract_structured_profile_returns_parsed_result(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = StructuredCandidateProfile(
        programming_languages=["Python", "JavaScript"], seniority="junior"
    )
    fake_parse = _FakeParse(result=expected)
    monkeypatch.setattr(
        structured_profile,
        "OpenAI",
        lambda **kwargs: _FakeOpenAIClient(fake_parse, **kwargs),
    )

    result = structured_profile.extract_structured_profile("some resume text")

    assert result == expected
    assert fake_parse.captured_kwargs["response_format"] is StructuredCandidateProfile


def test_extract_structured_profile_falls_back_when_unparsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_parse = _FakeParse(result=None)
    monkeypatch.setattr(
        structured_profile,
        "OpenAI",
        lambda **kwargs: _FakeOpenAIClient(fake_parse, **kwargs),
    )

    result = structured_profile.extract_structured_profile("some resume text")

    assert result == StructuredCandidateProfile()
