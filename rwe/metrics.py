"""Evaluation metrics (Section 7.3).

Accuracy
    :func:`auc`, :func:`mean_rank`, :func:`hit_rate_at_k`,
    :func:`precision_at_k`.

Long-tail diversity
    :func:`gini_diversity`, :func:`average_item_degree`,
    :func:`personalization`, :func:`surprisal`.

Ideological diversity
    :func:`rec_range_at_k`, :func:`ks_statistic`.

Throughout, ``recommendations`` is an integer ``(n_users, top_k)`` array of
ranked item indices (``-1`` padding for empty slots), as returned by the
recommenders; ``test_pos`` / ``train_pos`` are lists (indexed like the user
batch) of item-index arrays.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import ks_2samp, rankdata


# --------------------------------------------------------------------------- #
# Accuracy
# --------------------------------------------------------------------------- #
def auc(score_rows: np.ndarray, test_pos, train_pos) -> float:
    """Mean per-user implicit AUC.

    For each user the candidate set excludes training items; AUC is the
    probability that a held-out positive item is ranked above a random
    non-interacted item (Mann-Whitney U statistic).
    """
    vals = []
    n_items = score_rows.shape[1]
    for row in range(score_rows.shape[0]):
        pos = np.asarray(test_pos[row], dtype=int)
        if pos.size == 0:
            continue
        mask = np.ones(n_items, dtype=bool)
        mask[np.asarray(train_pos[row], dtype=int)] = False
        cand = np.flatnonzero(mask)
        if cand.size <= pos.size:
            continue
        ranks = rankdata(score_rows[row, cand])          # 1 = lowest score
        pos_set = set(pos.tolist())
        pos_local = np.array([idx for idx, c in enumerate(cand) if c in pos_set])
        n_pos, n_neg = pos_local.size, cand.size - pos_local.size
        if n_pos == 0 or n_neg == 0:
            continue
        rank_sum = ranks[pos_local].sum()
        vals.append((rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))
    return float(np.mean(vals)) if vals else float("nan")


def mean_rank(score_rows: np.ndarray, test_pos, train_pos) -> float:
    """Mean 1-indexed rank of held-out items among candidates (lower better)."""
    vals = []
    n_items = score_rows.shape[1]
    for row in range(score_rows.shape[0]):
        pos = np.asarray(test_pos[row], dtype=int)
        if pos.size == 0:
            continue
        mask = np.ones(n_items, dtype=bool)
        mask[np.asarray(train_pos[row], dtype=int)] = False
        cand = np.flatnonzero(mask)
        order = np.argsort(-score_rows[row, cand])
        rank_of = np.empty(cand.size, dtype=int)
        rank_of[order] = np.arange(1, cand.size + 1)
        local = {c: k for k, c in enumerate(cand)}
        for p in pos:
            if p in local:
                vals.append(rank_of[local[p]])
    return float(np.mean(vals)) if vals else float("nan")


def hit_rate_at_k(recommendations: np.ndarray, test_pos) -> float:
    """Recall-style hit rate: mean over users of ``|topk ∩ test| / |test|``."""
    vals = []
    for row in range(recommendations.shape[0]):
        pos = set(int(x) for x in np.asarray(test_pos[row]))
        if not pos:
            continue
        recs = [int(x) for x in recommendations[row] if x >= 0]
        vals.append(len(pos & set(recs)) / len(pos))
    return float(np.mean(vals)) if vals else float("nan")


def precision_at_k(recommendations: np.ndarray, test_pos, k: int | None = None) -> float:
    """Mean over users of ``|topk ∩ test| / k``."""
    k = k or recommendations.shape[1]
    vals = []
    for row in range(recommendations.shape[0]):
        pos = set(int(x) for x in np.asarray(test_pos[row]))
        if not pos:
            continue
        recs = [int(x) for x in recommendations[row][:k] if x >= 0]
        vals.append(len(pos & set(recs)) / k)
    return float(np.mean(vals)) if vals else float("nan")


# --------------------------------------------------------------------------- #
# Long-tail diversity
# --------------------------------------------------------------------------- #
def _recommendation_counts(recommendations: np.ndarray, n_items: int) -> np.ndarray:
    counts = np.zeros(n_items, dtype=float)
    flat = recommendations[recommendations >= 0]
    np.add.at(counts, flat, 1.0)
    return counts


def gini_diversity(recommendations: np.ndarray, n_items: int) -> float:
    """Aggregate diversity as ``1 - Gini`` of the recommendation-frequency
    distribution over the whole catalog (higher = items spread more evenly =
    more diverse), following the aggregate-diversity measures of refs. [1, 40].
    """
    counts = np.sort(_recommendation_counts(recommendations, n_items))
    total = counts.sum()
    if total == 0:
        return float("nan")
    n = counts.size
    idx = np.arange(1, n + 1)
    gini = (2.0 * np.sum(idx * counts) / (n * total)) - (n + 1.0) / n
    return float(1.0 - gini)


def average_item_degree(recommendations: np.ndarray, item_degrees) -> float:
    """Mean popularity (training degree) of recommended items (lower = long-tail)."""
    item_degrees = np.asarray(item_degrees, dtype=float)
    flat = recommendations[recommendations >= 0]
    return float(item_degrees[flat].mean()) if flat.size else float("nan")


def personalization(recommendations: np.ndarray, n_items: int,
                    max_pairs: int = 5000, seed: int = 0) -> float:
    """``1 - mean pairwise cosine`` between users' top-k lists (higher = more
    personalised / diverse across users)."""
    rows = [set(int(x) for x in r if x >= 0) for r in recommendations]
    rows = [r for r in rows if r]
    n = len(rows)
    if n < 2:
        return float("nan")
    rng = np.random.default_rng(seed)
    sims = []
    all_pairs = n * (n - 1) // 2
    if all_pairs <= max_pairs:
        pairs = ((i, j) for i in range(n) for j in range(i + 1, n))
    else:
        pairs = ((int(a), int(b)) for a, b in
                 rng.integers(0, n, size=(max_pairs, 2)) if a != b)
    for i, j in pairs:
        inter = len(rows[i] & rows[j])
        sims.append(inter / np.sqrt(len(rows[i]) * len(rows[j])))
    return float(1.0 - np.mean(sims)) if sims else float("nan")


def surprisal(recommendations: np.ndarray, item_degrees, n_users: int) -> float:
    """Mean self-information ``-log2(pop_i / n_users)`` of recommended items
    (higher = more novel / surprising)."""
    item_degrees = np.asarray(item_degrees, dtype=float)
    flat = recommendations[recommendations >= 0]
    if flat.size == 0:
        return float("nan")
    prob = np.clip(item_degrees[flat] / max(n_users, 1), 1e-12, 1.0)
    return float(np.mean(-np.log2(prob)))


# --------------------------------------------------------------------------- #
# Ideological diversity
# --------------------------------------------------------------------------- #
def rec_range_at_k(recommendations: np.ndarray, item_positions) -> float:
    """``RecRange@k`` (Section 7.5): mean over users of the spread
    ``max(pos) - min(pos)`` of ideological positions in the top-k list."""
    item_positions = np.asarray(item_positions, dtype=float)
    vals = []
    for row in recommendations:
        items = row[row >= 0]
        if items.size == 0:
            continue
        pos = item_positions[items]
        vals.append(pos.max() - pos.min())
    return float(np.mean(vals)) if vals else float("nan")


def recommended_positions(recommendations: np.ndarray, item_positions) -> np.ndarray:
    """Flat array of ideological positions of all recommended items."""
    item_positions = np.asarray(item_positions, dtype=float)
    return item_positions[recommendations[recommendations >= 0]]


def ks_statistic(recommendations_a: np.ndarray, recommendations_b: np.ndarray,
                 item_positions):
    """Kolmogorov-Smirnov test between the ideological-position distributions of
    two recommenders' top-k items (Section 7.5).  Returns ``(statistic, pvalue)``."""
    a = recommended_positions(recommendations_a, item_positions)
    b = recommended_positions(recommendations_b, item_positions)
    if a.size == 0 or b.size == 0:
        return float("nan"), float("nan")
    res = ks_2samp(a, b)
    return float(res.statistic), float(res.pvalue)
