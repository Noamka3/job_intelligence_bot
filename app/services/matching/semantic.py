"""Cosine similarity between two already-stored embeddings.

Component-level semantic scores (candidate_semantic_score,
intent_semantic_score) are legitimately raw-ish similarity - spec §8 says
not to use embedding similarity as the *final* user-facing score, not
that it can't be one input among several. Clamped to [0,1]: real text
embeddings for related professional content essentially never produce a
meaningfully negative cosine similarity in practice, so a negative result
is treated as "not similar" (0) rather than preserved.
"""

from __future__ import annotations

import math


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))
