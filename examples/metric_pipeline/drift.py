"""Stage 8 · Drift — did a metric move since the last recorded run of the same dataset?

The golden personas are deterministic: the independent RAW value for, say, ``echo_chamber``'s
Viewpoint Balance is a fixed number. So run-over-run drift on a golden dataset should be **zero** —
any non-zero drift means a production change (or a fixture edit) shifted a metric, which is precisely
the regression signal this stage exists to surface. Drift compares the current run's per-reader raw
values against the previous recorded run (Stage 10) for the same dataset; with no prior run it reports
a baseline.
"""
from __future__ import annotations

import math
from typing import Optional


def _isnan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def compute_drift(current_metrics: dict, previous_run: Optional[dict], *,
                  threshold: float = 1e-9) -> dict:
    """``current_metrics`` maps ``reader_label -> {metric: raw_value}`` (independent raw). Compare to
    ``previous_run['metrics']`` (same shape) and flag any absolute change beyond ``threshold``."""
    if not previous_run:
        return {"baseline": True, "drifted": False, "rows": [],
                "note": "no prior run for this dataset — recorded as the baseline"}

    prev = previous_run.get("metrics", {}) or {}
    rows = []
    for reader, metrics in current_metrics.items():
        prev_metrics = prev.get(reader, {})
        for metric, value in metrics.items():
            before = prev_metrics.get(metric)
            if _isnan(before) and _isnan(value):
                continue
            if _isnan(before) or _isnan(value):
                rows.append({"reader": reader, "metric": metric, "previous": before,
                             "current": value, "delta": None, "drifted": True,
                             "note": "defined/undefined flipped"})
                continue
            delta = abs(float(value) - float(before))
            if delta > threshold:
                rows.append({"reader": reader, "metric": metric, "previous": before,
                             "current": value, "delta": delta, "drifted": True})
    return {"baseline": False, "drifted": bool(rows), "rows": rows,
            "previousTimestamp": previous_run.get("timestamp")}
