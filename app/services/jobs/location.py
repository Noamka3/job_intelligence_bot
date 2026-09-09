"""Best-effort country classification from an ATS's free-text location
string (e.g. "Tel Aviv, IL", "Warsaw", "United States").

Spec §28: Israel is the initial market, and the architecture must not
hardcode Tel Aviv only nor block other countries being added later. This
returns UNKNOWN rather than guessing when a location can't be classified
confidently - an unmatched location should never be silently mislabeled
as "not Israel" and disappear from results.
"""

from __future__ import annotations

# Israeli city/region name variants actually seen across the real company
# sheet during Phase 4/5 verification, plus spec §28's explicit list.
# English and Hebrew. Not exhaustive - unmatched text falls back to
# UNKNOWN rather than a wrong guess.
_ISRAELI_PLACE_NAMES = {
    "tel aviv",
    "tel aviv-yafo",
    "tel-aviv",
    "herzliya",
    "herzliyya",
    "ramat gan",
    "petah tikva",
    "petach tikva",
    "haifa",
    "beer sheva",
    "be'er sheva",
    "beersheba",
    "rishon lezion",
    "rishon letzion",
    "rishon letsiyon",
    "netanya",
    "raanana",
    "ra'anana",
    "kfar saba",
    "modiin",
    "modi'in",
    "rehovot",
    "holon",
    "bnei brak",
    "ashdod",
    "ashkelon",
    "jerusalem",
    "nazareth",
    "kiryat ata",
    "kiryat gat",
    "kiryat bialik",
    "yokneam",
    "caesarea",
    "hod hasharon",
    "givatayim",
    "or yehuda",
    "airport city",
    "bat yam",
    "lod",
    "מרכז",
    "מרחב מרכז",
    "מחוז ירושלים",
    "נגב",
    "השרון",
    "תל אביב",
    "ירושלים",
    "חיפה",
    "באר שבע",
    "פתח תקווה",
    "רחובות",
}

_ISRAEL_TOKENS = {"israel", "ישראל"}

# Specific, unambiguous non-Israel signals seen in the real data - kept as
# an explicit allowlist (not e.g. every US state abbreviation) because
# "IL" alone is genuinely ambiguous (Illinois vs. the ISO code for
# Israel); city/country name matches above are checked first regardless.
# Exposed publicly so the /jobs API can build a SQL-level exclusion
# filter from the same set this module uses in Python.
NON_ISRAEL_LOCATION_HINTS = frozenset({
    "united states",
    "usa",
    "u.s.",
    "u.s.a.",
    "united kingdom",
    "london",
    "poland",
    "warsaw",
    "ukraine",
    "kyiv",
    "cyprus",
    "limassol",
    "boston",
    "washington",
    "chicago",
    "new york",
    "canada",
    "germany",
    "berlin",
    "france",
    "paris",
    "india",
    "remote - global",
})


def classify_country(location_text: str | None) -> str | None:
    """Returns "Israel" when confidently Israeli, None otherwise
    (covers both "confidently elsewhere" and "can't tell") - the
    JobPosting.country column, like the rest of ATS-sourced fields, is
    best left unset rather than populated with a guess (spec §22's
    "never invent" principle applies here too).
    """
    if not location_text:
        return None
    normalized = location_text.strip().lower()

    if any(place in normalized for place in _ISRAELI_PLACE_NAMES):
        return "Israel"
    if any(token in normalized for token in _ISRAEL_TOKENS):
        return "Israel"
    return None


def is_confidently_non_israeli(location_text: str | None) -> bool:
    """True only for an explicit, known non-Israel signal - used to
    support an "Israel-only" filter without silently hiding jobs whose
    location just wasn't recognized (those stay visible, unset country).
    """
    if not location_text:
        return False
    normalized = location_text.strip().lower()
    if classify_country(location_text) == "Israel":
        return False
    return any(hint in normalized for hint in NON_ISRAEL_LOCATION_HINTS)
