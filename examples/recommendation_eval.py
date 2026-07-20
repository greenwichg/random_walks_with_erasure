"""Recommendation evaluation & attribution (RC2.5) — a pure, deterministic, read-only leaf.

It measures how effective the Health Report's improvement recommendations *were*, from data that already
exists — the RC2.3 lifecycle ledger and the report-snapshot history — and it changes nothing about how
recommendations are generated, selected, ranked, evidenced, or impact-estimated. No heuristics, no
probabilities, no learning: every number is a deterministic function of the stored snapshots + ledger.

**Attribution (the core).** For each recommendation's metric, the total observed score change over the
snapshot history is split three ways so the parts sum to the whole:

    populationDrift          score change across a window in which the reader added NO reads. With reads
                             unchanged the report is served from the same cached model, so a score change
                             there is not the reader's reading — it is the reference population moving
                             under them (a corpus refresh), i.e. drift. (For reception-based metrics this
                             bucket can also hold cross-cutting-reception change; see the metric note.)
    recommendationAttributed score change in a window where the reader DID add reads AND the recommendation
                             was already accepted — the reader engaged the rec, then the metric moved.
    organic                  score change in a window where the reader added reads but had not accepted
                             the recommendation — improvement they made on their own.

This is an *association*, stated honestly: "reads made while this recommendation was accepted account for
+N", never "the recommendation caused +N".

**Calibration.** Compares the RC2.2 estimated impact against the recommendation-attributed gain, per rule,
surfacing where the estimate systematically over- or under-shoots. It only *exposes* this — it never
adjusts ranking or the estimator.
"""
from __future__ import annotations

# Sustained-improvement check: after completion the metric must hold within this margin for this many
# subsequent snapshots.
SUSTAIN_MARGIN = 3
SUSTAIN_WINDOWS = 2

_ACTED_STATES = frozenset({"accepted", "in_progress", "completed"})


def attribute(series, accepted_at=None):
    """Three-way split of a metric's total change across ``series`` (``[{date, reads, score}]``,
    oldest-first, finite scores). Returns the signed components (they sum to ``last − first``) plus the
    count of behavioural windows. Deterministic."""
    attributed = organic = drift = 0.0
    behavioural = 0
    for a, b in zip(series, series[1:]):
        delta = b["score"] - a["score"]
        reads_grew = (a.get("reads") is not None and b.get("reads") is not None
                      and b["reads"] > a["reads"])
        if not reads_grew:
            drift += delta                                   # no new reading → not behavioural
        else:
            behavioural += 1
            if accepted_at is not None and b.get("date") is not None and accepted_at <= b["date"]:
                attributed += delta
            else:
                organic += delta
    return {"recommendationAttributed": round(attributed, 2), "organic": round(organic, 2),
            "populationDrift": round(drift, 2), "behavioralWindows": behavioural}


def _estimated_gain(snapshots, metric):
    """The RC2.2 estimated-impact band midpoint from the earliest snapshot that carried an estimate for
    this metric (closest to when the recommendation was generated), or ``None``."""
    for s in snapshots:
        est = (s.get("estimates") or {}).get(metric)
        if est:
            return round((est["low"] + est["high"]) / 2)
    return None


def _confidence(behavioural_windows, accepted_at):
    """Deterministic confidence *tier* (not a probability) for the attribution: how much behavioural
    evidence backs it."""
    if accepted_at is None:
        return "not_acted"
    if behavioural_windows >= 3:
        return "high"
    if behavioural_windows >= 1:
        return "medium"
    return "low"


def _sustained(series, row):
    """Whether a completed recommendation's gain held: the metric stayed within ``SUSTAIN_MARGIN`` of the
    completion score for at least ``SUSTAIN_WINDOWS`` later snapshots. ``None`` when there isn't enough
    post-completion history to tell."""
    comp, comp_at = row.get("completedScore"), row.get("completedAt")
    if comp is None or comp_at is None:
        return None
    after = [p["score"] for p in series if p.get("date") and p["date"] > comp_at]
    if len(after) < SUSTAIN_WINDOWS:
        return None
    return all(sc >= comp - SUSTAIN_MARGIN for sc in after[:SUSTAIN_WINDOWS])


def evaluate_recommendation(snapshots, row):
    """Evaluate one recommendation (one lifecycle ``row``) against the reader's ``snapshots``. Returns
    outcome + estimated/realized gain + the three-way attribution + calibration error + confidence."""
    metric = row.get("metric")
    state = row.get("state")
    accepted_at = row.get("acceptedAt")
    generated_at = row.get("generatedAt")
    first = row.get("firstScore")

    series = [{"date": s.get("date"), "reads": s.get("reads"), "score": (s.get("metrics") or {}).get(metric)}
              for s in snapshots]
    series = [p for p in series if p["score"] is not None]
    if generated_at is not None:                             # anchor at generation when we can
        anchored = [p for p in series if p.get("date") and p["date"] >= generated_at]
        if len(anchored) >= 1:
            series = anchored

    last = series[-1]["score"] if series else row.get("currentScore")
    realized = (last - first) if (first is not None and last is not None) else None
    attr = attribute(series, accepted_at=accepted_at)
    estimated = _estimated_gain(snapshots, metric)
    acted = state in _ACTED_STATES
    calibration = (round(attr["recommendationAttributed"] - estimated, 2)
                   if (estimated is not None and acted) else None)

    return {"recKey": row.get("recKey"), "metric": metric, "outcome": state,
            "estimatedGain": estimated, "realizedGain": realized,
            "attribution": {k: attr[k] for k in
                            ("recommendationAttributed", "organic", "populationDrift")},
            "attributionConfidence": _confidence(attr["behavioralWindows"], accepted_at),
            "calibrationError": calibration,
            "sustainedImprovement": _sustained(series, row)}


def evaluate_reader(snapshots, lifecycle_rows):
    """Per-recommendation evaluation for one reader plus an outcome tally (RC2.5)."""
    recs = [evaluate_recommendation(snapshots, row) for row in lifecycle_rows]
    outcomes = {}
    for row in lifecycle_rows:
        outcomes[row.get("state")] = outcomes.get(row.get("state"), 0) + 1
    return {"recommendations": recs, "outcomes": outcomes}


def rule_quality(rec_evals, lifecycle_rows):
    """Deterministic per-rule (per-metric) quality + calibration across a cohort of evaluated
    recommendations. ``rec_evals`` are :func:`evaluate_recommendation` outputs; ``lifecycle_rows`` the
    matching ledger rows (for the outcome tallies). Returns ``{metric: {...rates + calibration...}}``."""
    by_metric: dict = {}
    for ev, row in zip(rec_evals, lifecycle_rows):
        by_metric.setdefault(ev["metric"], []).append((ev, row))

    out = {}
    for metric, pairs in by_metric.items():
        n = len(pairs)
        states = [row.get("state") for _, row in pairs]
        accepted = sum(1 for s in states if s in _ACTED_STATES)
        completed = sum(1 for s in states if s == "completed")
        dismissed = sum(1 for s in states if s == "dismissed")
        expired = sum(1 for s in states if s == "expired")
        # abandoned = accepted/in_progress that never completed and then left the active set
        abandoned = sum(1 for s in states if s in ("expired", "superseded"))
        realized = [ev["realizedGain"] for ev, _ in pairs if ev["realizedGain"] is not None]
        estimated = [ev["estimatedGain"] for ev, _ in pairs if ev["estimatedGain"] is not None]
        cal = [ev["calibrationError"] for ev, _ in pairs if ev["calibrationError"] is not None]
        sustained = [ev["sustainedImprovement"] for ev, _ in pairs
                     if ev["sustainedImprovement"] is not None]
        cal_mean = round(sum(cal) / len(cal), 2) if cal else None
        out[metric] = {
            "instances": n,
            "acceptanceRate": round(accepted / n, 3),
            "completionRate": round(completed / n, 3),
            "dismissalRate": round(dismissed / n, 3),
            "abandonmentRate": round(abandoned / n, 3),
            "realizedImprovementMean": round(sum(realized) / len(realized), 2) if realized else None,
            "estimatedImpactMean": round(sum(estimated) / len(estimated), 2) if estimated else None,
            "sustainedRate": round(sum(1 for x in sustained if x) / len(sustained), 3) if sustained else None,
            "calibrationError": cal_mean,
            "calibrationDirection": (None if cal_mean is None
                                     else "over_estimates" if cal_mean < 0
                                     else "under_estimates" if cal_mean > 0 else "calibrated"),
        }
    return out
