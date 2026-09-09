"""EmbeddingProvider abstraction.

Nothing outside this package talks to a specific embedding vendor's SDK
directly - candidate profiles, target roles, and (from Phase 4 on) job
postings all go through this interface, so the provider is replaceable
without touching business logic.
"""

from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    """Turns text into a fixed-length vector for pgvector storage."""

    dimensions: int

    def embed_one(self, text: str) -> list[float]:
        """Embed a single piece of text (e.g. a full CV or job description)."""
        ...

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts in one call where the provider supports it."""
        ...
