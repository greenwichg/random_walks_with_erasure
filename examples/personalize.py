"""personalize.py — the single personalization layer for a real user's *Measured* report.

Turns a signed-in reader's **stored reads** into a Measured Information Health Report,
Recommendations, and an AI Coach, all computed by the **unchanged** research engine over an
*augmented corpus*: the reference population plus one appended reader (the real user) whose
feedback is exactly the articles they read.

Pipeline — nothing here re-implements an algorithm or a serialiser:

    Store.get_reads(uid)                        stored scored reads (JSON verbatim)
      -> ScoredRead[]                           reconstructed B4 interface
      -> URL → catalog-id join                  a read of a catalog article lands on its REAL column
                                                (via the backend's url resolver, inverted), keeping the
                                                reader connected to the click graph; unknown URLs stay
                                                novel columns
      -> augmented_corpus.augment(base, reads)  append 1 row + novel columns (base untouched)
      -> health_report.compute(...)             UNCHANGED engine, over the augmented population
      -> Backend._serialize_report / _serialize_recommendations / _serialize_coach_*
                                                the Commit-1/2 corpus-parametric serialisers

The real reader is embedded as *one more reader in the same corpus* and ranked against that
population (which now includes them), so a Measured report speaks the exact same 0–100 scale
as the demo. Only ``article_id`` is required per read; every unscored field degrades to n/a
exactly as the engine already handles missing data.

The augmented model (augmented corpus + recomputed population + augmented RWE recommenders) is
expensive to build, so it is **cached per (user_id, reading_version)** where
``reading_version = Store.count_reads(uid)``: a new read changes the version and rebuilds; only
the latest version per user is retained, so memory is bounded by the number of active users.
Below the read threshold there is no measured model — the caller serves the Initial Estimate —
so this module only ever builds a model for a user who has crossed the threshold.

This module holds **no** algorithm and **no** serialisation logic of its own; it wires stored
reads into the existing engine and reuses the Backend's serialisers. The research files
(``health_report``, ``rwe/``, ``narrate_report``, ``simulate_users``) are untouched.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time as _time
from dataclasses import dataclass, fields as _dc_fields, replace as _dc_replace
from typing import Dict, Tuple

import numpy as np

import health_report as hr
import augmented_corpus as ac
import api_server as engine
import measurement                      # generic per-metric Measurement envelopes (ADR-001)
import obs_metrics                      # OBS: stage timers (observational only — never a behaviour)
from enrich import enricher_source       # names the enricher for the register/emotion measurements' provenance
from ingest import canonical_url as _canonical_url

_logger = logging.getLogger("ih.personalize")
if not _logger.handlers:
    # Self-configuring, exactly like ``ih.api`` — and for a measured reason, not symmetry. Under
    # uvicorn's default logging config the root logger has NO handler and NO level, so a record on
    # a bare ``ih.*`` logger is neither enabled at INFO nor reachable by any handler: these lines
    # would vanish in production while looking fine in tests. Instrumentation whose failure mode is
    # silence is worse than none, because it gets quoted later.
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_h)
    _logger.setLevel(os.environ.get("RWE_LOG_LEVEL", "INFO").upper())
    _logger.propagate = False


@contextlib.contextmanager
def _stage(name: str, sink: "dict | None" = None):
    """Time one pipeline stage into ``obs_metrics`` and (optionally) a per-request breakdown dict.

    Purely observational: the timer is read from the clock and recorded through the already
    exception-swallowing ``obs_metrics``; the recording itself is wrapped again so instrumentation
    can never turn a served feed into an error. ``finally`` so a raising stage is still timed —
    a slow failure is exactly the case worth seeing."""
    t0 = _time.perf_counter()
    try:
        yield
    finally:
        ms = (_time.perf_counter() - t0) * 1000.0
        try:
            obs_metrics.observe(f"rec_{name}_ms", ms)
            if sink is not None:
                sink[name] = round(ms, 1)
        except Exception:
            pass


def _count(name: str) -> None:
    """Increment an observational counter. Guarded like :func:`_stage`: the shipped ``obs_metrics``
    swallows its own failures, but instrumentation must not depend on that being true of whatever
    backend is wired in later."""
    try:
        obs_metrics.incr(name)
    except Exception:
        pass


def _log_stages(event: str, sink: dict, **fields) -> None:
    """One structured line per build/serve, so a single production request can be read end to end
    rather than inferred from percentiles. Never raises."""
    try:
        _logger.info(json.dumps({"event": event, "ms": sink,
                                 "totalMs": round(sum(sink.values()), 1), **fields}, default=str))
    except Exception:
        pass


# Field names of the B4 ScoredRead, so a stored read dict (dataclasses.asdict verbatim) is
# reconstructed by name — robust to an older/newer stored shape (extra keys ignored).
_SCORED_READ_FIELDS = {f.name for f in _dc_fields(ac.ScoredRead)}


def _scored_read_from_row(row: dict) -> ac.ScoredRead:
    """Reconstruct a B4 :class:`ScoredRead` from one stored read dict."""
    return ac.ScoredRead(**{k: v for k, v in row.items() if k in _SCORED_READ_FIELDS})


def story_slot_enabled() -> bool:
    """Whether the conditional Story-Match slot is on (``RWE_STORY_SLOT``, default off).

    When enabled, the default feed reserves AT MOST ONE card for a validated story sibling —
    another publisher's coverage of a story the reader actually read — inserted only when such an
    opportunity exists and no organically-selected card already explains as ``story_match``
    (an organic card counts toward the cap). Off by default so the beta can validate it before
    it becomes the default, mirroring the RWE_FEED_REQUIRE_DATED rollout pattern."""
    return os.environ.get("RWE_STORY_SLOT", "").strip().lower() in {"1", "true", "yes", "on"}


#: The Evidence Resolver's own priority ladder (P1..P6) — the product's established definition of
#: "most provable reason". The slot displaces the served card whose explanation sits LOWEST here,
#: so displacement is semantic, never an artifact of feed ordering.
_EXPLANATION_PRIORITY = ("story_match", "bridge", "new_publisher", "topic_continuity",
                         "long_tail", "coverage_breadth")


def _opposing_leans(anchor_lean, sibling_lean) -> bool:
    """Whether a sibling sits on the OPPOSITE side of the spectrum from the anchor the reader read.

    Bucket thresholds are the catalog's own (left <= -0.5, right >= +0.5 — the same cut every lean
    filter uses), so "opposite" here means left-vs-right, never merely "a different number":
    -1.5 vs -0.8 is the same side, and centre opposes nothing. Unrated (None/NaN) leans license no
    claim (L2.2) and are never counted as opposite — a fabricated opposition would be worse than a
    same-side card."""
    try:
        a, s = float(anchor_lean), float(sibling_lean)
    except (TypeError, ValueError):
        return False
    if a != a or s != s:                                 # NaN — unrated survives float()
        return False
    return (a <= -0.5 and s >= 0.5) or (a >= 0.5 and s <= -0.5)


@dataclass
class PersonalModel:
    """A real user's augmented model, cached by ``reading_version``.

    Holds the augmented corpus in the Backend serialiser's shape (:class:`api_server._Corpus`),
    the matrix row of the appended real reader, and the augmented RWE recommender stack —
    everything the corpus-parametric serialisers need to render this user's Measured report,
    recommendations, and coach.

    Cached by ``(reading_version, reception_version)``: a new read changes the reading version, and
    opening/surfacing more cross-cutting recommendations changes the reception version — either one
    rebuilds, so Open-Mindedness reflects the latest recommendation reception."""

    reading_version: int
    reception_version: Tuple[int, int]
    corpus: "engine._Corpus"
    reader_row: int
    rec: "engine._Recommenders"
    # Per-metric Measurement envelopes (ADR-001): coverage + provenance for the reader's Viewpoint /
    # Emotion dimensions, computed from the SAME scored reads that built the corpus (no second load).
    # Keyed by MetricKey; attached onto the matching metric card by the report serialiser.
    measurements: Dict[str, dict] = None  # type: ignore[assignment]


class Personalizer:
    """Builds + caches per-user augmented models and serves the Measured report / recs / coach.

    One instance is shared across the app (built at startup). A user's model is built lazily on
    the first request after their read-count changes and cached; concurrent builds are serialised
    by a lock, since a build recomputes ``health_report`` over the whole augmented population and
    is deliberately done once per ``reading_version``, not per request."""

    def __init__(self, backend: "engine.Backend", store, threshold: "int | None" = None,
                 persist: bool = True):
        self.backend = backend
        self.store = store
        self.threshold = int(threshold) if threshold is not None else engine.ESTIMATE_MIN_READS
        self._persist = persist
        self._cache: Dict[int, PersonalModel] = {}
        self._lock = threading.Lock()
        # URL → corpus-item-id join, inverted from the backend's url resolver (attached in live-feed
        # mode BEFORE the Personalizer is built, on boot and on every hot swap). A stored read is
        # identified by its canonical URL while corpus items are Q{i}/S{i}/N{i} ids, so without this
        # join every read appends as a novel column nobody else clicked — the reader becomes an
        # island in the click graph and the random walk (hence bridging/cross-cutting) degenerates.
        # Keyed by both the raw and the canonicalised URL form; empty when the corpus has no URL map
        # (synthetic/MIND), where reads keep appending as novel columns exactly as before.
        self._catalog_ids: Dict[str, str] = {}
        for iid, u in (getattr(backend, "url_by_id", None) or {}).items():
            for key in (str(u), _canonical_url(str(u))):
                if key:
                    self._catalog_ids.setdefault(key, str(iid))
        # Open-Mindedness activates once the reader has been surfaced enough cross-cutting
        # recommendations AND opened at least one — below that it stays an honest n/a (7/8), so a
        # single stray click can't fabricate the metric. Release-pinned, tunable via env.
        self._openmind_min_shown = max(1, int(os.environ.get("RWE_OPENMIND_MIN_SHOWN", "3")))
        self._openmind_min_opened = max(1, int(os.environ.get("RWE_OPENMIND_MIN_OPENED", "1")))
        # W2: κ = the neutral-prior pseudo-impression weight in the exposure shrinkage. Reuses the
        # Open-Mindedness gate above (same signal), so adaptive dosing activates exactly when the
        # metric does. Env-tunable; default 10 (a nudge — half-weight at ~10 cross-cutting impressions).
        self._adaptive_kappa = max(1.0, float(os.environ.get("RWE_ADAPTIVE_KAPPA", "10")))

    # -- threshold gate ----------------------------------------------------
    def has_measured(self, user_id: int) -> bool:
        """Whether the user has enough stored reads for a Measured report (>= threshold).

        This is the routing switch: at/above the threshold the caller serves a Measured report
        from the augmented corpus; below it, the Initial Estimate (or demo)."""
        return self.store.count_reads(user_id) >= self.threshold

    # -- Open-Mindedness feedback loop ------------------------------------
    def _reception_key(self, user_id: int) -> Tuple[int, int]:
        """Cache key for the user's recommendation reception. ``(0, 0)`` until Open-Mindedness is
        active (enough cross-cutting recs surfaced *and* opened), so merely surfacing recs doesn't
        churn the cache; once active it tracks ``(shown, opened)`` so opening more rebuilds."""
        r = self.store.recommendation_reception(user_id)
        if (r["openedCross"] >= self._openmind_min_opened
                and r["shownCross"] >= self._openmind_min_shown):
            return (int(r["shownCross"]), int(r["openedCross"]))
        return (0, 0)

    def _augmented_selective(self, user_id: int, reader_row: int):
        """The population's cross-cutting selective-exposure array with the real reader's measured
        recommendation reception appended at ``reader_row`` — fed to the unchanged
        ``health_report.compute(selective=...)`` so Open-Mindedness ranks the reader against the
        same distribution as everyone else. Returns ``None`` (Open-Mindedness stays n/a, unchanged)
        when the base corpus carries no selective signal or the reader isn't active yet."""
        base = getattr(self.backend, "selective", None)
        if base is None:
            return None
        r = self.store.recommendation_reception(user_id)
        active = (r["openedCross"] >= self._openmind_min_opened
                  and r["shownCross"] >= self._openmind_min_shown)
        if not active:
            return None                     # honest n/a until enough cross-cutting reception
        base = np.asarray(base, dtype=float)
        aug = np.full(reader_row + 1, np.nan)
        n = min(base.size, reader_row)
        aug[:n] = base[:n]
        aug[reader_row] = float(r["rate"])
        return aug

    def _reader_exposure(self, user_id: int) -> "float | None":
        """W2: the reader's AdaptiveRWEB exposure — their measured cross-cutting reception RATE
        (openedCross/shownCross) shrunk toward the neutral 0.5 prior by shownCross (κ pseudo-
        impressions, :func:`adaptive_satisfaction.shrunk_exposure`). Returns ``None`` (→ the neutral
        0.5 default, byte-identical to pre-W2) until the SAME Open-Mindedness gate the metric uses
        (``min_shown`` / ``min_opened``). Driven by the RATE, never the raw opened count, so a larger
        bridge budget (W1) cannot inflate it (feedback-loop audit). Best-effort."""
        try:
            r = self.store.recommendation_reception(user_id)
        except Exception:
            return None
        if not (r["shownCross"] >= self._openmind_min_shown
                and r["openedCross"] >= self._openmind_min_opened):
            return None                     # honest neutral default until enough cross-cutting reception
        import adaptive_satisfaction as asf
        return asf.shrunk_exposure(r["rate"], r["shownCross"], self._adaptive_kappa)

    # -- model build + cache ----------------------------------------------
    def _build_model(self, user_id: int, reading_version: int,
                     reception_version: Tuple[int, int]) -> PersonalModel:
        """Reconstruct the user's reads, augment the reference corpus, recompute the population
        with the unchanged engine, and build the augmented recommender stack. Expensive; called
        once per ``(reading_version, reception_version)`` via :meth:`_model`."""
        ms: dict = {}
        with _stage("build_reads", ms):
            reads = [_scored_read_from_row(r) for r in self.store.get_reads(user_id)]
        if not reads:
            raise ValueError("cannot build a measured model without stored reads")
        # Join reads to the corpus id space: a read whose (canonical) URL is a catalog article lands
        # on that REAL column — the one other readers clicked — so the reader is embedded in the
        # connected click graph and the walk-based recommenders (RWE-B bridging, cross-cutting) work
        # as designed. A URL not in the catalog keeps the previous behaviour (a novel column).
        if self._catalog_ids:
            with _stage("build_catalog_join", ms):
                reads = [(_dc_replace(r, article_id=self._catalog_ids[str(r.article_id)])
                          if str(r.article_id) in self._catalog_ids else r) for r in reads]

        # Per-metric Measurement envelopes (ADR-001) from the SAME scored reads the corpus is built
        # from — coverage + provenance computed alongside the metric values, never a second read load.
        # The catalog-id join above only rewrites ``article_id``; political / lean / emotion (all a
        # measurement reads) are untouched, so this is the exact projection behind the metric values.
        with _stage("build_measurements", ms):
            measurements = measurement.measurements_for_reads(reads, enricher_source=enricher_source())

        with _stage("build_augment", ms):
            base = ac.bundle_from_backend(self.backend)           # read-only view of the corpus
            aug = ac.augment(base, reads, user_id=f"__real_user_{user_id}__")
        b = aug.bundle

        # UNCHANGED engine over the augmented population. source=None keeps the outlet source axis
        # (news). ``selective`` carries the reader's measured cross-cutting recommendation reception
        # (else None), so Open-Mindedness populates once they've engaged with cross-cutting recs and
        # stays an honest n/a before then — the same array shape the population already uses.
        source = None if self.backend.domain == "news" else np.asarray(b.mind.titles)
        with _stage("build_selective", ms):
            selective = self._augmented_selective(user_id, aug.reader_row)
        with _stage("build_population", ms):
            pop = hr.compute(b.mind, register=b.register, emotion=b.emotion,
                             confidence=b.confidence, source=source, selective=selective)

        with _stage("build_outlet_lean", ms):
            corpus = engine._Corpus(mind=b.mind, pop=pop, register=b.register, emotion=b.emotion,
                                    confidence=b.confidence,
                                    outlet_lean=self.backend._build_outlet_lean(b.mind))
        # W2: give the real reader their measured adaptive exposure (gated + shrunk toward 0.5);
        # everyone else in the augmented population keeps the neutral prior. Only the reader is served.
        # Staged after the first production run: this is a THIRD recommendation_reception query per
        # rebuild (after the cache key's and build_selective's), and it was the one unattributed
        # store read inside serve_model — 6,151.9 ms of serve with only ~4,200 ms named is exactly
        # the hole an unstaged query leaves.
        with _stage("build_reader_exposure", ms):
            rexp = self._reader_exposure(user_id)
        ruid = str(b.mind.dataset.user_ids[aug.reader_row])
        with _stage("build_recommenders", ms):
            rec = self.backend._build_recommenders(
                b.mind, reader_exposure=((ruid, rexp) if rexp is not None else None))
        model = PersonalModel(reading_version=reading_version, reception_version=reception_version,
                              corpus=corpus, reader_row=aug.reader_row, rec=rec,
                              measurements=measurements)

        if self._persist:
            # Persist the Measured snapshot once per version so /api/me reflects the latest
            # result (append-only history). Serialising the report here is cheap next to the
            # compute above; a failure to persist must never fail the request.
            #
            # "Cheap next to the compute above" is an unmeasured claim from before this timer
            # existed — `build_persist_report` now says what it actually costs, on the critical
            # path of the first feed after every read.
            with _stage("build_persist_report", ms):
                try:
                    self.store.save_report(user_id,
                                           self.backend._serialize_report(corpus, aug.reader_row,
                                                                          measurements=measurements))
                except Exception:
                    pass
        _log_stages("rec_model_build", ms, userId=user_id, reads=len(reads),
                    readingVersion=reading_version)
        return model

    def _model(self, user_id: int) -> PersonalModel:
        """The user's cached augmented model, rebuilt when their reads *or* their cross-cutting
        recommendation reception changed (either shifts the version, so the Measured report and
        Open-Mindedness stay current)."""
        ms: dict = {}
        # The cache KEY is two store reads — the reading version (count_reads) and the reception
        # version. Timed separately because they are paid on every request, hit or miss, and a
        # cheap-looking key check over a growing table is the shape that hides.
        with _stage("cache_key", ms):
            version = self.store.count_reads(user_id)
            reception = self._reception_key(user_id)
        with self._lock:
            cached = self._cache.get(user_id)
            if (cached is not None and cached.reading_version == version
                    and cached.reception_version == reception):
                _count("rec_model_cache_hit_total")
                _log_stages("rec_model_lookup", ms, userId=user_id, hit=True)
                return cached
            _count("rec_model_cache_miss_total")
            model = self._build_model(user_id, version, reception)
            self._cache[user_id] = model      # keep only the latest version per user (bounded)
            return model

    def invalidate(self, user_id: int) -> None:
        """Drop a user's cached model (e.g. on new reads or recommendation opens). Optional —
        :meth:`_model` already rebuilds when the reading or reception version changes; this just
        frees the entry eagerly."""
        with self._lock:
            self._cache.pop(user_id, None)

    def openmindedness(self, user_id: int) -> dict:
        """The user's cross-cutting recommendation reception plus whether it's now enough for
        Open-Mindedness to populate on their Measured report — the exact gate :meth:`_build_model`
        uses. Lets the API report reception progress without duplicating the threshold logic."""
        r = self.store.recommendation_reception(user_id)
        active = (r["openedCross"] >= self._openmind_min_opened
                  and r["shownCross"] >= self._openmind_min_shown)
        return {**r, "minShown": self._openmind_min_shown,
                "minOpened": self._openmind_min_opened, "active": active}

    # -- served payloads — reuse the Backend's corpus-parametric serialisers --
    def report(self, user_id: int) -> dict:
        """The Measured Information Health Report from the user's augmented corpus."""
        m = self._model(user_id)
        return self.backend._serialize_report(m.corpus, m.reader_row, measurements=m.measurements)

    def recommendations(self, user_id: int, strategy: "str | None" = None,
                        params: "dict | None" = None) -> list:
        """RWE recommendations computed on the user's augmented feedback graph. ``params`` (the
        reader's slider-mapped hyperparameters, from ``api_server.rec_params_from_settings``) is a
        per-request override — the cached augmented model and its recommender stack are untouched,
        so preference changes never churn the model cache.

        With ``RWE_STORY_SLOT`` enabled, the DEFAULT feed (no explicit ``strategy``) additionally
        applies the conditional Story-Match slot post-pass (:meth:`_apply_story_slot`). An explicit
        strategy request stays a faithful single-model view and never gets the slot."""
        ms: dict = {}
        with _stage("serve_model", ms):            # cache hit, or the whole rebuild
            m = self._model(user_id)
        with _stage("serve_rank_serialize", ms):   # blend -> per-strategy columns -> serialise
            recs = self.backend._serialize_recommendations(m.corpus, m.rec, m.reader_row,
                                                           strategy, params)
        if strategy is None and story_slot_enabled():
            with _stage("serve_story_slot", ms):
                recs, _diag = self._apply_story_slot(user_id, m, recs)
        _log_stages("rec_serve", ms, userId=user_id, strategy=strategy or "all", cards=len(recs))
        return recs

    def _apply_story_slot(self, user_id: int, m: PersonalModel, recs: list) -> "tuple[list, dict]":
        """The conditional Story-Match slot (``RWE_STORY_SLOT``): insert AT MOST ONE validated
        story sibling at the top of the served feed. Returns ``(feed, diagnostic)``; the feed is
        returned unchanged (with the reason in the diagnostic) whenever any gate fails.

        Gates — each one is exactly what ``evidence_resolver.validate()`` re-derives for a
        ``story_match`` explanation, so the inserted card is P1-explainable by construction:
        the reader has a read inside a validated multi-publisher story cluster; the sibling is
        unread, from a different publisher than that read, not already served, and a recommendable
        node of the CURRENT corpus (so freshness/candidacy gates are inherited, never bypassed).

        Cap: one card, and an organically-selected ``story_match`` card counts toward it (the slot
        then no-ops; organic cards are never removed). Selection among qualifying siblings is
        deterministic and order-free: siblings whose lean OPPOSES the read anchor's (left-vs-right
        by the catalog's own +-0.5 buckets — see :func:`_opposing_leans`) rank ahead of same-side /
        centre / unrated ones, and recency (newest ``publishedAt``, ties by canonical URL) orders
        WITHIN each viewpoint group. With no opposite-side sibling the ordering is therefore
        exactly the pre-viewpoint behaviour, newest first — verified, not assumed, by the tests
        that predate the preference and still pass unchanged.
        Displacement is semantic: the served card whose resolved explanation sits lowest on the
        resolver's own priority ladder leaves the feed (ties by canonical URL) — never dependent
        on feed ordering. The card is serialized by the SAME ``_serialize_rec`` as every other
        card, with the truthful provenance ``strategy="story"`` (never a fabricated RWE label)."""
        import evidence_resolver as er
        diag: dict = {"enabled": True, "fired": False}
        # Sub-stages: this post-pass runs on EVERY served feed, warm or cold — the model cache does
        # nothing for it — so a breakdown that stopped at the slot boundary would show one large
        # opaque number where the actionable split (index build vs. reader join vs. re-resolving
        # every card's explanation) actually lives.
        sms: dict = {}
        with _stage("slot_index", sms):
            idx = er.story_index(self.store)
        if not recs or not idx:
            return recs, {**diag, "reason": "empty feed" if not recs else "no story clusters"}
        with _stage("slot_reads", sms):
            read_urls = {_canonical_url(str(r.get("article_id") or ""))
                         for r in self.store.get_reads(user_id)}
        served = {_canonical_url(str((r.get("article") or {}).get("url") or "")) for r in recs}
        item_of = {str(m.rec.rec_ids[j]): j for j in range(len(m.rec.rec_ids))}
        candidates: dict = {}
        with _stage("slot_candidates", sms):
            for ru in read_urls:
                story = idx.get(ru)
                if not story:
                    continue
                anchor = next((c for c in story["coverage"]
                               if _canonical_url(str(c.get("url") or "")) == ru), None)
                anchor_pub = str((anchor or {}).get("publisher") or "")
                anchor_lean = (anchor or {}).get("lean")
                for member in story["coverage"]:
                    cu = _canonical_url(str(member.get("url") or ""))
                    if (not cu or cu in read_urls or cu in served
                            or str(member.get("publisher") or "") == anchor_pub):
                        continue
                    col = item_of.get(str(self._catalog_ids.get(cu)))
                    if col is None:                  # not a recommendable corpus node -> never fabricate
                        continue
                    entry = candidates.setdefault(cu, {"col": int(col), "url": cu,
                                                      "publishedAt": str(member.get("publishedAt") or ""),
                                                      "opposite": False})
                    # OR across pairings: the reader may have read several members of this cluster,
                    # and a sibling that opposes ANY of those read anchors is a genuine other-side
                    # offer.
                    if _opposing_leans(anchor_lean, member.get("lean")):
                        entry["opposite"] = True
        if not candidates:
            _log_stages("rec_story_slot", sms, userId=user_id, fired=False)
            return recs, {**diag, "reason": "no qualifying story sibling in the current corpus"}
        with _stage("slot_context", sms):
            ctx = self.explanation_context(user_id)
        with _stage("slot_resolve_types", sms):
            types = [er.resolve(r, ctx, idx).get("type") for r in recs]
        _log_stages("rec_story_slot", sms, userId=user_id, fired=True, cards=len(recs))
        if "story_match" in types:
            return recs, {**diag, "reason": "an organic story_match card is served (cap 1)"}
        # Opposite-view group first; recency orders within each group. `True > False` does the
        # grouping, so with zero opposite candidates this key degenerates to the original
        # (publishedAt, url) — the no-opposite case is the old behaviour by construction.
        best = max(candidates.values(), key=lambda c: (c["opposite"], c["publishedAt"], c["url"]))
        prio = {t: i for i, t in enumerate(_EXPLANATION_PRIORITY)}
        drop = max(range(len(recs)), key=lambda i: (
            prio.get(types[i], len(prio)),
            _canonical_url(str((recs[i].get("article") or {}).get("url") or ""))))
        rep = hr.user_report(m.corpus.pop, m.corpus.mind, m.reader_row)
        user_side = float(np.sign(rep.get("mean_lean") or 0.0))
        try:
            familiarity = engine._familiarity_of(m.corpus.pop, m.reader_row)
        except Exception:
            familiarity = None
        card = self.backend._serialize_rec(m.corpus, best["col"], "story", user_side, familiarity)
        out = [card] + [r for i, r in enumerate(recs) if i != drop]
        diag.update(fired=True, inserted=best["url"], opposite=best["opposite"],
                    displaced={"url": _canonical_url(str((recs[drop].get("article") or {})
                                                         .get("url") or "")),
                               "explanation": types[drop]})
        return out, diag

    def explain(self, user_id: int, strategy: "str | None" = None,
                params: "dict | None" = None, article: "str | None" = None) -> dict:
        """Read-only explainability observer over the user's cached augmented model (Commit 21a)
        — the measured twin of :meth:`Backend.explain_recommendations`. Adds the reader's
        read-join evidence (how many stored reads landed on real catalog columns — the URL →
        catalog-id join that connects them to the click graph) and reuses the same catalog-id
        index so an exclusion query accepts raw or canonical URLs. Also passes the Story
        Service's cluster index + the reader's read URLs (C5), so every recommendation carries a
        ``storyMatch`` diagnostic — the story cluster that licenses (or the gate that blocks) a
        story_match explanation. Never mutates the model."""
        m = self._model(user_id)
        reads = [_scored_read_from_row(r) for r in self.store.get_reads(user_id)]
        joined = sum(1 for r in reads if str(r.article_id) in self._catalog_ids)
        import evidence_resolver
        import rec_explain
        out = rec_explain.explain(self.backend, m.corpus, m.rec, m.reader_row,
                                  strategy=strategy, params=params, article=article,
                                  reads_meta={"total": len(reads), "joined": joined},
                                  url_to_id=self._catalog_ids,
                                  story_index=evidence_resolver.story_index(self.store),
                                  read_urls={_canonical_url(str(r.article_id)) for r in reads})
        # The measured model's identity, so a reported feed is reproducible: which reads
        # version and reception version the cached augmented model was built from (21a.2).
        out["modelVersion"] = {"readingVersion": m.reading_version,
                               "receptionVersion": m.reception_version}
        # Story-slot transparency: report the slot decision (fired / inserted / displaced / why
        # not) for the default feed, so audits see the post-pass explicitly. The per-strategy
        # tables above remain the slice mirror — the slot card's own ranks are available via the
        # article=<url> exclusion query.
        if story_slot_enabled() and strategy is None and article is None:
            base = self.backend._serialize_recommendations(m.corpus, m.rec, m.reader_row,
                                                           None, params)
            _, out["storySlot"] = self._apply_story_slot(user_id, m, base)
        return out

    def explanation_context(self, user_id: int) -> dict:
        """Reader context for the Evidence Resolver (Commit 21a.3): the measured reader's reads
        (canonical URL + outlet, oldest first — recency by order), the same familiarity lookup
        the reason gating uses, and their top reading topics. Read-only over the cached model."""
        m = self._model(user_id)
        rep = hr.user_report(m.corpus.pop, m.corpus.mind, m.reader_row)
        reads = [{"url": _canonical_url(str(r.get("article_id") or "")),
                  "publisher": str(r.get("outlet") or ""),
                  "publishedAt": r.get("read_at")}
                 for r in self.store.get_reads(user_id)]
        return {"reads": reads,
                "familiarity": engine._familiarity_of(m.corpus.pop, m.reader_row),
                # Commit R2: uncategorized reads never become a claimable "topic" — blank (and
                # legacy-"general") buckets are excluded, so topic_continuity cites real topics only.
                "top_topics": [engine._prettify(t) for t, _ in (rep.get("top_categories") or [])
                               if str(t).strip() and str(t).strip().lower() != "general"],
                # Commit 23: the reader's mean political lean for the resolver's SEMANTIC
                # readerPoliticalProfile banding (the raw number never reaches presentation).
                # `rep` is already computed above — no new computation runs.
                "reader_mean_lean": round(float(rep.get("mean_lean") or 0.0), 3),
                # C6: the measured shares behind the CONCRETE readerFacts ("Politics represents
                # 42% of your recent reading" / "74% of your political reading leans left") —
                # the same user_report numbers the explain drawer shows, so the card, the
                # drawer, and the validation pipeline read one source.
                "topic_shares": engine._topic_shares_of(rep),
                **engine._lean_shares_of(rep)}

    def coach_greeting(self, user_id: int) -> list:
        """Coach greeting grounded on the user's Measured report."""
        m = self._model(user_id)
        return self.backend._serialize_coach_greeting(m.corpus, m.reader_row)

    def coach_reply(self, user_id: int, message: str) -> dict:
        """Grounded coach reply + bridging suggestions from the user's augmented corpus."""
        m = self._model(user_id)
        return self.backend._serialize_coach_reply(m.corpus, m.rec, m.reader_row, message)
