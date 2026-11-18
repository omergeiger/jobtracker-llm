"""Small, dependency-free ranking and agreement metrics (verified in tests/test_metrics.py).

Labels are graded relevance on a 1-5 scale (see evals/common.py RUBRIC);
a post counts as relevant at 4 (good fit) or 5 (perfect match).
"""

import math
import random
from collections import Counter
from itertools import combinations
from typing import Callable, Sequence

RELEVANT = 4
SCALE = (1, 2, 3, 4, 5)


def kendall_tau_b(x: Sequence[float], y: Sequence[float]) -> float:
    """Kendall's tau-b: (concordant - discordant) pairs, corrected for ties in either input.

    1 = same order, -1 = reversed, 0 = no relation (what a random order gets on average).
    """
    concordant = discordant = ties_x = ties_y = 0
    for i, j in combinations(range(len(x)), 2):
        dx, dy = x[i] - x[j], y[i] - y[j]
        if dx == 0 and dy == 0:
            ties_x += 1
            ties_y += 1
        elif dx == 0:
            ties_x += 1
        elif dy == 0:
            ties_y += 1
        elif (dx > 0) == (dy > 0):
            concordant += 1
        else:
            discordant += 1
    n0 = len(x) * (len(x) - 1) / 2
    denom = math.sqrt((n0 - ties_x) * (n0 - ties_y))
    return (concordant - discordant) / denom if denom else 0.0


def ndcg_at_k(labels_in_ranked_order: Sequence[int], k: int) -> float:
    """NDCG@k with gain 2^(label-1) - 1, so labels 1..5 are worth 0, 1, 3, 7, 15."""
    def dcg(labels):
        return sum((2 ** (rel - 1) - 1) / math.log2(i + 2) for i, rel in enumerate(labels[:k]))
    ideal = dcg(sorted(labels_in_ranked_order, reverse=True))
    return dcg(list(labels_in_ranked_order)) / ideal if ideal else 0.0


def precision_at_k(labels_in_ranked_order: Sequence[int], k: int) -> float:
    """Share of the top k that are relevant (label >= 4)."""
    top = list(labels_in_ranked_order)[:k]
    return sum(1 for rel in top if rel >= RELEVANT) / len(top) if top else 0.0


def recall_above(scores: Sequence[float], labels: Sequence[int], threshold: float) -> float:
    """Share of relevant posts (label >= 4) scored at or above the review threshold."""
    relevant = [s for s, rel in zip(scores, labels) if rel >= RELEVANT]
    return sum(1 for s in relevant if s >= threshold) / len(relevant) if relevant else 0.0


def weighted_kappa(a: Sequence[int], b: Sequence[int], quadratic: bool = True,
                   categories: Sequence[int] = SCALE) -> float:
    """Cohen's kappa between two raters; quadratic weights by default.

    With quadratic weights, a disagreement of d grades costs (d / (K-1))^2 of a full
    disagreement, so near misses (4 vs 5) barely count and 1 vs 5 counts fully.
    quadratic=False gives plain (unweighted) Cohen's kappa.
    """
    n, span = len(a), max(categories) - min(categories)

    def w(i, j):
        if quadratic:
            return ((i - j) / span) ** 2
        return 0.0 if i == j else 1.0

    observed = sum(w(i, j) for i, j in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[i] * cb[j] * w(i, j) for i in categories for j in categories) / (n * n)
    return 1 - observed / expected if expected else 1.0


def random_baseline(labels: Sequence[int], metric: Callable[[list[int]], float],
                    trials: int = 2000, seed: int = 0) -> float:
    """Expected value of a ranking metric when the order is random."""
    rng = random.Random(seed)
    total = 0.0
    shuffled = list(labels)
    for _ in range(trials):
        rng.shuffle(shuffled)
        total += metric(shuffled)
    return total / trials
