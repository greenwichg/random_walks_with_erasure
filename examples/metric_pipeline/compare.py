"""Stage 7 · Comparison — Expected (production) vs Application (independent), per metric.

Three comparisons, each isolating a different production surface:

  * **raw** — the independent raw value (Stage 5) vs the raw value the unchanged engine produced over
    the corpus (Stage 6). Exact to a tolerance. This validates the *formulas*.
  * **displayed** — an independent percentile ranking (re-derived here, NOT imported from production)
    vs ``health_report.percentiles``'s ranking of the same raw values across the pinned population.
    This validates the *percentile transformation*. Only runs for a population of >= 2.
  * **helper-parity** — a supplementary unit-level check that the four metrics with public raw helpers
    (Topic/Source/Viewpoint/Echo) match ``health_report``'s helpers directly, via the existing
    ``study_metrics.verify_against_production`` — belt-and-suspenders next to the corpus-driven check.

Only this stage and Stage 6 import production. The independent percentile function below deliberately
re-implements scipy's ``rankdata(method="average")`` rule so that ``health_report.percentiles`` is
checked against an independent implementation, not against itself.
"""
from __future__ import annotations

import math
from typing import List, Optional

from .engine import PERCENTILE_METRIC_KEYS, RAW_METRIC_KEYS

# Metrics whose DISPLAYED score ranks a *transformed* raw value. Echo Chamber is the one asymmetry
# in the engine: ``health_report.compute`` ranks ``percentiles(-echo)`` (health_report.py) — i.e. it
# ranks ``1 − echo`` so a higher displayed score means LESS echo-chambered. Percentile rank is
# invariant to the additive constant, so negating the raw echo reproduces the engine's ranking.
_INVERTED_FOR_DISPLAY = {"echoChamber"}


def _isnan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _rankdata_average(vals: List[float]) -> List[float]:
    """1-based ranks with ties assigned their average rank — scipy ``rankdata(method='average')``."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0                       # mean of 1-based ranks (i+1)..(j+1)
        for t in range(i, j + 1):
            ranks[order[t]] = avg
        i = j + 1
    return ranks


def independent_percentiles(values: List[float]) -> List[Optional[float]]:
    """Percentile rank (0–100) of each finite entry among the finite entries — an independent
    re-derivation of ``health_report.percentiles`` (lone finite value → 50; NaN stays NaN)."""
    finite = [i for i, v in enumerate(values) if not _isnan(v)]
    out: List[Optional[float]] = [float("nan")] * len(values)
    k = len(finite)
    if k == 1:
        out[finite[0]] = 50.0
        return out
    if k == 0:
        return out
    ranks = _rankdata_average([float(values[i]) for i in finite])
    for pos, i in enumerate(finite):
        out[i] = (ranks[pos] - 1.0) / (k - 1.0) * 100.0
    return out


def _row(metric: str, reader, expected, application, tol: float, basis: str) -> dict:
    e_nan, a_nan = _isnan(expected), _isnan(application)
    if e_nan and a_nan:
        ok, delta = True, 0.0
    elif e_nan or a_nan:
        ok, delta = False, float("nan")
    else:
        delta = abs(float(expected) - float(application))
        ok = delta <= tol
    return {"metric": metric, "reader": reader, "expected": expected, "application": application,
            "delta": delta, "pass": ok, "basis": basis}


def compare_raw(independent: List[dict], production: List[dict], *, tol: float,
                reader_labels: Optional[List[str]] = None) -> List[dict]:
    """Per-reader, per-metric raw comparison. ``readingTime`` has no ``health_report`` counterpart
    (the product assembles it in ``api_server`` from the same lossy title estimate), so it is reported
    independent-only rather than asserted equal."""
    rows: List[dict] = []
    for r, ind in enumerate(independent):
        label = reader_labels[r] if reader_labels else r
        prod = production[r]
        for metric in RAW_METRIC_KEYS:
            if metric == "readingTime":
                rows.append({"metric": metric, "reader": label, "expected": None,
                             "application": ind.get(metric), "delta": None, "pass": None,
                             "basis": "independent-only (no health_report counterpart)"})
                continue
            rows.append(_row(metric, label, prod.get(metric), ind.get(metric), tol,
                             "health_report.compute (corpus-driven)"))
    return rows


def compare_displayed(independent_raw: List[dict], production_displayed: List[dict], *,
                      tol: float, reader_labels: Optional[List[str]] = None) -> List[dict]:
    """Population percentile comparison: rank the independent raw values ourselves and check against
    ``health_report.percentiles``. Requires a population of >= 2 (else percentile is trivially 50)."""
    rows: List[dict] = []
    n = len(independent_raw)
    if n < 2:
        return rows
    for metric in PERCENTILE_METRIC_KEYS:
        column = [ind.get(metric) for ind in independent_raw]
        if metric in _INVERTED_FOR_DISPLAY:                 # engine ranks 1 − echo (see constant)
            column = [(-v if not _isnan(v) else v) for v in column]
        ours = independent_percentiles(column)
        for r in range(n):
            label = reader_labels[r] if reader_labels else r
            rows.append(_row(metric, label, production_displayed[r].get(metric), ours[r], tol,
                             "health_report.percentiles vs independent rankdata"))
    return rows


def helper_parity(readers: List[List[dict]], *, tol: float,
                  reader_labels: Optional[List[str]] = None) -> List[dict]:
    """Supplementary unit-level parity against the public raw helpers, via
    ``study_metrics.verify_against_production`` (Topic/Source/Viewpoint/Echo)."""
    import study_metrics as sm
    rows: List[dict] = []
    for r, reads in enumerate(readers):
        label = reader_labels[r] if reader_labels else r
        # The Viewpoint/Echo helpers operate on the political subset; with no political reads they are
        # out of domain (and ``hr.echo_score(nan, nan)`` returns 0.0 via a ``max(0.0, nan)`` quirk that
        # production's ``compute`` never triggers because it gates on ``min_political``). The RAW layer
        # already validates those metrics as n/a-vs-n/a for such a reader, so skip the helper rows here.
        has_political = bool(sm.political_leans(reads))
        for chk in sm.verify_against_production(reads, tol=tol):
            metric = chk["metric"]
            if not has_political and ("Viewpoint" in metric or "Echo" in metric):
                continue
            rows.append({"metric": metric, "reader": label, "expected": chk["expected"],
                         "application": chk["application"], "pass": chk["pass"],
                         "basis": "study_metrics.verify_against_production"})
    return rows


def summarize(rows: List[dict]) -> dict:
    """Pass/fail tally over comparison rows that carry a boolean ``pass`` (ignoring informational)."""
    graded = [r for r in rows if r.get("pass") is not None]
    passed = sum(1 for r in graded if r["pass"])
    return {"total": len(graded), "passed": passed, "failed": len(graded) - passed,
            "allPass": passed == len(graded)}
