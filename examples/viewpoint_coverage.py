#!/usr/bin/env python3
"""Viewpoint dimension coverage — the first (deliberately scoped) application of the
"coverage as a first-class concept" architecture (docs/DIMENSIONAL_COVERAGE.md).

It answers one honest question about a reader's *measured* Viewpoint mix: of the political articles
they actually read, how many carry an **authoritative** political lean (resolved from the outlet
registry / AllSides), and how many are **unknown-lean** and therefore not represented in the mix at
all?

This is COVERAGE (scope), not CONFIDENCE (certainty). The report already down-weights unknown-lean
reads toward zero via confidence-weighting, which silently drops them from the Viewpoint mix; this
module makes that scope explicit and countable. It is pure and read-only: it never changes the
viewpoint value, Information Health scoring, or recommendation behaviour.

"Authoritative lean" uses the same predicate the recommendation corpus keeps (a finite numeric
``scored.lean`` — feed_source._bias_label) and ``outlet_coverage._is_unknown`` counts: a missing,
``None``, non-numeric, or ``NaN`` lean (an unknown outlet) is *unknown*, exactly the value the qbias
projection and the recommender drop on.
"""
from __future__ import annotations

import math
from typing import Iterable

# The authoritative source of political lean: AllSides ratings via examples/outlet_registry.py.
PROVENANCE = "outlet_registry"


def _has_authoritative_lean(scored: dict) -> bool:
    """True iff the read's outlet resolves to a **finite** registry lean — the exact signal the
    recommendation corpus keeps and the recommender requires. Missing / None / non-numeric / NaN all
    count as *unknown* (an unrated outlet)."""
    lean = (scored or {}).get("lean")
    if lean is None:
        return False
    try:
        return math.isfinite(float(lean))
    except (TypeError, ValueError):
        return False


def viewpoint_coverage(reads: Iterable[dict]) -> dict:
    """Dimensional coverage for the Viewpoint dimension over a reader's ``reads`` (each a dict with a
    ``scored`` payload, e.g. the rows from ``store.list_reads``).

    Returns the coverage + provenance half of the dimension's measurement envelope::

        {"dimension": "viewpoint",
         "eligiblePoliticalReads": int,   # reads scored political — the honest denominator
         "authoritativeLeanReads": int,   # of those, how many carry a finite registry lean
         "unknownLeanReads": int,         # eligible - authoritative (not represented in the mix)
         "provenance": "outlet_registry"}

    Pure and read-only: it counts, it never mutates the reads. The Viewpoint *value*
    (left/center/right) is unchanged and lives where it always has.
    """
    eligible = authoritative = 0
    for r in reads:
        scored = (r or {}).get("scored") or {}
        if not scored.get("political"):
            continue
        eligible += 1
        if _has_authoritative_lean(scored):
            authoritative += 1
    return {
        "dimension": "viewpoint",
        "eligiblePoliticalReads": eligible,
        "authoritativeLeanReads": authoritative,
        "unknownLeanReads": eligible - authoritative,
        "provenance": PROVENANCE,
    }
