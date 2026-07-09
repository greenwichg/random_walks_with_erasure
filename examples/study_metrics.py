"""Study Mode — an independent, from-scratch calculator for the RAW Information-Health metrics.

WHY THIS EXISTS (read me first)
    This is a **developer learning + verification tool**, not a production feature. Its whole point
    is to recompute every metric *independently* from a reader's Reading History, so you can see —
    step by step — how raw article fields become a number, and check that number against the engine.

    It deliberately does NOT import or reuse the production metric functions in its calculators
    (``health_report.py`` / ``api_server.py``). Reusing them would only prove they equal themselves.
    Instead each metric is re-derived here in plain NumPy with the formula spelled out, and a separate
    :func:`verify_against_production` feeds the *same inputs* to the production raw functions and checks
    we agree. Optimised for transparency and mathematical correctness, not for brevity or code reuse.

RAW METRIC vs DISPLAYED SCORE (the distinction that governs everything)
    Every metric has two layers, and this module is about the FIRST one:

      1. RAW METRIC   — a deterministic number computed from *your* reads alone (e.g. Source
                        Diversity = effective number of publishers = 1 / HHI = 2.94 "effective
                        sources"). Reproducible here, exactly.
      2. DISPLAYED SCORE — the 0–100 you see in the app is a **percentile rank of that raw value
                        against a reference population** (``health_report.percentiles``). It needs the
                        whole population, so it is NOT reproducible from one reader's rows — and the
                        displayed number will usually differ from the raw value. We document that
                        transformation as its own layer (:data:`PERCENTILE_LAYER`) and never conflate
                        the two.

INPUT SHAPE
    A "read" is one row from ``store.list_reads`` / the Reading History API — a dict with a nested
    ``scored`` dict carrying the per-article fields the engine already computed:
        scored = {category, outlet, lean, political, register, emotion{...}, confidence, ...}
    We only read those fields; we never re-score anything.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Iterable, Optional

# Mirror the engine's lean-band constants so the political metrics line up 1:1 with production.
# (Copied intentionally rather than imported — this module must stand alone.)
LEAN_TAU = 0.5    # |lean| <= 0.5 is "centre"; < -0.5 left; > 0.5 right
CENTER = 0.0
EMOTION_BUCKETS = ("fear", "outrage", "analysis", "positive", "neutral")
CHARGED_BUCKETS = ("fear", "outrage")   # the "emotionally charged" side, for Emotional Balance


# --------------------------------------------------------------------------- #
# 0 · Reading-History field extraction (the only place we touch the raw rows)
# --------------------------------------------------------------------------- #
def _scored(read: dict) -> dict:
    """The per-article scored fields of one read row (tolerates a bare scored dict too)."""
    return read.get("scored", read) or {}


def topics(reads: Iterable[dict]) -> list:
    """Topic (category) label of every read, in order. Blanks kept as '(uncategorised)'."""
    return [(_scored(r).get("category") or "(uncategorised)") for r in reads]


def publishers(reads: Iterable[dict]) -> list:
    """Publisher (outlet) of every read. Blank/unknown publishers are dropped, matching the engine
    (Source Diversity excludes articles whose publisher couldn't be parsed)."""
    return [o for r in reads if (o := (_scored(r).get("outlet") or "").strip())]


def political_leans(reads: Iterable[dict]) -> list:
    """Lean position (a float in ~[-2, 2]) of every **political** read — the subset the viewpoint /
    echo metrics operate on. A non-political read has no meaningful stance, so it is excluded."""
    out = []
    for r in reads:
        s = _scored(r)
        if s.get("political") and isinstance(s.get("lean"), (int, float)) and math.isfinite(s["lean"]):
            out.append(float(s["lean"]))
    return out


def registers(reads: Iterable[dict]) -> list:
    """Register label (reporting | opinion | mixed) of every read that carries one."""
    return [reg for r in reads if (reg := (_scored(r).get("register") or "").strip())]


def emotion_vectors(reads: Iterable[dict]) -> list:
    """Per-read emotion share vectors {bucket: share}, for reads that carry an emotion dict."""
    out = []
    for r in reads:
        e = _scored(r).get("emotion")
        if isinstance(e, dict) and e:
            out.append({b: float(e.get(b, 0.0) or 0.0) for b in EMOTION_BUCKETS})
    return out


# --------------------------------------------------------------------------- #
# Tiny, transparent math primitives (each returns exactly what the name says)
# --------------------------------------------------------------------------- #
def shares(counts: list) -> list:
    """Turn counts into fractions that sum to 1 (empty / all-zero → all zeros)."""
    total = float(sum(counts))
    return [c / total for c in counts] if total > 0 else [0.0 for _ in counts]


def shannon_entropy_nats(share_vec: list) -> float:
    """H = −Σ pₖ·ln(pₖ) over pₖ>0, in **nats** (natural log). 0 for a single category."""
    return float(-sum(p * math.log(p) for p in share_vec if p > 0))


def normalized_entropy(share_vec: list, n_categories: int) -> float:
    """Topic-Diversity raw value: Shannon entropy divided by ln(C) → [0, 1]. NaN when C ≤ 1 (you
    can't be "diverse" across one bucket). Identical to ``health_report.normalized_entropy``."""
    nz = [p for p in share_vec if p > 0]
    if not nz or n_categories <= 1:
        return float("nan")
    return shannon_entropy_nats(nz) / math.log(n_categories)


def hhi(share_vec: list) -> float:
    """Herfindahl–Hirschman Index = Σ sₒ² (1 = one publisher dominates, → 0 = many even ones)."""
    return float(sum(s * s for s in share_vec))


def effective_number(share_vec: list) -> float:
    """Source-Diversity raw value: inverse-Simpson effective count = 1 / HHI ("N equally-read
    publishers would give this same concentration"). NaN when there are no publishers."""
    h = hhi(share_vec)
    return 1.0 / h if h > 0 else float("nan")


def top_n_share(share_vec: list, n: int) -> float:
    """Combined share of the n largest buckets — the 'Biggest Insight' concentration fact."""
    return float(sum(sorted(share_vec, reverse=True)[:n]))


def viewpoint_shares(positions: list, tau: float = LEAN_TAU) -> tuple:
    """(left, centre, right) fractions of political reading by lean band (equal-weighted).
    left = share with lean < −tau, right = share with lean > tau, centre = the rest."""
    n = len(positions)
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    left = sum(1 for p in positions if p < -tau) / n
    right = sum(1 for p in positions if p > tau) / n
    return left, 1.0 - left - right, right


def cross_cutting_share(positions: list, center: float = CENTER) -> float:
    """Viewpoint-Balance raw value: the share of political reading on the *opposite* side of your own
    average lean. A reader whose mean sits exactly at the centre has no "own side", so every off-centre
    read counts as cross-cutting. NaN only when there is no political reading. Matches the engine."""
    n = len(positions)
    if n == 0:
        return float("nan")
    mu = sum(positions) / n
    if mu == center:                                   # no dominant side → all off-centre is crossing
        return sum(1 for p in positions if p != center) / n
    return sum(1 for p in positions if (p - center) * (1 if mu > center else -1) < 0) / n


def echo_score(left: float, right: float) -> float:
    """Echo-Chamber raw value: 1 − 2·min(L,R)/(L+R) ∈ [0,1]. 0 = perfectly balanced L/R, 1 = entirely
    one side. NaN when there is no left/right reading. (The *displayed* score ranks 1−echo, so higher
    displayed = LESS echo-chambered — see :data:`PERCENTILE_LAYER`.)"""
    nonc = left + right
    if nonc <= 0:
        return float("nan")
    return float(max(0.0, 1.0 - 2.0 * (min(left, right) / nonc)))


# --------------------------------------------------------------------------- #
# The metrics — each returns its raw value PLUS every intermediate, for the trace
# --------------------------------------------------------------------------- #
def topic_diversity(reads: list, catalog_categories: Optional[int] = None) -> dict:
    """RAW = normalised entropy of your topic mix. ``catalog_categories`` (C) is a *catalog* property;
    the engine normalises by the number of categories in the whole catalog. Absent, we normalise by
    the number of distinct topics you read (self-contained) and flag it — this is the one spot where a
    from-history raw value can differ from the engine because C differs. Formula, worked example, edge
    cases: see docs/STUDY_MODE.md."""
    labels = topics(reads)
    counts = Counter(labels)
    order = list(counts.keys())
    count_vec = [counts[k] for k in order]
    share_vec = shares(count_vec)
    c = catalog_categories if catalog_categories is not None else len(counts)
    return {
        "labels": labels,
        "counts": dict(counts),
        "shares": {k: s for k, s in zip(order, share_vec)},
        "entropy_nats": shannon_entropy_nats(share_vec),
        "n_categories_C": c,
        "C_source": "catalog" if catalog_categories is not None else "reader's distinct topics",
        "raw": normalized_entropy(share_vec, c),
        "unit": "normalised topic entropy (0–1)",
    }


def source_diversity(reads: list) -> dict:
    """RAW = effective number of publishers = 1 / HHI. Also reports unique/total (your simpler mental
    model) and the top-N concentration used by 'Biggest Insight'."""
    pubs = publishers(reads)
    counts = Counter(pubs)
    order = list(counts.keys())
    share_vec = shares([counts[k] for k in order])
    return {
        "publishers": pubs,
        "counts": dict(counts),
        "shares": {k: s for k, s in zip(order, share_vec)},
        "unique_publishers": len(counts),
        "total_reads_with_publisher": len(pubs),
        "unique_over_total": (len(counts) / len(pubs)) if pubs else float("nan"),
        "hhi": hhi(share_vec),
        "raw": effective_number(share_vec),
        "top_2_share": top_n_share(share_vec, 2),
        "unit": "effective # sources",
    }


def political_exposure(reads: list) -> dict:
    """RAW political picture: left/centre/right shares, cross-cutting share (Viewpoint Balance) and
    echo (Echo Chamber). All on the **political subset** only."""
    pos = political_leans(reads)
    left, centre, right = viewpoint_shares(pos)
    echo = echo_score(left, right) if len(pos) else float("nan")
    return {
        "positions": pos,
        "n_political": len(pos),
        "mean_lean": (sum(pos) / len(pos)) if pos else float("nan"),
        "left_share": left, "centre_share": centre, "right_share": right,
        "cross_cutting_share": cross_cutting_share(pos),   # Viewpoint Balance raw
        "echo_raw": echo,                                  # Echo Chamber raw (0 balanced .. 1 one-sided)
        "balance_1_minus_echo": (1.0 - echo) if echo == echo else float("nan"),  # what the score ranks
        "unit": {"viewpoint": "cross-cutting share (0–1)", "echo": "balance = 1−echo (0–1)"},
    }


def emotional_exposure(reads: list) -> dict:
    """RAW emotional picture: the mean per-bucket share (Attention Profile) and Emotional Balance =
    1 − (fear + outrage) mean share. Descriptive + one scored value."""
    vecs = emotion_vectors(reads)
    n = len(vecs)
    means = {b: (sum(v[b] for v in vecs) / n if n else float("nan")) for b in EMOTION_BUCKETS}
    charged = sum(means[b] for b in CHARGED_BUCKETS) if n else float("nan")
    return {
        "n_with_emotion": n,
        "attention_profile_means": means,
        "charged_share_fear_outrage": charged,
        "raw_emotional_balance": (1.0 - charged) if n else float("nan"),
        "unit": "1 − (fear+outrage) share",
    }


def reporting_ratio(reads: list) -> dict:
    """RAW = share of reads labelled 'reporting' (vs opinion/mixed). NOTE: the engine's Reporting Ratio
    is the mean of a classifier's P(reporting) *probability* per article; a stored read keeps only the
    discrete register *label*, so from Reading History we can reproduce the label share, not the
    probability mean. We compute the label share and say so — an honest, reproducible proxy."""
    regs = registers(reads)
    counts = Counter(regs)
    n = len(regs)
    return {
        "register_counts": dict(counts),
        "n_with_register": n,
        "raw_reporting_share": (counts.get("reporting", 0) / n) if n else float("nan"),
        "unit": "share labelled 'reporting' (proxy for engine's mean P(reporting))",
    }


def reading_time(reads: list) -> dict:
    """RAW total reading time = Σ per-article reading minutes. If a read carries no minutes we fall
    back to the engine's estimate (≈ words / 220, clamped [1,20]); with no text we can't estimate, so
    such reads contribute the neutral default (2)."""
    per_read = []
    for r in reads:
        s = _scored(r)
        mins = r.get("readingMinutes")
        if not isinstance(mins, (int, float)):
            words = len((s.get("title") or "").split())    # reads store no body; title is all we have
            mins = max(1, min(20, round(words / 220) or 2))
        per_read.append(int(mins))
    return {
        "per_read_minutes": per_read,
        "n_reads": len(per_read),
        "raw_total_minutes": int(sum(per_read)),
        "unit": "minutes (Σ per-article estimates)",
    }


def compute_all_raw(reads: list, *, catalog_categories: Optional[int] = None) -> dict:
    """Every raw metric for one reader's Reading History — the 'ground truth' the app should match at
    the raw layer (before percentile ranking)."""
    return {
        "n_reads": len(reads),
        "topicDiversity": topic_diversity(reads, catalog_categories),
        "sourceDiversity": source_diversity(reads),
        "political": political_exposure(reads),
        "emotional": emotional_exposure(reads),
        "reportingRatio": reporting_ratio(reads),
        "readingTime": reading_time(reads),
    }


# --------------------------------------------------------------------------- #
# The percentile layer — documented, NOT reproduced from one reader's rows
# --------------------------------------------------------------------------- #
PERCENTILE_LAYER = """\
DISPLAYED SCORE = percentile rank of the RAW value against a reference population (0–100).

    score_u = (rank(raw_u among all readers' raw values) − 1) / (k − 1) × 100      [k = #readers]
    A lone reader scores 50 by convention.  (health_report.percentiles, scipy rankdata 'average'.)

Consequences:
  • The displayed 0–100 is a *relative standing*, not the raw quantity. Source Diversity raw = 2.94
    effective sources might display as 41 ("more concentrated than ~59% of readers").
  • It depends on the whole population, so it CANNOT be reproduced from one reader's Reading History —
    which is exactly why this framework validates the RAW layer and treats percentile ranking as a
    separate, population-dependent transformation.
  • Echo Chamber is ranked on (1 − echo), so a higher displayed score means LESS echo-chambered.
  • Overall Score = unweighted mean of the available displayed scores (health_report / _serialize_report).
"""


# --------------------------------------------------------------------------- #
# Verification — cross-check our raw functions against the PRODUCTION raw ones
# (the only place production is imported; used to prove agreement, never reused above)
# --------------------------------------------------------------------------- #
def verify_against_production(reads: list, *, tol: float = 1e-9) -> list:
    """Feed the *same* inputs to our raw functions and the engine's raw functions
    (``health_report``) and report Expected(production) vs Application(this module) per metric.

    This validates the FORMULAS, isolated from the percentile/population layer. Returns a list of
    ``{metric, expected, application, pass}`` rows."""
    import health_report as hr   # imported here ONLY — the calculators above stay production-free

    rows = []

    def _cmp(name, expected, application):
        e = None if expected != expected else expected      # normalise NaN
        a = None if application != application else application
        ok = (e is None and a is None) or (
            e is not None and a is not None and abs(float(e) - float(a)) <= tol)
        rows.append({"metric": name, "expected": expected, "application": application, "pass": ok})

    td = topic_diversity(reads)
    sv = list(td["shares"].values())
    _cmp("Topic Diversity — normalized_entropy", hr.normalized_entropy(sv, td["n_categories_C"]), td["raw"])

    sd = source_diversity(reads)
    sov = list(sd["shares"].values())
    _cmp("Source Diversity — HHI", hr.hhi(sov), sd["hhi"])
    _cmp("Source Diversity — effective_number", hr.effective_number(sov), sd["raw"])
    _cmp("Source Diversity — top_2_share", hr.top_n_share(sov, 2), sd["top_2_share"])

    pol = political_exposure(reads)
    pos = pol["positions"]
    hl, hc, hr_ = hr.viewpoint_shares(pos)
    _cmp("Viewpoint — left share", hl, pol["left_share"])
    _cmp("Viewpoint — right share", hr_, pol["right_share"])
    _cmp("Viewpoint Balance — cross_cutting_share", hr.cross_cutting_share(pos), pol["cross_cutting_share"])
    _cmp("Echo Chamber — echo_score", hr.echo_score(hl, hr_), pol["echo_raw"])

    return rows
