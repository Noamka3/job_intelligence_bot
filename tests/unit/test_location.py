from __future__ import annotations

import pytest

from app.services.jobs.location import classify_country, is_confidently_non_israeli


@pytest.mark.parametrize(
    "location_text",
    [
        "Tel Aviv",
        "Tel Aviv, IL",
        "Tel Aviv-Yafo",
        "Petah Tikva, Israel",
        "Rishon LeTsiyon",
        "Herzliyya",
        "Rehovot",
        "Kiryat Ata",
        "מרחב מרכז",
        "מחוז ירושלים",
        "Israel",
    ],
)
def test_classify_country_recognizes_israeli_locations(location_text: str) -> None:
    assert classify_country(location_text) == "Israel"


@pytest.mark.parametrize(
    "location_text",
    [
        "United States",
        "Boston, MA",
        "Washington, D.C.",
        "Warsaw",
        "Kyiv",
        "Limassol",
        "London",
    ],
)
def test_classify_country_returns_none_for_non_israeli_locations(location_text: str) -> None:
    assert classify_country(location_text) is None


def test_classify_country_returns_none_for_unrecognized_text() -> None:
    assert classify_country("Some Unrecognized Town") is None


def test_classify_country_returns_none_for_empty_input() -> None:
    assert classify_country(None) is None
    assert classify_country("") is None


@pytest.mark.parametrize(
    "location_text",
    ["United States", "Boston, MA", "Washington, D.C.", "Warsaw", "Kyiv", "Limassol", "London"],
)
def test_is_confidently_non_israeli_true_for_known_foreign_locations(location_text: str) -> None:
    assert is_confidently_non_israeli(location_text) is True


@pytest.mark.parametrize(
    "location_text",
    ["Tel Aviv", "Tel Aviv, IL", "Petah Tikva, Israel", "מרחב מרכז"],
)
def test_is_confidently_non_israeli_false_for_israeli_locations(location_text: str) -> None:
    assert is_confidently_non_israeli(location_text) is False


def test_is_confidently_non_israeli_false_for_unrecognized_text() -> None:
    """An unrecognized location must never be silently treated as
    "not Israel" - it should stay visible rather than risk hiding a real
    Israeli listing just because its city name isn't in the known list.
    """
    assert is_confidently_non_israeli("Some Unrecognized Town") is False
    assert is_confidently_non_israeli(None) is False
