"""Improvement-recommendation lifecycle reconciliation (RC2.3) — a pure, deterministic leaf.

This module owns the *state machine* for the Health Report's improvement recommendations. It never
touches the store, the recommender, the ranking, or the report computation: it is a pure function of
``(currently-generated recs, the stored ledger rows, the current metric scores, an injected clock)``
and returns the new lifecycle state for each recommendation plus the rows to persist. The API layer
does the store I/O around it; tests inject a fixed ``now`` to pin the output.

Lifecycle::

    generated → shown → (viewed) → accepted → in_progress → completed
    generated → dismissed
    generated → expired
    …and any open rec whose slot is taken by a newly-generated rec → superseded

**Identity.** A recommendation is identified by its ``recKey`` (``imp_<metric>``): the recommendation
to improve a given metric is "materially the same" across report regenerations, so it keeps one row and
one history, and the identity is stable through the Estimate→Measured transition.

**Completion is deterministic, from existing report metrics — never heuristic** (see
:func:`is_completed`): a recommendation completes when its metric has risen to at least its benchmark
(the typical reader) *and* improved by at least ``COMPLETION_MARGIN`` points since it was generated.
"""
from __future__ import annotations

#: Percentile points the targeted metric must gain (on top of reaching the benchmark) before a
#: recommendation is considered complete — the "the deficit actually closed" bar.
COMPLETION_MARGIN = 5
#: The typical-reader benchmark the measured/estimate report scores every metric against.
DEFAULT_BENCHMARK = 50

#: All lifecycle states a recommendation row can hold.
LIFECYCLE_STATES = ("generated", "shown", "viewed", "accepted", "in_progress",
                    "completed", "dismissed", "expired", "superseded")
#: Terminal states — a row in one of these is closed and is not reconciled again.
TERMINAL_STATES = frozenset({"completed", "expired", "superseded"})

#: Fields exposed to the API for a currently-shown recommendation (None values are dropped).
_PUBLIC_FIELDS = ("recKey", "state", "firstScore", "currentScore", "completedScore",
                  "generatedAt", "shownAt", "viewedAt", "acceptedAt", "dismissedAt",
                  "completedAt", "expiredAt", "supersededAt", "supersededBy")


def is_completed(first_score, current_score, benchmark: int = DEFAULT_BENCHMARK,
                 margin: int = COMPLETION_MARGIN) -> bool:
    """Deterministic completion rule for every improvement recommendation type: the metric has reached
    at least the typical reader (``benchmark``) *and* gained at least ``margin`` points since the
    recommendation was generated. Uses only scores already on the report — nothing is inferred."""
    if first_score is None or current_score is None:
        return False
    return current_score >= benchmark and (current_score - first_score) >= margin


def _apply_state(row: dict, scores: dict, now: str, benchmark: int, margin: int) -> None:
    """Compute the live state of a *currently-generated* recommendation row (mutates ``row``)."""
    metric = row.get("metric")
    cur = scores.get(metric)
    row["currentScore"] = cur
    first = row.get("firstScore")
    if is_completed(first, cur, benchmark, margin):
        row["state"] = "completed"
        row.setdefault("completedAt", now)
        if row.get("completedScore") is None:
            row["completedScore"] = cur
    elif row.get("dismissedAt"):
        row["state"] = "dismissed"
    elif row.get("acceptedAt"):
        # accepted, and once the metric has ticked up from where it started → in progress
        row["state"] = ("in_progress" if (first is not None and cur is not None and cur > first)
                        else "accepted")
    elif row.get("viewedAt"):
        row["state"] = "viewed"
    else:
        row["state"] = "shown"


def reconcile(current: list, ledger: dict, scores: dict, now: str, *,
              benchmark: int = DEFAULT_BENCHMARK, margin: int = COMPLETION_MARGIN):
    """Reconcile the ledger against the recommendations a fresh report just generated.

    ``current``  – ``[{"recKey", "metric"}, …]`` the recs in this report (report order preserved).
    ``ledger``   – ``{recKey: row-dict}`` the stored lifecycle rows (from ``list_improvement_lifecycle``).
    ``scores``   – ``{metric: score}`` the current report scores (for completion detection).
    ``now``      – injected ISO timestamp (deterministic in tests).

    Returns ``(annotated, updates)``:
      * ``annotated`` – ``{recKey: row}`` for the currently-generated recs (to attach to the report).
      * ``updates``   – ``{recKey: row}`` for every row that changed (currently-generated + any open
        rec whose state transitioned to completed / superseded / expired), to persist.

    Pure and deterministic: same inputs → same output, no clock read, no randomness."""
    current_keys = {c["recKey"] for c in current}
    # brand-new recs this cycle — their arrival is what "supersedes" a departing open rec's slot.
    entered = [c["recKey"] for c in current if c["recKey"] not in ledger]
    updates: dict = {}
    annotated: dict = {}

    for c in current:
        k, metric = c["recKey"], c["metric"]
        row = dict(ledger.get(k) or {})
        row["recKey"] = k
        row["metric"] = metric
        if not row.get("generatedAt"):
            row["generatedAt"] = now
            row["firstScore"] = scores.get(metric)
        row["shownAt"] = now
        _apply_state(row, scores, now, benchmark, margin)
        updates[k] = row
        annotated[k] = row

    for k, stored in ledger.items():
        if k in current_keys or stored.get("state") in TERMINAL_STATES:
            continue
        row = dict(stored)
        metric = row.get("metric")
        cur = scores.get(metric)
        row["currentScore"] = cur
        if is_completed(row.get("firstScore"), cur, benchmark, margin):
            row["state"] = "completed"
            row.setdefault("completedAt", now)
            if row.get("completedScore") is None:
                row["completedScore"] = cur
        elif entered:
            row["state"] = "superseded"
            row["supersededAt"] = now
            row["supersededBy"] = entered[0]
        else:
            row["state"] = "expired"
            row["expiredAt"] = now
        updates[k] = row

    return annotated, updates


def public_view(row: dict) -> dict:
    """The API-facing lifecycle projection for a currently-shown recommendation (drops empty fields)."""
    return {f: row[f] for f in _PUBLIC_FIELDS if row.get(f) is not None}
