"""The eval metrics themselves are verified on hand-computed toy inputs."""

import math

import pytest

from evals.metrics import (
    kendall_tau_b,
    ndcg_at_k,
    precision_at_k,
    random_baseline,
    recall_above,
    weighted_kappa,
)


def test_kendall_tau_b_extremes_and_ties():
    assert kendall_tau_b([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert kendall_tau_b([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    # pairs: (1,2) tied in y, (1,3) and (2,3) concordant -> 2 / sqrt(3 * 2)
    assert kendall_tau_b([1, 2, 3], [1, 1, 2]) == pytest.approx(2 / math.sqrt(6))


def test_ndcg():
    assert ndcg_at_k([5, 3, 1], 3) == pytest.approx(1.0)
    # gains: label 5 -> 15, 3 -> 3, 1 -> 0
    worst = ndcg_at_k([1, 3, 5], 3)
    expected = (3 / math.log2(3) + 15 / math.log2(4)) / (15 + 3 / math.log2(3))
    assert worst == pytest.approx(expected)
    assert ndcg_at_k([1, 1, 1], 3) == 0.0


def test_precision_and_recall_use_label_4_and_up():
    assert precision_at_k([5, 4, 3, 1], 2) == 1.0
    assert precision_at_k([5, 3, 2, 4], 4) == 0.5
    assert recall_above([90, 40, 70, 10], [5, 4, 3, 1], threshold=50) == 0.5


def test_weighted_kappa():
    assert weighted_kappa([1, 2, 3, 5], [1, 2, 3, 5]) == pytest.approx(1.0)
    assert weighted_kappa([1, 5], [5, 1]) == pytest.approx(-1.0)
    # Two near misses (1 vs 2, 5 vs 4): plain kappa calls this weak agreement,
    # quadratic weighting sees it as nearly perfect. Hand-computed:
    #   plain:     (0.5 - 0.25) / 0.75            = 0.333
    #   quadratic: 1 - (1/32) / (6.5/16)          = 0.923
    a, b = [1, 1, 5, 5], [1, 2, 5, 4]
    assert weighted_kappa(a, b, quadratic=False) == pytest.approx(1 / 3)
    assert weighted_kappa(a, b) == pytest.approx(1 - (1 / 32) / (6.5 / 16))


def test_random_baseline_matches_expectation():
    labels = [5, 1, 1, 1]
    # P(the one relevant post lands in top 1) = 1/4
    assert random_baseline(labels, lambda l: precision_at_k(l, 1), trials=20000) == pytest.approx(0.25, abs=0.02)
    assert random_baseline([5, 4, 3, 2, 1], lambda l: kendall_tau_b(l, [5, 4, 3, 2, 1])) == pytest.approx(0.0, abs=0.03)
