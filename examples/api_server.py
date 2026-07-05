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

    def _article_payload(self, *, item_id, headline, outlet, topic, lean, register,
                         emotion: dict, confidence, outlet_lean: dict) -> dict:
        """Build one article payload from already-resolved fields — the SINGLE source of the
        ``Article`` shape (web/types/domain.ts). Both the corpus serialiser (:meth:`_serialize_article`)
        and the reading-history serialiser (:meth:`serialize_history`) feed this, so a catalog
        article and a real reader's stored read render identically. Missing lean/confidence degrade
        to the same neutral defaults the corpus path already used."""
        item_id = str(item_id)
        pos = float(lean) if lean is not None and np.isfinite(lean) else 0.0
        conf = float(confidence) if confidence is not None and np.isfinite(confidence) else 0.7
        dom = max(emotion, key=emotion.get) if emotion else "neutral"
        return {
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
            "publishedAt": _iso_recent(item_id),
            "readingMinutes": 2 + (_stable_int(item_id) % 8),
        }

    def _serialize_article(self, corpus: _Corpus, col: int) -> dict:
        """Serialise one article (column) of a corpus. Corpus-parametric so the same code
        renders a reference-catalog article and a novel article a real reader brought in."""
        mind = corpus.mind
        return self._article_payload(
            item_id=str(np.asarray(mind.dataset.item_ids)[col]),
            headline=str(np.asarray(mind.titles)[col]),
            outlet=str(np.asarray(mind.outlets)[col]),
            topic=str(np.asarray(mind.categories)[col]),
            lean=np.asarray(mind.item_positions, dtype=float)[col],
            register=(corpus.register[col] if corpus.register is not None else None),
            emotion=self._emotion_share_of(corpus.emotion, col),
            confidence=(corpus.confidence[col] if corpus.confidence is not None else None),
            outlet_lean=corpus.outlet_lean)

    def serialize_history(self, reads: list) -> list:
        """A reader's reading history from their stored, scored reads (``store.list_reads`` rows).

        Each read is rendered as the SAME ``Article`` shape as a recommendation or report article
        (via :meth:`_article_payload`), so history reuses one serialiser and stays contract-identical
        for a future mobile client. No corpus or augmented model is needed — a stored read already
        carries its scored fields; outlet house-lean comes from the base corpus map (unknown → 0).
        Rows arrive newest-first from the store and are preserved in that order.

        ``readAt`` is the reader's observed timestamp; ``completed`` is ``True`` (opening a read is
        the only signal we have — real completion tracking is future work), and ``readingMinutes`` /
        ``publishedAt`` are the same deterministic estimates the article serialiser already uses."""
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
                outlet_lean=self.outlet_lean)
            out.append({
                "id": str(row.get("id")),
                "article": article,
                "readAt": row.get("observedAt") or sc.get("read_at") or row.get("createdAt"),
                "readingMinutes": article["readingMinutes"],
                "completed": True,
            })
        return out

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
    def _rec_cols_of(mind, rec: "_Recommenders", u: int, strategy: str, k: int = 12) -> list:
        """Top-k item columns (in ``mind`` space) recommender ``strategy`` surfaces for row ``u``,
        so we can serialise full articles. Corpus-parametric so a real user's augmented recommender
        selects columns exactly as the base one does. Returns [] on any failure (caller falls back)."""
        try:
            uid = np.asarray(mind.dataset.user_ids)[u]
            rows = np.flatnonzero(np.asarray(rec.rec_dataset.user_ids) == uid)
            if rows.size == 0:
                return []
            model = rec.models.get(strategy, rec.models["rwe-b"])
            ranked = model.recommend(np.array([rows[0]]), top_k=k)[0]
            cols = []
            for j in ranked:
                if int(j) < 0:
                    continue
                col = rec.id2col.get(str(rec.rec_ids[int(j)]))
                if col is not None:
                    cols.append(col)
            return cols
        except Exception:
            return []

    def _serialize_rec(self, corpus: _Corpus, col: int, strategy: str, user_side: float) -> dict:
        """Turn a recommended column (in ``corpus.mind`` space) into a rec payload. Corpus-
        parametric so a real user's augmented recs serialise through the same reason/impact
        logic as the demo path — only the corpus the article is drawn from differs."""
        art = self._serialize_article(corpus, col)
        cross = user_side != 0 and np.sign(art["lean"]) == -user_side and abs(art["lean"]) >= 0.5
        side = {"left": "left-leaning", "right": "right-leaning", "center": "centrist"}[art["leanBucket"]]
        topic = art["topic"].lower()
        if strategy == "rwe-d":
            reason = f"A long-tail read on {topic} from an outlet you rarely reach — RWE-D widens your sources."
            helps = "sourceDiversity"
        elif strategy == "adaptive":
            reason = (f"A {side} take on {topic}, sized to how open you've been to the other side — "
                      f"adaptive bridging tunes the stretch to you.")
            helps = "openMindedness" if cross else "topicDiversity"
        else:
            reason = (f"A {side} take on {topic} — RWE-B surfaced it to bridge you across the centre."
                      if cross else f"Broadens your {topic} coverage from an outlet you rarely read.")
            helps = "viewpointBalance" if cross else "sourceDiversity"
        base = 3 if cross else 1
        return {
            "article": art,
            "reason": reason,
            "strategy": strategy,
            "healthImpact": base + (_stable_int(art["id"], strategy) % 4),
            "helpsMetric": "viewpointBalance" if cross else helps,
            "crossCutting": bool(cross),
        }

    def _serialize_recs(self, corpus: _Corpus, cols_by_strategy, user_side: float) -> list:
        """Dedup + serialise chosen ``(strategy, columns)`` groups into a rec list, preserving
        first-seen order. Shared by the base path and the augmented (Measured) path; only the
        recommender that *chose* the columns differs, never this assembly."""
        out, seen = [], set()
        for strat, cols in cols_by_strategy:
            for col in cols:
                if col in seen:
                    continue
                seen.add(col)
                out.append(self._serialize_rec(corpus, col, strat, user_side))
        return out

    def _serialize_recommendations(self, corpus: _Corpus, rec: "_Recommenders", u: int,
                                   strategy: str | None = None) -> list:
        """Full recommendation pipeline over a corpus + its recommender stack: derive the
        reader's side, pick the plan (one strategy, or the default blend), select columns, and
        serialise. Shared verbatim by the base path and the augmented (Measured) path, so a real
        user's recs use the same blend and reason logic — only the corpus + recommender differ."""
        rep = hr.user_report(corpus.pop, corpus.mind, u)
        user_side = np.sign(rep.get("mean_lean") or 0.0)
        # a single strategy, or a blend across the family for the default "all" view
        plan = ([(strategy, 12)] if strategy in ("rwe-b", "rwe-d", "adaptive")
                else [("rwe-b", 6), ("rwe-d", 4), ("adaptive", 4)])
        cols_by_strategy = [(strat, self._rec_cols_of(corpus.mind, rec, u, strat, k))
                            for strat, k in plan]
        return self._serialize_recs(corpus, cols_by_strategy, user_side)

    def recommendations(self, u: int, strategy: str | None = None) -> list:
        """Base reference-corpus recommendations (demo / ``?user=`` path)."""
        return self._serialize_recommendations(self.base_corpus, self.rec, u, strategy)

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
        # attach up to two real bridging articles as suggestions
        cols = self._rec_cols_of(corpus.mind, rec, u, "rwe-b", k=2)
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
