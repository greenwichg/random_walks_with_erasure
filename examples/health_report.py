"""Personalized Information Health Report (v1) — a descriptive consumption profile.

Aggregates each user's *clicked* articles (their row of the click matrix) into a
reading-diet profile, using only metadata already in an ingested MIND ``.npz``
(``categories``, ``outlets``, ``item_positions`` lean, ``political`` mask) -- no
new models.  See ``docs/HEALTH_REPORT_PLAN.md`` for the feasibility scope.

    python examples/health_report.py --npz mind_text.npz --sample 3

==========================  v1 metric spec (frozen)  ==========================
For a user u, over their clicked items (shares sum to 1):

* Topic diversity  = normalized Shannon entropy of the category distribution
      H_norm = ( -Σ_c p_c·ln p_c ) / ln(C)
  where p_c = user's share of category c and C = #distinct categories in the
  *catalog* (fixed denominator -> comparable across users).  0 = one topic,
  1 = uniform over every catalog topic.
* Source concentration  = Herfindahl  HHI = Σ_o s_o²  over publisher shares s_o
  (empty/unknown outlets excluded); effective #sources = 1/HHI (inverse Simpson);
  top-N share = sum of the N largest s_o (the "X% from N publishers" line).
* Viewpoint (political subset only: political & known lean):
      left/centre/right = share with pos < -τ / |pos| ≤ τ / pos > τ   (τ = 0.5);
      cross-cutting share = share on the *opposite* side of the user's own mean;
      echo = 1 - 2·min(L,R)/(L+R)   in [0,1]   (0 = balanced L/R, 1 = one-sided).
* Scores reported as **population percentiles** over users above the click floor
  (higher = more diverse / more cross-cutting / less echo).  The composite
  "Overall" is an *unweighted, illustrative* average of the v1 percentiles only.

* Open-Mindedness (with ``--behaviors``): cross-cutting click-through — of the
  *opposite-side* articles the feed actually showed a user (MIND impressions),
  the fraction they clicked.  An *agency* signal (do you read the other side when
  offered?), distinct from what ended up in the diet.  Percentile.
* Political share (always): fraction of a user's clicks that are political — shown
  as context for the viewpoint scores, not itself a score.

Boundaries:  topic & source over ALL clicks;  viewpoint/echo/open-mindedness over
the political subset.  Users with < ``--min-clicks`` clicks get no scores;
viewpoint needs ≥ ``--min-political`` political clicks.  Reporting-ratio and
emotional exposure populate **only** with ``--register-csv`` / ``--emotion-csv``
(from ``classify_register.py`` / ``classify_emotion.py``); Open-Mindedness only
with ``--behaviors``.  The emotion signal is **experimental** (headline-only).
``--confidence-csv`` (the third column of ``classify_lean.py``'s output)
**confidence-weights** the viewpoint / echo / cross metrics so ambiguous low-margin
articles count less and prints a per-reader *axis confidence* — the per-article
down-weighting the reliability analysis (``examples/lean_agreement.py``) motivates.
==============================================================================
"""

import argparse
from pathlib import Path

import numpy as np
import scipy.sparse as sp
from scipy.stats import rankdata

from rwe.mind import MINDData

LEAN_TAU = 0.5     # |pos| <= tau is "centre"
CENTER = 0.0


# --------------------------------------------------------------------------- #
# Pure metric helpers (unit-tested)
# --------------------------------------------------------------------------- #
def shares(counts) -> np.ndarray:
    counts = np.asarray(counts, dtype=float)
    total = counts.sum()
    return counts / total if total > 0 else counts


def normalized_entropy(share_vec, n_categories: int) -> float:
    """Shannon entropy of ``share_vec`` divided by ``ln(n_categories)`` -> [0, 1]."""
    p = np.asarray(share_vec, dtype=float)
    p = p[p > 0]
    if p.size == 0 or n_categories <= 1:
        return float("nan")
    return float(-(p * np.log(p)).sum() / np.log(n_categories))


def hhi(share_vec) -> float:
    """Herfindahl concentration Σ s² (1 = one source, →0 = many)."""
    p = np.asarray(share_vec, dtype=float)
    return float((p * p).sum())


def effective_number(share_vec) -> float:
    """Inverse-Simpson effective count = 1 / HHI."""
    h = hhi(share_vec)
    return float(1.0 / h) if h > 0 else float("nan")


def top_n_share(share_vec, n: int) -> float:
    p = np.sort(np.asarray(share_vec, dtype=float))[::-1]
    return float(p[:n].sum())


def _finite_pw(positions, weights=None):
    """Finite positions with matching **normalized** weights (uniform when ``weights``
    is None, so every metric below is unchanged without a confidence signal).

    Weights let a per-article *confidence* down-weight ambiguous items: a 0-confidence
    article contributes nothing, an *unknown*-confidence one (nan) falls back to the mean
    known weight (neutral, not dropped), and an all-zero/empty weight vector reverts to
    uniform so a metric never silently vanishes."""
    p = np.asarray(positions, dtype=float)
    fin = np.isfinite(p)
    p = p[fin]
    if weights is None:
        w = np.ones(p.size)
    else:
        w = np.asarray(weights, dtype=float)[fin]
        known = np.isfinite(w)
        w = (np.where(known, np.clip(w, 0.0, None), np.mean(w[known])) if known.any()
             else np.ones(p.size))
    s = w.sum()
    if s <= 0:
        w = np.ones(p.size)
        s = float(p.size)
    return p, (w / s if s > 0 else w)


def viewpoint_shares(positions, tau: float = LEAN_TAU, weights=None):
    """(left, centre, right) fractions of political items by lean band (confidence-
    weighted when ``weights`` given, else equal-weighted)."""
    p, w = _finite_pw(positions, weights)
    if p.size == 0:
        return float("nan"), float("nan"), float("nan")
    left = float(w[p < -tau].sum())
    right = float(w[p > tau].sum())
    return left, 1.0 - left - right, right


def cross_cutting_share(positions, center: float = CENTER, weights=None) -> float:
    """Share of political items on the *opposite* side of the user's own (weighted) mean."""
    p, w = _finite_pw(positions, weights)
    if p.size == 0:
        return float("nan")
    mu = float((w * p).sum())
    if mu == center:
        return float("nan")
    opposite = (p - center) * np.sign(mu - center) < 0
    return float(w[opposite].sum())


def echo_score(left: float, right: float) -> float:
    """0 (balanced L/R) .. 1 (one-sided) = 1 - 2·min(L,R)/(L+R)."""
    nonc = left + right
    if nonc <= 0:
        return float("nan")
    return float(max(0.0, 1.0 - 2.0 * (min(left, right) / nonc)))


def percentiles(values) -> np.ndarray:
    """Percentile rank (0-100) of each finite entry within the finite entries."""
    v = np.asarray(values, dtype=float)
    out = np.full(v.shape, np.nan)
    ok = np.isfinite(v)
    k = int(ok.sum())
    if k == 1:
        out[ok] = 50.0
    elif k > 1:
        out[ok] = (rankdata(v[ok], method="average") - 1) / (k - 1) * 100.0
    return out


# --------------------------------------------------------------------------- #
# Population aggregation
# --------------------------------------------------------------------------- #
def _onehot(labels):
    uniq, inv = np.unique(labels, return_inverse=True)
    n = len(labels)
    oh = sp.csr_matrix((np.ones(n), (np.arange(n), inv)), shape=(n, len(uniq)))
    return oh, uniq


def _row_shares(M):
    tot = M.sum(axis=1, keepdims=True)
    return np.divide(M, tot, where=tot > 0, out=np.zeros_like(M, dtype=float))


def compute(mind: MINDData, min_clicks: int = 5, min_political: int = 3,
            top_n: int = 4, register=None, emotion=None, selective=None,
            source=None, confidence=None) -> dict:
    """Per-user raw metrics + population percentiles + the aux matrices.

    ``register`` (per-column P(reporting)) and ``emotion`` (dict label->per-column
    share) are optional enrichment from ``classify_register.py`` /
    ``classify_emotion.py``; when given they populate the Reporting Ratio /
    Emotional Balance scores and the attention profile.

    ``confidence`` (per-column axis confidence in [0,1], from ``classify_lean.py``'s
    top-2 margin) *weights* the viewpoint / echo / cross-cutting metrics so ambiguous
    articles count less -- the per-article down-weighting the reliability analysis
    (``examples/lean_agreement.py``) motivates. Absent, every article counts equally.
    """
    A = mind.dataset.matrix.tocsr().astype(float)
    n_users = A.shape[0]
    cats = np.asarray(mind.categories)
    # the "source" axis defaults to publisher (`outlets`); a domain can override it
    # (e.g. Reddit uses the subreddit in `subcategories`) so Source Diversity works.
    outs = np.asarray(mind.outlets if source is None else source)
    pos = np.asarray(mind.item_positions, dtype=float)
    pol = np.asarray(mind.political, dtype=bool)
    conf = None if confidence is None else np.asarray(confidence, dtype=float)
    n_clicks = np.asarray(A.sum(axis=1)).ravel()

    # Topic & source distributions per user (vectorised matrix products).
    cat_oh, cat_u = _onehot(cats)
    out_oh, out_u = _onehot(outs)
    UC = np.asarray((A @ cat_oh).todense())            # users x categories
    UO = np.asarray((A @ out_oh).todense())            # users x outlets
    if "" in set(out_u):                               # drop the unknown-outlet bucket
        keep = out_u != ""
        UO, out_u = UO[:, keep], out_u[keep]

    n_cat = len(cat_u)
    SC, SO = _row_shares(UC), _row_shares(UO)
    with np.errstate(divide="ignore", invalid="ignore"):
        topic = -np.nansum(np.where(SC > 0, SC * np.log(SC), 0.0), axis=1) / np.log(n_cat)
    hhi_u = (SO * SO).sum(axis=1)
    eff_src = np.divide(1.0, hhi_u, where=hhi_u > 0,
                        out=np.full_like(hhi_u, np.nan, dtype=float))
    topn = np.sort(SO, axis=1)[:, ::-1][:, :top_n].sum(axis=1)

    enough = n_clicks >= min_clicks
    for arr in (topic, eff_src, topn):
        arr[~enough] = np.nan

    # Share of a user's reading that is political at all (context, not a score).
    pol_clicks = np.asarray(A @ pol.astype(float)).ravel()
    political_share = np.divide(pol_clicks, n_clicks, where=n_clicks > 0,
                                out=np.full(n_users, np.nan))
    political_share[~enough] = np.nan

    # Viewpoint metrics over each user's political clicks.
    cross = np.full(n_users, np.nan)
    echo = np.full(n_users, np.nan)
    mean_lean = np.full(n_users, np.nan)
    n_pol = np.zeros(n_users, dtype=int)
    for u in range(n_users):
        items = A.indices[A.indptr[u]:A.indptr[u + 1]]
        m = pol[items] & np.isfinite(pos[items])
        pp = pos[items][m]
        n_pol[u] = pp.size
        if pp.size < min_political:
            continue
        ww = conf[items][m] if conf is not None else None
        L, _, R = viewpoint_shares(pp, weights=ww)
        cross[u] = cross_cutting_share(pp, weights=ww)
        echo[u] = echo_score(L, R)
        _, wn = _finite_pw(pp, ww)
        mean_lean[u] = float((wn * pp).sum())

    # Optional enrichment: reporting register + emotional attention.
    reporting = reporting_pct = None
    if register is not None:
        reg = np.asarray(register, dtype=float)
        known = np.isfinite(reg)
        num = np.asarray(A.multiply(np.where(known, reg, 0.0)).sum(axis=1)).ravel()
        den = np.asarray(A.multiply(known.astype(float)).sum(axis=1)).ravel()
        reporting = np.divide(num, den, where=den > 0, out=np.full(n_users, np.nan))
        reporting[~enough] = np.nan
        reporting_pct = percentiles(reporting)

    attn = balance = balance_pct = emo_labels = None
    if emotion is not None:
        emo_labels = list(emotion)
        EMO = np.column_stack([np.nan_to_num(np.asarray(emotion[l], dtype=float))
                               for l in emo_labels])
        known = np.isfinite(np.asarray(emotion[emo_labels[0]], dtype=float))
        UE = np.asarray(A @ EMO)
        cnt = np.asarray(A.multiply(known.astype(float)).sum(axis=1)).ravel()
        attn = np.divide(UE, cnt[:, None], where=cnt[:, None] > 0,
                         out=np.full(UE.shape, np.nan))
        charged = [i for i, l in enumerate(emo_labels) if l.lower() in ("fear", "outrage")]
        balance = (1.0 - attn[:, charged].sum(axis=1) if charged
                   else np.full(n_users, np.nan))
        bad = (cnt < 1) | ~enough
        balance[bad] = np.nan
        attn[bad] = np.nan
        balance_pct = percentiles(balance)

    # Selective exposure (cross-cutting click-through), if impressions were passed.
    selective_pct = None
    if selective is not None:
        sel = np.asarray(selective, dtype=float).copy()
        sel[~enough] = np.nan
        selective_pct = percentiles(sel)

    return dict(
        n_clicks=n_clicks, n_pol=n_pol, political_share=political_share,
        topic=topic, eff_src=eff_src, topn=topn, cross=cross, echo=echo,
        mean_lean=mean_lean, item_confidence=conf,
        topic_pct=percentiles(topic), source_pct=percentiles(eff_src),
        viewpoint_pct=percentiles(cross), echo_pct=percentiles(-echo),  # less echo = higher
        reporting=reporting, reporting_pct=reporting_pct,
        balance=balance, balance_pct=balance_pct, attn=attn, emo_labels=emo_labels,
        selective_pct=selective_pct,
        UC=UC, UO=UO, cat_u=cat_u, out_u=out_u,
        catalog_cat_share=shares(np.bincount(_onehot(cats)[0].indices, minlength=n_cat)),
        top_n=top_n,
    )


def population_summary(pop: dict, eligible) -> dict:
    """Population-level context for the report — the *typical* reader, so an individual
    percentile has an absolute anchor ("you read 12 topics; the median reader reads 7").

    Aggregates the **raw** metric values over the ``eligible`` users (median + IQR), the
    share who are *political readers* (have a defined viewpoint score), and the median
    political-engagement share. All higher-is-healthier, matching the per-user scores."""
    idx = np.asarray(eligible, dtype=int)

    def stat(arr):
        if arr is None:
            return None
        a = np.asarray(arr, dtype=float)[idx]
        a = a[np.isfinite(a)]
        if a.size == 0:
            return None
        return dict(median=float(np.median(a)), p25=float(np.percentile(a, 25)),
                    p75=float(np.percentile(a, 75)), n=int(a.size))

    echo = pop.get("echo")
    metrics = {"Topic Diversity": pop.get("topic"),
               "Source Diversity": pop.get("eff_src"),
               "Viewpoint Balance": pop.get("cross"),
               "Echo Chamber Score": (1.0 - np.asarray(echo, dtype=float)
                                      if echo is not None else None),
               "Reporting Ratio": pop.get("reporting"),
               "Emotional Balance": pop.get("balance")}
    cross = np.asarray(pop["cross"], dtype=float)[idx]
    return dict(n_users=int(idx.size),
                metrics={k: stat(v) for k, v in metrics.items()},
                political_reader_frac=(float(np.isfinite(cross).mean())
                                       if cross.size else float("nan")),
                median_political_share=stat(pop.get("political_share")))


_POP_UNITS = {"Topic Diversity": "normalised topic entropy (0–1)",
              "Source Diversity": "effective # sources",
              "Viewpoint Balance": "cross-cutting share (0–1)",
              "Echo Chamber Score": "balance = 1−echo (0–1)",
              "Reporting Ratio": "P(reporting)",
              "Emotional Balance": "1−(fear+outrage) share"}


def format_population(summary: dict) -> str:
    """Render the population summary as a text block."""
    L = ["POPULATION VIEW — the typical reader", "=" * 38,
         f"profiled readers: {summary['n_users']}"]
    pr = summary.get("political_reader_frac")
    if pr is not None and np.isfinite(pr):
        L.append(f"political readers (have a viewpoint score): {pr * 100:.0f}%")
    L.append("\nmedian reader (raw metric · IQR):")
    for name, s in summary["metrics"].items():
        if s is None:
            continue
        L.append(f"  {name:<20} {s['median']:.2f}  "
                 f"(IQR {s['p25']:.2f}–{s['p75']:.2f}; {_POP_UNITS.get(name, '')})")
    return "\n".join(L)


def user_report(pop: dict, mind: MINDData, u: int) -> dict:
    """Assemble the detailed report dict for one user."""
    UC, UO, cat_u, out_u = pop["UC"], pop["UO"], pop["cat_u"], pop["out_u"]
    p_c = shares(UC[u])
    s_o = shares(UO[u])
    q_c = pop["catalog_cat_share"]

    top_cats = sorted(((cat_u[i], p_c[i]) for i in np.argsort(-p_c)[:3] if p_c[i] > 0),
                      key=lambda x: -x[1])
    gaps = sorted(((cat_u[i], p_c[i], q_c[i]) for i in range(len(cat_u))
                   if q_c[i] > 0.02 and p_c[i] < 0.5 * q_c[i]),
                  key=lambda x: -(x[2] - x[1]))
    top_pubs = [(out_u[i], s_o[i]) for i in np.argsort(-s_o)[:pop["top_n"]] if s_o[i] > 0]

    def _sc(arr):
        a = pop.get(arr)
        return None if a is None or not np.isfinite(a[u]) else round(float(a[u]))

    scores = {"Topic Diversity": _sc("topic_pct"),
              "Source Diversity": _sc("source_pct"),
              "Reporting Ratio": _sc("reporting_pct"),
              "Emotional Balance": _sc("balance_pct"),
              "Echo Chamber Score": _sc("echo_pct"),
              "Viewpoint Balance": _sc("viewpoint_pct"),
              "Open-Mindedness": _sc("selective_pct")}
    have = [v for v in scores.values() if v is not None]
    overall = round(float(np.mean(have))) if have else None

    attention = None
    if pop.get("attn") is not None and np.all(np.isfinite(pop["attn"][u])):
        attention = {l: float(pop["attn"][u][i]) for i, l in enumerate(pop["emo_labels"])}

    ps = pop.get("political_share")
    political_share = (float(ps[u]) if ps is not None and np.isfinite(ps[u]) else None)

    conf = pop.get("item_confidence")
    ppos = _political_positions(mind, u)
    pconf = _political_confidence(mind, u, conf) if conf is not None else None
    viewpoint_confidence = (float(np.nanmean(pconf)) if pconf is not None and pconf.size
                            and np.isfinite(pconf).any() else None)

    return dict(
        user=int(u), n_clicks=int(pop["n_clicks"][u]), n_political=int(pop["n_pol"][u]),
        scores=scores, overall=overall, attention=attention,
        political_share=political_share,
        top_categories=top_cats, blind_spots=gaps[:2], top_publishers=top_pubs,
        top_n_share=float(pop["topn"][u]) if np.isfinite(pop["topn"][u]) else None,
        effective_sources=float(pop["eff_src"][u]) if np.isfinite(pop["eff_src"][u]) else None,
        distinct_outlets=int((UO[u] > 0).sum()),
        viewpoint=viewpoint_shares(ppos, weights=pconf),
        viewpoint_confidence=viewpoint_confidence,
        mean_lean=float(pop["mean_lean"][u]) if np.isfinite(pop["mean_lean"][u]) else None,
    )


def _political_positions(mind: MINDData, u: int) -> np.ndarray:
    A = mind.dataset.matrix.tocsr()
    pos = np.asarray(mind.item_positions, dtype=float)
    pol = np.asarray(mind.political, dtype=bool)
    items = A.indices[A.indptr[u]:A.indptr[u + 1]]
    return pos[items][pol[items] & np.isfinite(pos[items])]


def _political_confidence(mind: MINDData, u: int, conf) -> np.ndarray:
    """Per-article confidence for user u's political items, aligned 1:1 with
    :func:`_political_positions` (same items, same finite-position mask)."""
    A = mind.dataset.matrix.tocsr()
    pos = np.asarray(mind.item_positions, dtype=float)
    pol = np.asarray(mind.political, dtype=bool)
    items = A.indices[A.indptr[u]:A.indptr[u + 1]]
    return np.asarray(conf, dtype=float)[items][pol[items] & np.isfinite(pos[items])]


def _na_reason(rep: dict, name: str, labels: dict | None = None) -> str:
    """Short why-it's-blank note for a *structurally* undefined score, so an
    unavoidable ``n/a`` reads as a known limitation rather than a broken metric.
    On MIND, Source Diversity is always undefined: the URLs are MSN URLs, so the
    original publisher isn't in the data (needs an external source-map)."""
    lab = labels or _LABELS["news"]
    if (name == "Source Diversity" and rep["scores"].get(name) is None
            and rep.get("distinct_outlets", 0) == 0):
        return lab.get("source_na", "")
    if rep["scores"].get(name) is None:                 # domain-specific structural n/a
        return lab.get("na_reasons", {}).get(name, "")
    return ""


def _conf_band(vc: float) -> str:
    """Coarse label for an axis-confidence value in [0,1] (top-2 classifier margin)."""
    return "low" if vc < 0.34 else "medium" if vc < 0.67 else "high"


def format_report(rep: dict, labels: dict | None = None) -> str:
    """Render the report dict as the INFORMATION HEALTH REPORT text block."""
    lab = labels or _LABELS["news"]
    pol_clause = lab["pol_clause"].format(n_political=rep["n_political"])
    L = ["INFORMATION HEALTH REPORT", "=" * 32,
         f"user #{rep['user']}   ({rep['n_clicks']} {lab['unit']}{lab['read_suffix']}"
         f"{pol_clause})\n"]
    if rep["overall"] is not None:
        L.append(f"Overall Score: {rep['overall']}/100   "
                 "(illustrative unweighted avg of v1 dimensions)\n")
    for name, v in rep["scores"].items():
        if v is not None:
            L.append(f"{name}: {v}/100")
        else:
            why = _na_reason(rep, name, lab)
            L.append(f"{name}: n/a" + (f"  ({why})" if why else ""))
    L.append("")

    if rep["top_n_share"] is not None and rep["top_publishers"]:
        L.append("Biggest Insight:")
        L.append("  " + lab["insight"].format(
            pct=rep["top_n_share"] * 100, n=len(rep["top_publishers"]),
            m=rep["distinct_outlets"]) + "\n")
    if lab["show_blindspot"] and rep["blind_spots"]:
        cat, us, cs = rep["blind_spots"][0]
        L.append("Blind Spot:")
        L.append(f"  You read little '{cat}' news — {us * 100:.0f}% of your reading "
                 f"vs {cs * 100:.0f}% of the catalog.\n")
    topics_src = (rep["top_categories"] if lab["topics_from"] == "categories"
                  else rep["top_publishers"])
    if topics_src:
        L.append(f"{lab['topics_label']}:  " + ", ".join(f"{c} {s * 100:.0f}%"
                                                         for c, s in topics_src))
    lo, ce, ri = rep["viewpoint"]
    if np.isfinite(lo):
        L.append(f"Viewpoint mix: left {lo * 100:.0f}% · centre {ce * 100:.0f}% · "
                 f"right {ri * 100:.0f}%")
        vc = rep.get("viewpoint_confidence")
        if vc is not None:
            L.append(f"  Axis confidence: {vc:.2f} ({_conf_band(vc)}) — mean top-2 margin "
                     "of the lean classifier over your political reading; low = placed "
                     "from ambiguous headlines, so read the mix as directional.")
    if lab["show_political_share"] and rep.get("political_share") is not None:
        L.append(f"Political reading: {rep['political_share'] * 100:.0f}% of your clicks")
    if rep.get("attention"):
        L.append("Attention profile (experimental):  " + "  ".join(
            f"{k} {v * 100:.0f}%" for k, v in rep["attention"].items()))
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# User-facing rendering (standalone HTML, no external deps)
# --------------------------------------------------------------------------- #
_CSS = """
:root{--ink:#2b2b2b;--mute:#8a8a8a;--user:#4C72B0;--right:#C44E52;--keep:#55A868;--score:#5a7d9a}
*{box-sizing:border-box} body{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
  color:var(--ink);max-width:760px;margin:24px auto;padding:0 16px;background:#fafafa}
h1{font-size:22px;margin:0 0 4px} .disclaimer{color:var(--mute);font-size:13px;margin:0 0 4px}
.legend{color:var(--mute);font-size:12px;margin:0 0 20px}
.card{background:#fff;border:1px solid #e7e7e7;border-radius:12px;padding:18px 20px;
  margin:0 0 18px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.head{display:flex;justify-content:space-between;align-items:flex-start}
.head h2{font-size:17px;margin:0} .sub{color:var(--mute);font-size:13px}
.overall{text-align:center;color:var(--keep);font-weight:700;font-size:30px;line-height:1}
.overall span{font-size:14px;color:var(--mute)} .ov-note{font-size:10px;color:var(--mute);font-weight:400}
.scores{margin:10px 0}
.section{font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;
  color:var(--mute);margin:13px 0 5px}
.row{display:flex;align-items:center;gap:10px;margin:7px 0;font-size:13px}
.lbl{width:172px;flex:none;line-height:1.2} .hint{display:block;font-size:10px;color:var(--mute)}
.track{flex:1;height:9px;background:#eee;border-radius:6px;position:relative}
.fill{display:block;height:100%;background:var(--score);border-radius:6px}
.mid{position:absolute;left:50%;top:-3px;height:15px;width:1px;background:rgba(0,0,0,.30)}
.val{width:28px;text-align:right;color:var(--mute)} .na{color:var(--mute);font-style:italic}
.callout{background:#eef3fb;border-left:3px solid var(--user);padding:8px 12px;border-radius:6px;margin:8px 0;font-size:13px}
.callout.warn{background:#fcefef;border-left-color:var(--right)}
.vpwrap,.attnwrap{margin:12px 0} .vplbl,.attnlbl{font-size:13px;font-weight:600}
.vp,.attn-bar{display:flex;height:14px;border-radius:6px;overflow:hidden;margin-top:4px}
.vp .l{background:var(--user)} .vp .c{background:#cfcfcf} .vp .r{background:var(--right)}
.attn-bar span{display:block;height:100%}
.vplab,.attn-lab{font-size:12px;color:var(--mute);margin-top:3px}
.exp{font-size:10px;color:var(--mute);font-style:italic;font-weight:400}
.topics{font-size:13px;margin-top:10px} .attn{font-size:12px;color:var(--mute);margin-top:8px;font-style:italic}
"""

# Score grouping, one-line hints, and Attention-Profile bucket colours (UI).
_GROUPS = [("Variety", ["Topic Diversity", "Source Diversity"]),
           ("Balance & openness", ["Viewpoint Balance", "Echo Chamber Score", "Open-Mindedness"]),
           ("Tone & substance", ["Reporting Ratio", "Emotional Balance"])]
# Per-section honesty note (the political metrics rest on the MIND text-lean axis,
# a weak proxy ~0.27 vs human labels — see docs/RESULTS.md Limitation 1; a behavioral
# axis validated elsewhere (Politosphere) but isn't available for MIND articles).
_SECTION_NOTES = {"Balance & openness": "rests on a weak text-lean axis — directional only"}

# Domain presets — the report's nouns. "news" (default) reproduces the MIND wording
# exactly; "reddit" re-labels it for the Politosphere participation graph (items are
# subreddits, "source" = community). On Politosphere the political metrics sit on the
# *validated* behavioral axis (the inverse of MIND — see docs/HEALTH_REPORT.md).
_LABELS = {
    "news": dict(
        subject_bold="MSN-News", subject_tail=" reading (headlines only)",
        unit="articles", read_suffix=" read", pol_clause=", {n_political} political",
        sub_html="{n_clicks} articles · {n_political} political",
        insight="{pct:.0f}% of your reading came from your top {n} publishers "
                "(you read {m} distinct sources).",
        topics_label="Top topics", topics_from="categories",
        show_blindspot=True, show_political_share=True,
        source_attr="outlets",                      # publisher parsed from the URL
        section_notes=_SECTION_NOTES, hints={}, na_reasons={},
        source_na="no publisher labels — MIND URLs are MSN",
        attn_absent="Attention profile — run classify_emotion.py to populate"),
    "reddit": dict(
        subject_bold="Reddit", subject_tail=" political-subreddit participation",
        unit="subreddits", read_suffix="", pol_clause=", all political",
        sub_html="{n_clicks} subreddits",
        insight="{pct:.0f}% of your commenting came from your top {n} subreddits "
                "({m} distinct communities).",
        topics_label="Top subreddits", topics_from="publishers",
        show_blindspot=False, show_political_share=False,
        # the subreddit (always in `titles` as "r/<sub>", even on an older ingest) is
        # the "source"/community -> Source Diversity = community breadth; works without
        # a rebuild. The political metrics sit on the *validated* behavioral axis.
        source_attr="titles",
        section_notes={"Balance & openness": "on the validated behavioral axis "
                       "(lean_corr ≈ 0.57 ± 0.19 over 5 seeds) — see docs/RESULTS.md"},
        hints={"Source Diversity": "how many communities"},
        source_na="",
        # subreddits have no article text / no topic taxonomy, so the text-derived
        # metrics are *structurally* n/a here (running a classifier can't help) — say so.
        na_reasons={"Topic Diversity": "one category — every subreddit is political",
                    "Reporting Ratio": "no article text on Reddit",
                    "Emotional Balance": "no article text on Reddit",
                    "Open-Mindedness": "no impressions data in Politosphere"},
        attn_absent="Attention profile — n/a (no article text to classify on Reddit)"),
}
_HINTS = {"Topic Diversity": "how many topics you read",
          "Source Diversity": "how many publishers",
          "Viewpoint Balance": "reading across the centre",
          "Echo Chamber Score": "higher = less one-sided",
          "Open-Mindedness": "clicking the other side when shown",
          "Reporting Ratio": "reporting vs opinion",
          "Emotional Balance": "calmer vs more charged"}
_ATTN_COLORS = {"fear": "#C44E52", "outrage": "#E08A3E", "analysis": "#4C72B0",
                "positive": "#55A868", "neutral": "#cfcfcf"}


def _bar(label: str, score, hint: str = "", reason: str = "") -> str:
    lab = f'<span class="lbl">{label}<span class="hint">{hint}</span></span>'
    if score is None:
        na = f'n/a <span class="exp">{reason}</span>' if reason else "n/a"
        return f'<div class="row">{lab}<span class="na">{na}</span></div>'
    return (f'<div class="row">{lab}<span class="track">'
            f'<span class="fill" style="width:{score}%"></span><i class="mid"></i></span>'
            f'<span class="val">{score}</span></div>')


def _population_card_html(summary: dict) -> str:
    """A 'typical reader' context card (raw medians + IQR) for the top of the page."""
    rows = ""
    for name, s in summary["metrics"].items():
        if s is None:
            continue
        rows += (f'<div class="row"><span class="lbl">{name}'
                 f'<span class="hint">{_POP_UNITS.get(name, "")}</span></span>'
                 f'<span class="track"><span class="fill" style="width:'
                 f'{min(100, max(2, s["median"] * 100 if s["median"] <= 1 else 50)):.0f}%">'
                 f'</span></span><span class="val">{s["median"]:.2f}</span></div>')
    pr = summary.get("political_reader_frac")
    sub = f"{summary['n_users']} readers profiled"
    if pr is not None and np.isfinite(pr):
        sub += f" · {pr * 100:.0f}% political readers"
    return (f'<div class="card"><div class="head"><div><h2>Population view</h2>'
            f'<div class="sub">{sub}</div></div></div>'
            f'<div class="section">Typical reader (median · raw value)</div>'
            f'<div class="scores">{rows}</div></div>')


def render_html(reports, out: str | None = None,
                title: str = "Information Health Report",
                labels: dict | None = None, population: dict | None = None) -> str:
    """Render report dicts (from :func:`user_report`) as a standalone HTML page."""
    lab = labels or _LABELS["news"]
    cards = []
    if population is not None:
        cards.append(_population_card_html(population))
    for r in reports:
        bars = ""
        for sect, names in _GROUPS:
            present = [n for n in names if n in r["scores"]]
            if not present:
                continue
            note = lab.get("section_notes", _SECTION_NOTES).get(sect, "")
            note_html = f' <span class="exp">· {note}</span>' if note else ""
            bars += f'<div class="section">{sect}{note_html}</div>'
            hints = lab.get("hints", {})
            bars += "".join(
                _bar(n, r["scores"][n], hints.get(n, _HINTS.get(n, "")), _na_reason(r, n, lab))
                for n in present)
        overall = (f'<div class="overall">{r["overall"]}<span>/100</span>'
                   f'<div class="ov-note">illustrative</div></div>'
                   if r["overall"] is not None else "")
        insight = ""
        if r["top_n_share"] is not None and r["top_publishers"]:
            insight = ('<div class="callout"><b>Biggest insight.</b> '
                       + lab["insight"].format(pct=r["top_n_share"] * 100,
                                               n=len(r["top_publishers"]),
                                               m=r["distinct_outlets"]) + '</div>')
        blind = ""
        if lab["show_blindspot"] and r["blind_spots"]:
            cat, us, cs = r["blind_spots"][0]
            blind = (f'<div class="callout warn"><b>Blind spot.</b> Little '
                     f'&lsquo;{cat}&rsquo; news — {us * 100:.0f}% of your reading vs '
                     f'{cs * 100:.0f}% of the catalog.</div>')
        lo, ce, ri = r["viewpoint"]
        vp = ""
        if lo == lo:                                          # not NaN
            vc = r.get("viewpoint_confidence")
            conf_html = (f' <span class="exp">· axis confidence {vc:.2f} '
                         f'({_conf_band(vc)})</span>' if vc is not None else "")
            vp = (f'<div class="vpwrap"><span class="vplbl">Viewpoint mix{conf_html}</span>'
                  f'<div class="vp"><span class="l" style="width:{lo * 100:.0f}%"></span>'
                  f'<span class="c" style="width:{ce * 100:.0f}%"></span>'
                  f'<span class="r" style="width:{ri * 100:.0f}%"></span></div>'
                  f'<div class="vplab">left {lo * 100:.0f}% · centre {ce * 100:.0f}% · '
                  f'right {ri * 100:.0f}%</div></div>')
        topics_src = (r["top_categories"] if lab["topics_from"] == "categories"
                      else r["top_publishers"])
        topics = ", ".join(f"{c} {s * 100:.0f}%" for c, s in topics_src)
        pol = (f' · <span style="color:var(--mute)">{r["political_share"] * 100:.0f}% '
               f'political</span>' if lab["show_political_share"]
               and r.get("political_share") is not None else "")
        if r.get("attention"):
            segs = "".join(
                f'<span style="width:{v * 100:.1f}%;background:'
                f'{_ATTN_COLORS.get(k.lower(), "#cfcfcf")}"></span>'
                for k, v in r["attention"].items())
            attn_lab = " · ".join(f"{k} {v * 100:.0f}%" for k, v in r["attention"].items())
            attn_html = (f'<div class="attnwrap"><span class="attnlbl">Attention profile</span> '
                         f'<span class="exp">experimental</span>'
                         f'<div class="attn-bar">{segs}</div>'
                         f'<div class="attn-lab">{attn_lab}</div></div>')
        else:
            attn_html = f'<div class="attn">{lab["attn_absent"]}</div>'
        cards.append(
            f'<div class="card"><div class="head"><div><h2>Reader #{r["user"]}</h2>'
            f'<div class="sub">{lab["sub_html"].format(n_clicks=r["n_clicks"], n_political=r["n_political"])}</div>'
            f'</div>{overall}</div><div class="scores">{bars}</div>{insight}{blind}{vp}'
            f'<div class="topics"><b>{lab["topics_label"]}:</b> {topics}{pol}</div>{attn_html}</div>')
    html = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title}</title><style>{_CSS}</style></head><body>'
            f'<h1>{title}</h1><p class="disclaimer">A descriptive profile of your '
            f'<b>{lab["subject_bold"]}</b>{lab["subject_tail"]} — a mirror, not a verdict.</p>'
            f'<p class="legend">Scores are percentiles vs other readers; the '
            f'&#9474; tick marks the typical reader (50th), and higher is healthier.</p>'
            f'{"".join(cards)}</body></html>')
    if out:
        Path(out).write_text(html, encoding="utf-8")
    return html


def _read_impressions(path: str):
    """``behaviors.tsv`` -> ``(shown, clicked)`` dicts of ``user_id -> set(news_id)``
    from the Impressions column (``Nxxx-1`` clicked / ``Nxxx-0`` shown-not-clicked)."""
    shown, clicked = {}, {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 5 or not p[4]:
                continue
            sh = shown.setdefault(p[1], set())
            cl = clicked.setdefault(p[1], set())
            for tok in p[4].split():
                nid, _, lab = tok.rpartition("-")
                if not nid:
                    continue
                sh.add(nid)
                if lab == "1":
                    cl.add(nid)
    return shown, clicked


def selective_exposure_array(mind, behaviors_path: str, center: float = 0.0) -> np.ndarray:
    """Per-user **cross-cutting click-through**: of the *opposite-side* articles the
    feed actually showed a user, what fraction did they click?  ``nan`` if none were
    shown.  Higher = more willing to read the other side when offered (an *agency*
    signal, distinct from what ended up in their diet)."""
    shown, clicked = _read_impressions(behaviors_path)
    item_idx = {nid: i for i, nid in enumerate(np.asarray(mind.dataset.item_ids).tolist())}
    user_idx = {uid: r for r, uid in enumerate(np.asarray(mind.dataset.user_ids).tolist())}
    pos = np.asarray(mind.item_positions, dtype=float)
    theta = np.asarray(mind.user_positions if mind.user_positions is not None
                       else mind.user_positions_from_clicks(fill=np.nan), dtype=float)
    out = np.full(mind.n_users, np.nan)
    for uid, sh in shown.items():
        u = user_idx.get(uid)
        if u is None or not np.isfinite(theta[u]):
            continue
        side = np.sign(theta[u] - center)
        if side == 0:
            continue
        cl = clicked.get(uid, set())
        opp_shown = opp_clicked = 0
        for nid in sh:
            i = item_idx.get(nid)
            if i is None or not np.isfinite(pos[i]):
                continue
            if np.sign(pos[i] - center) == -side:            # opposite side of centre
                opp_shown += 1
                opp_clicked += nid in cl
        if opp_shown:
            out[u] = opp_clicked / opp_shown
    return out


def _load_item_csv(path: str, item_ids) -> dict:
    """Read a ``news_id,col1,col2,...`` CSV into ``{col: per-column array}`` aligned
    to ``item_ids`` (a column's value is ``nan`` where the article is absent)."""
    import csv
    idx = {nid: i for i, nid in enumerate(np.asarray(item_ids).tolist())}
    out = None
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        cols = [c for c in rd.fieldnames if c != "news_id"]
        out = {c: np.full(len(item_ids), np.nan) for c in cols}
        for row in rd:
            i = idx.get(row["news_id"])
            if i is None:
                continue
            for c in cols:
                try:
                    out[c][i] = float(row[c])
                except (TypeError, ValueError):
                    pass
    return out


def _eligible_pool(pop: dict, min_clicks: int, min_political: int | None = None) -> np.ndarray:
    """User indices above the click floor (and, if given, the political-click floor).

    On a full-catalog ingest most users read little or no political news, so their
    viewpoint/echo/open-mindedness scores are *legitimately* ``n/a``.  Passing
    ``min_political`` restricts a demo sample to users for whom those scores are
    defined (they still have diverse topics, so the other metrics stay meaningful)."""
    pool = np.flatnonzero(pop["n_clicks"] >= min_clicks)
    if min_political is not None:
        pool = pool[pop["n_pol"][pool] >= min_political]
    return pool


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--npz", required=True, help="ingested MIND .npz (from ingest_mind.py)")
    ap.add_argument("--user", type=int, default=None, help="report a single user index")
    ap.add_argument("--sample", type=int, default=3, help="report this many random users")
    ap.add_argument("--min-clicks", type=int, default=5)
    ap.add_argument("--min-political", type=int, default=3)
    ap.add_argument("--top-n", type=int, default=4, help="publishers in the concentration line")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--html", default=None, help="also write a standalone HTML report here")
    ap.add_argument("--register-csv", default=None,
                    help="news_id,reporting CSV from classify_register.py (Reporting Ratio)")
    ap.add_argument("--emotion-csv", default=None,
                    help="news_id,<buckets> CSV from classify_emotion.py (Attention/Emotional)")
    ap.add_argument("--confidence-csv", default=None,
                    help="news_id,position,confidence CSV from classify_lean.py: the "
                         "confidence column *weights* the viewpoint/echo metrics so "
                         "ambiguous (low-margin) articles count less (article-level "
                         "reliability, see examples/lean_agreement.py)")
    ap.add_argument("--behaviors", default=None,
                    help="MIND behaviors.tsv -> Open-Mindedness (cross-cutting click-through)")
    ap.add_argument("--require-political", action="store_true",
                    help="sample only users with >= --min-political political clicks, so "
                         "the viewpoint/echo/open-mindedness scores populate in the demo")
    ap.add_argument("--domain", choices=["news", "reddit"], default="news",
                    help="label preset: 'news' (MIND wording) or 'reddit' (Politosphere "
                         "participation — items are subreddits, political metrics on the "
                         "validated behavioral axis)")
    ap.add_argument("--population", action="store_true",
                    help="also show a 'typical reader' population view (raw medians + IQR "
                         "over all eligible readers), so each percentile has an absolute anchor")
    args = ap.parse_args()
    lab = _LABELS[args.domain]

    mind = MINDData.load(args.npz)
    register = emotion = selective = confidence = None
    if args.register_csv:
        register = _load_item_csv(args.register_csv, mind.dataset.item_ids)["reporting"]
    if args.emotion_csv:
        emotion = _load_item_csv(args.emotion_csv, mind.dataset.item_ids)
    if args.confidence_csv:
        confidence = _load_item_csv(args.confidence_csv, mind.dataset.item_ids).get("confidence")
        if confidence is None:
            print(f"NOTE: {args.confidence_csv} has no 'confidence' column (the old 2-column "
                  "format) — viewpoint confidence-weighting + the axis-confidence line are "
                  "OFF. Re-run classify_lean.py (notebook # 7) to regenerate it with the "
                  "confidence column.")
    if args.behaviors:
        selective = selective_exposure_array(mind, args.behaviors)
    # the "source" axis for Source Diversity: publisher (default) or, for reddit, the
    # subreddit (`subcategories`) — works on any Politosphere npz without a rebuild.
    src = None if lab["source_attr"] == "outlets" else np.asarray(
        getattr(mind, lab["source_attr"]))
    pop = compute(mind, min_clicks=args.min_clicks, min_political=args.min_political,
                  top_n=args.top_n, register=register, emotion=emotion,
                  selective=selective, source=src, confidence=confidence)
    eligible = _eligible_pool(pop, args.min_clicks)
    print(f"users={mind.n_users}  items={mind.n_items}  eligible(>= {args.min_clicks} "
          f"clicks)={eligible.size}")
    pool = eligible
    if args.require_political:
        pool = _eligible_pool(pop, args.min_clicks, args.min_political)
        print(f"with >= {args.min_political} political clicks: {pool.size} "
              "(the viewpoint/echo/open-mindedness scores populate for these)")
    print()
    if pool.size == 0:
        print("no users meet the floor."); return

    pop_summary = None
    if args.population and eligible.size:
        pop_summary = population_summary(pop, eligible)
        print(format_population(pop_summary))
        print("\n" + "-" * 60 + "\n")

    if args.user is not None:
        users = [args.user]
    else:
        rng = np.random.default_rng(args.seed)
        users = rng.choice(pool, size=min(args.sample, pool.size), replace=False)
    reports = [user_report(pop, mind, int(u)) for u in users]
    for rep in reports:
        print(format_report(rep, lab))
        print("\n" + "-" * 60 + "\n")
    if args.html:
        render_html(reports, out=args.html, labels=lab, population=pop_summary)
        print(f"wrote HTML report → {args.html}")


if __name__ == "__main__":
    main()
