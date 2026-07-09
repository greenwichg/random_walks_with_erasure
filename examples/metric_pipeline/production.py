"""Stage 6 · Production Collection — the same reads, scored by the UNCHANGED production engine.

This is the only stage that touches production, and it does so read-only: it builds a small
in-memory corpus (``rwe.MINDData``) in which each reader is one row and each of their reads is one
column carrying that read's scored metadata, then hands it to ``health_report.compute`` — the exact,
unmodified function the product uses. Because a reader who "clicked" all their own columns once makes
the engine's matrix aggregates collapse to plain per-reader means, this reproduces production's RAW
values for all six percentile metrics *and* — over a population of >= 2 readers — production's
percentile-ranked DISPLAYED scores, with **no formula re-implemented here**.

Why building a corpus (rather than calling raw helpers) is the honest choice:
    Emotional Balance and Reporting Ratio have no standalone raw helper in ``health_report`` — the
    product computes them inline inside ``compute`` over the click matrix. Driving ``compute`` exercises
    that real code path for every metric, not just the four with public helpers, so Stage 7 compares
    against production as the product actually runs it.

Fidelity note (documented, not hidden): a stored read keeps a discrete register *label* and a
headline-derived emotion vector, whereas the live product may score a continuous ``P(reporting)`` and
a body-derived emotion vector. We map the label to ``reporting → 1.0 / opinion|mixed → 0.0`` and pass
the stored emotion shares, so what we validate is the engine's *formula* on Reading-History-fidelity
inputs — the honest, reproducible proxy Study Mode already documents.
"""
from __future__ import annotations

import math
from typing import List, Optional

import numpy as np
import scipy.sparse as sp

import health_report as hr
from rwe.data import Dataset
from rwe.mind import MINDData
from study_metrics import EMOTION_BUCKETS, _scored

_NAN = float("nan")


def _register_probability(label: str) -> float:
    """Register label → the ``P(reporting)`` the engine averages: reporting→1, opinion/mixed→0,
    unknown/blank→NaN (excluded from the mean, matching the independent label-share proxy)."""
    lab = (label or "").strip().lower()
    if lab == "reporting":
        return 1.0
    if lab in ("opinion", "mixed"):
        return 0.0
    return _NAN


def build_corpus(readers: List[List[dict]]):
    """Build a ``(MINDData, register_array, emotion_dict)`` corpus from a list of readers' reads.

    Row ``r`` is reader ``r``; every read becomes its own column with that read's scored metadata, so
    each reader's per-row aggregate is exactly their own reading. This is a from-scratch sibling of
    ``augmented_corpus.augment`` (which appends to a base corpus); here there is no base — the readers
    passed in *are* the population."""
    rows, cols = [], []
    cat, out, title, sub, pol, pos, reg = [], [], [], [], [], [], []
    emo = {b: [] for b in EMOTION_BUCKETS}
    col = 0
    for r_idx, reader in enumerate(readers):
        for read in reader:
            s = _scored(read)
            rows.append(r_idx)
            cols.append(col)
            cat.append(s.get("category") or "")
            out.append((s.get("outlet") or "").strip())
            title.append(s.get("title") or "")
            sub.append(s.get("subcategory") or "")
            pol.append(bool(s.get("political")))
            lean = s.get("lean")
            pos.append(float(lean) if isinstance(lean, (int, float)) and math.isfinite(lean) else _NAN)
            reg.append(_register_probability(s.get("register")))
            e = s.get("emotion")
            for b in EMOTION_BUCKETS:
                emo[b].append(float(e[b]) if isinstance(e, dict) and e and e.get(b) is not None else _NAN)
            col += 1

    n_users, n_items = len(readers), col
    matrix = sp.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n_users, n_items))
    mind = MINDData(
        dataset=Dataset(matrix=matrix,
                        user_ids=np.array([f"reader_{i}" for i in range(n_users)], dtype=object),
                        item_ids=np.array([str(i) for i in range(n_items)], dtype=object)),
        categories=np.array(cat, dtype=object), subcategories=np.array(sub, dtype=object),
        titles=np.array(title, dtype=object), outlets=np.array(out, dtype=object),
        political=np.array(pol, dtype=bool), item_positions=np.array(pos, dtype=float),
        user_positions=None)
    register = np.array(reg, dtype=float)
    emotion = {b: np.array(emo[b], dtype=float) for b in EMOTION_BUCKETS}
    return mind, register, emotion


def _cell(arr, r) -> float:
    return float(arr[r]) if arr is not None else _NAN


def production_metrics(readers: List[List[dict]]) -> dict:
    """Drive the unchanged ``health_report.compute`` over the corpus and read back, per reader, the
    RAW values and the percentile-ranked DISPLAYED scores.

    Returns ``{"raw": [per-reader dict], "displayed": [per-reader dict], "catalog_categories": C,
    "population": K}``. ``displayed`` is only meaningful for ``K >= 2`` (a lone reader percentile-ranks
    to 50 by the engine's convention); the caller gates the displayed comparison on the population size."""
    mind, register, emotion = build_corpus(readers)
    # min_clicks=1 / min_political=1 disable the research floors so a short Reading History is scored
    # (the floors exist to suppress noisy demo users, not to change any formula).
    pop = hr.compute(mind, min_clicks=1, min_political=1, register=register, emotion=emotion)
    catalog_categories = int(len(np.unique(mind.categories)))

    raw, displayed = [], []
    for r in range(len(readers)):
        raw.append({
            "topicDiversity": _cell(pop["topic"], r),
            "sourceDiversity": _cell(pop["eff_src"], r),
            "viewpointBalance": _cell(pop["cross"], r),
            "echoChamber": _cell(pop["echo"], r),
            "emotionalBalance": _cell(pop["balance"], r),
            "reportingRatio": _cell(pop["reporting"], r),
        })
        displayed.append({
            "topicDiversity": _cell(pop["topic_pct"], r),
            "sourceDiversity": _cell(pop["source_pct"], r),
            "viewpointBalance": _cell(pop["viewpoint_pct"], r),
            "echoChamber": _cell(pop["echo_pct"], r),
            "emotionalBalance": _cell(pop["balance_pct"], r),
            "reportingRatio": _cell(pop["reporting_pct"], r),
        })
    return {"raw": raw, "displayed": displayed,
            "catalog_categories": catalog_categories, "population": len(readers)}
