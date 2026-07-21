#!/usr/bin/env python3
"""measurement.py — the generic **Measurement metadata** envelope (ADR-001).

A *measurement* wraps a metric's VALUE with the metadata that makes the number honest:

* **coverage** — *scope*: of the reads that could carry this dimension's signal, how many actually
  did. ``{"observed": int, "eligible": int, "basis": str}``. ``eligible`` is the honest denominator
  (the reads the metric is *about*); ``observed`` is how many of those carried the signal; ``basis``
  names the eligibility population.
* **provenance** — *where the signal comes from*: ``{"kind": str, "source": str}``. ``kind`` is how
  the value was obtained (``authoritative`` = looked up from a source of truth; ``derived`` = inferred
  by a model); ``source`` names that source of truth / model.
* **confidence** — *certainty* (optional): how sure we are of the value given the reads that carried
  the signal. Orthogonal to coverage. **Omitted** unless a value genuinely represents uncertainty
  about the prediction (see ADR-001 — the stored emotion outputs do not preserve enough inference
  metadata to compute a defensible confidence, so it is absent rather than a heuristic).

This generalises the Viewpoint coverage pilot (``docs/DIMENSIONAL_COVERAGE.md``): coverage names the
*scope* of a metric, and coverage != confidence. It is a **pure leaf** — it counts over the reader's
already-scored reads and returns plain dicts; it changes no score, no metric value, and no
recommendation. Computation lives here (pure) and is attached to the report by the engine serialiser
(``api_server.Backend._serialize_report``) alongside the metric values, from the SAME read projection
the values are computed over — never a second read load.

The metric keys returned (``topicDiversity`` / ``reportingRatio`` / ``emotionalBalance`` /
``viewpointBalance``) are the frontend ``MetricKey``s (``api_server._METRIC_KEYS`` /
``web/types/domain.ts``), so a measurement attaches directly onto its metric card.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Optional

# Provenance sources of truth.
_VIEWPOINT_SOURCE = "outlet_registry"           # AllSides lean via examples/outlet_registry.py
_TOPIC_SOURCE = "topic_classifier"              # the deterministic classifier (ingest.classify_topic)
_DEFAULT_ENRICHER_SOURCE = "baseline_lexical"   # the offline headline enricher (enrich.BaselineEnricher);
                                                # derives BOTH register and emotion in one call


def _get(read: Any, name: str):
    """Read a scored field from a read, whether it is a :class:`augmented_corpus.ScoredRead`
    (attribute access) or a plain scored dict (``store.get_reads`` verbatim). Keeps this leaf
    testable with dict fixtures while the engine passes ``ScoredRead`` objects."""
    if isinstance(read, dict):
        return read.get(name)
    return getattr(read, name, None)


def _finite(value) -> bool:
    """True iff ``value`` is a **finite** number — the signal the recommendation corpus keeps for a
    lean (``outlet_coverage._is_unknown`` / the qbias projection drop on the complement) and the
    enricher writes for a register (``enrich`` leaves it ``NaN`` when there is no text). A missing,
    ``None``, non-numeric, or ``NaN`` value is *unknown*, never a crash."""
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _coverage(observed: int, eligible: int, basis: str) -> dict:
    return {"observed": int(observed), "eligible": int(eligible), "basis": basis}


def _provenance(kind: str, source: str) -> dict:
    return {"kind": kind, "source": source}


def _envelope(*, dimension: str, coverage: dict, provenance: dict,
              confidence: Optional[dict] = None) -> dict:
    """Assemble a Measurement envelope. ``confidence`` is included only when supplied — the field is
    genuinely absent (never a placeholder) when we have no defensible uncertainty to report."""
    env = {"dimension": dimension, "coverage": coverage, "provenance": provenance}
    if confidence is not None:
        env["confidence"] = confidence
    return env


def viewpoint_measurement(reads: Iterable[Any]) -> Optional[dict]:
    """Measurement for the **Viewpoint** dimension over a reader's scored ``reads``.

    Coverage: of the reader's POLITICAL reads (``basis = political_reads``, the honest denominator for
    the left/center/right mix), how many carry an **authoritative** finite outlet-registry lean
    (``observed``) vs. how many are unknown-lean and therefore not represented in the mix
    (``eligible - observed``). Provenance is ``authoritative`` from the ``outlet_registry`` (AllSides).

    Returns ``None`` when the reader has no political reads (no Viewpoint mix to describe) — the metric
    card then simply carries no measurement, exactly as before the pilot.
    """
    eligible = observed = 0
    for r in reads:
        if not _get(r, "political"):
            continue
        eligible += 1
        if _finite(_get(r, "lean")):
            observed += 1
    if eligible == 0:
        return None
    return _envelope(
        dimension="viewpoint",
        coverage=_coverage(observed, eligible, basis="political_reads"),
        provenance=_provenance(kind="authoritative", source=_VIEWPOINT_SOURCE),
    )


def topic_measurement(reads: Iterable[Any]) -> Optional[dict]:
    """Measurement for the **Topic** dimension over a reader's scored ``reads``.

    Coverage: of ALL the reader's reads (``basis = all_reads``), how many carry a resolved topic
    (``observed``) — ``ingest.classify_topic`` returns a taxonomy member or ``""`` (uncategorized),
    so a read with an empty ``category`` is eligible but not observed. Provenance is ``derived`` from
    the deterministic ``topic_classifier``.

    Returns ``None`` only when the reader has no reads at all (no dimension to describe).
    """
    eligible = observed = 0
    for r in reads:
        eligible += 1
        cat = _get(r, "category")
        if isinstance(cat, str) and cat.strip():       # a resolved taxonomy topic (not "" / uncategorized)
            observed += 1
    if eligible == 0:
        return None
    return _envelope(
        dimension="topic",
        coverage=_coverage(observed, eligible, basis="all_reads"),
        provenance=_provenance(kind="derived", source=_TOPIC_SOURCE),
    )


def register_measurement(reads: Iterable[Any], *,
                         source: str = _DEFAULT_ENRICHER_SOURCE) -> Optional[dict]:
    """Measurement for the **Register** (reporting-vs-opinion) dimension over a reader's ``reads``.

    Coverage: of ALL the reader's reads (``basis = all_reads``), how many carry a register score
    (``observed``) — the enricher writes ``register`` = P(reporting) only when there is text, leaving
    it ``NaN`` otherwise (exactly as Emotion), so a headline with no usable text is eligible but not
    observed. Provenance is ``derived`` from the current enricher (``source``, shared with Emotion —
    the same ``enrich`` call sets both).

    Confidence is omitted, as for the other derived dimensions (ADR-001): the stored register is a
    point estimate, not an uncertainty estimate.

    Returns ``None`` only when the reader has no reads at all (no dimension to describe).
    """
    eligible = observed = 0
    for r in reads:
        eligible += 1
        if _finite(_get(r, "register")):
            observed += 1
    if eligible == 0:
        return None
    return _envelope(
        dimension="register",
        coverage=_coverage(observed, eligible, basis="all_reads"),
        provenance=_provenance(kind="derived", source=source),
    )


def emotion_measurement(reads: Iterable[Any], *,
                        source: str = _DEFAULT_ENRICHER_SOURCE) -> Optional[dict]:
    """Measurement for the **Emotion** dimension over a reader's scored ``reads``.

    Coverage: of ALL the reader's reads (``basis = all_reads``), how many carry an emotion vector
    (``observed``) — a headline with no usable text leaves emotion ``n/a`` (``enrich``), so it is
    eligible but not observed. Provenance is ``derived`` from the current emotion model (``source``).

    **Confidence is intentionally omitted** (ADR-001 implementation note): the stored emotion outputs
    are a distribution over labels, not an uncertainty estimate, so there is no defensible confidence
    to report — the field is absent rather than populated with a heuristic (e.g. output concentration,
    which is not the same concept as model confidence).

    Returns ``None`` only when the reader has no reads at all (no dimension to describe).
    """
    eligible = observed = 0
    for r in reads:
        eligible += 1
        emo = _get(r, "emotion")
        if isinstance(emo, dict) and emo:      # a present, non-empty emotion vector
            observed += 1
    if eligible == 0:
        return None
    return _envelope(
        dimension="emotion",
        coverage=_coverage(observed, eligible, basis="all_reads"),
        provenance=_provenance(kind="derived", source=source),
        # confidence intentionally omitted — see the docstring / ADR-001.
    )


def measurements_for_reads(reads: Iterable[Any], *,
                           enricher_source: str = _DEFAULT_ENRICHER_SOURCE) -> dict:
    """Compute every per-metric Measurement envelope for a reader's scored ``reads``.

    Returns ``{metric_key: envelope}`` keyed by the frontend ``MetricKey`` (``topicDiversity`` /
    ``reportingRatio`` / ``emotionalBalance`` / ``viewpointBalance``), so the serialiser attaches each
    envelope onto its metric card. A dimension with nothing to describe (e.g. no political reads) is
    simply absent from the mapping. ``enricher_source`` names the model behind the two enricher-derived
    dimensions (register + emotion). Pure and read-only; the ``reads`` are materialised once (each
    dimension scans them)."""
    reads = list(reads)
    out: dict = {}
    topic = topic_measurement(reads)
    if topic is not None:
        out["topicDiversity"] = topic
    reg = register_measurement(reads, source=enricher_source)
    if reg is not None:
        out["reportingRatio"] = reg
    emo = emotion_measurement(reads, source=enricher_source)
    if emo is not None:
        out["emotionalBalance"] = emo
    vp = viewpoint_measurement(reads)
    if vp is not None:
        out["viewpointBalance"] = vp
    return out
