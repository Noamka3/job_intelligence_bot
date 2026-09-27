"""The one place that talks to TypeSafe's API.

One client per process, and every call goes through ask(), which turns
any SDK failure into None and a warning: a crawl or a rescore must never
depend on a network service being up. With TYPESAFE_API_KEY empty,
everything here answers None and nothing is sent anywhere.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from functools import lru_cache

from typesafe_sdk import (
    Choice,
    JSONContent,
    Noul,
    Score,
    SystemOneResponse,
    TypeSafeClient,
    TypeSafeError,
)

from app.core.config import get_settings

logger = logging.getLogger(__name__)

Question = Choice | Noul | Score

# Jev's accuracy drops as the state fills with text unrelated to the
# question, and a posting's tail is boilerplate; ~2,000 tokens of it is
# plenty for what is asked here.
MAX_STATE_CHARS = 8_000


def is_enabled() -> bool:
    return bool(get_settings().typesafe_api_key)


@lru_cache
def _client() -> TypeSafeClient:
    settings = get_settings()
    return TypeSafeClient(api_key=settings.typesafe_api_key, model=settings.jev_model)


def ask(state: JSONContent, questions: Mapping[str, Question]) -> SystemOneResponse | None:
    """One System One call, or None when Jev is off or the call failed."""
    if not is_enabled():
        return None
    try:
        return _client().system_one(state, questions)
    except TypeSafeError as exc:
        logger.warning("jev call failed", extra={"error_type": type(exc).__name__})
        return None
