from __future__ import annotations

import math

from app.services.matching.semantic import cosine_similarity


def test_identical_vectors_score_one() -> None:
    vector = [0.1, 0.2, 0.3, 0.4]
    assert math.isclose(cosine_similarity(vector, vector), 1.0, rel_tol=1e-9)


def test_orthogonal_vectors_score_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_opposite_vectors_clamp_to_zero_not_negative() -> None:
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == 0.0


def test_zero_vector_scores_zero() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
