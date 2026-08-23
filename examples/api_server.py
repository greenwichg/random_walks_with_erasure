"""JSON API over the real Information Health pipeline — the backend the web app talks to.

This is the thin **machine-readable** counterpart to ``examples/app.py`` (which renders
HTML). It exposes the *same* engine — the deterministic Information Health Report
(``health_report.compute`` / ``user_report``), the real **RWE-B** bounded-bridging
recommender (``rwe.RWEB``), and the grounded LLM narrative (``narrate_report``) — as JSON
shaped for the Next.js frontend (``web/types/domain.ts``). Nothing here re-implements an
algorithm; it serialises what the engine computes.

Data: point ``--npz`` at an ingested dataset (MIND / Politosphere) for real behaviour. With
no ``--npz`` it boots the repo's **own synthetic simulator** (``simulate_users.py``) so the
whole stack runs with zero external data — every metric is still computed by the real
pipeline, just over generated clicks. Enrichment (register/emotion/impressions) is wired the
same way ``app.py`` wires it, so Reporting Ratio / Emotional Balance / Open-Mindedness / the
attention profile populate.

    python examples/api_server.py                              # synthetic corpus, port 8000
    python examples/api_server.py --profile mind --npz mind_full.npz
    python examples/api_server.py --profile politosphere --npz politosphere.npz
    python examples/api_server.py --profile qbias --qbias allsides_balanced_news.csv
    RWE_PROFILE=mind RWE_NPZ=mind_full.npz python examples/api_server.py   # config-only
    export ANTHROPIC_API_KEY=...                               # optional: live AI-coach narrative

Endpoints (all JSON, CORS-enabled):
    GET  /api/health                    readiness + dataset summary
    GET  /api/report[?user=<id>]        Information Health Report for a reader
    GET  /api/recommendations[?user=&strategy=rwe-b|rwe-d|adaptive]   RWE recs (full articles)
    GET  /api/coach                     coach greeting + opening (grounded)
    POST /api/coach   {message,user}    grounded reply (LLM narrative if a key is set)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import tempfile
import http.server
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # sibling examples
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
import numpy as np
import health_report as hr
import score_reference
import narrate_report as nr
import obs_metrics
from rwe.mind import MINDData
# Re-export the settings schema + normaliser from the dependency-free ``settings_service`` leaf, so
# this module's public surface (``api_server.DEFAULT_SETTINGS`` / ``normalize_settings`` / ...) is
# unchanged for every existing caller while the definitions live in one place. What STAYS here is the
# only settings code that depends on recommender vocabulary — ``rec_params_from_settings`` (below).
from settings_service import (       # noqa: F401  (re-exported for callers)
    DEFAULT_SETTINGS, normalize_settings,
    _SETTINGS_THEMES, _SETTINGS_LANGUAGES,
    _clamp_int, _layered, _merge_bool_group,
)

# ------------------------------------------------------------------ #
# Domain mapping — engine labels -> frontend MetricKey (web/types/domain.ts)
# ------------------------------------------------------------------ #
_METRIC_KEYS = [
    ("topicDiversity", "Topic Diversity"),
    ("sourceDiversity", "Source Diversity"),
    ("reportingRatio", "Reporting Ratio"),
    ("emotionalBalance", "Emotional Balance"),
    ("echoChamber", "Echo Chamber Score"),
    ("viewpointBalance", "Viewpoint Balance"),
    ("openMindedness", "Open-Mindedness"),
]

_IMPROVEMENTS = {
    "viewpointBalance": ("Add two cross-cutting reads a week",
                         "Your reading sits mostly on one side of the centre. Two opposite-but-close "
                         "reads a week lift Viewpoint Balance the most.", 8),
    "emotionalBalance": ("Trade one charged read a day for analysis",
                         "A large share of your reading leans on fear and outrage. Swapping one for "
                         "calm analysis raises Emotional Balance.", 6),
    "sourceDiversity": ("Broaden beyond your top outlets",
                        "A few outlets dominate your diet. Two new sources meaningfully lift Source "
                        "Diversity.", 5),
    "echoChamber": ("Hear the other side on a contested topic",
                    "Your political reading is fairly one-sided. One good-faith opposite-side piece "
                    "loosens the echo chamber.", 5),
    "topicDiversity": ("Widen the range of subjects you read",
                       "You circle a few topics. Deliberately reading an unfamiliar subject lifts "
                       "Topic Diversity.", 4),
    "reportingRatio": ("Anchor opinion with reporting",
                       "Opinion outweighs reporting in your diet. Pairing commentary with straight "
                       "reporting raises the Reporting Ratio.", 4),
    "openMindedness": ("Click the cross-cutting reads we surface",
                       "When we show you the other side, engaging with it lifts Open-Mindedness — the "
                       "metric that measures receptiveness.", 5),
}


def _prettify(label: str) -> str:
    """`topic_3` -> `Topic 3`, `r/Conservative` -> `r/Conservative`, real names pass through."""
    s = str(label)
    if s.startswith("r/") or " " in s or s.isupper():
        return s
    return s.replace("_", " ").strip().title() if ("_" in s or s.islower()) else s


def _is_named(label) -> bool:
    """Whether a category label can be shown to a reader.

    ``ingest.classify_topic`` returns ``""`` for an article it cannot classify — deliberately, since
    a guessed topic is worse than an admitted unknown. That is a fine value to COUNT (those reads
    are real), but not one to NAME, and a blind spot is a recommendation built out of a name: the
    note interpolates it, so an unnamed category produced " is 31% of what's available, but barely
    shows up in your reading." — a sentence with a hole where its subject belongs, telling the
    reader to go read more of nothing."""
    return bool(str(label or "").strip())


# Human labels for the improvable metrics, for evidence prose (frontend still owns localisation).
_METRIC_LABEL = dict(_METRIC_KEYS)


def _pct_whole(x) -> int:
    """Whole-percent of a 0–1 share, matching how the report rounds shares in its own prose."""
    return int(round(float(x) * 100))


def _join_names(names) -> str:
    """`A` · `A and B` · `A, B, and C` — Oxford-joined names for evidence prose."""
    xs = [str(n) for n in names if str(n)]
    if not xs:
        return ""
    if len(xs) == 1:
        return xs[0]
    if len(xs) == 2:
        return f"{xs[0]} and {xs[1]}"
    return ", ".join(xs[:-1]) + f", and {xs[-1]}"


def _improvement_evidence(key, *, metric, topics, sources, viewpoint, attention, blind, measured):
    """User-specific evidence for one improvement, bound ONLY from fields already in *this* report.

    Returns ``(trigger, evidence, action, benefit, basis)`` — or ``None`` when the report lacks the
    grounded data to say anything specific, in which case the caller leaves the static title/detail to
    stand (backward compatible). This is **evidence binding, not generation**: every number traces to a
    field passed in (the same ``topics``/``sources``/``viewpoint``/``attention``/``blind`` that go into
    the payload, and the metric's own ``score``/``benchmark``), and ``basis`` records exactly which
    field fed each claim. No value is invented — in particular no alternate outlet the reader hasn't
    used is ever named (the catalog isn't in the report).

    **Mode-aware wording (RC2.1.1):** a *measured* report may describe the reader's actual reading; an
    *estimate* is built from the CHARACTER of the chosen outlets with **zero reads** (see
    :meth:`Backend.estimate`), so estimate wording never says "you've read"/"your reading" — it speaks
    to "the outlets you picked", mirroring the estimate's own blind-spot note. Displayed percentages are
    derived from the same rounded parts the evidence shows (so a trigger total always equals the sum of
    the parts on screen), and benefits use non-guaranteeing "Can improve/broaden …" wording — no impact
    is estimated here."""
    label = _METRIC_LABEL.get(key, _prettify(key))
    score = metric.get("score")
    benchmark = metric.get("benchmark")

    def _score_fallback(obs_measured, obs_estimate=None):
        """Always-grounded evidence from the metric's own score vs the typical reader — used when the
        distribution this metric would quote isn't meaningfully present (a raw ratio not on the report,
        or a low-political reader). The score-vs-benchmark trigger is a *score* statement, not a reading
        claim, so it is honest in both modes; the ``obs_*`` observation is chosen by mode. The comparison
        never claims 'below typical' unless the score truly is below the benchmark (the selection
        surfaces a reader's *lowest* metrics, which can still sit at or above the median)."""
        b = [{"field": "metric.score", "label": label, "value": float(score)}]
        trig = f"Your {label} is {score}"
        if benchmark is not None:
            b.append({"field": "metric.benchmark", "label": "typical reader", "value": float(benchmark)})
            if score < benchmark:
                trig += f", below the typical reader's {benchmark}."
            elif score == benchmark:
                trig += f", at the typical reader's {benchmark}."
            else:
                trig += f", above the typical reader's {benchmark} but still among your lowest metrics."
        else:
            trig += "."
        obs = obs_measured if measured else (obs_estimate or obs_measured)
        return trig, obs, b

    if key == "sourceDiversity":
        if measured and sources:
            top = sources[:2]
            pairs = [(str(s["source"]), _pct_whole(s["share"])) for s in top]
            names = _join_names([p[0] for p in pairs])
            labeled = _join_names([f"{p[0]} ({p[1]}%)" for p in pairs])
            basis = [{"field": "sources", "label": str(s["source"]), "value": float(s["share"])}
                     for s in top]
            share_pct = sum(p[1] for p in pairs)            # sum the DISPLAYED parts so totals agree
            trigger = f"{share_pct}% of your reading came from {names}."
            evidence = f"{labeled} account for most of your reading."
            action = f"Reading from an outlet beyond {names} would widen your sources."
        elif sources:
            n = len(sources)                       # estimate: speak to the RANGE of picks, not a mix
            basis = [{"field": "sources", "label": "outlets", "value": float(n)}]
            trigger = f"Your estimate is based on {n} outlet{'s' if n != 1 else ''}."
            evidence = "A wider range of outlets would raise Source Diversity."
            action = "Add a couple of outlets outside your usual set."
        else:
            trigger, evidence, basis = _score_fallback(
                "This tracks how many different outlets your reading draws on.",
                "This tracks how many different outlets your chosen sources span.")
            action = "Broaden beyond your top outlets."
        return trigger, evidence, action, f"Can broaden your {label}.", basis

    if key == "topicDiversity":
        basis = []
        trigger = None
        if topics:
            top = topics[:2]
            basis += [{"field": "topics", "label": str(t["topic"]), "value": float(t["share"])}
                      for t in top]
            pairs = [(str(t["topic"]), _pct_whole(t["share"])) for t in top]
            if measured:
                trigger = f"You've read {_join_names([f'{p[1]}% {p[0]}' for p in pairs])}."
            else:
                if len(pairs) == 1:
                    body = f"about {pairs[0][1]}% of the available content is {pairs[0][0]}"
                else:
                    body = (f"about {pairs[0][1]}% of the available content is {pairs[0][0]} "
                            f"and {pairs[1][1]}% is {pairs[1][0]}")
                trigger = f"Based on the outlets you picked, {body}."
        under = [str(b["topic"]) for b in (blind or [])][:2]
        if under:
            basis += [{"field": "blindSpots", "label": str(b["topic"]), "value": float(b["gap"])}
                      for b in (blind or [])[:2]]
            verb = ("underrepresented in your reading" if measured
                    else "lightly covered by the outlets you picked")
            evidence = f"{_join_names(under)} {'is' if len(under) == 1 else 'are'} {verb}."
            action = f"Reading a {under[0]} piece would broaden your topics."
        else:
            evidence = ("This tracks how many different subjects your reading spans." if measured
                        else "This reflects the range of subjects across the outlets you picked.")
            action = "Deliberately read an unfamiliar subject."
        if trigger is None:
            trigger, evidence, basis = _score_fallback(
                "This tracks how many different subjects your reading spans.",
                "This reflects the range of subjects across the outlets you picked.")
        return trigger, evidence, action, f"Can broaden your {label}.", basis

    if key in ("viewpointBalance", "echoChamber"):
        vp = viewpoint or {}
        l, c, r = float(vp.get("left", 0)), float(vp.get("center", 0)), float(vp.get("right", 0))
        if (l + c + r) > 0:
            basis = [{"field": "viewpoint", "label": side, "value": val}
                     for side, val in (("left", l), ("center", c), ("right", r))]
            lp, cp, rp = _pct_whole(l), _pct_whole(c), _pct_whole(r)
            subject = "Your political reading is" if measured else "The outlets you picked lean"
            trigger = f"{subject} {lp}% left, {cp}% center, {rp}% right."
            if key == "viewpointBalance":
                if l == r:                              # neutral, non-contradictory tie wording
                    evidence = ("Your left and right reading are evenly split." if measured
                                else "The outlets you picked are evenly split between left and right.")
                    action = "Adding cross-cutting reads would strengthen your viewpoint balance."
                else:
                    lean, weak = ("left", "right") if l > r else ("right", "left")
                    who = "Your reading leans" if measured else "The outlets you picked lean"
                    evidence = f"{who} {lean}; the other side is thin."
                    action = f"Adding a couple of {weak}-leaning reads would balance your viewpoints."
            else:
                evidence = (f"About {max(lp, rp)}% of your political reading sits on one side."
                            if measured
                            else f"The outlets you picked sit mostly on one side (about {max(lp, rp)}%).")
                action = "A good-faith opposite-side read loosens the echo chamber."
            return trigger, evidence, action, f"Can improve your {label}.", basis
        trigger, evidence, basis = _score_fallback(
            "This tracks how balanced your political reading is across the spectrum.",
            "This reflects how balanced the outlets you picked are across the spectrum.")
        action = ("Add a couple of cross-cutting reads." if key == "viewpointBalance"
                  else "Hear the other side on a contested topic.")
        return trigger, evidence, action, f"Can improve your {label}.", basis

    if key == "emotionalBalance":
        att = attention or {}
        fear, outrage, analysis = (float(att.get("fear", 0)), float(att.get("outrage", 0)),
                                   float(att.get("analysis", 0)))
        if (fear + outrage) > 0 or analysis > 0:
            basis = [{"field": "attention", "label": k, "value": float(att.get(k, 0))}
                     for k in ("fear", "outrage", "analysis")]
            fp, op, ap = _pct_whole(fear), _pct_whole(outrage), _pct_whole(analysis)
            charged = fp + op                               # sum the DISPLAYED parts so totals agree
            if measured:
                trigger = f"{charged}% of your reading leans on fear and outrage."
                action = "Swapping one charged read a day for calm analysis raises the balance."
            else:
                trigger = (f"About {charged}% of the content in the outlets you picked "
                           f"leans on fear and outrage.")
                action = "Favouring analysis over charged pieces would support a healthier balance."
            evidence = f"Fear {fp}% and outrage {op}%; analysis is {ap}%."
        else:
            trigger, evidence, basis = _score_fallback(
                "This tracks how much of your reading leans on fear and outrage rather than analysis.",
                "This reflects how much of the outlets you picked lean on fear and outrage "
                "rather than analysis.")
            action = "Trade one charged read a day for analysis."
        return trigger, evidence, action, f"Can improve your {label}.", basis

    if key == "reportingRatio":
        trigger, evidence, basis = _score_fallback(
            "This tracks how much of your reading is straight reporting rather than opinion.",
            "This reflects how much of the outlets you picked is straight reporting rather than opinion.")
        return trigger, evidence, "Pair commentary with a straight-reporting source.", f"Can improve your {label}.", basis

    if key == "openMindedness":                             # never reached in estimate mode (unavailable)
        trigger, evidence, basis = _score_fallback("This measures how often you engage views that challenge your own.")
        return trigger, evidence, "Open the cross-cutting reads we surface.", f"Can improve your {label}.", basis

    return None


def _attach_evidence(item, key, *, metric, topics, sources, viewpoint, attention, blind, measured):
    """Bind the RC2.1 evidence onto an improvement ``item`` in place (additive; selection/order/impact
    untouched). A no-op when :func:`_improvement_evidence` can't ground a claim, so the static
    title/detail stand alone and the payload stays backward compatible."""
    ev = _improvement_evidence(key, metric=metric, topics=topics, sources=sources,
                               viewpoint=viewpoint, attention=attention, blind=blind, measured=measured)
    if ev is not None:
        trigger, evidence, action, benefit, basis = ev
        item.update({"trigger": trigger, "evidence": evidence, "suggestedAction": action,
                     "expectedBenefit": benefit, "evidenceBasis": basis})
    return item


# --------------------------------------------------------------------------- #
# RC2.2 — deterministic dynamic impact estimation
# --------------------------------------------------------------------------- #
# The five metrics whose raw value is a closed-form function of the reader's own distribution, so a
# hypothetical read can be simulated with health_report's OWN raw functions and re-percentiled against
# the population raw array already in the cached model. Graph metrics (echoChamber, openMindedness) and
# any estimate report (no reads to perturb) use the deterministic deficit-band fallback instead.
_SIMULATABLE = {"topicDiversity", "sourceDiversity", "reportingRatio",
                "emotionalBalance", "viewpointBalance"}
#: population raw array (in ``pop``) that each metric's percentile is ranked against.
_RAW_POP_KEY = {"topicDiversity": "topic", "sourceDiversity": "eff_src",
                "reportingRatio": "reporting", "emotionalBalance": "balance",
                "viewpointBalance": "cross"}
#: how many times the suggested action is applied for the low / high ends of the band (matched to the
#: recommendation's own cadence — "one" vs "a couple / a few").
_ACTION_APPS = {"topicDiversity": (1, 3), "sourceDiversity": (1, 2), "reportingRatio": (1, 3),
                "emotionalBalance": (1, 3), "viewpointBalance": (1, 2)}
#: credibility cap on a single recommendation's estimated percentile gain. A few reads can swing a
#: sparse reader's raw metric a long way; an unbounded band (e.g. +25–43) reads as a bug and breaks
#: parity with the prior +4–8 scale, so the band is scaled into [0, _MAX_IMPACT] and its confidence is
#: lowered when it had to be capped (the honest signal that the underlying estimate was volatile).
_MAX_IMPACT = 10


def _sim_raw(key, pop, u, apps):
    """The reader's raw metric value after applying the suggested action ``apps`` times, computed with
    the SAME ``health_report`` raw functions the engine uses (no scoring change). Returns ``None`` when
    the inputs aren't clean enough to simulate honestly."""
    nclicks = pop.get("n_clicks")
    n = float(nclicks[u]) if nclicks is not None else 0.0
    if key == "topicDiversity":
        uc = np.asarray(pop["UC"][u], dtype=float).copy()
        if uc.sum() <= 0:
            return None
        uc[int(np.argmin(uc))] += apps                     # read into the most under-covered category
        return hr.normalized_entropy(hr.shares(uc), len(pop["cat_u"]))
    if key == "sourceDiversity":
        uo = np.asarray(pop["UO"][u], dtype=float)
        if uo.sum() <= 0:
            return None
        uo2 = np.concatenate([uo, np.ones(apps)])          # `apps` new outlets, one read each
        return hr.effective_number(hr.shares(uo2))
    if key == "reportingRatio":
        arr = pop.get("reporting")
        v0 = None if arr is None else float(arr[u])
        if v0 is None or not np.isfinite(v0) or n <= 0:
            return None
        return (v0 * n + 1.0 * apps) / (n + apps)          # add `apps` straight-reporting reads (=1.0)
    if key == "emotionalBalance":
        arr = pop.get("balance")
        v0 = None if arr is None else float(arr[u])
        if v0 is None or not np.isfinite(v0) or n <= 0:
            return None
        charged = max(0.0, 1.0 - v0)                       # charged share = 1 − balance
        return 1.0 - (charged * n) / (n + apps)            # add `apps` analysis reads (charged mass fixed)
    if key == "viewpointBalance":
        arr = pop.get("cross")
        v0 = None if arr is None else float(arr[u])
        npol_arr = pop.get("n_pol")
        npol = float(npol_arr[u]) if npol_arr is not None else 0.0
        if v0 is None or not np.isfinite(v0) or npol <= 0:
            return None
        return (v0 * npol + 1.0 * apps) / (npol + apps)    # add `apps` cross-cutting reads
    return None


def _sim_percentile(pop_raw, v):
    """Percentile of a hypothetical raw value ``v`` within the population's finite raw distribution —
    the same "fraction below" ranking the scores use (``_pct_vs_pop`` / ``percentiles``)."""
    if pop_raw is None or v is None or not np.isfinite(v):
        return None
    arr = np.asarray(pop_raw, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None
    return 100.0 * float((arr < v).mean())


def _impact_estimate(key, *, score, benchmark, measured, pop=None, u=None):
    """Deterministic estimated-impact band (percentile points) for one improvement.

    **Simulated** for the five distribution metrics of a *measured* report: perturb the reader's own
    distribution by the suggested action (1× for the low end, a few× for the high end), recompute the
    raw metric with ``health_report``'s functions, and re-percentile against the population raw array —
    every input is already in the cached model, so there is no new query and no scoring change.
    **Deficit-banded fallback** for graph metrics (echoChamber, openMindedness) and any estimate report
    (no reads to perturb): a coarse guide from how far the score sits below the typical reader.

    Returns a dict with ``low``/``high`` (the band), ``method``, ``metric``, ``confidence``,
    ``fromScore``/``toScore`` (the percentile it would move from → to, anchored to the score shown on
    the card), and a plain-language ``explanation``. Fully deterministic."""
    label = _METRIC_LABEL.get(key, _prettify(key))
    s = int(score)
    bench = 50 if benchmark is None else int(benchmark)
    head = max(0, 100 - s)

    band = None
    if measured and pop is not None and u is not None and key in _SIMULATABLE:
        try:
            raw_key = _RAW_POP_KEY[key]
            pr = pop.get(raw_key)
            v0 = float(pop[raw_key][u]) if pr is not None else float("nan")
            p0 = _sim_percentile(pr, v0)
            lo_apps, hi_apps = _ACTION_APPS[key]
            plo = _sim_percentile(pr, _sim_raw(key, pop, u, lo_apps))
            phi = _sim_percentile(pr, _sim_raw(key, pop, u, hi_apps))
            if None not in (p0, plo, phi):
                lo = int(round(max(0.0, plo - p0)))
                hi = int(round(max(0.0, phi - p0)))
                lo, hi = min(lo, hi), max(lo, hi)
                hi = min(hi, head)
                lo = min(lo, hi)
                band = (lo, hi)
        except Exception:
            band = None

    if band is not None:
        lo, hi = band
        capped = hi > _MAX_IMPACT
        if capped:                                     # scale into the credible range, keep the shape
            lo = int(round(lo * _MAX_IMPACT / hi)) if hi > 0 else 0
            hi = _MAX_IMPACT
            lo = min(lo, hi)
        n = int(pop["n_clicks"][u])
        method = "simulated"
        conf = "high" if (n >= 20 and not capped) else ("medium" if (n >= 8 and not capped) else "low")
        to_low, to_high = min(100, s + lo), min(100, s + hi)
        rng = f"+{lo}" if lo == hi else f"+{lo}–{hi}"
        explanation = (f"Simulated: taking this step would move your {label} percentile from {s} to "
                       f"about {to_low}–{to_high} ({rng}), by recomputing the metric with the added "
                       f"reading against the reference population.")
    else:
        gap = max(0, bench - s)
        mag = min(head, max(gap, 4))
        lo = max(0, min(head, _MAX_IMPACT, int(round(mag * 0.10))))
        hi = max(lo, min(head, _MAX_IMPACT, int(round(mag * 0.25))))
        if hi == 0:
            hi = min(2, head)
            lo = min(lo, hi)
        method, conf = "deficit", "low"
        to_low, to_high = min(100, s + lo), min(100, s + hi)
        explanation = (f"Estimated from how far your {label} ({s}) sits below the typical reader "
                       f"({bench}) — a rough guide; this metric isn't simulated per action yet.")

    return {"low": lo, "high": hi, "method": method, "metric": key, "confidence": conf,
            "fromScore": s, "toScore": {"low": to_low, "high": to_high},
            "explanation": explanation}


def _attach_impact(item, key, *, score, benchmark, measured, pop=None, u=None):
    """Attach the RC2.2 dynamic impact estimate to an improvement ``item`` and refresh the backward-compat
    scalar ``impact`` to the band midpoint (selection/order untouched — only the impact value changes)."""
    est = _impact_estimate(key, score=score, benchmark=benchmark, measured=measured, pop=pop, u=u)
    item["impact"] = int(round((est["low"] + est["high"]) / 2))
    item["impactEstimate"] = est
    return item


def _stable_int(*parts) -> int:
    h = hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(h[:8], 16)


def _iso_recent(seed, max_days: float = 5.0) -> str:
    frac = (_stable_int(seed) % 10_000) / 10_000.0
    dt = datetime.now(timezone.utc) - timedelta(days=frac * max_days)
    return dt.isoformat()


def _lean_bucket(pos: float, tau: float = hr.LEAN_TAU) -> str:
    """Bucket a lean onto left/center/right using the engine's own centre half-width."""
    if pos < -tau:
        return "left"
    if pos > tau:
        return "right"
    return "center"


def _topic_shares_of(rep: dict) -> dict:
    """``{topic: share 0..1}`` for the reader's claimable top topics, straight from the report's
    own ``top_categories`` (C6) — the measured facts behind "X represents N% of your reading".
    Blank / legacy-"general" buckets are excluded exactly like ``top_topics``."""
    out = {}
    for t, share in (rep.get("top_categories") or []):
        name = str(t).strip()
        if name and name.lower() != "general" and share is not None and np.isfinite(float(share)):
            out[_prettify(name)] = float(share)
    return out


def _lean_shares_of(rep: dict) -> dict:
    """``{"lean_shares": {"left","center","right"}}`` from the report's confidence-weighted
    viewpoint computation (C6), or ``{}`` when the reader is below the report's own political
    minimum (NaN shares) — no share, no claim. Returned as a splattable dict so callers simply
    ``**`` it into the context."""
    vp = rep.get("viewpoint")
    try:
        l, c, r = float(vp[0]), float(vp[1]), float(vp[2])
    except (TypeError, ValueError, IndexError):
        return {}
    if not (np.isfinite(l) and np.isfinite(c) and np.isfinite(r)):
        return {}
    return {"lean_shares": {"left": l, "center": c, "right": r}}


#: The default recommendation blend — how many columns each RWE strategy contributes to the
#: "all" feed (deduped first-seen; a single-strategy request uses ``(strategy, 12)`` instead).
#: Rationale (W5 audit): for a reader with a side, EVERY rwe-b (Bridging) column is an
#: opposite-viewpoint / cross-cutting article, so the rwe-b budget is the guaranteed
#: cross-cutting-cards-per-feed floor — "6" is the viewpoint-diversity dial — while the rwe-d
#: (Discovery) + adaptive columns buy source diversity (distinct outlets). Reader-invariant by
#: design. Single source of truth: imported by rec_explain and audit_story_coverage, and pinned
#: equal to the served feed by the parity tests.
DEFAULT_BLEND_PLAN = (("rwe-b", 6), ("rwe-d", 4), ("adaptive", 4))

#: Most cards one outlet may hold in a served feed.
#:
#: The feed deduped ARTICLES and nothing else, so a single high-volume outlet could take several
#: of the 14 slots — measured on the reference corpus, 33% of feeds repeated one outlet three or
#: more times and a 10-card feed averaged only 7.8 distinct outlets. Source diversity is a product
#: promise the blend already buys with its rwe-d/adaptive budget; those slots are wasted when they
#: land on an outlet the reader has already been shown.
#:
#: This is a DIVERSITY constraint, not an evidence gate: it never admits an item the strategy's
#: own admission rule rejected, never reorders within a publisher, and never lowers a threshold.
#:
#: The DEFAULT; the effective value is :func:`max_cards_per_publisher`, which reads
#: ``RWE_RECS_MAX_PER_PUBLISHER``. Every other tunable in this system (``RWE_STORY_MERGE_SIM``,
#: ``RWE_CLUSTER_LINK_QUORUM``, …) is env-settable so it can be moved during an incident without a
#: rebuild; this one shipped as a bare constant, which made its documented ``cap=0`` kill switch
#: reachable only by redeploying. That gap is what this pair closes.
MAX_CARDS_PER_PUBLISHER = 2


def max_cards_per_publisher() -> int:
    """Effective per-outlet feed cap. ``RWE_RECS_MAX_PER_PUBLISHER=0`` disables the constraint
    entirely — the kill switch, restoring pre-0541ed9 selection with a restart rather than a
    deploy. Junk or a negative value falls back to the default rather than silently disabling a
    product guarantee."""
    raw = os.environ.get("RWE_RECS_MAX_PER_PUBLISHER", "").strip()
    if not raw:
        return MAX_CARDS_PER_PUBLISHER
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return MAX_CARDS_PER_PUBLISHER
    return v if v >= 0 else MAX_CARDS_PER_PUBLISHER


def _env_cap(name: str) -> int:
    """A feed-quota cap read from the environment per call (kill-switch semantics, like
    :func:`max_cards_per_publisher`). ``0`` / unset / junk / negative → 0 = quota off — the
    Tier-1 quotas (docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md) ship dark and are turned on by an
    operator, not by a deploy."""
    raw = os.environ.get(name, "").strip()
    try:
        v = int(raw) if raw else 0
    except (TypeError, ValueError):
        return 0
    return v if v > 0 else 0


def max_cards_per_story() -> int:
    """Per-story feed cap (``RWE_REC_MAX_PER_STORY``; 0 = off). Five outlets' takes on one event
    are near-duplicates a reader experiences as one story served five times — X dedups whole
    conversations for the same reason (DedupConversationFilter). ``1`` is the intended setting:
    each story appears once, and which *take* fills that one slot is still the ranker's choice."""
    return _env_cap("RWE_REC_MAX_PER_STORY")


def max_cards_per_topic() -> int:
    """Per-topic feed cap (``RWE_REC_MAX_PER_TOPIC``; 0 = off). A monoculture guard, not a
    ranking signal: it bounds how much of one feed a single topic can occupy, the way the
    publisher cap bounds one outlet. Set it ABOVE what a maxed Interest slider should legitimately
    concentrate (the slider is the reader's explicit ask; ~6 of 14 leaves it meaningful)."""
    return _env_cap("RWE_REC_MAX_PER_TOPIC")


def blindspot_boost_enabled() -> bool:
    """``RWE_REC_BLINDSPOT`` — the Tier-1 blind-spot slice, v1
    (docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md). The report has always *named* the reader's
    under-read topics (``health_report.blind_spot_gaps``) while no candidate source acted on
    them; with this on, the discovery slice starts closing the gaps it names. Default off."""
    return os.environ.get("RWE_REC_BLINDSPOT", "0").strip() == "1"


# ── Tier-2 candidate sources (docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md) ─────────────────────────
# Three sources beyond the RWE family, each claiming a small budget of feed slots. They are the
# Source seam the Tier-1 status table consciously deferred ("belongs with the Tier-2 story
# source, the first change that genuinely needs a new Source seam"): a source contributes an
# ordered, already-admitted column list plus a slot budget, and everything downstream — first-seen
# dedup, the publisher/story/topic quotas, serialisation, the explain observer — is the SAME
# funnel the RWE slices flow through. No source replaces RWE-D/RWE-B; each *selects after* them
# by taking its budget out of the discovery/adaptive slices (never the rwe-b bridge floor, W5).


def story_source_slots() -> int:
    """``RWE_REC_STORY_SOURCE`` — feed slots the story source may claim (0 = off). The Tier-2
    "story source proper": takes on stories the reader has READ INTO, from publishers they have
    not read, opposite-lean takes first. Replaces the one-card ``RWE_STORY_SLOT`` post-pass when
    enabled (the source supersedes the slot; both firing would double-serve story cards)."""
    return _env_cap("RWE_REC_STORY_SOURCE")


def emerging_slots() -> int:
    """``RWE_REC_EMERGING`` — feed slots the emerging-story source may claim (0 = off). Stories
    gaining multi-publisher coverage NOW that the reader has not read into: early enough that the
    takes are still forming, validated by the same Story Service clusters the Stories page shows."""
    return _env_cap("RWE_REC_EMERGING")


def blindspot_slots() -> int:
    """``RWE_REC_BLINDSPOT_SLOTS`` — the Tier-2 blind-spot slice v2: its OWN slot budget (0 = off)
    rather than v1's rank nudge inside the discovery slice. Candidates are the reader's rwe-d
    ranking filtered to their measured gap topics, so the cards stay personalized and the claim
    ("a measured gap in your diet") stays the report's own. When v2 slots are set, the v1 boost
    is suppressed — one mechanism at a time, or the effect is unmeasurable."""
    return _env_cap("RWE_REC_BLINDSPOT_SLOTS")


#: The extra sources' fixed plan order (after the RWE slices, deterministic), and where their
#: budget comes from: non-bridge slices, taken round-robin from the END of the plan (adaptive
#: first, then rwe-d), each floored at 1 slot — the rwe-b budget is the cross-cutting floor (W5)
#: and is never touched. If the floors bind, the EXTRAS shrink, never the RWE slices below 1.
EXTRA_SOURCE_ORDER = ("story", "emerging", "blindspot")

#: Emerging-story gates: a story is "gaining coverage" only when at least this many distinct
#: publishers already cover it (the Story Service's own multi-publisher validation threshold),
#: and its newest member is at most this old. Both are claims the card's copy makes.
EMERGING_MIN_PUBLISHERS = 3
EMERGING_MAX_AGE_HOURS = 48.0


def _plan_with_extras(plan, extras) -> tuple:
    """Rebalance a blend plan to make room for extra-source budgets, preserving the feed's total
    length and the rwe-b bridge floor exactly.

    ``extras`` is ``[(name, k), …]`` in :data:`EXTRA_SOURCE_ORDER`. Slots are taken one at a
    time from the FULLEST non-``rwe-b`` plan entry (ties resolved toward the end of the plan:
    adaptive before rwe-d), so the tax spreads evenly instead of draining one slice; each donor
    is floored at 1 slot, and when no slice can give another slot the remaining extra budget is
    DROPPED — the feed never grows past the plan total, and no RWE slice ever reaches 0 (a slice
    at 0 would silently remove a strategy from the blend, which is a product change, not a
    rebalance). Extras with a resulting budget of 0 are omitted. Deterministic; ``extras`` empty
    → ``plan`` unchanged (the exact tuple, so the no-source path is provably the historical
    plan)."""
    extras = [(n, int(k)) for n, k in (extras or ()) if int(k) > 0]
    if not extras:
        return tuple(plan)
    counts = [[name, int(k)] for name, k in plan]
    donors = [e for e in counts if e[0] != "rwe-b"]
    granted = []
    for name, want in extras:
        got = 0
        for _ in range(want):
            donor = max(donors, key=lambda d: (d[1], donors.index(d)), default=None)
            if donor is None or donor[1] <= 1:
                break
            donor[1] -= 1
            got += 1
        if got:
            granted.append((name, got))
    return tuple((n, k) for n, k in counts) + tuple(granted)


#: The blind-spot rank divisor inside the RWE-D slice. In the interest-anchor currency
#: (weight-10 slider = 8): strong enough to pull a measured gap topic into the served slice from
#: ~4x the cutoff, deliberately below the sliders — a reader who explicitly demotes a topic
#: outbids the report's suggestion that they read more of it. RWE-D only, by design: it is the
#: "widens your sources" slice, so a boosted card's rationale stays true word for word, and the
#: bridge slice's political-only admits and cross-first ordering are never touched.
_BLINDSPOT_BOOST = 4.0

#: Extra columns each strategy ranks so the cap has something to backfill FROM. Without it a
#: declined slot would shrink the feed instead of moving to the next-ranked admissible item —
#: the same backfill principle ``_rec_cols_of`` already applies to admission.
REC_OVERFETCH = 3


def record_feed_composition(recs: list, *, user_side: float, kind: str,
                            story_of=None, already_shown=(), blindspot_topics=()) -> None:
    """Record what a served feed was MADE OF, as ``/api/metrics`` counters.

    Written because verifying the publisher cap in production required reconstructing, by hand and
    after the fact, evidence the box had never recorded: the engine emitted ``requests_total`` and
    friends but nothing about feed composition, so the deploy's whole effect was unmeasurable until
    someone thought to sample the endpoint. The cap happened to leave an observable signature; the
    next change to this path may not.

    Counters (sums, so any mean is ``x_total / feed_served_total`` for the same ``kind``):

    * ``feed_served_total``       feeds served
    * ``feed_cards_total``        cards in them            → mean feed length vs the plan
    * ``feed_outlets_total``      distinct publishers      → mean source spread
    * ``feed_cross_cutting_total``cross-cutting cards
    * ``feed_sided_reader_total`` feeds whose reader HAS a side — the honest denominator for the
      line above, since ``_cross_of`` scores zero for a reader with no measured lean and averaging
      over everyone understates the bridge by ~2x (measured in production: 2.78 over all readers,
      6.07 over sided ones, out of a 6-card bridge slice)
    * ``feed_top_outlet``         histogram of the top outlet's card count — the cap's signature
    * ``feed_empty_total``        feeds that came back with NOTHING

    That last one is not symmetry. Every counter above is conditioned on a non-empty feed, because
    a mean over empty feeds is meaningless — which means that without it, a regression that emptied
    feeds would make this instrumentation go QUIET rather than show a problem, and quiet reads as
    "less traffic" instead of "the feed broke". ``feed_served_total + feed_empty_total`` is the
    honest request count; the ratio between them is the health signal.

    **Feed-quality extension (Tier 1, docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md)** — the audit's
    evaluation framework, computed where the feed is assembled so eval and dashboards read one
    code path. All sums over non-empty feeds, same-``kind`` means as above:

    * ``feed_hhi_bp_total``       publisher-concentration HHI in basis points (Σ share² × 10⁴) —
      the metric ``PUBLISHER_CONCENTRATION_EVALUATION.md`` computed by hand, recorded per serve
    * ``feed_topics_total``       distinct topics — the spread the topic quota bounds
    * ``feed_story_dup_total``    cards beyond a story's first (``story_of``: article id → story
      id; the story quota's target, measurable before AND after enabling it)
    * ``feed_repeat_total``       cards this reader was already shown (``already_shown``: the
      repetition ids the request ranked with) — the repetition decay's target, same logic
    * ``feed_blindspot_total``    cards on the reader's measured blind-spot topics

    Median card age is deliberately absent: a card's real ``publishedAt`` is joined AFTER this
    point (``_enrich_rec_media``), and recording a fabricated age here would be the exact
    dishonesty Commit C4 removed from the serializer.

    ``kind`` separates the blended feed from single-strategy requests, whose plan totals differ;
    mixing them would corrupt every mean. Bounded by construction (a handful of kinds x a feed's
    length) and never raises — recording is purely observational."""
    try:
        if not recs:
            obs_metrics.incr(f"feed_empty_total|{kind}")
            return
        pubs: dict = {}
        topics: set = set()
        stories: dict = {}
        cross = repeat = blind = 0
        shown = set(already_shown or ())
        gaps = {str(t).strip().lower() for t in (blindspot_topics or ()) if str(t).strip()}
        for r in recs:
            art = r.get("article") or {}
            p = art.get("publisher") or "?"
            pubs[p] = pubs.get(p, 0) + 1
            t = str(art.get("topic") or "").strip().lower()
            if t:
                topics.add(t)
            if t and t in gaps:
                blind += 1
            aid = str(art.get("id") or "")
            if aid and aid in shown:
                repeat += 1
            if story_of is not None and aid:
                sid = story_of(aid)
                if sid is not None:
                    stories[sid] = stories.get(sid, 0) + 1
            if r.get("crossCutting"):
                cross += 1
        obs_metrics.incr(f"feed_served_total|{kind}")
        obs_metrics.incr(f"feed_cards_total|{kind}", len(recs))
        obs_metrics.incr(f"feed_outlets_total|{kind}", len(pubs))
        obs_metrics.incr(f"feed_cross_cutting_total|{kind}", cross)
        if user_side:
            obs_metrics.incr(f"feed_sided_reader_total|{kind}")
        obs_metrics.incr(f"feed_top_outlet|{kind}|{max(pubs.values())}")
        n = len(recs)
        obs_metrics.incr(f"feed_hhi_bp_total|{kind}",
                         int(round(10000 * sum((c / n) ** 2 for c in pubs.values()))))
        obs_metrics.incr(f"feed_topics_total|{kind}", len(topics))
        obs_metrics.incr(f"feed_story_dup_total|{kind}",
                         sum(c - 1 for c in stories.values() if c > 1))
        obs_metrics.incr(f"feed_repeat_total|{kind}", repeat)
        obs_metrics.incr(f"feed_blindspot_total|{kind}", blind)
        # Tier-2 sources: how many cards each non-RWE source actually placed (the experiment
        # denominators for "story source vs one-slot status quo" and friends). Bounded: the
        # strategy vocabulary is a fixed handful, and only non-RWE strategies emit a counter.
        by_strat: dict = {}
        for r in recs:
            s = str(r.get("strategy") or "?")
            if s not in ("rwe-b", "rwe-d", "adaptive"):
                by_strat[s] = by_strat.get(s, 0) + 1
        for s, n in by_strat.items():
            obs_metrics.incr(f"feed_source_cards_total|{kind}|{s}", n)
    except Exception:
        pass                                   # observation must never break a served feed


def _cross_of(user_side: float, lean: float, political: bool) -> bool:
    """The cross-cutting gate, shared by the recommendation serializer and the explain observer
    (Commit 21a) — one definition, so an explanation can never disagree with the card it explains.

    ``political`` (Commit R1) is the ARTICLE-level classification (corpus mask / scored flag): a
    non-political article can never be cross-cutting — its lean is only the outlet's house lean,
    which does not license "offers another political perspective" for a promo or a sports piece.
    The parameter is required (no default) so no call site can silently skip the gate."""
    return bool(political and user_side != 0 and np.sign(lean) == -user_side and abs(lean) >= 0.5)


def _familiarity_band(count: int, share: float) -> str:
    """Three-tier outlet familiarity behind the reason copy: ``never`` (no reads), ``rarely``
    (< 5 % of the reader's diet), ``familiar`` (>= 5 %)."""
    if count <= 0:
        return "never"
    return "rarely" if share < 0.05 else "familiar"


def _familiarity_of(pop: dict, u: int):
    """Per-outlet familiarity lookup for reader row ``u`` — the evidence behind reason claims.
    A sentence like "an outlet you rarely read" must be computed from the reader's measured
    outlet shares, never asserted (Commit 21a truthfulness fix)."""
    counts = np.asarray(pop["UO"][u], dtype=float)
    share = hr.shares(counts)
    idx = {str(n): i for i, n in enumerate(pop["out_u"])}

    def fam(publisher: str) -> dict:
        i = idx.get(str(publisher))
        c = int(counts[i]) if i is not None else 0
        s = float(share[i]) if i is not None else 0.0
        return {"reads": c, "share": s, "band": _familiarity_band(c, s)}

    return fam


def _score_band(score) -> str:
    """The product's health band for a 0–100 score — the single source of truth for the
    thresholds the UI shows (web `scoreBand()` consumes this, falling back to the same cut-offs)."""
    if score is None:
        return "Unknown"
    if score >= 67:
        return "Healthy"
    if score >= 40:
        return "Fair"
    return "Needs work"


def _register_enum(p_reporting) -> str:
    if p_reporting is None or not np.isfinite(p_reporting):
        return "mixed"
    if p_reporting >= 0.6:
        return "reporting"
    if p_reporting <= 0.4:
        return "opinion"
    return "mixed"


#: Zone names already reported as unresolvable — warned about once each, not once per read.
_ZONE_MISSES: set = set()


def _zone(time_zone: "str | None"):
    """An IANA name → tzinfo, or UTC. Never raises: an unresolvable name degrades to UTC, which is
    the behaviour every caller had before zones existed.

    The degradation is LOUD. A name is only ever stored because it resolved on the machine that
    stored it, so an unresolvable one here means the tz database changed underneath the data —
    typically a rebuild whose base image dropped the backward-compatibility links. Browsers really
    do report those: Chrome on many systems says ``Asia/Calcutta``, not ``Asia/Kolkata``. Silently
    falling back to UTC would revert exactly the readers this feature exists for, and would look
    like nothing at all; observed once per distinct name, it is one grep away."""
    if not time_zone:
        return timezone.utc
    name = str(time_zone)
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        if name not in _ZONE_MISSES:
            _ZONE_MISSES.add(name)
            import logging
            logging.getLogger(__name__).warning(
                json.dumps({"event": "time_zone_unresolvable", "timeZone": name,
                            "effect": "day bucketing for this reader fell back to UTC",
                            "likelyCause": "the tz database lacks this name — install tzdata"}))
        return timezone.utc


def _local_day(ts, time_zone: "str | None" = None) -> "str | None":
    """The ``YYYY-MM-DD`` day an instant falls on **in the reader's zone**.

    A day is a local idea. Slicing ``ts[:10]`` takes the UTC day, which is a different day from the
    reader's for part of every 24 hours — the whole evening in Asia, the small hours in the
    Americas. A Delhi reader finishing an article at 02:00 local was filed under the UTC day that
    had ended two hours earlier, so a genuinely unbroken week of late-night reading showed up as a
    broken streak. With no zone this returns the UTC day, exactly as before."""
    if not isinstance(ts, str) or len(ts) < 10:
        return None
    if time_zone is None:
        return ts[:10]
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts[:10]                       # unparseable: the prefix is the best available answer
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)   # the engine's own naive stamps are UTC (see _utc_marked)
    return d.astimezone(_zone(time_zone)).date().isoformat()


def _local_days(read_ats, time_zone: "str | None" = None) -> set:
    """The distinct reader-local days a set of ``readAt`` strings falls on."""
    return {d for d in (_local_day(ra, time_zone) for ra in read_ats) if d}


def _reading_streak(read_ats, time_zone: "str | None" = None) -> int:
    """Consecutive days ending today on which the reader recorded at least one read, from ISO
    ``readAt`` strings. A break (no read today) makes it 0 — an honest current-streak count, not a
    best-ever.

    Days are the READER's days when ``time_zone`` is known, and "today" is today where they are —
    both halves have to move together, since counting local days back from a UTC today would break
    the streak of anyone whose local date is already tomorrow. Without a zone: UTC, as before."""
    days = _local_days(read_ats, time_zone)
    if not days:
        return 0
    streak, d = 0, datetime.now(_zone(time_zone)).date()
    while d.isoformat() in days:
        streak += 1
        d = d - timedelta(days=1)
    return streak


def _utc_marked(ts) -> "str | None":
    """Put back the UTC marker SQLite drops.

    ``created_at`` is written by ``store._utcnow()`` — an AWARE UTC datetime — but the column is a
    plain ``DateTime``, so SQLite returns it naive and ``.isoformat()`` writes no offset at all:
    ``2026-08-19T15:01:14.807509``. ECMAScript reads a bare date-time as LOCAL, so every browser
    consumer silently shifted these stamps by the reader's own UTC offset — which is how Reading
    History's "Preferred time" came to report the server's clock instead of the reader's.

    This states what the value already is; it never converts. Applied ONLY to ``created_at``, whose
    zone is known — a client-supplied ``observedAt`` is left exactly as sent, since a naive one
    would be the client's local time and stamping it UTC would invent an hour."""
    if not isinstance(ts, str) or len(ts) < 11 or ts[10] not in "T ":
        return ts                                   # not a date-time (or absent): unchanged
    tail = ts[10:]
    if ts.endswith(("Z", "z")) or "+" in tail or "-" in tail:
        return ts                                   # already states its offset
    return ts + "Z"


def _read_at(row) -> "str | None":
    """A stored-read row's effective timestamp: the reader's observed time, else the scored read's
    own timestamp, else the row's insert time. One definition shared by the history and analytics
    serialisers so day-bucketing never diverges."""
    sc = row.get("scored") or {}
    return row.get("observedAt") or sc.get("read_at") or _utc_marked(row.get("createdAt"))


def _day(ts) -> "str | None":
    """The ``YYYY-MM-DD`` day of an ISO timestamp string, or ``None`` if it isn't one."""
    return ts[:10] if isinstance(ts, str) and len(ts) >= 10 else None


def _overall_trend(snapshots) -> list:
    """Compact overall-score trend points from report snapshots (as ``list_report_snapshots`` rows),
    oldest-first. One definition shared by the dashboard trend and the profile score history."""
    return [{"date": _day(s.get("createdAt")), "overall": int(s.get("overall") or 0)}
            for s in snapshots if _day(s.get("createdAt"))]


def _longest_streak(read_ats, time_zone: "str | None" = None) -> int:
    """The longest run of consecutive days with at least one read, over all of a reader's reads (not
    necessarily ending today), in the reader's zone when known. Deterministic; ``0`` when there are
    no reads."""
    days = sorted(_local_days(read_ats, time_zone))
    if not days:
        return 0
    from datetime import date
    best = run = 1
    prev = date.fromisoformat(days[0])
    for d in days[1:]:
        cur = date.fromisoformat(d)
        run = run + 1 if (cur - prev).days == 1 else 1
        best, prev = max(best, run), cur
    return best


def _handle_from(name: str, email: str) -> str:
    """A stable @handle derived from the email local-part (else the name): lower-cased, alphanumerics
    only. There is no stored handle; this is a deterministic display derivation, never fabricated."""
    base = (email.split("@")[0] if email else "") or name or "reader"
    h = "".join(c for c in base.lower() if c.isalnum())
    return h or "reader"


# --------------------------------------------------------------------------- #
# Product preferences (settings). The schema, defaults and normaliser now live in the
# dependency-free ``settings_service`` leaf and are re-exported at the top of this module
# (``DEFAULT_SETTINGS`` / ``normalize_settings`` / the allowlists / the private coercers), so
# ``api_server``'s public surface is byte-for-byte what callers already import. What STAYS here is
# the piece that depends on recommender vocabulary: the two sliders — ``politicalOpenness`` /
# ``recommendationStrength`` — map to per-request RWE-B/RWE-D hyperparameters (see
# :func:`rec_params_from_settings`); ``readingGoalMinutes`` drives the dashboard's today-vs-goal
# progress. Settings still wire NOTHING into the health report or its metrics.
# --------------------------------------------------------------------------- #


# Slider → recommender mapping. Piecewise-linear through three anchors, pinned so **slider 50 maps
# exactly to the values the stack has always used**: an untouched slider changes nothing, byte for
# byte. Ranges are deliberately gentle — a nudge, never a hard flip.
#
# Political openness → the RWE-B **bridge-slot budget** in the blend (W1): how many of the feed's
# slots surface cross-cutting "bridge" articles (the remaining slots split evenly across the other
# strategies; see :func:`blend_plan_for`). This replaced the historical openness→epsilon mapping,
# which the W1 audit proved inert on the *served* feed (docs/W1_OPENNESS_SLIDER_AUDIT.md): epsilon
# only rescales same-side erasure, which the per-user argsort and the cross-first slice both cancel.
# Tunable — change these anchors to retune openness without touching the implementation.
_OPENNESS_BRIDGE_BUDGET = (4, 6, 8)       # slider 0 / 50 / 100 → RWE-B slots (DEFAULT_BLEND_PLAN total = 14)
_STRENGTH_BETA = (0.30, 0.50, 0.80)       # slider 0 / 50 / 100 → RWE-D beta (popularity suppression)

# Interest Intensity → a per-topic RANK NUDGE inside each strategy's admitted candidate pool
# (:meth:`Backend._interest_rerank`). The eight slider keys are settings vocabulary
# (``settings_service.INTEREST_KEYS``); THIS map is where they become recommender vocabulary — the
# lower-cased catalog topics of the closed taxonomy (``ingest.TAXONOMY``) each slider weights. One
# slider ("artsCulture") spans the taxonomy's adjacent Arts + Culture topics. Politics is
# deliberately absent: the feed's political composition is the openness slider's contract (the
# rwe-b slice admits political items only, W1), and an interest knob on the same axis would fight
# it; Opinion is a register lens; the World / U.S. desks are geography (Places settings).
_INTEREST_TOPICS = {
    "business": ("business",), "technology": ("technology",), "science": ("science",),
    "health": ("health",), "climate": ("climate",), "sports": ("sports",),
    "entertainment": ("entertainment",), "artsCulture": ("arts", "culture"),
}
_INTEREST_NEUTRAL = 5     # the slider midpoint — contributes no key; the feed stays byte-identical

# The slider → rank-multiplier curve, piecewise-linear through three anchors (slider 1 / 5 / 10),
# the same tunable-anchor pattern as _OPENNESS_BRIDGE_BUDGET / _STRENGTH_BETA. Retuned 2026-08-17
# against production measurements: the original curve (multiplier = w/5, so max = 2x) was verified
# mechanically correct but too gentle to matter for a topic-concentrated reader — the live probe
# put that reader's nearest unserved Sports items at raw ranks 404–1,075, and a 2x boost reaches
# only ~2x the serving cutoff. Max weight is now an 8x boost: it reaches items ranked within ~8x
# of the cutoff (the near-to-mid walk neighborhood, top ~3% of a 1,500-node ranking) while a
# rank-400 item still never overrides the model's head — a stronger nudge, still never a hard
# flip, and never an admission or exclusion. The demote side (0.2 at slider 1, a 5x rank penalty)
# was verified effective in the same probe and is byte-identical to the original curve, as is the
# neutral identity at 5.
_INTEREST_ANCHORS = (0.2, 1.0, 8.0)   # slider 1 / 5 / 10 → rank divisor (boost above 1, demote below)


def _interest_multiplier(w: float) -> float:
    """Slider weight (1–10) → the rank divisor of :data:`_INTEREST_ANCHORS`, linear on each side
    of the neutral 5. Clamped defensively: params are also a direct-call surface (tests, the rec
    sandbox), and a junk weight must degrade to a bounded nudge, never a zero/negative divisor."""
    w = max(1.0, min(10.0, float(w)))
    lo, mid, hi = _INTEREST_ANCHORS
    return lo + (mid - lo) * (w - 1.0) / 4.0 if w <= 5.0 else mid + (hi - mid) * (w - 5.0) / 5.0


#: The For You country preference's rank divisor. One boost, not a curve: the preference is
#: binary (this article is from the chosen country, or it is not), so there is no slider to
#: interpolate. 8.0 matches the Interest Intensity maximum deliberately — the two nudges MULTIPLY
#: into one sort key, and a country boost that dwarfed the interest scale would make the eight
#: sliders decorative the moment a country was picked. At 8x a country item ranked within ~8x of
#: the serving cutoff reaches the served slice, which is the same reach the retuned interest
#: anchors were measured to need.
_COUNTRY_BOOST = 8.0

#: How a selected country orders the pool. ``first`` (the shipped default) sorts every
#: country-matching item ahead of every non-matching one — "show me India news" — while
#: ``boost`` applies :data:`_COUNTRY_BOOST` as a rank divisor, the gentler nudge this feature
#: shipped with first. Env-gated so the stronger behaviour has a kill switch that needs no
#: deploy, the same discipline as RWE_CLUSTER_TEMPLATE_GATE.
#:
#: Neither mode EXCLUDES anything: ``first`` partitions the admitted pool, it does not filter it,
#: so once the country's items run out the rest of the feed backfills the remaining slots
#: automatically. That is what keeps a low-supply country (India is 2.6% of the catalog) from
#: silently serving a four-card feed, and it is why this is a partition rather than a filter.
_COUNTRY_MODES = ("first", "boost")


def country_mode() -> str:
    """The active country ordering mode; anything unrecognised falls back to the default, never
    to a guess."""
    v = os.environ.get("RWE_REC_COUNTRY_MODE", "").strip().lower()
    return v if v in _COUNTRY_MODES else "first"


def _country_multiplier(item_country, want: "str | None", boost: "float | None" = None) -> float:
    """Rank divisor for one item under a country preference: the boost on a match, 1.0 otherwise.

    An item with NO known country is neutral (1.0), never demoted. That is the load-bearing
    choice: event geography resolves for a minority of the catalog, so demoting unlocated items
    would not prioritize the reader's country — it would bury everything the geocoder happened to
    miss, which is a coverage artefact rather than a preference. Preference expressed as "lift the
    matches", never "sink the rest".

    ``boost`` overrides :data:`_COUNTRY_BOOST` for one call. It exists so the anchor can be SWEPT
    and chosen from measurements (``examples/audit_country_rerank.py --boost-sweep``) the way the
    Interest Intensity curve was retuned, rather than argued about. No reader can set it:
    :func:`rec_params_from_settings` never emits the key, so the serving path always uses the
    module constant."""
    if not want or not item_country:
        return 1.0
    if want not in item_country:          # a SET: an article can belong to several countries
        return 1.0
    b = _COUNTRY_BOOST if boost is None else float(boost)
    return b if b > 0 else 1.0


def _piecewise(v: float, lo: float, mid: float, hi: float) -> float:
    """Linear 0→``lo``, 50→``mid``, 100→``hi`` (callers pass an already-clamped 0–100 value)."""
    v = float(v)
    return lo + (mid - lo) * (v / 50.0) if v <= 50.0 else mid + (hi - mid) * ((v - 50.0) / 50.0)


# ── Reader-state anchors — Tier 1, docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md ─────────────────────
# X's production objective puts its largest magnitudes on explicit negative feedback (report −234
# vs favorite +0.5, home-mixer/params/param.rs): a reader saying "no" is the most informative
# signal a feed receives. Here the same lesson lands as BOUNDED RANK MULTIPLIERS over the admitted
# pool — never a score term, never a slice budget. The values are anchors in the same currency as
# _INTEREST_ANCHORS (a weight-10 slider divides an item's effective rank by 8) and deliberately
# sit BELOW it: an explicit slider the reader set always outbids anything inferred from their card
# behavior. The composed reader-state factor is clamped to [_READER_STATE_FLOOR, _READER_STATE_CAP]
# so no accumulation of feedback can make a topic unreachable (the anti-filter-bubble floor) or
# compound into a rich-get-richer loop (the cap). Dislike is the one exclusion: the reader named a
# specific article; re-serving it to honor a "nudge only" principle would be malicious compliance.
_FEEDBACK_IGNORE_DECAY = 0.35      # "ignore" on the card ≈ triples the effective rank, once
_REPEAT_UNOPENED_DECAY = 0.35      # surfaced in-window, never opened: reads as an implicit ignore
_REPEAT_OPENED_DECAY = 0.25        # surfaced AND opened: they followed it once; strongest decay
_DISLIKE_PUBLISHER_DECAY = 0.6     # per disliked article, on that article's publisher…
_DISLIKE_PUBLISHER_FLOOR = 0.35    # …never below — a publisher is dimmed, not disappeared
_DISLIKE_TOPIC_DECAY = 0.8         # gentler on the topic: a dislike is usually about the article
_DISLIKE_TOPIC_FLOOR = 0.5         # or the outlet, and only weakly about the whole topic
_LIKE_TOPIC_BOOST = 1.5            # per liked/saved article, on its topic…
_LIKE_TOPIC_CAP = 3.0              # …capped well under weight-10 interest (8x)
_READER_STATE_FLOOR = 0.1
_READER_STATE_CAP = 8.0

# Tier-2 feedback vocabulary → the SAME anchors, scoped to what the reader actually said
# (docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md, Phase 13.6). No new magnitudes: each type reuses a
# Tier-1 anchor, differing only in WHICH dimensions it touches — the whole point of the finer
# vocabulary is that "fewer from this source" can dim the publisher without smearing the topic,
# and "more of this topic" can lift the topic without privileging one outlet.
#
#   type                article   topic                       publisher
#   another_viewpoint   drop      —                           —        (the story source also
#                                                                       ranks the asked story first)
#   already_know        drop      —                           —
#   too_repetitive      drop      ×0.8, floor 0.5             —
#   fewer_from_source   drop      —                           ×0.6, floor 0.35
#   more_topic          —         ×1.5, cap 3.0               —
#
# All four negative types drop the named article for the same reason dislike does: the reader
# acted on THAT card expecting it gone, and re-serving it to honor a nudge-only principle would
# be malicious compliance.


def _reader_state_factors(mind, fb: dict, rep: dict):
    """Resolve reader-state article ids (``params["feedback"]`` / ``params["repetition"]``,
    built by :mod:`rec_context`) against THIS corpus.

    Returns ``(drop, art_by_col, topic_mult, pub_mult)``: the columns dislike excludes, a
    per-column decay for ignored / recently-surfaced articles, and the topic / publisher
    multipliers derived from likes and dislikes. Ids are the served ``article.id`` — the corpus
    item id — so an article that has rotated out of the current corpus simply matches nothing;
    topic/publisher effects are therefore derived only from feedback whose subject is still
    resolvable, which keeps every claim about "this publisher" grounded in the catalog being
    ranked rather than in a remembered string."""
    referenced: set = set()
    for key in ("dislike", "like", "ignore", "another_viewpoint", "already_know",
                "too_repetitive", "fewer_from_source", "more_topic"):
        referenced.update(fb.get(key) or ())
    for key in ("unopened", "opened"):
        referenced.update(rep.get(key) or ())
    if not referenced:
        return set(), {}, {}, {}
    ids = np.asarray(mind.dataset.item_ids)
    col_of = {aid: c for c, aid in enumerate(ids.astype(str)) if aid in referenced}
    cats = np.asarray(mind.categories)
    outlets = np.asarray(mind.outlets)

    def _bucket(aid):
        c = col_of.get(aid)
        if c is None:
            return None, None
        return str(cats[c]).strip().lower(), str(outlets[c])

    topic_mult: dict = {}
    pub_mult: dict = {}
    for aid in set(fb.get("dislike") or ()):
        t, p = _bucket(aid)
        if t:
            topic_mult[t] = max(topic_mult.get(t, 1.0) * _DISLIKE_TOPIC_DECAY,
                                _DISLIKE_TOPIC_FLOOR)
        if p:
            pub_mult[p] = max(pub_mult.get(p, 1.0) * _DISLIKE_PUBLISHER_DECAY,
                              _DISLIKE_PUBLISHER_FLOOR)
    for aid in set(fb.get("like") or ()):
        t, _ = _bucket(aid)
        if t:
            topic_mult[t] = min(topic_mult.get(t, 1.0) * _LIKE_TOPIC_BOOST, _LIKE_TOPIC_CAP)
    # Tier-2 vocabulary — the mapping table above the anchors: scoped reuse, no new magnitudes.
    for aid in set(fb.get("too_repetitive") or ()):
        t, _ = _bucket(aid)
        if t:
            topic_mult[t] = max(topic_mult.get(t, 1.0) * _DISLIKE_TOPIC_DECAY,
                                _DISLIKE_TOPIC_FLOOR)
    for aid in set(fb.get("fewer_from_source") or ()):
        _, p = _bucket(aid)
        if p:
            pub_mult[p] = max(pub_mult.get(p, 1.0) * _DISLIKE_PUBLISHER_DECAY,
                              _DISLIKE_PUBLISHER_FLOOR)
    for aid in set(fb.get("more_topic") or ()):
        t, _ = _bucket(aid)
        if t:
            topic_mult[t] = min(topic_mult.get(t, 1.0) * _LIKE_TOPIC_BOOST, _LIKE_TOPIC_CAP)
    art_decay: dict = {}
    for aid in set(fb.get("ignore") or ()):
        art_decay[aid] = min(art_decay.get(aid, 1.0), _FEEDBACK_IGNORE_DECAY)
    for aid in set(rep.get("unopened") or ()):
        art_decay[aid] = min(art_decay.get(aid, 1.0), _REPEAT_UNOPENED_DECAY)
    for aid in set(rep.get("opened") or ()):
        art_decay[aid] = min(art_decay.get(aid, 1.0), _REPEAT_OPENED_DECAY)
    dropped_types = ("dislike", "another_viewpoint", "already_know",
                     "too_repetitive", "fewer_from_source")
    drop = {col_of[aid] for key in dropped_types
            for aid in set(fb.get(key) or ()) if aid in col_of}
    art_by_col = {col_of[aid]: m for aid, m in art_decay.items() if aid in col_of}
    return drop, art_by_col, topic_mult, pub_mult


def rec_params_from_settings(settings: "dict | None") -> "dict | None":
    """Per-request recommender parameters from a reader's stored preferences, or ``None``.

    Three preferences contribute — Political openness → the RWE-B **bridge-slot budget** (carried
    as the ``openness`` key, consumed by :func:`blend_plan_for`; W1), Recommendation strength →
    RWE-D ``beta`` (how strongly popular items are suppressed), and the Interest Intensity sliders
    → ``interests``, a lower-cased topic → weight map (via :data:`_INTEREST_TOPICS`) consumed by
    :meth:`Backend._interest_rerank`. Only a *moved* slider contributes a key, and ``None`` means
    "use the shared default stack" — so demo, anonymous, and untouched-slider requests are provably
    identical to the pre-slider behaviour. The algorithms themselves are untouched; openness
    reshapes only the blend's slot allocation, beta only a constructor arg, and interests only the
    order of each strategy's already-admitted candidate pool."""
    s = normalize_settings(settings)
    params = {}
    if s["politicalOpenness"] != 50:
        params["openness"] = int(s["politicalOpenness"])     # W1: drives the RWE-B bridge budget
    if s["recommendationStrength"] != 50:
        params["beta"] = _piecewise(s["recommendationStrength"], *_STRENGTH_BETA)
    interests = {topic: int(w) for key, w in s["interests"].items()
                 if int(w) != _INTEREST_NEUTRAL for topic in _INTEREST_TOPICS.get(key, ())}
    if interests:
        params["interests"] = interests
    if s["recommendationCountry"]:
        params["country"] = s["recommendationCountry"]       # None (Global) contributes no key
    return params or None


def blend_plan_for(params: "dict | None") -> tuple:
    """The per-request blend plan. **Political openness (W1)** moves the RWE-B bridge-slot budget
    (:data:`_OPENNESS_BRIDGE_BUDGET`); the remaining slots split evenly across the other strategies,
    in ``DEFAULT_BLEND_PLAN`` order and preserving its total. Absent / slider-50 → the shared
    ``DEFAULT_BLEND_PLAN`` (byte-identical to the historical feed). Shared by the serving path
    (:meth:`Backend._serialize_recommendations`) and the explain observer (:mod:`rec_explain`) so the
    replicated plan stays byte-exact — the W1 / 21a parity guarantee."""
    if not params or "openness" not in params:
        return DEFAULT_BLEND_PLAN
    total = sum(k for _, k in DEFAULT_BLEND_PLAN)
    budget = max(0, min(total, int(round(_piecewise(params["openness"], *_OPENNESS_BRIDGE_BUDGET)))))
    others = [name for name, _ in DEFAULT_BLEND_PLAN if name != "rwe-b"]
    base, extra = divmod(total - budget, len(others)) if others else (0, 0)
    counts = {"rwe-b": budget}
    for i, name in enumerate(others):
        counts[name] = base + (1 if i < extra else 0)
    return tuple((name, counts[name]) for name, _ in DEFAULT_BLEND_PLAN)


# ------------------------------------------------------------------ #
# Backend state — built once at startup.
# ------------------------------------------------------------------ #
# Dataset profiles — the single knob for switching data sources.
# ------------------------------------------------------------------ #
@dataclass
class DatasetProfile:
    """Everything the engine needs to pick and configure a data source, so a
    deployment can switch between MIND, Politosphere, Qbias, a synthetic PoC, or
    future production data by *configuration only* — never code.

    ``lean_tau`` defaults to the engine's own centre half-width; a standardized
    (z-score) axis such as Politosphere's may want it tuned per profile.
    """
    name: str = "synthetic"
    kind: str = "synthetic"          # "synthetic" | "npz"
    domain: str = "news"             # "news" | "reddit"
    lean_tau: float = hr.LEAN_TAU
    # synthetic
    n_users: int = 500
    max_items: int = 1500
    seed: int = 0
    qbias_csv: str | None = None     # set → synthetic users over a real Qbias catalog
    # npz (MIND / Politosphere / production export)
    npz: str | None = None
    register_csv: str | None = None
    emotion_csv: str | None = None
    behaviors_tsv: str | None = None

    @classmethod
    def synthetic(cls, n_users: int = 500, max_items: int = 1500, seed: int = 0,
                  qbias_csv: str | None = None) -> "DatasetProfile":
        return cls(name="qbias" if qbias_csv else "synthetic", kind="synthetic",
                   domain="news", n_users=n_users, max_items=max_items, seed=seed,
                   qbias_csv=qbias_csv)


# Named base templates; paths/sizes are supplied per deployment (flags or env).
BUILTIN_PROFILES = {
    "synthetic":    DatasetProfile(name="synthetic", kind="synthetic", domain="news"),
    "qbias":        DatasetProfile(name="qbias", kind="synthetic", domain="news"),
    "mind":         DatasetProfile(name="mind", kind="npz", domain="news"),
    "politosphere": DatasetProfile(name="politosphere", kind="npz", domain="reddit"),
}


def resolve_profile(args) -> DatasetProfile:
    """Named base profile overlaid with CLI/env overrides (CLI > env > base). This
    is what makes data selectable by configuration alone (`RWE_PROFILE`, `RWE_NPZ`, …)."""
    name = args.profile or os.environ.get("RWE_PROFILE") or "synthetic"
    base = BUILTIN_PROFILES.get(name)
    if base is None:
        raise SystemExit(f"unknown profile '{name}'; choose from {sorted(BUILTIN_PROFILES)}")

    def pick(cli, env, default):
        return cli if cli is not None else os.environ.get(env, default)

    tau = args.lean_tau if args.lean_tau is not None else float(os.environ.get("RWE_LEAN_TAU", base.lean_tau))
    return DatasetProfile(
        name=name, kind=base.kind,
        domain=pick(args.domain, "RWE_DOMAIN", base.domain),
        lean_tau=tau,
        n_users=args.n_users if args.n_users is not None else base.n_users,
        max_items=args.max_items if args.max_items is not None else base.max_items,
        seed=args.seed if args.seed is not None else base.seed,
        qbias_csv=pick(args.qbias, "RWE_QBIAS", base.qbias_csv),
        npz=pick(args.npz, "RWE_NPZ", base.npz),
        register_csv=pick(args.register_csv, "RWE_REGISTER_CSV", base.register_csv),
        emotion_csv=pick(args.emotion_csv, "RWE_EMOTION_CSV", base.emotion_csv),
        behaviors_tsv=pick(args.behaviors, "RWE_BEHAVIORS", base.behaviors_tsv),
    )


# ------------------------------------------------------------------ #
# Real reads needed before an Initial Estimate becomes a Measured report — the report's own
# click floor (health_report.compute default min_clicks).
ESTIMATE_MIN_READS = 5


def _unavailable_metric(key: str) -> dict:
    """A metric card the backend cannot compute yet — there isn't enough of the reader's activity to
    measure it reliably (e.g. no political reads for Viewpoint Balance, or no recommendation reception
    for Open-Mindedness). Carries an EXPLICIT ``available: False`` (+ ``reason`` / ``minimumActivity``)
    so the UI shows a consistent "not enough data yet" empty state INSTEAD of hiding the card or
    implying a real 0. No score is fabricated — ``score`` is a neutral placeholder the UI never renders
    for an unavailable metric, and ``band`` is ``"Unknown"``. This is metadata only: no scoring logic
    or metric calculation changes, and the overall score / improvements are computed from the
    available metrics exactly as before."""
    return {"key": key, "score": 0, "delta": 0, "band": _score_band(None),
            "available": False, "reason": "insufficient_data", "minimumActivity": ESTIMATE_MIN_READS}


@dataclass(frozen=True)
class _Corpus:
    """The corpus-varying inputs the JSON serialisers read.

    Two instances share this exact shape: the base **reference** corpus, built once at
    startup, and a per-user **augmented** corpus (base + one reader row + the novel article
    columns that reader brought in), built in ``personalize.py``. Everything else the
    serialisers use — ``lean_tau`` and the metric templates — is corpus-invariant and stays
    on the Backend. Feeding the *same* serialisers a different ``_Corpus`` is what lets a
    real reader's Measured report be rendered by identical code to the demo reader's, with no
    algorithm or JSON-contract change.
    """
    mind: object          # MINDData: matrix, item_positions, outlets, categories, titles, ids
    pop: dict             # health_report.compute(...) output for this corpus
    register: object      # per-item P(reporting) array, or None
    emotion: object       # per-item emotion dict, or None
    confidence: object    # per-item lean-confidence array, or None
    outlet_lean: dict     # outlet name -> house lean (mean finite item position)


@dataclass(frozen=True)
class _Recommenders:
    """The RWE recommender stack over one corpus: the derived recommender dataset, the shared
    ``FeedbackGraph``, the built RWE-B / RWE-D / Adaptive models, and the maps back to ``mind``
    columns. Built for the base corpus at startup and rebuilt by ``personalize.py`` over a
    real user's augmented corpus, so both use *identical* construction and hyperparameters
    (one source of truth — the RWE classes themselves are unchanged)."""
    rec_dataset: object   # MINDData.recommender_inputs() dataset (NaN-pos items / empty users dropped)
    theta: object         # per-user ideology estimate
    item_pos: object      # per-item position (recommender space)
    fg: object            # rwe.FeedbackGraph over rec_dataset.matrix
    models: dict          # strategy -> built RWE recommender (shared, recommend() is read-only)
    id2col: dict          # mind item id -> column index in mind space
    rec_ids: object       # rec_dataset.item_ids (rec index -> mind item id)
    exposure: object      # per-user AdaptiveRWEB exposure (measured probe, else neutral 0.5)


class Backend:
    def __init__(self, profile: DatasetProfile, provider: str = "anthropic", model=None):
        self.profile = profile
        self.domain = profile.domain
        self.lean_tau = profile.lean_tau
        self.provider = provider
        self.model = model or nr._DEFAULT_MODELS.get(provider)
        register = emotion = selective = confidence = None
        self._probe_csv = None   # satisfaction-probe CSV (drives AdaptiveRWEB exposure)
        # corpus item-id -> canonical publisher URL, attached by the serving layer when the catalog is
        # sourced from the live RSS feed (empty otherwise → no URL is emitted, unchanged behaviour).
        self.url_by_id: dict = {}
        # corpus item-id -> ISO country, from the same catalog CSV as url_by_id (empty until a
        # live feed catalog is attached → the country nudge is simply never consulted).
        self.country_by_id: dict = {}
        self._country_col_cache: dict = {}
        # story membership (item id / canonical URL -> story id), attached beside the two above;
        # empty until a live catalog with stories is attached → the story quota has no input.
        self.story_by_id: dict = {}
        self.story_by_url: dict = {}
        self._story_col_cache: dict = {}

        if profile.kind == "npz":
            if not profile.npz:
                raise SystemExit(f"profile '{profile.name}' needs a dataset path (--npz / RWE_NPZ)")
            self.mind = MINDData.load(profile.npz)
            src = None if profile.domain == "news" else np.asarray(self.mind.titles)
            register, emotion, selective = self._load_enrichment(profile)
        else:
            # Synthetic corpus via the repo's own simulator (real pipeline, generated clicks);
            # a Qbias CSV swaps the catalog for real outlets + gold leans.
            import simulate_users as su
            cfg = su.SimConfig(n_users=profile.n_users, max_items=profile.max_items, seed=profile.seed)
            cat, _pop, _ev, impressions, mind, _metric_rows, probe_rows = su.run(cfg, qbias=profile.qbias_csv)
            self.mind = mind
            src = None  # news domain: source axis = outlet
            tmp = tempfile.mkdtemp(prefix="ih_api_")
            su.write_enrichment_csvs(os.path.join(tmp, "register.csv"),
                                     os.path.join(tmp, "emotion.csv"), cat)
            su.write_behaviors_tsv(os.path.join(tmp, "behaviors.tsv"), impressions, cat)
            self._probe_csv = os.path.join(tmp, "satisfaction_probe.csv")
            su._write_csv(self._probe_csv, probe_rows)   # measured cross-cutting reception
            # Aligned enrichment: when the profile supplies register/emotion CSVs (e.g.
            # prepare_qbias's baseline-enriched sidecars), load THOSE so the reference population
            # carries the SAME register/emotion semantics as ingested reads; otherwise the sim's
            # synthetic enrichment (unchanged default). Data source only — no algorithm change.
            reg_csv = profile.register_csv or os.path.join(tmp, "register.csv")
            emo_csv = profile.emotion_csv or os.path.join(tmp, "emotion.csv")
            register = hr._load_item_csv(reg_csv, self.mind.dataset.item_ids)["reporting"]
            emotion = hr._load_item_csv(emo_csv, self.mind.dataset.item_ids)
            selective = hr.selective_exposure_array(self.mind, os.path.join(tmp, "behaviors.tsv"))
            # Gold-lean axis is high-confidence by construction; give compute() a per-item
            # confidence so the Confidence metric + axis weighting populate realistically.
            rng = np.random.default_rng(profile.seed)
            confidence = np.clip(rng.normal(0.85, 0.07, self.mind.dataset.matrix.shape[1]), 0.4, 0.99)

        self.register = register
        self.emotion = emotion
        # Base per-user cross-cutting selective-exposure array (behavioural agency signal). Kept so
        # personalize.py can rank a real user's measured recommendation reception against the SAME
        # population distribution — the seam that lets Open-Mindedness populate for a real reader.
        self.selective = selective
        self.pop = hr.compute(self.mind, source=src, register=register, emotion=emotion,
                              selective=selective, confidence=confidence)
        self.eligible = hr._eligible_pool(self.pop, 5, min_political=3)
        if len(self.eligible) == 0:
            raise SystemExit("no eligible readers (>=5 clicks, >=3 political) in this dataset")

        # Per-item confidence array actually used (for serialising article confidence).
        self.item_confidence = confidence
        # Outlet -> house lean (mean item position), for source/publisher leans.
        self.outlet_lean = self._build_outlet_lean(self.mind)

        # The serialiser's view of the reference corpus. personalize.py builds an
        # identically-shaped _Corpus (base + one reader row + novel columns) so the *same*
        # serialisers below render a Measured report from a real user's augmented corpus.
        self.base_corpus = _Corpus(mind=self.mind, pop=self.pop, register=self.register,
                                   emotion=self.emotion, confidence=self.item_confidence,
                                   outlet_lean=self.outlet_lean)

        # RWE recommender stack over the base corpus, built once at startup and reused across
        # requests (recommend() is a pure read of immutable state, so a shared instance is safe
        # under the threadpool). personalize.py builds the same stack over an augmented corpus.
        self.rec = self._build_recommenders(self.mind, self._probe_csv)
        # Back-compat attributes: existing code (and any external reader) still sees these on
        # the Backend; they're now just views onto the recommender bundle.
        self.rec_dataset, self.theta, self.item_pos = self.rec.rec_dataset, self.rec.theta, self.rec.item_pos
        self.fg, self.exposure = self.rec.fg, self.rec.exposure
        self._id2col, self._rec_ids, self._models = self.rec.id2col, self.rec.rec_ids, self.rec.models

        self.demo_user = self._pick_demo_user()

    # -- enrichment loading ------------------------------------------------ #
    def _load_enrichment(self, profile: DatasetProfile):
        """Register / emotion / selective-exposure arrays for an npz dataset, from the
        profile's paths, falling back to the conventional working-directory filenames."""
        import glob
        ids = self.mind.dataset.item_ids
        reg_csv = profile.register_csv or ("register.csv" if os.path.exists("register.csv") else None)
        emo_csv = profile.emotion_csv or ("emotion.csv" if os.path.exists("emotion.csv") else None)
        beh = profile.behaviors_tsv
        if not beh:
            found = [b for b in glob.glob("**/behaviors.tsv", recursive=True) if "fixture" not in b]
            beh = found[0] if found else None
        register = hr._load_item_csv(reg_csv, ids)["reporting"] if reg_csv else None
        emotion = hr._load_item_csv(emo_csv, ids) if emo_csv else None
        selective = hr.selective_exposure_array(self.mind, beh) if beh else None
        return register, emotion, selective

    @staticmethod
    def _build_outlet_lean(mind) -> dict:
        """Outlet -> house lean (mean finite item position) over a corpus. Built for the base
        reference corpus at startup and recomputed by ``personalize.py`` for an augmented
        corpus, so both speak the same publisher-lean scale."""
        outs = np.asarray(mind.outlets)
        pos = np.asarray(mind.item_positions, dtype=float)
        lean = {}
        for o in np.unique(outs):
            m = (outs == o) & np.isfinite(pos)
            lean[str(o)] = float(np.mean(pos[m])) if m.any() else 0.0
        return lean

    @staticmethod
    def _build_recommenders(mind, probe_csv: "str | None" = None,
                            reader_exposure: "tuple[str, float] | None" = None) -> "_Recommenders":
        """Build the RWE recommender stack over ``mind``. Shared by the base corpus (startup)
        and a real user's augmented corpus (``personalize.py``) so both construct RWE-B / RWE-D
        / Adaptive with identical inputs and hyperparameters. The RWE algorithms are unchanged —
        this only assembles their inputs, exactly as ``__init__`` did inline before.

        ``reader_exposure`` (W2) is an optional ``(user_id, exposure)`` for a single real reader whose
        measured cross-cutting reception ``personalize`` computed (gated + shrunk); it splices only
        that reader's **AdaptiveRWEB** exposure. Every other user keeps the neutral 0.5 prior, so the
        base corpus, anonymous/demo, and the eval fixtures stay byte-identical. RWE-B / RWE-D are
        never touched by it (adaptive-slice-only)."""
        from rwe import FeedbackGraph, RWEB, RWED
        from rwe.satisfaction import AdaptiveRWEB
        # Recommender inputs, shared by all RWE variants (drops NaN-pos items + empty users).
        rec_dataset, theta, item_pos = mind.recommender_inputs()
        fg = FeedbackGraph(rec_dataset.matrix)
        id2col = {str(i): c for c, i in enumerate(np.asarray(mind.dataset.item_ids))}
        rec_ids = np.asarray(rec_dataset.item_ids)
        # Per-user exposure for AdaptiveRWEB: the sim's measured cross-cutting reception (real
        # signal) where a probe exists; a neutral 0.5 otherwise (a bare --npz, or a real user
        # who hasn't yet been measured on cross-cutting reads).
        exposure = np.full(len(rec_dataset.user_ids), 0.5, dtype=float)
        if probe_csv and os.path.exists(probe_csv):
            try:
                import adaptive_satisfaction as asf
                exposure, _ = asf.measured_exposure(asf.read_probe_csv(probe_csv),
                                                    rec_dataset.user_ids)
            except Exception:
                pass
        # W2: splice one real reader's measured adaptive exposure (personalize computed it from the
        # store — gated by the Open-Mindedness thresholds, shrunk toward the 0.5 prior by shownCross).
        # Only that reader's row moves; RWE-B / RWE-D below are untouched (adaptive-slice-only).
        if reader_exposure is not None:
            ruid, rexp = reader_exposure
            rows = np.flatnonzero(np.asarray(rec_dataset.user_ids) == ruid)
            if rows.size:
                exposure[int(rows[0])] = float(np.clip(rexp, 0.0, 1.0))
        models = {
            "rwe-b": RWEB(fg, theta, item_pos, epsilon=0.9),
            "rwe-d": RWED(fg, beta=0.5),
            "adaptive": AdaptiveRWEB(fg, theta, item_pos, exposure),
        }
        return _Recommenders(rec_dataset=rec_dataset, theta=theta, item_pos=item_pos, fg=fg,
                             models=models, id2col=id2col, rec_ids=rec_ids, exposure=exposure)

    # -- reader selection -------------------------------------------------- #
    @property
    def score_reference(self) -> "dict | None":
        """The frozen reference cohort every served score is ranked against.

        Captured from THIS corpus the first time (so switching it on costs no visible jump) and
        then reused from disk by every later build — including the ones `corpus_refresh` swaps in,
        which is exactly what stops the score drifting when the catalog grows. `None` means no
        reference is available (disabled, or unwritable and uncapturable), which falls back to the
        old population-relative behaviour rather than failing the report."""
        cached = getattr(self, "_score_reference", None)
        if cached is None:
            name = getattr(self.profile, "name", "") or ""
            cached = score_reference.load_or_capture(
                lambda: hr.freeze_reference(self.pop),
                provenance={"profile": name,
                            # `arr or ()` raises on a numpy array — this is the second time that
                            # trap has bitten in this change; count the array explicitly.
                            "users": int(len(_n) if (_n := self.pop.get("n_clicks")) is not None else 0),
                            "items": int(getattr(self.profile, "max_items", 0) or 0)}) or {}
            # A reference captured from a different KIND of corpus means every reader is ranked
            # against the wrong population, permanently — first-write-wins does not self-correct.
            warn = score_reference.provenance_warning(score_reference.load_doc(), name)
            if warn:
                logging.getLogger(__name__).warning(
                    json.dumps({"event": "score_reference_wrong_corpus", "detail": warn}))
            self._score_reference = cached
        return cached or None

    def _pick_demo_user(self) -> int:
        """A 'fair, improvable' reader (overall nearest ~58) so the report shows the full
        range of states — some healthy, some amber, real blind spots, meaningful bridging."""
        best, best_d = int(self.eligible[0]), 1e9
        for u in self.eligible:
            rep = hr.user_report(self.pop, self.mind, int(u))
            if rep["overall"] is None or rep.get("viewpoint_confidence") is None:
                continue
            d = abs(rep["overall"] - 58)
            if d < best_d:
                best, best_d = int(u), d
        return best

    def resolve_user(self, query: dict) -> int:
        q = (query.get("user", [""])[0] or "").strip()
        if q.lstrip("-").isdigit() and 0 <= int(q) < self.mind.dataset.matrix.shape[0]:
            return int(q)
        return self.demo_user

    # -- serialisers ------------------------------------------------------- #
    @staticmethod
    def _emotion_share_of(emotion, col: int) -> dict:
        labels = ["fear", "outrage", "analysis", "positive", "neutral"]
        if emotion is None:
            return {l: (0.2 if l == "neutral" else 0.2) for l in labels}
        out = {}
        for l in labels:
            arr = emotion.get(l)
            v = float(arr[col]) if arr is not None and np.isfinite(arr[col]) else 0.0
            out[l] = v
        s = sum(out.values()) or 1.0
        return {l: out[l] / s for l in labels}

    @staticmethod
    def _emotion_from_dict(emotion) -> dict:
        """Normalise a stored read's ``label -> share`` emotion dict to the five labels summing to 1
        (the shape ``_article_payload`` expects). Missing/blank enrichment degrades to fully neutral,
        exactly as the engine treats missing tone elsewhere."""
        labels = ["fear", "outrage", "analysis", "positive", "neutral"]
        if not isinstance(emotion, dict) or not emotion:
            return {l: (1.0 if l == "neutral" else 0.0) for l in labels}
        vals = {l: max(0.0, float(emotion.get(l, 0.0) or 0.0)) for l in labels}
        s = sum(vals.values()) or 1.0
        return {l: vals[l] / s for l in labels}

    def attach_country_resolver(self, mapping: dict) -> None:
        """Attach a corpus item-id -> ISO country map (``feed_source.load_country_map``), the input
        to the For You country preference's rank nudge. Additive and inert on its own: with no
        reader asking for a country, ``params`` carries no ``country`` key and the nudge is never
        consulted. Attached beside the URL resolver, from the same catalog CSV and the same row
        indexing, so the two maps cannot disagree about which article a column is."""
        self.country_by_id = dict(mapping or {})
        self._country_col_cache = {}

    def _country_by_col(self, mind) -> dict:
        """Column index -> ISO country for one corpus, memoized per ``mind``.

        Built from the item ids rather than stored alongside them because the augmented corpus a
        real reader gets is constructed per request (``personalize.py``): its novel columns are
        that reader's own reads, keyed by URL rather than ``Q{i}``, and they simply resolve to no
        country — neutral in the nudge, never demoted. Memoizing on ``id(mind)`` keeps a hot
        request from rebuilding the map for a corpus it already saw; the cache clears whenever a
        new catalog is attached, which is the only moment the mapping can change."""
        if not getattr(self, "country_by_id", None):
            return {}
        cache = getattr(self, "_country_col_cache", None)
        if cache is None:
            cache = self._country_col_cache = {}
        key = id(mind)
        hit = cache.get(key)
        if hit is None:
            ids = np.asarray(mind.dataset.item_ids)
            hit = {i: cs for i, cs in ((i, self.country_by_id.get(str(iid)))
                                       for i, iid in enumerate(ids)) if cs}
            cache[key] = hit
        return hit

    def attach_story_resolver(self, by_id: dict, by_url: "dict | None" = None) -> None:
        """Attach story membership (``feed_source.load_story_maps``): corpus item id → story id,
        plus canonical URL → story id for the augmented corpus's novel columns (a real reader's
        own reads, whose item id IS their URL). The input to the per-story feed quota
        (:func:`max_cards_per_story` / :meth:`_select_diverse`). An ENRICHMENT like the country
        map: inert until the quota is enabled, fail-soft at every attach site — losing it costs
        the story quota its input and nothing else."""
        self.story_by_id = dict(by_id or {})
        self.story_by_url = dict(by_url or {})
        self._story_col_cache = {}

    def _story_by_col(self, mind) -> dict:
        """Column index → story id for one corpus, memoized per ``mind`` exactly as
        :meth:`_country_by_col` is (same rebuild triggers, same augmented-corpus reasoning). A
        column with no known story is simply absent — uncapped, never grouped by guess."""
        if not (getattr(self, "story_by_id", None) or getattr(self, "story_by_url", None)):
            return {}
        cache = getattr(self, "_story_col_cache", None)
        if cache is None:
            cache = self._story_col_cache = {}
        key = id(mind)
        hit = cache.get(key)
        if hit is None:
            by_id = getattr(self, "story_by_id", {})
            by_url = getattr(self, "story_by_url", {})
            ids = np.asarray(mind.dataset.item_ids)
            hit = {}
            for i, iid in enumerate(ids):
                s = str(iid)
                sid = by_id.get(s)
                if sid is None and s.startswith(("http://", "https://")):
                    sid = by_url.get(s)
                if sid is not None:
                    hit[i] = sid
            cache[key] = hit
        return hit

    def attach_url_resolver(self, mapping: dict) -> None:
        """Attach a corpus item-id -> canonical publisher URL map (from the live RSS feed source), so
        serialized articles carry the real openable URL. Purely additive — the recommender, ranking,
        scoring, report, and personalization are untouched; this only enriches the article payload."""
        self.url_by_id = dict(mapping or {})

    def _resolve_url(self, item_id) -> "str | None":
        """The verified canonical publisher URL for an article id, or ``None``. Two honest sources,
        never fabricated: the id is itself a canonical URL (a real reader's stored read), or the live
        feed source mapped this corpus id (``Q{i}``) to a FeedArticle URL."""
        s = str(item_id)
        if s.startswith("http://") or s.startswith("https://"):
            return s
        return self.url_by_id.get(s)

    def _article_payload(self, *, item_id, headline, outlet, topic, lean, register,
                         emotion: dict, confidence, outlet_lean: dict,
                         political: "bool | None" = None,
                         unknown_lean_to_null: bool = False) -> dict:
        """Build one article payload from already-resolved fields — the SINGLE source of the
        ``Article`` shape (web/types/domain.ts). Both the corpus serialiser (:meth:`_serialize_article`)
        and the reading-history serialiser (:meth:`serialize_history`) feed this, so a catalog
        article and a real reader's stored read render identically. Missing lean/confidence degrade
        to the same neutral defaults the corpus path already used. ``political`` (Commit R1) is the
        article-level classification — included when known, omitted when unknown (never fabricated).

        ``publishedAt`` (Commit C4) is never fabricated for a REAL article — one with a verified
        URL (a live-feed corpus item or a reader's stored read): it serialises as ``""`` here and
        the API layer joins the real publication timestamp from the ``FeedArticle`` catalog. Only
        a demo/synthetic corpus item (no URL exists anywhere) keeps the deterministic
        ``_iso_recent`` estimate, so demo data stays plausible without ever lying about live news."""
        item_id = str(item_id)
        known_lean = lean is not None and np.isfinite(lean)
        pos = float(lean) if known_lean else 0.0
        # ``unknown_lean_to_null`` (the reading-history path, L2.2): when the article's lean is
        # unknown (missing/NaN), emit ``lean``/``leanBucket`` as ``null`` rather than a fabricated
        # centre — so an unknown outlet is never shown, filtered, or aggregated as "center". The
        # corpus/recommendation/story path (flag off) keeps the neutral 0.0 default unchanged.
        lean_out = pos if (known_lean or not unknown_lean_to_null) else None
        conf = float(confidence) if confidence is not None and np.isfinite(confidence) else 0.7
        dom = max(emotion, key=emotion.get) if emotion else "neutral"
        url = self._resolve_url(item_id)
        payload = {
            "id": item_id,
            "headline": str(headline),
            "publisher": _prettify(outlet),
            "publisherLean": float(outlet_lean.get(str(outlet), 0.0)),
            "topic": _prettify(topic),
            "lean": lean_out,
            "leanBucket": _lean_bucket(pos, self.lean_tau) if lean_out is not None else None,
            "confidence": conf,
            "emotion": emotion,
            "dominantEmotion": dom,
            "register": _register_enum(register),
            "publishedAt": "" if url else _iso_recent(item_id),
            "readingMinutes": 2 + (_stable_int(item_id) % 8),
        }
        if political is not None:
            payload["political"] = bool(political)
        # Additive: include the canonical publisher URL only when one is verified (never fabricated),
        # so the frontend can open the real article. Omitted otherwise (response_model_exclude_none).
        if url:
            payload["url"] = url
        return payload

    def _serialize_article(self, corpus: _Corpus, col: int) -> dict:
        """Serialise one article (column) of a corpus. Corpus-parametric so the same code
        renders a reference-catalog article and a novel article a real reader brought in."""
        mind = corpus.mind
        pol = getattr(mind, "political", None)
        return self._article_payload(
            item_id=str(np.asarray(mind.dataset.item_ids)[col]),
            headline=str(np.asarray(mind.titles)[col]),
            outlet=str(np.asarray(mind.outlets)[col]),
            topic=str(np.asarray(mind.categories)[col]),
            lean=np.asarray(mind.item_positions, dtype=float)[col],
            register=(corpus.register[col] if corpus.register is not None else None),
            emotion=self._emotion_share_of(corpus.emotion, col),
            confidence=(corpus.confidence[col] if corpus.confidence is not None else None),
            outlet_lean=corpus.outlet_lean,
            political=(bool(np.asarray(pol, dtype=bool)[col]) if pol is not None else None))

    def serialize_history(self, reads: list) -> list:
        """A reader's reading history from their stored, scored reads (``store.list_reads`` rows).

        Each read is rendered as the SAME ``Article`` shape as a recommendation or report article
        (via :meth:`_article_payload`), so history reuses one serialiser and stays contract-identical
        for a future mobile client. No corpus or augmented model is needed — a stored read already
        carries its scored fields. An article whose political lean is unknown (an outlet the registry
        doesn't know) serialises ``lean``/``leanBucket`` as **null** (``unknown_lean_to_null``, L2.2)
        — never a fabricated centre, so history never shows, filters, or aggregates it as "center".
        Rows arrive newest-first from the store and are preserved in that order.

        ``readAt`` is the reader's observed timestamp; ``completed`` is ``True`` (opening a read is
        the only signal we have — real completion tracking is future work); ``readingMinutes`` is
        the same deterministic estimate the article serialiser already uses. ``publishedAt`` (C4)
        is ``""`` here — a stored read is a real article, so the API layer attaches the real
        publication timestamp from the catalog when the article is known there, and the UI hides
        the segment otherwise (never a fabricated date)."""
        out = []
        for row in reads:
            sc = row.get("scored", {}) or {}
            item_id = str(sc.get("article_id") or row.get("canonicalUrl") or row.get("id"))
            article = self._article_payload(
                item_id=item_id,
                headline=(sc.get("title") or _prettify(str(sc.get("outlet") or "")) or "Untitled read"),
                outlet=sc.get("outlet", ""),
                topic=sc.get("category", ""),
                lean=sc.get("lean"),
                register=sc.get("register"),
                emotion=self._emotion_from_dict(sc.get("emotion")),
                confidence=sc.get("confidence"),
                outlet_lean=self.outlet_lean,
                political=sc.get("political"),
                unknown_lean_to_null=True)
            out.append({
                "id": str(row.get("id")),
                "article": article,
                "readAt": _read_at(row),
                "readingMinutes": article["readingMinutes"],
                "completed": True,
                # Additive attribution (Commit 14) — carried through verbatim, omitted when unknown
                # (legacy / extension reads). Metadata only; nothing downstream branches on it.
                "readSource": row.get("readSource"),
                "openedFrom": row.get("openedFrom"),
            })
        return out

    def build_dashboard(self, report: dict, reads: list, snapshots: list,
                        goal_minutes: "int | None" = None,
                        time_zone: "str | None" = None) -> dict:
        """Compose the dashboard summary from data that already exists — **no new report
        serialisation**. ``overall`` / ``overallDelta`` / ``metrics`` are lifted verbatim from the
        Measured/Estimate/Demo ``report`` this reader would see; ``trend`` is their saved report
        snapshots; ``today`` and ``streakDays`` are light aggregations of their recent reads (via
        the shared :meth:`serialize_history`). ``goal_minutes`` (the reader's stored daily reading
        goal) adds today-vs-goal progress — minutes here are the same per-read *estimates* the
        history already shows, so the goal tracks estimated reading time, not measured dwell.
        Corpus-independent and mobile-friendly."""
        recent = self.serialize_history(reads)                       # reuse the one history serialiser
        political_by_id = {str(r.get("id")): bool((r.get("scored") or {}).get("political"))
                           for r in reads}
        today = datetime.now(timezone.utc).date().isoformat()
        todays = [e for e in recent if str(e.get("readAt") or "")[:10] == today]
        n = len(todays)
        total_min = int(sum(e["readingMinutes"] for e in todays))
        avg_min = round(total_min / n) if n else 0
        pol_share = (sum(political_by_id.get(e["id"], False) for e in todays) / n) if n else 0.0
        top_topics = [t for t, _ in Counter(e["article"]["topic"] for e in todays
                                            if e["article"]["topic"]).most_common(4)]

        # trend + month-over-version delta from the saved snapshots (oldest -> newest)
        trend = _overall_trend(snapshots)
        overall = int(report.get("overall") or 0)
        delta = (overall - int(snapshots[-2]["overall"]) if len(snapshots) >= 2
                 else int(report.get("overallDelta") or 0))

        today_block = {"articlesRead": n, "avgReadingMinutes": avg_min, "minutesRead": total_min,
                       "politicalShare": round(float(pol_share), 4), "topTopics": top_topics}
        if goal_minutes is not None:                                 # signed-in reader with a goal
            today_block["goalMinutes"] = int(goal_minutes)
            today_block["goalMet"] = total_min >= int(goal_minutes)

        return {
            # Estimate vs Measured + coverage, lifted verbatim from the report this reader would see
            # (same routing as /api/report) so the dashboard keeps the onboarding context instead of
            # dropping it — no new report serialisation, no algorithm. `coverage.reads` is the honest
            # progress toward the measured threshold (accurate in both modes).
            "mode": report.get("mode"),
            "coverage": report.get("coverage"),
            "overall": overall,
            "overallDelta": delta,
            "trend": trend,
            "today": today_block,
            "metrics": report.get("metrics", []),                    # the report's metrics, reused as-is
            "streakDays": _reading_streak([e.get("readAt") for e in recent], time_zone),
        }

    # -- analytics: visualise stored data only (no new intelligence) -------- #
    _EMO_LABELS = ("fear", "outrage", "analysis", "positive", "neutral")

    @staticmethod
    def _reads_per_day(reads) -> list:
        """Reading volume: articles recorded per UTC day, ascending — a count of stored reads."""
        counts: "Counter[str]" = Counter()
        for row in reads:
            d = _day(_read_at(row))
            if d:
                counts[d] += 1
        return [{"date": d, "overall": counts[d]} for d in sorted(counts)]

    @staticmethod
    def _reporting_per_day(reads) -> list:
        """Reporting-vs-opinion share per day = the mean stored ``register`` (P(reporting)) over the
        day's reads, and its complement. Only reads carrying a finite register count; days with none
        are omitted. A plain average of values the enricher already computed — no re-scoring."""
        acc: dict = {}
        for row in reads:
            d = _day(_read_at(row))
            reg = (row.get("scored") or {}).get("register")
            if not d or reg is None or not np.isfinite(reg):
                continue
            a = acc.setdefault(d, [0.0, 0])
            a[0] += float(reg)
            a[1] += 1
        out = []
        for d in sorted(acc):
            mean = acc[d][0] / acc[d][1]
            out.append({"date": d, "reporting": round(mean, 4), "opinion": round(1.0 - mean, 4)})
        return out

    @staticmethod
    def _recommendation_acceptance(rec_events) -> list:
        """Recommendation acceptance per day from stored rec events: an opened rec counts as
        *accepted* on its opened day; a surfaced-but-never-opened rec counts as *ignored* on its
        shown day. Each event contributes once — a deterministic tally, no inference."""
        accepted: "Counter[str]" = Counter()
        ignored: "Counter[str]" = Counter()
        for r in rec_events:
            od, sd = _day(r.get("openedAt")), _day(r.get("shownAt"))
            if od:
                accepted[od] += 1
            elif sd:
                ignored[sd] += 1
        days = sorted(set(accepted) | set(ignored))
        return [{"date": d, "accepted": accepted.get(d, 0), "ignored": ignored.get(d, 0)} for d in days]

    def build_analytics(self, snapshots: list, reads: list, rec_events: list) -> dict:
        """The AnalyticsSeries — composed ENTIRELY from stored data: report snapshots (score/metric
        trends + emotion attention), stored reads (volume + reporting share), and recommendation
        events (acceptance). Every point is a deterministic aggregation of values already computed
        and saved; empty inputs yield empty series (an honest empty state, never fabricated).

        ``snapshots`` are ``store.report_metric_series`` rows: ``{date, overall, metrics{key:score},
        attention{label:share}}``, oldest-first."""
        def metric_trend(key):
            return [{"date": s["date"], "overall": int(s["metrics"][key])}
                    for s in snapshots if s.get("date") and s.get("metrics", {}).get(key) is not None]

        health = [{"date": s["date"], "overall": int(s["overall"])}
                  for s in snapshots if s.get("date")]
        emotion = [{"date": s["date"], **{l: round(float(s["attention"].get(l, 0.0)), 4)
                                          for l in self._EMO_LABELS}}
                   for s in snapshots if s.get("date") and s.get("attention")]
        return {
            # Reading coverage toward the measured threshold — the same progress the dashboard/report
            # show, so Analytics carries the Estimate-vs-Measured context too (a low-coverage reader is
            # still building their profile, and the trends grow as they read). Real read count, no calc.
            "coverage": {"reads": len(reads), "threshold": ESTIMATE_MIN_READS,
                         "sufficient": len(reads) >= ESTIMATE_MIN_READS},
            "readingOverTime": self._reads_per_day(reads),
            "topicDiversity": metric_trend("topicDiversity"),
            "politicalDiversity": metric_trend("viewpointBalance"),
            "publisherDiversity": metric_trend("sourceDiversity"),
            "emotion": emotion,
            "reporting": self._reporting_per_day(reads),
            "recommendationAcceptance": self._recommendation_acceptance(rec_events),
            "healthImprovement": health,
        }

    @staticmethod
    def build_profile(user: dict, reads: list, snapshots: list, saved_count: int = 0,
                      time_zone: "str | None" = None) -> dict:
        """The account profile, built entirely from persisted data — identity from the user row,
        streaks from stored reads (shared ``_reading_streak`` / ``_longest_streak``), the health
        journey from saved report snapshots (shared ``_overall_trend``), and ``savedCount`` from the
        caller's real persisted saved-article count. Achievements that don't exist yet return an
        **honest empty state**, never fabricated. Corpus-independent; no algorithm."""
        email = (user.get("email") or "").strip()
        name = ((user.get("displayName") or "").strip()
                or (email.split("@")[0] if email else "") or "Reader")
        read_ats = [_read_at(r) for r in reads]
        return {
            "name": name,
            "handle": _handle_from(name, email),
            "email": email,
            "joinedAt": user.get("createdAt") or datetime.now(timezone.utc).isoformat(),
            "streakDays": _reading_streak(read_ats, time_zone),
            "longestStreak": _longest_streak(read_ats, time_zone),
            "scoreHistory": _overall_trend(snapshots),
            "achievements": [],          # not built yet — honest empty, not fabricated
            "savedCount": saved_count,   # the real persisted count (Saved is the single concept)
        }

    def article(self, col: int) -> dict:
        """Base reference-corpus article (the demo / ``?user=`` path)."""
        return self._serialize_article(self.base_corpus, col)

    def _serialize_report(self, corpus: _Corpus, u: int,
                          measurements: "dict | None" = None) -> dict:
        """Serialise the Measured Information Health Report for reader row ``u`` of a corpus.

        Corpus-parametric: the base reference corpus (demo / ``?user=`` path) and a real
        user's augmented corpus both flow through this one path, so a Measured report is
        identical in shape and computation regardless of which reader it describes. Every
        number still comes from ``health_report.user_report`` on the corpus — this only
        serialises what the unchanged engine computed.

        ``measurements`` (ADR-001) is an optional ``{metric_key: envelope}`` of per-metric
        coverage + provenance, computed by :mod:`measurement` from the reader's scored reads (the
        real-user / `personalize` path supplies it; the demo path leaves it ``None``). Each envelope
        is attached onto its metric card additively — a metric with no measurement is unchanged, and
        the coverage of an *unavailable* metric still surfaces (it explains the empty state)."""
        rep = hr.user_report(corpus.pop, corpus.mind, u, reference=self.score_reference)
        scores = rep.get("scores", {}) or {}
        n_clicks = rep.get("n_clicks") or 0
        measurements = measurements or {}

        metrics = []
        for key, label in _METRIC_KEYS:
            s = scores.get(label)
            if s is None:
                metric = _unavailable_metric(key)          # show an empty-state card, never hide it
            else:
                metric = {"key": key, "score": int(s), "delta": 0, "benchmark": 50,
                          "band": _score_band(int(s)), "available": True}
            meas = measurements.get(key)
            if meas is not None:
                metric["measurement"] = meas               # coverage + provenance (additive)
            metrics.append(metric)
        vc = rep.get("viewpoint_confidence")
        axis_conf = float(vc) if vc is not None else 0.7
        conf_score = round(axis_conf * 100)
        metrics.append({"key": "confidence", "score": conf_score, "delta": 0, "benchmark": 70,
                        "band": _score_band(conf_score), "available": True,
                        "raw": {"value": round(axis_conf, 2), "unit": "axis margin"}})

        # per-user topic + source distributions (real shares from the click matrix)
        UC, UO = corpus.pop["UC"], corpus.pop["UO"]
        cat_u, out_u = corpus.pop["cat_u"], corpus.pop["out_u"]
        sc, so = hr.shares(UC[u]), hr.shares(UO[u])
        topics = sorted(
            ({"topic": _prettify(cat_u[i]), "share": float(sc[i]),
              "count": int(round(sc[i] * n_clicks))} for i in range(len(cat_u)) if sc[i] > 0),
            key=lambda d: -d["share"])[:10]
        sources = sorted(
            ({"source": _prettify(out_u[i]), "share": float(so[i]),
              "count": int(round(so[i] * n_clicks)),
              "lean": corpus.outlet_lean.get(str(out_u[i]), 0.0)}
             for i in range(len(out_u)) if so[i] > 0),
            key=lambda d: -d["share"])[:9]

        # A reader with no political items has an undefined viewpoint mix (health_report returns
        # NaN); render it as zeros so the payload stays valid JSON. Demo/eligible readers are
        # always finite, so their output is unchanged — this only guards the low-political
        # readers the measured path can now surface (a real user just over the read threshold).
        vp_raw = rep.get("viewpoint") or (0.0, 0.0, 0.0)
        left, center, right = (float(x) if x is not None and np.isfinite(x) else 0.0 for x in vp_raw)
        attention = rep.get("attention") or {l: 0.2 for l in
                                              ["fear", "outrage", "analysis", "positive", "neutral"]}

        blind = []
        for cat, user_share, cat_share in (rep.get("blind_spots") or []):
            if not _is_named(cat):
                continue                       # unclassified: counted elsewhere, never named here
            gap = float((cat_share - user_share) / cat_share) if cat_share else 0.0
            blind.append({"topic": _prettify(cat), "gap": max(0.0, min(1.0, gap)),
                          "note": f"{_prettify(cat)} is {round(cat_share*100)}% of what's available, "
                                  f"but barely shows up in your reading."})

        # improvements from the lowest real metrics (available only — an unavailable metric has no
        # real score to rank, so the suggestion set is unchanged from before)
        ranked = sorted((m for m in metrics if m["key"] != "confidence" and m["available"]),
                        key=lambda m: m["score"])
        improvements = []
        for m in ranked[:3]:
            tpl = _IMPROVEMENTS.get(m["key"])
            if tpl:
                item = {"id": f"imp_{m['key']}", "title": tpl[0], "detail": tpl[1],
                        "metric": m["key"], "impact": tpl[2]}
                # RC2.1 — bind user-specific evidence from fields already in this report (additive;
                # selection/order above are unchanged).
                _attach_evidence(item, m["key"], metric=m, topics=topics, sources=sources,
                                 viewpoint={"left": left, "center": center, "right": right},
                                 attention=attention, blind=blind, measured=True)
                # RC2.2 — replace the fixed impact with a simulated band (distribution metrics) or a
                # deficit-band fallback (graph metrics), from the cached model — no new query.
                _attach_impact(item, m["key"], score=m["score"], benchmark=m.get("benchmark"),
                               measured=True, pop=corpus.pop, u=u)
                improvements.append(item)

        overall = rep.get("overall") or 0
        n = int(n_clicks)
        return {
            # explicit counterpart to the estimate's mode/coverage — same contract, both paths
            "mode": "measured",
            "coverage": {"reads": n, "threshold": ESTIMATE_MIN_READS,
                         "sufficient": n >= ESTIMATE_MIN_READS},
            "overall": overall,
            "overallDelta": 0,
            "band": _score_band(overall),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
            "viewpoint": {"left": float(left), "center": float(center), "right": float(right)},
            "attention": {k: float(v) for k, v in attention.items()},
            "topics": topics,
            "sources": sources,
            "blindSpots": blind,
            "improvements": improvements,
            "axisConfidence": axis_conf,
        }

    def report(self, u: int) -> dict:
        """Base reference-corpus Measured report (the demo / ``?user=`` path)."""
        return self._serialize_report(self.base_corpus, u)

    # -- onboarding: outlets + the Initial Information Health Estimate --------- #
    def outlets(self) -> list:
        """Publishers present in the reference corpus, for onboarding selection — name, house
        lean, coarse bucket, and article count. Reference metadata (which outlets exist), not
        user data; ordered by how much of the catalog each covers."""
        outs = np.asarray(self.mind.outlets)
        result = []
        for o in np.unique(outs):
            name = str(o)
            if name == "":
                continue
            lean = float(self.outlet_lean.get(name, 0.0))
            result.append({"id": name, "name": _prettify(name), "lean": lean,
                           "leanBucket": _lean_bucket(lean, self.lean_tau),
                           "articles": int((outs == o).sum())})
        result.sort(key=lambda d: -d["articles"])
        return result

    def _pct_vs_pop(self, value, pop_key: str, invert: bool = False):
        """Percentile (0–100) of a single raw metric value within the reference population's
        real distribution for that metric — the same higher-is-healthier ranking the measured
        report uses, so an estimate speaks the same language. ``invert`` for echo (less = better)."""
        raw = self.pop.get(pop_key)
        if raw is None or value is None or not np.isfinite(value):
            return None
        arr = np.asarray(raw, dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            return None
        a = -arr if invert else arr
        v = -value if invert else value
        return int(round(float((a < v).mean() * 100.0)))

    def estimate(self, outlet_names) -> dict:
        """Initial Information Health **Estimate** from selected outlets only.

        Computed from the *character* of the chosen publishers — each outlet's house lean, its
        topical mix, and its typical tone/register in the reference catalog — aggregated with
        equal weight (we do not know how much of each a reader consumes). It reuses the existing
        ``health_report`` helpers and ranks each estimated raw metric against the reference
        population, so the scores read on the same 0–100 scale as a measured report. It is
        explicitly flagged ``mode="estimate"`` with zero-read ``coverage``.

        No reading history is fabricated: there is **no** user row and no synthetic clicks.
        Metrics that require behaviour (Open-Mindedness) or article-level lean confidence (axis
        confidence) are omitted — they cannot be honestly estimated from outlets alone."""
        outs = np.asarray(self.mind.outlets)
        cats = np.asarray(self.mind.categories)
        known = [str(o) for o in outlet_names if str(o) in self.outlet_lean and str(o) != ""]
        mask = np.isin(outs, known)
        if not known or not mask.any():
            raise ValueError("no known outlets selected")

        # topic diversity: entropy of the selected outlets' catalog topic mix
        cat_u = self.pop["cat_u"]
        n_cat = len(cat_u)
        cat_index = {c: i for i, c in enumerate(cat_u)}
        counts = np.zeros(n_cat)
        for c in cats[mask]:
            j = cat_index.get(c)
            if j is not None:
                counts[j] += 1.0
        topic_share = hr.shares(counts)
        topic_raw = hr.normalized_entropy(topic_share, n_cat)

        # source diversity: N equally-weighted outlets -> effective #sources = N
        src_raw = float(len(known))

        # viewpoint / echo from the outlets' house leans (one position per outlet, equal weight)
        leans = np.array([self.outlet_lean[o] for o in known], dtype=float)
        left, center, right = hr.viewpoint_shares(leans, tau=self.lean_tau)
        cross_raw = hr.cross_cutting_share(leans)
        echo_raw = hr.echo_score(left, right)

        # emotion / register aggregated over the selected outlets' catalog articles
        labels = ["fear", "outrage", "analysis", "positive", "neutral"]
        if self.emotion is not None:
            emo = {}
            for l in labels:
                arr = np.asarray(self.emotion[l], dtype=float)[mask]
                arr = arr[np.isfinite(arr)]
                emo[l] = float(arr.mean()) if arr.size else 0.0
            tot = sum(emo.values()) or 1.0
            attention = {l: emo[l] / tot for l in labels}
            balance_raw = 1.0 - (attention["fear"] + attention["outrage"])
        else:
            attention = {l: 0.2 for l in labels}
            balance_raw = None
        if self.register is not None:
            reg = np.asarray(self.register, dtype=float)[mask]
            reg = reg[np.isfinite(reg)]
            reporting_raw = float(reg.mean()) if reg.size else None
        else:
            reporting_raw = None

        raw = {"topicDiversity": self._pct_vs_pop(topic_raw, "topic"),
               "sourceDiversity": self._pct_vs_pop(src_raw, "eff_src"),
               "viewpointBalance": self._pct_vs_pop(cross_raw, "cross"),
               "echoChamber": self._pct_vs_pop(echo_raw, "echo", invert=True),
               "reportingRatio": self._pct_vs_pop(reporting_raw, "reporting"),
               "emotionalBalance": self._pct_vs_pop(balance_raw, "balance")}
        metrics = []
        for key, _label in _METRIC_KEYS:
            s = raw.get(key)
            if s is None:                          # Open-Mindedness has no raw here — empty-state card
                metrics.append(_unavailable_metric(key))
            else:
                metrics.append({"key": key, "score": int(s), "delta": 0, "benchmark": 50,
                                "band": _score_band(int(s)), "available": True})
        # Confidence is a measured-reads metric; from onboarding outlets alone it is not yet available.
        metrics.append(_unavailable_metric("confidence"))
        have = [m["score"] for m in metrics if m["available"]]   # overall from the available metrics only
        overall = int(round(sum(have) / len(have))) if have else 0

        topics = sorted(({"topic": _prettify(cat_u[i]), "share": float(topic_share[i]),
                          "count": int(counts[i])} for i in range(n_cat) if topic_share[i] > 0),
                        key=lambda d: -d["share"])[:10]
        share_each = 1.0 / len(known)
        sources = sorted(({"source": _prettify(o), "share": share_each,
                           "count": int((outs == o).sum()), "lean": float(self.outlet_lean[o])}
                          for o in known), key=lambda d: -d["lean"])[:9]

        q_c = self.pop["catalog_cat_share"]
        gaps = sorted(((cat_u[i], float(topic_share[i]), float(q_c[i])) for i in range(n_cat)
                       if q_c[i] > 0.02 and topic_share[i] < 0.5 * q_c[i] and _is_named(cat_u[i])),
                      key=lambda t: -(t[2] - t[1]))
        blind = [{"topic": _prettify(c), "gap": max(0.0, min(1.0, (qc - us) / qc if qc else 0.0)),
                  "note": f"{_prettify(c)} is {round(qc * 100)}% of what's available, but light in "
                          f"the outlets you picked."}
                 for (c, us, qc) in gaps[:2]]

        improvements = []
        for m in sorted((m for m in metrics if m["available"]), key=lambda d: d["score"])[:3]:
            tpl = _IMPROVEMENTS.get(m["key"])
            if tpl:
                item = {"id": f"imp_{m['key']}", "title": tpl[0], "detail": tpl[1],
                        "metric": m["key"], "impact": tpl[2]}
                # RC2.1 — same evidence binding as the measured path; measured=False so the
                # equal-weighted estimate source shares are never presented as a real reading mix.
                _attach_evidence(item, m["key"], metric=m, topics=topics, sources=sources,
                                 viewpoint={"left": float(left), "center": float(center),
                                            "right": float(right)},
                                 attention={l: float(attention[l]) for l in labels},
                                 blind=blind, measured=False)
                # RC2.2 — an estimate has no reads to simulate, so impact uses the deterministic
                # deficit-band fallback (measured=False, no pop/u).
                _attach_impact(item, m["key"], score=m["score"], benchmark=m.get("benchmark"),
                               measured=False)
                improvements.append(item)

        return {
            "mode": "estimate",
            "coverage": {"reads": 0, "threshold": ESTIMATE_MIN_READS, "sufficient": False},
            "overall": overall,
            "overallDelta": 0,
            "band": _score_band(overall),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
            "viewpoint": {"left": float(left), "center": float(center), "right": float(right)},
            "attention": {l: float(attention[l]) for l in labels},
            "topics": topics,
            "sources": sources,
            "blindSpots": blind,
            "improvements": improvements,
            # axisConfidence intentionally omitted — article-level lean confidence is n/a from outlets
        }

    # -- recommendations (real RWE family: bridging / discovery / adaptive) --- #
    def _model(self, strategy: str):
        """The real RWE recommender for a strategy (Section 5.2 / 7), built once at startup
        and reused. Unknown strategies fall back to RWE-B, matching prior behaviour."""
        return self._models.get(strategy, self._models["rwe-b"])

    @staticmethod
    def _model_for(rec: "_Recommenders", strategy: str, params: "dict | None"):
        """The strategy's model — the shared default, or a per-request rebuild with the reader's
        slider-mapped hyperparameters (:func:`rec_params_from_settings`).

        The rebuild reuses the cached ``FeedbackGraph`` / positions, so constructing an RWEB/RWED
        around them is cheap and the shared stack is never mutated or churned. Only the two wired
        knobs exist: ``epsilon`` for rwe-b, ``beta`` for rwe-d; ``adaptive`` always serves the
        shared model (its epsilon is the measured satisfaction policy, not a preference)."""
        default = rec.models.get(strategy, rec.models["rwe-b"])
        if not params:
            return default
        from rwe import RWEB, RWED
        if strategy == "rwe-b" and "epsilon" in params:
            return RWEB(rec.fg, rec.theta, rec.item_pos, epsilon=float(params["epsilon"]))
        if strategy == "rwe-d" and "beta" in params:
            return RWED(rec.fg, beta=float(params["beta"]))
        return default

    @staticmethod
    def _slice_admits(mind, strategy: str, col: int) -> bool:
        """Whether a ranked column may occupy a slot in ``strategy``'s slice (Commit R1).

        The bridging slice (rwe-b) exists to surface *political* perspective articles — RWEB's
        bridge test is pure lean geometry (outlet house lean), so a promo or sports piece from a
        leaning outlet ranks like a perspective article. Non-political items are therefore
        excluded from the rwe-b slice (backfilled by the next-ranked political item); every other
        strategy admits everything. Shared by the serving path and the explain observer so the
        replicated plan stays byte-identical (the 21a parity guarantee)."""
        if strategy != "rwe-b":
            return True
        pol = getattr(mind, "political", None)
        if pol is None:
            return True
        return bool(np.asarray(pol, dtype=bool)[col])

    @staticmethod
    def _slice_select(mind, strategy: str, cols: list, k: int, user_side: float) -> list:
        """The ``k`` slice slot-holders from already-ADMITTED candidates in rank order.

        Commit R1.5: the Bridge strategy preferentially exposes opposing viewpoints — rwe-b takes
        cross-cutting political items first (in rank order) and falls back to same-side political
        items only when fewer than ``k`` cross candidates exist. Every other strategy keeps pure
        rank order, as does rwe-b for a sideless reader (user_side == 0: no cross direction
        exists). The article's ACTUAL crossCutting fact is still computed at serialization; this
        only orders the slice. Shared by serving and the explain observer (the parity guarantee)."""
        if strategy != "rwe-b" or not user_side:
            return cols[:k]
        pos = np.asarray(mind.item_positions, dtype=float)
        cross, same = [], []
        for c in cols:
            lean = float(pos[c]) if np.isfinite(pos[c]) else 0.0
            (cross if _cross_of(user_side, lean, True) else same).append(c)
        return (cross + same)[:k]

    @staticmethod
    def _preference_rerank(mind, cols: list, params: "dict | None",
                           country_by_col: "dict | None" = None) -> list:
        """Stable rank nudge over an ADMITTED candidate list — Interest Intensity and the For You
        country preference, applied as ONE sort.

        ``params["interests"]`` maps lower-cased catalog topics to slider weights 1–10 (the
        neutral 5 never ships a key — :func:`rec_params_from_settings` drops it).
        ``params["country"]`` is an ISO alpha-2 code (Global ships no key). An item at 0-based
        pool position ``i`` re-sorts on ``(i + 1) / (interest_mult * country_mult)``
        (:data:`_INTEREST_ANCHORS`: weight 10 divides the effective rank by 8, weight 1
        multiplies it by 5; :data:`_COUNTRY_BOOST`: a country match divides by 8).

        The two preferences MULTIPLY into a single key rather than running as two sorts, so they
        compose the way a reader would expect — a high-interest item from the chosen country
        outranks either signal alone (up to 64x), and neither preference silently undoes the
        other, which is exactly what a second stable sort over the first's output would do.

        It is a nudge on the ORDER of the pool, never an admission or exclusion, so every item
        stays reachable and the slice budgets / publisher cap downstream mean what they always
        meant. Items the nudge does not name — unweighted topics, "" uncategorized, and articles
        with no known country — keep their exact key, and the sort is stable, so the no-preference
        case preserves model order exactly: the identity claim is by construction, not tolerance.

        Runs BEFORE :meth:`_slice_select`, so the rwe-b cross-cutting-first partition (Commit
        R1.5) and the W1 bridge budget keep their guarantees over the nudged order. Shared by
        the serving path and the explain observer — the 21a parity rule: one implementation,
        or drift.

        **Reader state (Tier 1, docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md).** ``params["feedback"]``
        and ``params["repetition"]`` — built by :mod:`rec_context` only when their flags are on —
        add bounded per-article / per-topic / per-publisher multipliers into the SAME sort key
        (anchors and floors above :func:`_reader_state_factors`). One exception to "never an
        exclusion": an article the reader explicitly **disliked** is dropped from the pool — the
        reader named that specific article, and re-serving it to honor a nudge-only principle
        would be malicious compliance. Everything else remains a nudge, floored so no topic or
        publisher becomes unreachable. Params without these keys → the historical feed, exactly."""
        weights = (params or {}).get("interests")
        want_country = (params or {}).get("country")
        fb = (params or {}).get("feedback") or {}
        rep = (params or {}).get("repetition") or {}
        if (not weights and not want_country and not fb and not rep) or not cols:
            return cols
        cats = np.asarray(mind.categories)
        by_col = country_by_col or {}
        mode = (params or {}).get("countryMode") or country_mode()
        strict = bool(want_country) and mode == "first"
        if fb or rep:
            drop, art_by_col, topic_mult, pub_mult = _reader_state_factors(mind, fb, rep)
            outlets = np.asarray(mind.outlets) if pub_mult else None
            cols = [c for c in cols if int(c) not in drop]
            if not cols:
                return cols
        else:
            art_by_col, topic_mult, pub_mult, outlets = {}, {}, {}, None
        keys = []
        for i, col in enumerate(cols):
            mult = 1.0
            if weights:
                w = weights.get(str(cats[col]).strip().lower())
                if w:
                    mult *= _interest_multiplier(w)
            hit = bool(want_country) and want_country in by_col.get(int(col), ())
            if want_country and not strict:
                mult *= _country_multiplier(by_col.get(int(col), ()), want_country,
                                            (params or {}).get("countryBoost"))
            if art_by_col or topic_mult or pub_mult:
                f = art_by_col.get(int(col), 1.0)
                if topic_mult:
                    f *= topic_mult.get(str(cats[col]).strip().lower(), 1.0)
                if pub_mult and outlets is not None:
                    f *= pub_mult.get(str(outlets[col]), 1.0)
                # One clamp over the COMPOSED reader-state factor: feedback can dim or lift, but
                # it can neither bury (floor) nor dominate the reader's own sliders (cap ≤ the
                # weight-10 interest divisor).
                mult *= min(max(f, _READER_STATE_FLOOR), _READER_STATE_CAP)
            # In ``first`` the country is a PARTITION rank, kept out of the multiplier so the
            # interest nudge still orders items WITHIN each group — collapsing both into one
            # divisor (an "infinite boost") would drive every country item's key to zero and
            # throw the reader's interest ordering away exactly where they asked for it most.
            group = 0 if (strict and hit) else 1
            keys.append((group, (i + 1) / mult if mult != 1.0 else float(i + 1)))
        order = sorted(range(len(cols)), key=lambda i: (keys[i], i))
        return [cols[i] for i in order]

    @staticmethod
    def _rec_cols_of(mind, rec: "_Recommenders", u: int, strategy: str, k: int = 12,
                     params: "dict | None" = None, user_side: float = 0.0,
                     country_by_col: "dict | None" = None, blindspot=()) -> list:
        """Top-k *admitted* item columns (in ``mind`` space) recommender ``strategy`` surfaces for
        row ``u``, so we can serialise full articles. Ranks the full list, keeps the columns that
        pass :meth:`_slice_admits` (rwe-b: political only) — so a filtered slot is backfilled by
        the next-ranked admissible item instead of shrinking the slice — applies the reader's
        Interest Intensity nudge over the admitted pool (:meth:`_interest_rerank`; identity
        without weights), and orders the slice via :meth:`_slice_select` (rwe-b: cross-cutting
        first, Commit R1.5; needs ``user_side``). Corpus-parametric so a real user's augmented
        recommender selects columns exactly as the base one does; ``params`` (slider-mapped
        hyperparameters) swaps in a per-request model via :meth:`_model_for`. Returns [] on any
        failure (caller falls back)."""
        try:
            uid = np.asarray(mind.dataset.user_ids)[u]
            rows = np.flatnonzero(np.asarray(rec.rec_dataset.user_ids) == uid)
            if rows.size == 0:
                return []
            model = Backend._model_for(rec, strategy, params)
            ranked = model.recommend(np.array([rows[0]]), top_k=int(len(rec.rec_ids)))[0]
            # cross-first selection and the interest nudge both reorder the WHOLE admitted list;
            # plain slices stop at k
            need_all = ((strategy == "rwe-b" and bool(user_side))
                        or bool((params or {}).get("interests"))
                        or bool((params or {}).get("country"))
                        # Reader state also reorders (and dislike shrinks) the whole admitted
                        # pool, so an early stop at k would decay items into — not out of — the
                        # served slice.
                        or bool((params or {}).get("feedback"))
                        or bool((params or {}).get("repetition"))
                        # The blind-spot boost lifts items from ~4x past the cutoff, so the
                        # discovery slice must rank its whole pool too.
                        or (strategy == "rwe-d" and bool(blindspot)))
            admitted = []
            for j in ranked:
                if int(j) < 0:
                    continue
                col = rec.id2col.get(str(rec.rec_ids[int(j)]))
                if col is not None and Backend._slice_admits(mind, strategy, col):
                    admitted.append(col)
                    if not need_all and len(admitted) >= k:
                        break
            admitted = Backend._preference_rerank(mind, admitted, params, country_by_col)
            if strategy == "rwe-d" and blindspot:
                # After the reader's own preferences, before slice ordering: the report's gap
                # topics lift within the DISCOVERY slice only (see _BLINDSPOT_BOOST).
                admitted = Backend._blindspot_rerank(mind, admitted, blindspot)
            return Backend._slice_select(mind, strategy, admitted, k, user_side)
        except Exception:
            return []

    def _serialize_rec(self, corpus: _Corpus, col: int, strategy: str, user_side: float,
                       familiarity=None) -> dict:
        """Turn a recommended column (in ``corpus.mind`` space) into a rec payload. Corpus-
        parametric so a real user's augmented recs serialise through the same reason logic as
        the demo path — only the corpus the article is drawn from differs.

        Commit 21a: reasons are **evidence-gated** — a familiarity claim ("an outlet you rarely
        read") appears only when the reader's measured outlet share says so; the adaptive copy
        states the neutral-exposure truth (measured exposure isn't wired into AdaptiveRWEB yet);
        and the placeholder ``healthImpact`` number is gone (it was a stable hash, not a
        measurement — the explain endpoint carries the real evidence instead)."""
        art = self._serialize_article(corpus, col)
        # Conservative: an article with an unknown political flag is never claimed cross-cutting.
        cross = _cross_of(user_side, art["lean"], bool(art.get("political")))
        side = {"left": "left-leaning", "right": "right-leaning", "center": "centrist"}[art["leanBucket"]]
        topic = art["topic"].lower()
        band = (familiarity(art["publisher"]) if familiarity is not None else {}).get("band")
        novelty = {"never": "an outlet you've never read",
                   "rarely": "an outlet you rarely read"}.get(band)
        if strategy == "story":
            # The story sources (the RWE_STORY_SLOT post-pass, or the Tier-2 RWE_REC_STORY_SOURCE
            # slice). Every claim is guaranteed by the source's own gates (personalize
            # ``_apply_story_slot`` / ``_story_source_cols``): the reader read an article of the
            # same validated story cluster, and this sibling is from a different publisher.
            reason = (f"Another outlet's coverage of a story you read — {novelty}."
                      if novelty else "Another outlet's coverage of a story you read.")
            helps = "sourceDiversity"
        elif strategy == "emerging":
            # Tier-2 emerging-story source: multi-publisher validation (EMERGING_MIN_PUBLISHERS)
            # and the recency window are the source's admission gates, so "gaining coverage" is
            # licensed by construction — never inferred at serialisation time.
            reason = ("A story gaining coverage across outlets right now — early enough to "
                      "see every side form.")
            helps = "sourceDiversity"
        elif strategy == "blindspot":
            # Tier-2 blind-spot slice v2: the topic IS one of the reader's measured gap topics —
            # the source admits nothing else — and the same report the copy cites renders the gap.
            reason = (f"You rarely read {topic} — a measured gap in your diet this widens."
                      if topic else "Widens a measured gap in your reading diet.")
            helps = "topicDiversity"
        elif strategy == "rwe-d":
            reason = (f"A long-tail read on {topic} from {novelty} — RWE-D widens your sources."
                      if novelty else
                      f"A long-tail read on {topic} — RWE-D reaches past the popular head to "
                      f"widen your sources.")
            helps = "sourceDiversity"
        elif strategy == "adaptive":
            reason = (f"A {side} take on {topic} — adaptive bridging balances the stretch, and "
                      f"tunes further as your open-mindedness signal accrues.")
            helps = "openMindedness" if cross else "topicDiversity"
        else:
            reason = (f"A {side} take on {topic} — RWE-B surfaced it to bridge you across the centre."
                      if cross else
                      (f"Broadens your {topic} coverage from {novelty}." if novelty else
                       f"Broadens your {topic} coverage beyond your usual mix."))
            helps = "viewpointBalance" if cross else "sourceDiversity"
        return {
            "article": art,
            "reason": reason,
            "strategy": strategy,
            # C6: no longer rendered — the card's generic "Helps {metric}" chip was a strategy-
            # derived label, not evidence, so the UI now shows the resolver's concrete measured
            # facts instead. Kept in the payload as a back-compat tag for older clients.
            "helpsMetric": "viewpointBalance" if cross else helps,
            "crossCutting": bool(cross),
        }

    @staticmethod
    def _blindspot_topics(rep) -> tuple:
        """The reader's measured blind-spot topics, lower-cased for the catalog-topic
        comparison — or ``()`` when the flag is off or none are measured. Reads the SAME
        ``rep["blind_spots"]`` the Health Report renders (``(topic, reader_share,
        corpus_share)`` tuples), so the feed can never chase a gap the report does not show."""
        if not blindspot_boost_enabled():
            return ()
        gaps = (rep or {}).get("blind_spots") or ()
        return tuple(t for t in (str(cat).strip().lower() for cat, *_ in gaps) if t)

    @staticmethod
    def _blindspot_source_cols(mind, rec: "_Recommenders", u: int, params: "dict | None",
                               user_side: float, topics, k: int,
                               country_by_col: "dict | None" = None) -> list:
        """Blind-spot v2 candidate columns: the reader's OWN rwe-d ranking, filtered to their
        measured gap topics, cut to ``k``. Built on :meth:`_rec_cols_of` with the full pool
        (so the topic filter has everything the discovery model admits to draw from) and NO v1
        boost — the filter replaces the nudge; both at once would double-count the same signal.
        Personalized by construction (it is the same model that ranks the rwe-d slice), and every
        candidate's topic is in ``topics``, so the card copy's claim is the admission rule.
        Shared by the serving path and the explain observer (the 21a parity rule)."""
        if not topics or k <= 0:
            return []
        pool = Backend._rec_cols_of(mind, rec, u, "rwe-d", int(len(rec.rec_ids)), params,
                                    user_side=user_side, country_by_col=country_by_col,
                                    blindspot=())
        cats = np.asarray(mind.categories)
        want = {str(t).strip().lower() for t in topics}
        return [c for c in pool if str(cats[c]).strip().lower() in want][:k]

    @staticmethod
    def _blindspot_rerank(mind, cols: list, topics, boost: "float | None" = None) -> list:
        """Stable, bounded rank nudge lifting blind-spot-topic items in an admitted pool —
        the same construction as :meth:`_preference_rerank` (identity without topics, a nudge
        never an exclusion, within-topic model order intact, deterministic), kept as its OWN
        pass because its input is server-measured (the reader's report) rather than
        reader-chosen (params), and the two must stay separately auditable. Shared by the
        serving path and the explain observer — the 21a parity rule."""
        if not topics or not cols:
            return cols
        b = _BLINDSPOT_BOOST if boost is None else float(boost)
        cats = np.asarray(mind.categories)
        want = set(topics)
        keys = [(i + 1) / b if str(cats[col]).strip().lower() in want else float(i + 1)
                for i, col in enumerate(cols)]
        order = sorted(range(len(cols)), key=lambda i: (keys[i], i))
        return [cols[i] for i in order]

    @staticmethod
    def _select_diverse(cols_by_strategy, plan, publisher_of, cap: "int | None" = None,
                        *, story_of=None, topic_of=None,
                        story_cap: "int | None" = None, topic_cap: "int | None" = None) -> list:
        """Choose the final ``[(col, strategy)]`` feed: first-seen article dedup, plus a
        per-publisher cap applied ACROSS the whole feed — and, when enabled, per-story and
        per-topic quotas of exactly the same shape (Tier 1,
        docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md). ``story_of`` / ``topic_of`` are optional
        callables (col → story id / topic, ``None``/"" = ungrouped and uncapped); the caps read
        :func:`max_cards_per_story` / :func:`max_cards_per_topic` per call, default **off**, so
        the historical feed is untouched until an operator enables them.

        Shared by the serving path and the ``rec_explain`` observer so the two can never drift
        into disagreeing about which cards were served — the same reason ``_slice_admits`` and
        ``_slice_select`` are shared helpers rather than duplicated loops.

        Three invariants, each of which a naive cap would break:

        * **Per-strategy budgets are preserved exactly** whenever each slice's pool can fill
          them. Each strategy contributes its planned ``k``, so the openness slider's rwe-b
          budget — the cross-cutting floor — means the same thing after this change as before
          it. The one exception is the Tier-2 cross-slice backfill below: a thin EXTRA source
          hands its unfillable slots back to the other slices rather than shrinking the feed.
        * **The feed never shrinks.** Columns the cap declines are held in ``spill`` and top the
          slice back up to ``k`` if the over-fetched pool cannot fill it under the cap. A reader
          in a thin catalog gets the old feed, not a short one.
        * **Rank order is untouched.** This only ever SKIPS a candidate; it never promotes a
          lower-ranked item over a higher-ranked one of the same publisher.

        ``cap`` defaults to :func:`max_cards_per_publisher` (env-tunable). Resolved HERE rather
        than as a parameter default so the environment is read per call — a default evaluated at
        def time would freeze the value at import and defeat the kill switch.

        Deterministic: same inputs → same feed."""
        if cap is None:
            cap = max_cards_per_publisher()
        if story_cap is None:
            story_cap = max_cards_per_story()
        if topic_cap is None:
            topic_cap = max_cards_per_topic()
        chosen, seen, per_pub, per_story, per_topic = [], set(), {}, {}, {}

        def _declined(col) -> bool:
            """Any quota full for this candidate? One decision so a card is spilled once, for
            whichever quota it hits first — the spill path does not care which."""
            if cap > 0 and per_pub.get(publisher_of(col), 0) >= cap:
                return True
            if story_cap > 0 and story_of is not None:
                sid = story_of(col)
                if sid is not None and per_story.get(sid, 0) >= story_cap:
                    return True
            if topic_cap > 0 and topic_of is not None:
                t = topic_of(col)
                if t and per_topic.get(t, 0) >= topic_cap:
                    return True
            return False

        def _count(col) -> None:
            pub = publisher_of(col)
            per_pub[pub] = per_pub.get(pub, 0) + 1
            if story_of is not None:
                sid = story_of(col)
                if sid is not None:
                    per_story[sid] = per_story.get(sid, 0) + 1
            if topic_of is not None:
                t = topic_of(col)
                if t:
                    per_topic[t] = per_topic.get(t, 0) + 1

        budgets = [k for _, k in plan]
        for (strat, cols), k in zip(cols_by_strategy, budgets):
            taken, spill = 0, []
            for col in cols:
                if taken >= k:
                    break
                if col in seen:
                    continue
                if _declined(col):
                    spill.append(col)          # declined by a quota, not by any evidence gate
                    continue
                seen.add(col)
                _count(col)
                chosen.append((col, strat))
                taken += 1
            for col in spill:                  # top up rather than serve a short feed
                if taken >= k:
                    break
                if col in seen:
                    continue
                seen.add(col)
                _count(col)
                chosen.append((col, strat))
                taken += 1
        # Cross-slice backfill (Tier 2): an extra source's pool is inherently thin (real story
        # siblings, current gap topics), so after dedup against earlier slices it can under-fill
        # the budget the plan granted it — and those slots came OUT of the RWE slices, so dying
        # unfilled would shrink the feed. Hand them back instead: in plan order, still-unchosen
        # candidates that respect the quotas first, then quota-declined ones (the same concession
        # the per-slice spill already makes). A no-op whenever every slice filled its budget —
        # the pre-Tier-2 feed is byte-identical by construction, and rank order is still never
        # violated within any slice.
        want_total = sum(budgets)
        for pass_declined in (False, True):
            if len(chosen) >= want_total:
                break
            for (strat, cols), _k in zip(cols_by_strategy, budgets):
                for col in cols:
                    if len(chosen) >= want_total:
                        break
                    if col in seen or (not pass_declined and _declined(col)):
                        continue
                    seen.add(col)
                    _count(col)
                    chosen.append((col, strat))
        return chosen

    @staticmethod
    def _present_order(picks: list) -> list:
        """Presentation order over selected ``(col, strategy)`` picks: story-source cards move to
        the FRONT of the feed (the one-card slot's precedent — "continue the story you read" is
        the above-the-fold hook), everything else keeps selection order. A pure, stable reorder
        AFTER selection, so dedup priority (plan order: RWE slices first) and quota accounting are
        untouched. Shared by the serving path and the explain observer (21a parity)."""
        if not any(s == "story" for _, s in picks):
            return picks
        return ([p for p in picks if p[1] == "story"]
                + [p for p in picks if p[1] != "story"])

    def _serialize_recs(self, corpus: _Corpus, cols_by_strategy, user_side: float,
                        familiarity=None, plan=None) -> list:
        """Select + serialise chosen ``(strategy, columns)`` groups into a rec list, preserving
        first-seen order. Shared by the base path and the augmented (Measured) path; only the
        recommender that *chose* the columns differs, never this assembly.

        ``plan`` carries each strategy's slot budget so :meth:`_select_diverse` knows how many
        cards a strategy owes; omitted (older callers) it degrades to "take everything offered",
        which is the pre-cap behaviour."""
        outlets = np.asarray(corpus.mind.outlets)
        if plan is None:
            plan = [(strat, len(cols)) for strat, cols in cols_by_strategy]
        # Story / topic quota inputs (Tier 1) — cheap to build and inert while the caps are off:
        # _select_diverse consults the callables only when its per-call cap is > 0.
        story_by_col = self._story_by_col(corpus.mind)
        cats = np.asarray(corpus.mind.categories)
        picks = self._select_diverse(cols_by_strategy, plan, lambda c: str(outlets[c]),
                                     story_of=story_by_col.get,
                                     topic_of=lambda c: str(cats[c]).strip().lower())
        return [self._serialize_rec(corpus, col, strat, user_side, familiarity)
                for col, strat in self._present_order(picks)]

    def _serialize_recommendations(self, corpus: _Corpus, rec: "_Recommenders", u: int,
                                   strategy: str | None = None,
                                   params: "dict | None" = None,
                                   extra_cols=None, extra_off: tuple = (),
                                   metrics_kind: "str | None" = None) -> list:
        """Full recommendation pipeline over a corpus + its recommender stack: derive the
        reader's side, pick the plan (one strategy, or the default blend), select columns, and
        serialise. Shared verbatim by the base path and the augmented (Measured) path, so a real
        user's recs use the same blend and reason logic — only the corpus + recommender differ.
        ``params`` (the reader's slider-mapped hyperparameters) applies per strategy inside the
        blend too, so a moved slider shapes its slice of the default feed as well.

        **Tier-2 sources.** ``extra_cols`` — ``[(name, k, cols), …]`` — lets a caller with store
        access (the measured path) contribute non-RWE candidate sources (the story and emerging
        sources) into the SAME selection funnel: :func:`_plan_with_extras` takes their budget out
        of the discovery/adaptive slices, and dedup/quotas/serialisation treat them exactly like
        an RWE slice. The blind-spot v2 source needs no store, so it is built HERE
        (:meth:`_blindspot_source_cols`) whenever its slots are configured — and v1's rank nudge
        is then suppressed, one mechanism at a time. Extras apply to the blended feed only; a
        single-strategy request stays a faithful single-model view. ``extra_off`` names internal
        sources the caller suppresses for THIS request — the cohort harness's control arm for a
        source the engine would otherwise build itself. ``metrics_kind`` overrides the
        composition-metrics kind (the shadow harness records a would-be feed under ``shadow:*``
        without contaminating the serving means)."""
        rep = hr.user_report(corpus.pop, corpus.mind, u)
        user_side = np.sign(rep.get("mean_lean") or 0.0)
        try:
            familiarity = _familiarity_of(corpus.pop, u)   # evidence for the reason templates
        except Exception:
            familiarity = None                             # best-effort: claims are then omitted
        # a single strategy, or a blend across the family for the default "all" view. The blend's
        # slot budget follows the reader's openness slider (W1); absent/50 → DEFAULT_BLEND_PLAN.
        single = strategy in ("rwe-b", "rwe-d", "adaptive")
        plan = [(strategy, 12)] if single else blend_plan_for(params)
        # Over-fetch so the publisher cap has lower-ranked admissible items to backfill FROM;
        # the plan still decides how many cards each strategy contributes (_select_diverse).
        by_col = self._country_by_col(corpus.mind) if (params or {}).get("country") else None
        bs_topics = self._blindspot_topics(rep)      # () unless RWE_REC_BLINDSPOT is on
        bs_k = blindspot_slots() if (not single and "blindspot" not in extra_off) else 0
        # v2 replaces v1: with a slot budget configured, the discovery slice ranks WITHOUT the
        # nudge and the gap topics get their own slice instead.
        bs_nudge = () if bs_k > 0 else bs_topics
        cols_by_strategy = [(strat, self._rec_cols_of(corpus.mind, rec, u, strat,
                                                      k * REC_OVERFETCH, params,
                                                      user_side=float(user_side),
                                                      country_by_col=by_col,
                                                      blindspot=bs_nudge))
                            for strat, k in plan]
        extras = [(n, int(k), cols) for n, k, cols in (extra_cols or ()) if cols and int(k) > 0]
        if bs_k > 0 and bs_topics:
            bs_cols = self._blindspot_source_cols(corpus.mind, rec, u, params,
                                                  float(user_side), bs_topics,
                                                  bs_k * REC_OVERFETCH, country_by_col=by_col)
            if bs_cols:
                extras.append(("blindspot", bs_k, bs_cols))
        if extras:
            # Reader policy applies to EVERY source, not only the RWE slices: each extra's
            # columns pass through the same shared rerank — so a disliked article stays dropped
            # (exclusion is law regardless of which source re-found it), recently-shown cards
            # decay instead of fronting every serve (the first shadow run measured exactly that
            # leak: repeat 8.6/feed shadowed vs 5.2 served), and the reader's sliders shape a
            # source slice like any other. Identity when params carry no state — the builders'
            # own ordering (opposite-lean first, round-robin, emergence rank) is preserved then.
            extras = [(n, k, Backend._preference_rerank(corpus.mind, cols, params, by_col))
                      for n, k, cols in extras]
            extras = [(n, k, cols) for n, k, cols in extras if cols]
        if extras:
            order = {n: i for i, n in enumerate(EXTRA_SOURCE_ORDER)}
            extras.sort(key=lambda e: order.get(e[0], len(order)))
            plan = _plan_with_extras(plan, [(n, k) for n, k, _ in extras])
            granted = {n: k for n, k in plan}
            cols_by_strategy += [(n, cols) for n, k, cols in extras if granted.get(n)]
        recs = self._serialize_recs(corpus, cols_by_strategy, user_side, familiarity, plan=plan)
        # Mark which cards actually matched the reader's country. A country with thin coverage
        # cannot fill the feed, and the remaining slots are BACKFILL — ordinary recommendations,
        # not country coverage. Serving them unlabelled would quietly overstate the catalog: the
        # reader asked for one country and would be shown a feed that looks entirely like it.
        # Present only when a country is selected, so every other response is byte-identical.
        want_country = (params or {}).get("country")
        if want_country and by_col:
            id_of = np.asarray(corpus.mind.dataset.item_ids)
            matched = {str(id_of[c]) for c, cs in by_col.items() if want_country in cs}
            for r in recs:
                r["countryMatch"] = str((r.get("article") or {}).get("id")) in matched
        # One funnel: the demo path and the signed-in Measured path (personalize.py) both land
        # here, so every served feed is counted exactly once. The quality inputs mirror the
        # ranking inputs above — same story maps, same repetition ids, same blind-spot topics —
        # so the metrics measure exactly what the mechanisms they observe consumed.
        rep_ids = (params or {}).get("repetition") or {}
        record_feed_composition(
            recs, user_side=float(user_side),
            kind=metrics_kind or (strategy if strategy in ("rwe-b", "rwe-d", "adaptive")
                                  else "blend"),
            story_of=lambda aid: (self.story_by_id.get(aid)
                                  or (self.story_by_url.get(aid)
                                      if aid.startswith(("http://", "https://")) else None)),
            already_shown=set(rep_ids.get("unopened") or ()) | set(rep_ids.get("opened") or ()),
            blindspot_topics=bs_topics)
        return recs

    def recommendations(self, u: int, strategy: str | None = None,
                        params: "dict | None" = None) -> list:
        """Base reference-corpus recommendations (demo / ``?user=`` path). ``params`` carries a
        signed-in reader's slider-mapped hyperparameters (None → the shared default stack)."""
        return self._serialize_recommendations(self.base_corpus, self.rec, u, strategy, params)

    def explain_recommendations(self, u: int, strategy: str | None = None,
                                params: "dict | None" = None,
                                article: str | None = None) -> dict:
        """Read-only explainability observer for the base/demo path (Commit 21a) — see
        :mod:`rec_explain`. Observes the same models :meth:`recommendations` serves from and
        replicates its plan; parity is pinned by tests. Never mutates or re-ranks."""
        import rec_explain
        return rec_explain.explain(self, self.base_corpus, self.rec, u,
                                   strategy=strategy, params=params, article=article)

    def explanation_context(self, u: int) -> dict:
        """Reader context for the Evidence Resolver (Commit 21a.3), base/demo path: the same
        familiarity lookup the reason gating uses + the reader's top topics. No store reads
        exist here (synthetic personas), so the history-based priorities fall through honestly."""
        rep = hr.user_report(self.base_corpus.pop, self.base_corpus.mind, u)
        return {"reads": [],
                "familiarity": _familiarity_of(self.base_corpus.pop, u),
                # Commit R2: blank / legacy-"general" buckets are not claimable topics.
                "top_topics": [_prettify(t) for t, _ in (rep.get("top_categories") or [])
                               if str(t).strip() and str(t).strip().lower() != "general"],
                # C6: the measured shares behind the CONCRETE readerFacts — the same
                # user_report numbers the explain drawer shows, so surfaces cannot disagree.
                "topic_shares": _topic_shares_of(rep),
                **_lean_shares_of(rep)}

    # -- coach ------------------------------------------------------------- #
    def _facts_of(self, corpus: _Corpus, u: int):
        """(report dict, narrate facts) for reader ``u`` of a corpus — corpus-parametric so the
        coach grounds on a real user's augmented corpus exactly as on the base corpus."""
        rep = hr.user_report(corpus.pop, corpus.mind, u, reference=self.score_reference)
        return rep, nr.report_facts(rep, self.domain)

    def _citations(self, rep: dict) -> list:
        sc = rep.get("scores", {}) or {}
        pairs = [("echoChamber", "Echo Chamber Score"), ("viewpointBalance", "Viewpoint Balance"),
                 ("emotionalBalance", "Emotional Balance"), ("openMindedness", "Open-Mindedness")]
        return [{"metric": k, "value": int(sc[label])} for k, label in pairs if sc.get(label) is not None][:2]

    def _grounded_fallback(self, rep: dict) -> str:
        """A deterministic, fully-grounded reply when no LLM key is set — every number is a
        measured metric (same discipline as narrate_report's grounding rule)."""
        sc = rep.get("scores", {}) or {}
        left, center, right = rep.get("viewpoint") or (0, 0, 0)
        bits = [f"Your overall Information Health is {rep.get('overall')}/100."]
        if sc.get("Echo Chamber Score") is not None:
            bits.append(f"Your Echo Chamber score is {sc['Echo Chamber Score']}/100, and your political "
                        f"reading runs {round(left*100)}% left, {round(center*100)}% center, "
                        f"{round(right*100)}% right.")
        low = min((k for k in ("Emotional Balance", "Viewpoint Balance", "Source Diversity")
                   if sc.get(k) is not None), key=lambda k: sc[k], default=None)
        if low:
            bits.append(f"Your biggest opportunity is {low} ({sc[low]}/100) — that's where a small "
                        f"change would move your score the most. Ask me how, or for reads to help.")
        return " ".join(bits)

    def _serialize_coach_reply(self, corpus: _Corpus, rec: "_Recommenders", u: int,
                               message: str) -> dict:
        """Grounded coach reply for reader ``u`` of a corpus, with bridging suggestions from the
        matching recommender. Corpus-parametric so a real user is coached from their augmented
        corpus through the same grounding + narration path as the demo."""
        rep, facts = self._facts_of(corpus, u)
        recs = nr.rweb_recommendations(corpus.mind, rep) or []
        content = None
        if os.environ.get("GEMINI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"):
            try:
                caller = nr.make_text_caller(self.provider, self.model)
                content = nr.narrate(nr.facts_to_text(facts), caller, recs, self.domain)
            except Exception:
                content = None
        if not content:
            content = self._grounded_fallback(rep)
        # attach up to two real bridging articles as suggestions (cross-first, Commit R1.5)
        side = float(np.sign(rep.get("mean_lean") or 0.0))
        cols = self._rec_cols_of(corpus.mind, rec, u, "rwe-b", k=2, user_side=side)
        suggestions = [self._serialize_article(corpus, c) for c in cols[:2]]
        return {
            "id": f"msg_{_stable_int(message, u)}",
            "role": "assistant",
            "content": content,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "citations": self._citations(rep),
            "suggestions": suggestions,
        }

    def coach_reply(self, u: int, message: str) -> dict:
        """Base reference-corpus coach reply (demo / ``?user=`` path)."""
        return self._serialize_coach_reply(self.base_corpus, self.rec, u, message)

    def _serialize_coach_greeting(self, corpus: _Corpus, u: int) -> list:
        """Coach greeting for reader ``u`` of a corpus (citations grounded on that corpus)."""
        rep, _ = self._facts_of(corpus, u)
        return [{
            "id": "msg_0",
            "role": "assistant",
            # User-facing product name is "Guide" (the internal service/route/file names stay
            # `coach` — see the greeting pins in tests/test_coach_v1_contract.py).
            "content": ("Hi — I'm your Information Health guide. I read your metrics straight from the "
                        "engine, so I can explain any score, spot patterns in your reading, and suggest "
                        "balanced reads. What would you like to look at?"),
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "citations": self._citations(rep),
        }]

    def coach_greeting(self, u: int) -> list:
        """Base reference-corpus coach greeting (demo / ``?user=`` path)."""
        return self._serialize_coach_greeting(self.base_corpus, u)

    def health(self) -> dict:
        s = self.mind.summary()
        return {"ok": True, "profile": self.profile.name, "domain": self.domain,
                "demoUser": self.demo_user, "eligibleReaders": int(len(self.eligible)),
                "narrative": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")),
                "dataset": {k: (int(v) if isinstance(v, (int, np.integer)) else v)
                            for k, v in s.items() if not isinstance(v, (list, dict, np.ndarray))}}


# ------------------------------------------------------------------ #
# HTTP layer
# ------------------------------------------------------------------ #
def _make_handler(be: Backend):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, obj, status=200):
            data = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):
            self._send({}, 204)

        def do_GET(self):
            p = urlparse(self.path)
            q = parse_qs(p.query)
            try:
                if p.path == "/api/health":
                    return self._send(be.health())
                if p.path == "/api/report":
                    return self._send(be.report(be.resolve_user(q)))
                if p.path == "/api/recommendations":
                    strat = (q.get("strategy", [""])[0] or "").strip() or None
                    return self._send(be.recommendations(be.resolve_user(q), strat))
                if p.path == "/api/coach":
                    return self._send(be.coach_greeting(be.resolve_user(q)))
                return self._send({"error": "not found"}, 404)
            except Exception as e:  # never 500 the app
                return self._send({"error": str(e)}, 500)

        def do_POST(self):
            p = urlparse(self.path)
            q = parse_qs(p.query)
            try:
                n = int(self.headers.get("Content-Length", 0) or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except Exception:
                body = {}
            try:
                if p.path == "/api/coach":
                    u = be.demo_user
                    if str(body.get("user", "")).lstrip("-").isdigit():
                        u = int(body["user"])
                    return self._send(be.coach_reply(u, str(body.get("message", ""))))
                return self._send({"error": "not found"}, 404)
            except Exception as e:
                return self._send({"error": str(e)}, 500)

        def log_message(self, *a):
            pass
    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=sorted(BUILTIN_PROFILES), default=None,
                    help="data source: synthetic (default) | qbias | mind | politosphere "
                         "(env RWE_PROFILE)")
    ap.add_argument("--npz", default=None, help="ingested .npz for mind/politosphere (env RWE_NPZ)")
    ap.add_argument("--qbias", default=None, help="Qbias allsides CSV for the qbias profile (env RWE_QBIAS)")
    ap.add_argument("--register-csv", default=None, help="per-item P(reporting) CSV (env RWE_REGISTER_CSV)")
    ap.add_argument("--emotion-csv", default=None, help="per-item emotion CSV (env RWE_EMOTION_CSV)")
    ap.add_argument("--behaviors", default=None, help="MIND behaviors.tsv for Open-Mindedness (env RWE_BEHAVIORS)")
    ap.add_argument("--lean-tau", type=float, default=None, help="centre half-width on the lean axis (env RWE_LEAN_TAU)")
    ap.add_argument("--domain", choices=["news", "reddit"], default=None)
    ap.add_argument("--provider", choices=["gemini", "anthropic"], default="anthropic")
    ap.add_argument("--model", default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--n-users", type=int, default=None)
    ap.add_argument("--max-items", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    profile = resolve_profile(args)
    be = Backend(profile, args.provider, args.model)
    key = "ON" if (os.environ.get("GEMINI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")) else "OFF"
    src = profile.npz or profile.qbias_csv or f"synthetic ({profile.n_users}u x {profile.max_items}i)"
    print(f"Information Health API on http://{args.host}:{args.port}  "
          f"(profile={profile.name}, data={src}, demo reader={be.demo_user}, narrative={key})")
    http.server.ThreadingHTTPServer((args.host, args.port), _make_handler(be)).serve_forever()


if __name__ == "__main__":
    main()
