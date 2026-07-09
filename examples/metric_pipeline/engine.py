"""Stages 4 & 5 · Feature Engineering + the Independent Metric Engine.

Stage 4 (Feature Engineering) turns normalized reads into the intermediate quantities the metrics
are built from — topic shares, publisher shares, political positions, per-bucket emotion means,
register counts — so a reviewer can inspect the inputs a metric used, not just its output.

Stage 5 (the Independent Metric Engine) is :mod:`study_metrics`, reused **verbatim**. That module
re-derives every raw metric in plain Python with *no* production imports, which is exactly what makes
this a real cross-check: Stage 6 feeds the same reads to the production engine and Stage 7 compares.
Nothing here imports ``health_report`` / ``api_server``; the only permitted production touch-point is
Stage 6.

Canonical metric keys reported by the pipeline (aligned with ``api_server._METRIC_KEYS`` where a
production counterpart exists):

    topicDiversity, sourceDiversity, viewpointBalance, echoChamber, emotionalBalance, reportingRatio
        — the six raw metrics that production also percentile-ranks into displayed scores;
    readingTime
        — raw only (production shows it directly, never percentile-ranked), so it is validated at the
          raw layer and excluded from the percentile layer;
    openMindedness
        — needs feed-impression data (cross-cutting click-through) that a Reading History does not
          carry, so it is out of scope here and reported as ``n/a`` with that reason.
"""
from __future__ import annotations

from typing import List, Optional

import study_metrics as sm

# The raw metrics we validate, in report order. ``readingTime`` is raw-only (see module docstring).
RAW_METRIC_KEYS = ("topicDiversity", "sourceDiversity", "viewpointBalance", "echoChamber",
                   "emotionalBalance", "reportingRatio", "readingTime")
# The subset production also turns into a 0–100 percentile (the displayed layer).
PERCENTILE_METRIC_KEYS = ("topicDiversity", "sourceDiversity", "viewpointBalance", "echoChamber",
                          "emotionalBalance", "reportingRatio")


def features(reads: List[dict], catalog_categories: Optional[int] = None) -> dict:
    """Stage 4 — the intermediate feature vectors behind the metrics (for the trace / report)."""
    td = sm.topic_diversity(reads, catalog_categories)
    sd = sm.source_diversity(reads)
    pol = sm.political_exposure(reads)
    emo = sm.emotional_exposure(reads)
    rep = sm.reporting_ratio(reads)
    return {
        "topic_shares": td["shares"],
        "topic_entropy_nats": td["entropy_nats"],
        "n_categories_C": td["n_categories_C"],
        "publisher_shares": sd["shares"],
        "hhi": sd["hhi"],
        "political_positions": pol["positions"],
        "mean_lean": pol["mean_lean"],
        "lcr_shares": (pol["left_share"], pol["centre_share"], pol["right_share"]),
        "emotion_means": emo["attention_profile_means"],
        "register_counts": rep["register_counts"],
    }


def independent_metrics(reads: List[dict], catalog_categories: Optional[int] = None) -> dict:
    """Stage 5 — every RAW metric for one reader, computed independently by :mod:`study_metrics`.

    Returns a flat ``{metric_key: raw_value}`` map plus the full per-metric ``detail`` (intermediates)
    that ``study_metrics.compute_all_raw`` produces. ``catalog_categories`` (C) pins the Topic-Diversity
    denominator to a shared catalog so a reader's value is comparable across a population; absent, each
    reader is normalised by their own distinct-topic count (self-contained, flagged in ``detail``)."""
    detail = sm.compute_all_raw(reads, catalog_categories=catalog_categories)
    raw = {
        "topicDiversity": detail["topicDiversity"]["raw"],
        "sourceDiversity": detail["sourceDiversity"]["raw"],
        "viewpointBalance": detail["political"]["cross_cutting_share"],
        "echoChamber": detail["political"]["echo_raw"],
        "emotionalBalance": detail["emotional"]["raw_emotional_balance"],
        "reportingRatio": detail["reportingRatio"]["raw_reporting_share"],
        "readingTime": float(detail["readingTime"]["raw_total_minutes"]),
    }
    return {"raw": raw, "detail": detail}
