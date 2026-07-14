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
import narrate_report as nr
from rwe.mind import MINDData

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


def _reading_streak(read_ats) -> int:
    """Consecutive UTC days ending today on which the reader recorded at least one read, from ISO
    ``readAt`` strings. A break (no read today) makes it 0 — an honest current-streak count, not a
    best-ever. Timestamps are compared by their date prefix (``YYYY-MM-DD``)."""
    days = {ra[:10] for ra in read_ats if isinstance(ra, str) and len(ra) >= 10}
    if not days:
        return 0
    streak, d = 0, datetime.now(timezone.utc).date()
    while d.isoformat() in days:
        streak += 1
        d = d - timedelta(days=1)
    return streak


def _read_at(row) -> "str | None":
    """A stored-read row's effective timestamp: the reader's observed time, else the scored read's
    own timestamp, else the row's insert time. One definition shared by the history and analytics
    serialisers so day-bucketing never diverges."""
    sc = row.get("scored") or {}
    return row.get("observedAt") or sc.get("read_at") or row.get("createdAt")


def _day(ts) -> "str | None":
    """The ``YYYY-MM-DD`` day of an ISO timestamp string, or ``None`` if it isn't one."""
    return ts[:10] if isinstance(ts, str) and len(ts) >= 10 else None


def _overall_trend(snapshots) -> list:
    """Compact overall-score trend points from report snapshots (as ``list_report_snapshots`` rows),
    oldest-first. One definition shared by the dashboard trend and the profile score history."""
    return [{"date": _day(s.get("createdAt")), "overall": int(s.get("overall") or 0)}
            for s in snapshots if _day(s.get("createdAt"))]


def _longest_streak(read_ats) -> int:
    """The longest run of consecutive UTC days with at least one read, over all of a reader's reads
    (not necessarily ending today). Deterministic; ``0`` when there are no reads."""
    days = sorted({ra[:10] for ra in read_ats if isinstance(ra, str) and len(ra) >= 10})
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
# Product preferences (settings). Three of these now shape product behaviour —
# ``politicalOpenness`` / ``recommendationStrength`` map to per-request RWE-B/RWE-D
# hyperparameters (see :func:`rec_params_from_settings`) and ``readingGoalMinutes``
# drives the dashboard's today-vs-goal progress. Settings still wire NOTHING into
# the health report or its metrics.
# --------------------------------------------------------------------------- #
DEFAULT_SETTINGS = {
    "theme": "system",
    "language": "en",
    "politicalOpenness": 50,          # 50 = the stack's default RWE-B epsilon (0.9)
    "recommendationStrength": 50,     # 50 = the stack's default RWE-D beta (0.5)
    "readingGoalMinutes": 20,
    "weeklyReport": True,
    "monthlyReport": False,
    "notifications": {"recommendations": True, "weeklyDigest": True,
                      "streakReminders": False, "blindSpotAlerts": False},
    "privacy": {"shareAnonymizedMetrics": False, "personalizedAds": False},
}
_SETTINGS_THEMES = ("light", "dark", "system")
# Supported interface languages (Commit 20). An unsupported/garbage value falls back to English —
# the same allowlist the web LanguageProvider enforces, so the two never disagree.
_SETTINGS_LANGUAGES = ("en", "es", "fr", "de", "pt")


def _clamp_int(value, lo, hi, default):
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _layered(key, layers, default):
    """The value of ``key`` from the last layer (dict) that defines it — defaults < stored < patch."""
    v = default
    for layer in layers:
        if isinstance(layer, dict) and key in layer:
            v = layer[key]
    return v


def _merge_bool_group(defaults: dict, layers, group: str) -> dict:
    subs = [layer[group] for layer in layers
            if isinstance(layer, dict) and isinstance(layer.get(group), dict)]
    return {k: bool(_layered(k, subs, dv)) for k, dv in defaults.items()}


def normalize_settings(stored: "dict | None", patch: "dict | None" = None) -> dict:
    """A complete, type-safe preferences object = server defaults, overlaid with the user's stored
    preferences, overlaid with an optional incoming ``patch``. Unknown keys are dropped and every
    value is coerced / clamped to the contract, so a partial update from any client (web, iOS,
    Android, extension, RSS) is safe and the response shape is always stable. ``patch=None`` reads
    (with honest defaults for anything unset); a patch merges an update. Normalisation only — the
    behavioural mapping of the two recommendation sliders lives in
    :func:`rec_params_from_settings`, and nothing here ever shapes the health report."""
    layers = [DEFAULT_SETTINGS, stored or {}, patch or {}]
    theme = _layered("theme", layers, DEFAULT_SETTINGS["theme"])
    return {
        "theme": theme if theme in _SETTINGS_THEMES else DEFAULT_SETTINGS["theme"],
        "language": (lambda v: v if v in _SETTINGS_LANGUAGES else "en")(
            str(_layered("language", layers, "en")).strip().lower()),
        "politicalOpenness": _clamp_int(_layered("politicalOpenness", layers, 50), 0, 100, 50),
        "recommendationStrength": _clamp_int(_layered("recommendationStrength", layers, 50), 0, 100, 50),
        "readingGoalMinutes": _clamp_int(_layered("readingGoalMinutes", layers, 20), 0, 600, 20),
        "weeklyReport": bool(_layered("weeklyReport", layers, True)),
        "monthlyReport": bool(_layered("monthlyReport", layers, False)),
        "notifications": _merge_bool_group(DEFAULT_SETTINGS["notifications"], layers, "notifications"),
        "privacy": _merge_bool_group(DEFAULT_SETTINGS["privacy"], layers, "privacy"),
    }


# Slider → recommender-parameter mapping. Piecewise-linear through three anchors, pinned so
# **slider 50 maps exactly to the constants the stack has always used** (RWE-B epsilon 0.9,
# RWE-D beta 0.5): an untouched slider changes nothing, byte for byte. The ranges are deliberately
# gentle — Political openness rides the weak text-lean axis (directional, not a measurement; see
# docs/HEALTH_REPORT.md), so its reach is a nudge, never a hard flip.
_OPENNESS_EPSILON = (0.70, 0.90, 0.97)    # slider 0 / 50 / 100 → RWE-B epsilon (non-bridge erasure)
_STRENGTH_BETA = (0.30, 0.50, 0.80)       # slider 0 / 50 / 100 → RWE-D beta (popularity suppression)


def _piecewise(v: float, lo: float, mid: float, hi: float) -> float:
    """Linear 0→``lo``, 50→``mid``, 100→``hi`` (callers pass an already-clamped 0–100 value)."""
    v = float(v)
    return lo + (mid - lo) * (v / 50.0) if v <= 50.0 else mid + (hi - mid) * ((v - 50.0) / 50.0)


def rec_params_from_settings(settings: "dict | None") -> "dict | None":
    """Per-request recommender parameters from a reader's stored preferences, or ``None``.

    The two sliders map to hyperparameters the RWE classes have always accepted — Political
    openness → RWE-B ``epsilon`` (how strongly same-side items are erased, i.e. how far the walk
    reaches for cross-cutting reads) and Recommendation strength → RWE-D ``beta`` (how strongly
    popular items are suppressed, i.e. how far the feed diversifies from the usual diet). Only a
    *moved* slider contributes a key, and ``None`` means "use the shared default stack" — so demo,
    anonymous, and untouched-slider requests are provably identical to the pre-slider behaviour.
    The algorithms themselves are untouched; this only chooses constructor arguments."""
    s = normalize_settings(settings)
    params = {}
    if s["politicalOpenness"] != 50:
        params["epsilon"] = _piecewise(s["politicalOpenness"], *_OPENNESS_EPSILON)
    if s["recommendationStrength"] != 50:
        params["beta"] = _piecewise(s["recommendationStrength"], *_STRENGTH_BETA)
    return params or None


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
    def _build_recommenders(mind, probe_csv: "str | None" = None) -> "_Recommenders":
        """Build the RWE recommender stack over ``mind``. Shared by the base corpus (startup)
        and a real user's augmented corpus (``personalize.py``) so both construct RWE-B / RWE-D
        / Adaptive with identical inputs and hyperparameters. The RWE algorithms are unchanged —
        this only assembles their inputs, exactly as ``__init__`` did inline before."""
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
        models = {
            "rwe-b": RWEB(fg, theta, item_pos, epsilon=0.9),
            "rwe-d": RWED(fg, beta=0.5),
            "adaptive": AdaptiveRWEB(fg, theta, item_pos, exposure),
        }
        return _Recommenders(rec_dataset=rec_dataset, theta=theta, item_pos=item_pos, fg=fg,
                             models=models, id2col=id2col, rec_ids=rec_ids, exposure=exposure)

    # -- reader selection -------------------------------------------------- #
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
                         political: "bool | None" = None) -> dict:
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
        pos = float(lean) if lean is not None and np.isfinite(lean) else 0.0
        conf = float(confidence) if confidence is not None and np.isfinite(confidence) else 0.7
        dom = max(emotion, key=emotion.get) if emotion else "neutral"
        url = self._resolve_url(item_id)
        payload = {
            "id": item_id,
            "headline": str(headline),
            "publisher": _prettify(outlet),
            "publisherLean": float(outlet_lean.get(str(outlet), 0.0)),
            "topic": _prettify(topic),
            "lean": pos,
            "leanBucket": _lean_bucket(pos, self.lean_tau),
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
        carries its scored fields; outlet house-lean comes from the base corpus map (unknown → 0).
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
                political=sc.get("political"))
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
                        goal_minutes: "int | None" = None) -> dict:
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
            "overall": overall,
            "overallDelta": delta,
            "trend": trend,
            "today": today_block,
            "metrics": report.get("metrics", []),                    # the report's metrics, reused as-is
            "streakDays": _reading_streak([e.get("readAt") for e in recent]),
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
    def build_profile(user: dict, reads: list, snapshots: list, saved_count: int = 0) -> dict:
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
            "streakDays": _reading_streak(read_ats),
            "longestStreak": _longest_streak(read_ats),
            "scoreHistory": _overall_trend(snapshots),
            "achievements": [],          # not built yet — honest empty, not fabricated
            "savedCount": saved_count,   # the real persisted count (Saved is the single concept)
        }

    def article(self, col: int) -> dict:
        """Base reference-corpus article (the demo / ``?user=`` path)."""
        return self._serialize_article(self.base_corpus, col)

    def _serialize_report(self, corpus: _Corpus, u: int) -> dict:
        """Serialise the Measured Information Health Report for reader row ``u`` of a corpus.

        Corpus-parametric: the base reference corpus (demo / ``?user=`` path) and a real
        user's augmented corpus both flow through this one path, so a Measured report is
        identical in shape and computation regardless of which reader it describes. Every
        number still comes from ``health_report.user_report`` on the corpus — this only
        serialises what the unchanged engine computed."""
        rep = hr.user_report(corpus.pop, corpus.mind, u)
        scores = rep.get("scores", {}) or {}
        n_clicks = rep.get("n_clicks") or 0

        metrics = []
        for key, label in _METRIC_KEYS:
            s = scores.get(label)
            if s is None:
                continue
            metrics.append({"key": key, "score": int(s), "delta": 0, "benchmark": 50,
                            "band": _score_band(int(s))})
        vc = rep.get("viewpoint_confidence")
        axis_conf = float(vc) if vc is not None else 0.7
        conf_score = round(axis_conf * 100)
        metrics.append({"key": "confidence", "score": conf_score, "delta": 0, "benchmark": 70,
                        "band": _score_band(conf_score),
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
            gap = float((cat_share - user_share) / cat_share) if cat_share else 0.0
            blind.append({"topic": _prettify(cat), "gap": max(0.0, min(1.0, gap)),
                          "note": f"{_prettify(cat)} is {round(cat_share*100)}% of what's available, "
                                  f"but barely shows up in your reading."})

        # improvements from the lowest real metrics
        ranked = sorted((m for m in metrics if m["key"] != "confidence"), key=lambda m: m["score"])
        improvements = []
        for m in ranked[:3]:
            tpl = _IMPROVEMENTS.get(m["key"])
            if tpl:
                improvements.append({"id": f"imp_{m['key']}", "title": tpl[0], "detail": tpl[1],
                                     "metric": m["key"], "impact": tpl[2]})

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
        for key, _label in _METRIC_KEYS:          # Open-Mindedness has no raw here -> skipped
            s = raw.get(key)
            if s is None:
                continue
            metrics.append({"key": key, "score": int(s), "delta": 0, "benchmark": 50,
                            "band": _score_band(int(s))})
        have = [m["score"] for m in metrics]
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
                       if q_c[i] > 0.02 and topic_share[i] < 0.5 * q_c[i]),
                      key=lambda t: -(t[2] - t[1]))
        blind = [{"topic": _prettify(c), "gap": max(0.0, min(1.0, (qc - us) / qc if qc else 0.0)),
                  "note": f"{_prettify(c)} is {round(qc * 100)}% of what's available, but light in "
                          f"the outlets you picked."}
                 for (c, us, qc) in gaps[:2]]

        improvements = []
        for m in sorted(metrics, key=lambda d: d["score"])[:3]:
            tpl = _IMPROVEMENTS.get(m["key"])
            if tpl:
                improvements.append({"id": f"imp_{m['key']}", "title": tpl[0], "detail": tpl[1],
                                     "metric": m["key"], "impact": tpl[2]})

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
    def _rec_cols_of(mind, rec: "_Recommenders", u: int, strategy: str, k: int = 12,
                     params: "dict | None" = None, user_side: float = 0.0) -> list:
        """Top-k *admitted* item columns (in ``mind`` space) recommender ``strategy`` surfaces for
        row ``u``, so we can serialise full articles. Ranks the full list, keeps the columns that
        pass :meth:`_slice_admits` (rwe-b: political only) — so a filtered slot is backfilled by
        the next-ranked admissible item instead of shrinking the slice — and orders the slice via
        :meth:`_slice_select` (rwe-b: cross-cutting first, Commit R1.5; needs ``user_side``).
        Corpus-parametric so a real user's augmented recommender selects columns exactly as the
        base one does; ``params`` (slider-mapped hyperparameters) swaps in a per-request model via
        :meth:`_model_for`. Returns [] on any failure (caller falls back)."""
        try:
            uid = np.asarray(mind.dataset.user_ids)[u]
            rows = np.flatnonzero(np.asarray(rec.rec_dataset.user_ids) == uid)
            if rows.size == 0:
                return []
            model = Backend._model_for(rec, strategy, params)
            ranked = model.recommend(np.array([rows[0]]), top_k=int(len(rec.rec_ids)))[0]
            # cross-first selection partitions the WHOLE admitted list; plain slices stop at k
            need_all = strategy == "rwe-b" and bool(user_side)
            admitted = []
            for j in ranked:
                if int(j) < 0:
                    continue
                col = rec.id2col.get(str(rec.rec_ids[int(j)]))
                if col is not None and Backend._slice_admits(mind, strategy, col):
                    admitted.append(col)
                    if not need_all and len(admitted) >= k:
                        break
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
            # The conditional Story-Match slot (RWE_STORY_SLOT). Every claim is guaranteed by the
            # slot's own gates (personalize._apply_story_slot): the reader read an article of the
            # same validated story cluster, and this sibling is from a different publisher.
            reason = (f"Another outlet's coverage of a story you read — {novelty}."
                      if novelty else "Another outlet's coverage of a story you read.")
            helps = "sourceDiversity"
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

    def _serialize_recs(self, corpus: _Corpus, cols_by_strategy, user_side: float,
                        familiarity=None) -> list:
        """Dedup + serialise chosen ``(strategy, columns)`` groups into a rec list, preserving
        first-seen order. Shared by the base path and the augmented (Measured) path; only the
        recommender that *chose* the columns differs, never this assembly."""
        out, seen = [], set()
        for strat, cols in cols_by_strategy:
            for col in cols:
                if col in seen:
                    continue
                seen.add(col)
                out.append(self._serialize_rec(corpus, col, strat, user_side, familiarity))
        return out

    def _serialize_recommendations(self, corpus: _Corpus, rec: "_Recommenders", u: int,
                                   strategy: str | None = None,
                                   params: "dict | None" = None) -> list:
        """Full recommendation pipeline over a corpus + its recommender stack: derive the
        reader's side, pick the plan (one strategy, or the default blend), select columns, and
        serialise. Shared verbatim by the base path and the augmented (Measured) path, so a real
        user's recs use the same blend and reason logic — only the corpus + recommender differ.
        ``params`` (the reader's slider-mapped hyperparameters) applies per strategy inside the
        blend too, so a moved slider shapes its slice of the default feed as well."""
        rep = hr.user_report(corpus.pop, corpus.mind, u)
        user_side = np.sign(rep.get("mean_lean") or 0.0)
        try:
            familiarity = _familiarity_of(corpus.pop, u)   # evidence for the reason templates
        except Exception:
            familiarity = None                             # best-effort: claims are then omitted
        # a single strategy, or a blend across the family for the default "all" view
        plan = ([(strategy, 12)] if strategy in ("rwe-b", "rwe-d", "adaptive")
                else DEFAULT_BLEND_PLAN)
        cols_by_strategy = [(strat, self._rec_cols_of(corpus.mind, rec, u, strat, k, params,
                                                      user_side=float(user_side)))
                            for strat, k in plan]
        return self._serialize_recs(corpus, cols_by_strategy, user_side, familiarity)

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
        rep = hr.user_report(corpus.pop, corpus.mind, u)
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
            "content": ("Hi — I'm your Information Health coach. I read your metrics straight from the "
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
