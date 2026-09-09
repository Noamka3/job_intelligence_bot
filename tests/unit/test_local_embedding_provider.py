"""Exercises the real fastembed model (cached locally after first
download - see LocalEmbeddingProvider's docstring). Not mocked: this is
the one component where testing the real thing matters more than testing
our own thin wrapper around it - a mock could never catch "the model name
changed" or "fastembed's output shape changed".
"""

from __future__ import annotations

import math

from app.models.constants import EMBEDDING_DIM
from app.services.embeddings.local_provider import LocalEmbeddingProvider


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b)


def test_embed_one_returns_configured_dimension() -> None:
    provider = LocalEmbeddingProvider()
    vector = provider.embed_one("Junior Software Engineer, Python, REST APIs")

    assert len(vector) == EMBEDDING_DIM == provider.dimensions
    assert all(isinstance(v, float) for v in vector)


def test_embed_many_matches_embed_one_count() -> None:
    provider = LocalEmbeddingProvider()
    vectors = provider.embed_many(["Junior Software Engineer", "Senior Data Scientist"])

    assert len(vectors) == 2
    assert all(len(v) == EMBEDDING_DIM for v in vectors)


def test_semantically_similar_text_scores_higher_than_dissimilar() -> None:
    provider = LocalEmbeddingProvider()
    junior_swe = provider.embed_one("Junior Software Engineer - Python, REST APIs, Docker")
    similar = provider.embed_one("Entry level Backend Developer - Python and web APIs")
    unrelated = provider.embed_one("Senior Pastry Chef - artisan bread and cake decoration")

    assert _cosine_similarity(junior_swe, similar) > _cosine_similarity(junior_swe, unrelated)
