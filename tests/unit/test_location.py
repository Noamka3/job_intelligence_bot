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


@pytest.mark.parametrize("location_text", ["Lodz, Poland", "Lodi, CA", "Lodzkie"])
def test_lod_does_not_match_inside_other_place_names(location_text: str) -> None:
    """A substring match on "lod" used to classify Lodz, Poland as Israel."""
    assert classify_country(location_text) is None
    assert is_confidently_non_israeli("Lodz, Poland") is True


def test_usa_does_not_match_inside_jerusalem() -> None:
    assert classify_country("Jerusalem, Israel") == "Israel"
    assert is_confidently_non_israeli("Jerusalem") is False


def test_location_naming_israel_and_elsewhere_counts_as_israel() -> None:
    assert classify_country("Tel Aviv / New York") == "Israel"
    assert is_confidently_non_israeli("Tel Aviv / New York") is False


def test_sql_regex_uses_the_same_vocabulary_as_the_python_check() -> None:
    from app.services.jobs.location import (
        NON_ISRAEL_LOCATION_HINTS,
        NON_ISRAEL_LOCATION_SQL_REGEX,
    )

    for hint in NON_ISRAEL_LOCATION_HINTS:
        assert hint.replace(".", r"\.").replace(" ", r"\ ").replace("-", r"\-") in (
            NON_ISRAEL_LOCATION_SQL_REGEX
        ), hint
    assert r"\yusa\y" in NON_ISRAEL_LOCATION_SQL_REGEX


def test_is_confidently_non_israeli_false_for_unrecognized_text() -> None:
    """An unrecognized location must never be silently treated as
    "not Israel" - it should stay visible rather than risk hiding a real
    Israeli listing just because its city name isn't in the known list.
    """
    assert is_confidently_non_israeli("Some Unrecognized Town") is False
    assert is_confidently_non_israeli(None) is False
