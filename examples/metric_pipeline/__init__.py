"""metric_pipeline — an independent, additive validation pipeline for the Information-Health metrics.

WHAT THIS IS
    A **developer tool** that recomputes every Information-Health metric from a reader's Reading
    History *independently of production* and checks the result against the production engine, in ten
    explicit stages:

        1 Extract → 2 Normalize → 3 Data Quality → 4 Feature Engineering → 5 Independent Metric Engine
        → 6 Production Collection → 7 Comparison → 8 Drift → 9 Validation Report → 10 Trend History

WHAT THIS IS NOT
    It is not a production feature and it changes nothing in the product. Nothing here is imported
    *into* the recommendation, story, search, feed, media, or API surfaces; only the Production
    Collection and Comparison stages import *from* production (``health_report``), read-only, to check
    agreement. The independent metric engine (Stage 5) is :mod:`study_metrics`, reused verbatim and
    never permitted to call production.

TWO LAYERS, VALIDATED SEPARATELY (see docs/METRIC_PIPELINE.md and docs/STUDY_MODE.md)
    * RAW layer       — deterministic per reader; validated exactly (independent vs the unchanged
                        ``health_report.compute`` driven over a small corpus we build from the reads).
    * DISPLAYED layer — percentile rank of raw values vs a population; validated over a *pinned* golden
                        population (independent ranking vs ``health_report.percentiles``). A lone reader
                        has no population, so the percentile stage only runs for a population of >= 2.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

# ``study_metrics`` / ``health_report`` live in the parent ``examples/`` dir, and ``health_report``
# imports the root ``rwe`` package — put both on the path so imports resolve whether this package is
# imported as a test module or run as ``python -m metric_pipeline`` from anywhere.
_HERE = _Path(__file__).resolve()
_EXAMPLES = _HERE.parent.parent            # examples/
_ROOT = _EXAMPLES.parent                   # repo root (carries the rwe/ package)
for _p in (_ROOT, _EXAMPLES):
    if str(_p) not in _sys.path:
        _sys.path.insert(0, str(_p))

from .pipeline import run_pipeline, PipelineResult   # noqa: E402

__all__ = ["run_pipeline", "PipelineResult"]
