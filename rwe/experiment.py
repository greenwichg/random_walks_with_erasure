"""Experiment runner: evaluate recommenders and grid-search hyper-parameters.

Ties together :mod:`rwe.data`, the recommenders and :mod:`rwe.metrics` to
reproduce the structure of the paper's evaluation (Tables 7 and 8): accuracy
plus long-tail and ideological diversity, averaged over the evaluated users.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from . import metrics
from .graph import FeedbackGraph


def evaluate(recommender, train_graph: FeedbackGraph, test_pos, top_k: int = 10,
             diversity_k: int = 20, item_positions=None, user_positions=None,
             n_users_total=None) -> dict:
    """Evaluate one recommender against a held-out test set.

    Parameters
    ----------
    recommender:
        Any object exposing ``recommend(user_ids, top_k, exclude_seen)`` and,
        for accuracy metrics, ``scores(user_ids)``.
    train_graph:
        :class:`FeedbackGraph` built from the *training* interactions.
    test_pos:
        Per-user list of held-out item-index arrays.
    item_positions:
        Optional ideological positions per item (enables ``RecRange``).
    user_positions:
        Optional ideological positions per user; together with ``item_positions``
        this enables the ``shift`` and position-weighted diversity measures
        (talk Results III/IV).
    """
    eval_users = np.array([u for u in range(train_graph.m)
                           if len(test_pos[u]) > 0], dtype=int)
    train_pos = [train_graph.seen_items(u) for u in eval_users]
    test_sub = [test_pos[u] for u in eval_users]
    n_users_total = n_users_total or train_graph.m

    recs_topk = recommender.recommend(eval_users, top_k=top_k, exclude_seen=True)
    recs_div = (recommender.recommend(eval_users, top_k=diversity_k,
                                      exclude_seen=True)
                if diversity_k != top_k else recs_topk)

    result = {
        "hit_rate@%d" % top_k: metrics.hit_rate_at_k(recs_topk, test_sub),
        "ndcg@%d" % top_k: metrics.ndcg_at_k(recs_topk, test_sub, top_k),
        "precision@%d" % top_k: metrics.precision_at_k(recs_topk, test_sub, top_k),
        "gini_div@%d" % diversity_k: metrics.gini_diversity(recs_div, train_graph.n),
        "coverage@%d" % diversity_k: metrics.catalog_coverage(recs_div, train_graph.n),
        "avg_deg@%d" % diversity_k: metrics.average_item_degree(
            recs_div, train_graph.item_degrees),
        "personalization@%d" % diversity_k: metrics.personalization(
            recs_div, train_graph.n),
        "surprisal@%d" % diversity_k: metrics.surprisal(
            recs_div, train_graph.item_degrees, n_users_total),
    }
    if hasattr(recommender, "scores"):
        S = recommender.scores(eval_users)
        result["auc"] = metrics.auc(S, test_sub, train_pos)
        result["mean_rank"] = metrics.mean_rank(S, test_sub, train_pos)
    if item_positions is not None:
        result["rec_range@%d" % top_k] = metrics.rec_range_at_k(
            recs_topk, item_positions)
        if user_positions is not None:
            ref = np.asarray(user_positions)[eval_users]
            result["shift@%d" % top_k] = metrics.directed_shift(
                recs_topk, item_positions, ref)
            result["uw_recs"] = metrics.weighted_position(
                recs_topk, item_positions, ref)
            result["uw_shift"] = metrics.weighted_shift(
                recs_topk, item_positions, ref)
            result["uw_range"] = metrics.weighted_range(
                recs_topk, item_positions, ref)
    return result


def compare(recommenders: dict, train_graph: FeedbackGraph, test_pos,
            **eval_kwargs) -> pd.DataFrame:
    """Evaluate a ``{name: recommender}`` mapping and return a results table."""
    rows = {name: evaluate(rec, train_graph, test_pos, **eval_kwargs)
            for name, rec in recommenders.items()}
    return pd.DataFrame(rows).T


def grid_search(factory: Callable, param_grid: dict, train_graph: FeedbackGraph,
                test_pos, objective: str = "auc", maximize: bool = True,
                **eval_kwargs):
    """Search ``param_grid`` for the recommender maximising ``objective``.

    Parameters
    ----------
    factory:
        Callable ``**params -> recommender``.
    param_grid:
        Mapping ``param_name -> list of values``; the full Cartesian product is
        evaluated.

    Returns
    -------
    (best_params, best_recommender, full_results_dataframe)
    """
    import itertools

    names = list(param_grid)
    combos = list(itertools.product(*(param_grid[k] for k in names)))
    records, best = [], None
    for combo in combos:
        params = dict(zip(names, combo))
        rec = factory(**params)
        res = evaluate(rec, train_graph, test_pos, **eval_kwargs)
        records.append({**params, **res})
        score = res.get(objective, float("nan"))
        if score == score:  # not NaN
            better = (best is None or (score > best[0]) == maximize or score == best[0])
            if best is None or (maximize and score > best[0]) or \
               (not maximize and score < best[0]):
                best = (score, params, rec)
    results = pd.DataFrame(records)
    best_params, best_rec = (best[1], best[2]) if best else ({}, None)
    return best_params, best_rec, results
