from app.services.jev.agreement import pairwise_accuracy


def test_pairwise_accuracy_is_the_share_of_pairs_ranked_positive_first() -> None:
    assert pairwise_accuracy([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert pairwise_accuracy([0.1], [0.9]) == 0.0
    assert pairwise_accuracy([0.5], [0.5]) == 0.5  # a tie is a coin flip
    assert pairwise_accuracy([0.9, 0.3], [0.5]) == 0.5
    assert pairwise_accuracy([], [0.5]) is None
