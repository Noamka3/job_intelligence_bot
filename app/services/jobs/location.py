"""Best-effort country classification from an ATS's free-text location
string (e.g. "Tel Aviv, IL", "Warsaw", "United States").

Spec §28: Israel is the initial market, and the architecture must not
hardcode Tel Aviv only nor block other countries being added later. This
returns UNKNOWN rather than guessing when a location can't be classified
confidently - an unmatched location should never be silently mislabeled
as "not Israel" and disappear from results.

All matching is on word boundaries, never bare substrings: "Lod" must not
match "Lodz, Poland", and "USA" must not match "JerUSAlem". The same
vocabulary is exported as a Postgres regex so the /jobs API filter can't
drift from what this module decides in Python.
"""

from __future__ import annotations

import re

# Israeli city/region name variants actually seen across the real company
# sheet during Phase 4/5 verification, plus spec §28's explicit list.
# English and Hebrew. Not exhaustive - unmatched text falls back to
# UNKNOWN rather than a wrong guess.
_ISRAELI_PLACE_NAMES = {
    "tel aviv",
    "tel aviv-yafo",
    "tel-aviv",
    "tlv",
    "herzliya",
    "herzliyya",
    "herzelia",
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
    "הרצליה",
    "רמת גן",
}

_ISRAEL_TOKENS = {"israel", "ישראל"}

# Specific, unambiguous non-Israel signals seen in the real data - kept as
# an explicit allowlist (not e.g. every US state abbreviation) because
# "IL" alone is genuinely ambiguous (Illinois vs. the ISO code for
# Israel); city/country name matches above are checked first regardless.
NON_ISRAEL_LOCATION_HINTS = frozenset(
    {
        # countries / regions
        "united states",
        "usa",
        "u.s.",
        "u.s.a.",
        "united kingdom",
        "england",
        "ireland",
        "germany",
        "france",
        "spain",
        "portugal",
        "italy",
        "netherlands",
        "belgium",
        "switzerland",
        "austria",
        "sweden",
        "denmark",
        "norway",
        "finland",
        "poland",
        "ukraine",
        "armenia",
        "georgia",
        "romania",
        "bulgaria",
        "serbia",
        "greece",
        "cyprus",
        "turkey",
        "india",
        "singapore",
        "japan",
        "china",
        "south korea",
        "australia",
        "canada",
        "mexico",
        "brazil",
        "argentina",
        "colombia",
        "south africa",
        "uae",
        "united arab emirates",
        # cities (unambiguous ones only - never "IL"-style codes)
        "london",
        "dublin",
        "berlin",
        "munich",
        "paris",
        "madrid",
        "barcelona",
        "lisbon",
        "milan",
        "amsterdam",
        "brussels",
        "zurich",
        "vienna",
        "stockholm",
        "copenhagen",
        "oslo",
        "helsinki",
        "warsaw",
        "krakow",
        "kyiv",
        "yerevan",
        "tbilisi",
        "bucharest",
        "sofia",
        "belgrade",
        "athens",
        "prague",
        "limassol",
        "nicosia",
        "istanbul",
        "bangalore",
        "bengaluru",
        "tokyo",
        "sydney",
        "melbourne",
        "toronto",
        "vancouver",
        "dubai",
        "new york",
        "boston",
        "washington",
        "chicago",
        "san francisco",
        "san jose",
        "sunnyvale",
        "palo alto",
        "mountain view",
        "santa clara",
        "seattle",
        "austin",
        "denver",
        "los angeles",
        "san diego",
        "atlanta",
        "dallas",
        "houston",
        "miami",
        "phoenix",
        "portland",
        "raleigh",
        "salt lake city",
        "california",
        "texas",
        "arizona",
        "oregon",
        "colorado",
        "florida",
        "massachusetts",
        "virginia",
        "new jersey",
        "north carolina",
        # remote-elsewhere phrasings
        "remote - global",
        "remote us",
        "remote - us",
        "us remote",
        "remote usa",
        "remote, us",
        "remote (us)",
        "remote - europe",
        "remote europe",
        "remote, europe",
        "emea",
    }
)


def _boundary_pattern(term: str, boundary: str) -> str:
    """`term` as a regex anchored on word boundaries - only where the term
    itself starts/ends with a word character, since there is no boundary
    to anchor against after a trailing "." ("u.s.")."""
    escaped = re.escape(term)
    prefix = boundary if term[0].isalnum() else ""
    suffix = boundary if term[-1].isalnum() else ""
    return f"{prefix}{escaped}{suffix}"


def _alternation(terms: set[str] | frozenset[str], boundary: str) -> str:
    # Longest first so "tel aviv-yafo" wins over "tel aviv" inside it.
    return "|".join(
        _boundary_pattern(term, boundary) for term in sorted(terms, key=len, reverse=True)
    )


_ISRAEL_PATTERN = re.compile(
    _alternation(_ISRAELI_PLACE_NAMES | _ISRAEL_TOKENS, r"\b"), re.IGNORECASE
)
_NON_ISRAEL_PATTERN = re.compile(_alternation(NON_ISRAEL_LOCATION_HINTS, r"\b"), re.IGNORECASE)

# The same non-Israel vocabulary as a Postgres (ARE) regex - \y is
# Postgres's word boundary. Match it case-insensitively (~*).
NON_ISRAEL_LOCATION_SQL_REGEX = _alternation(NON_ISRAEL_LOCATION_HINTS, r"\y")


def classify_country(location_text: str | None) -> str | None:
    """Returns "Israel" when confidently Israeli, None otherwise
    (covers both "confidently elsewhere" and "can't tell") - the
    JobPosting.country column, like the rest of ATS-sourced fields, is
    best left unset rather than populated with a guess (spec §22's
    "never invent" principle applies here too).
    """
    if not location_text:
        return None
    if _ISRAEL_PATTERN.search(location_text):
        return "Israel"
    return None


def is_confidently_non_israeli(location_text: str | None) -> bool:
    """True only for an explicit, known non-Israel signal - used to
    support an "Israel-only" filter without silently hiding jobs whose
    location just wasn't recognized (those stay visible, unset country).
    A location naming both ("Tel Aviv / New York") counts as Israel.
    """
    if not location_text:
        return False
    if classify_country(location_text) == "Israel":
        return False
    return _NON_ISRAEL_PATTERN.search(location_text) is not None
