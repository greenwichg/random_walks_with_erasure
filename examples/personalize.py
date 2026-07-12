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

import os
import threading
from dataclasses import dataclass, fields as _dc_fields, replace as _dc_replace
from typing import Dict, Tuple

import numpy as np

import health_report as hr
import augmented_corpus as ac
import api_server as engine
from ingest import canonical_url as _canonical_url


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

    # -- model build + cache ----------------------------------------------
    def _build_model(self, user_id: int, reading_version: int,
                     reception_version: Tuple[int, int]) -> PersonalModel:
        """Reconstruct the user's reads, augment the reference corpus, recompute the population
        with the unchanged engine, and build the augmented recommender stack. Expensive; called
        once per ``(reading_version, reception_version)`` via :meth:`_model`."""
        reads = [_scored_read_from_row(r) for r in self.store.get_reads(user_id)]
        if not reads:
            raise ValueError("cannot build a measured model without stored reads")
        # Join reads to the corpus id space: a read whose (canonical) URL is a catalog article lands
        # on that REAL column — the one other readers clicked — so the reader is embedded in the
        # connected click graph and the walk-based recommenders (RWE-B bridging, cross-cutting) work
        # as designed. A URL not in the catalog keeps the previous behaviour (a novel column).
        if self._catalog_ids:
            reads = [(_dc_replace(r, article_id=self._catalog_ids[str(r.article_id)])
                      if str(r.article_id) in self._catalog_ids else r) for r in reads]

        base = ac.bundle_from_backend(self.backend)               # read-only view of the corpus
        aug = ac.augment(base, reads, user_id=f"__real_user_{user_id}__")
        b = aug.bundle

        # UNCHANGED engine over the augmented population. source=None keeps the outlet source axis
        # (news). ``selective`` carries the reader's measured cross-cutting recommendation reception
        # (else None), so Open-Mindedness populates once they've engaged with cross-cutting recs and
        # stays an honest n/a before then — the same array shape the population already uses.
        source = None if self.backend.domain == "news" else np.asarray(b.mind.titles)
        selective = self._augmented_selective(user_id, aug.reader_row)
        pop = hr.compute(b.mind, register=b.register, emotion=b.emotion,
                         confidence=b.confidence, source=source, selective=selective)

        corpus = engine._Corpus(mind=b.mind, pop=pop, register=b.register, emotion=b.emotion,
                                confidence=b.confidence,
                                outlet_lean=self.backend._build_outlet_lean(b.mind))
        rec = self.backend._build_recommenders(b.mind)            # neutral exposure (no probe)
        model = PersonalModel(reading_version=reading_version, reception_version=reception_version,
                              corpus=corpus, reader_row=aug.reader_row, rec=rec)

        if self._persist:
            # Persist the Measured snapshot once per version so /api/me reflects the latest
            # result (append-only history). Serialising the report here is cheap next to the
            # compute above; a failure to persist must never fail the request.
            try:
                self.store.save_report(user_id,
                                       self.backend._serialize_report(corpus, aug.reader_row))
            except Exception:
                pass
        return model

    def _model(self, user_id: int) -> PersonalModel:
        """The user's cached augmented model, rebuilt when their reads *or* their cross-cutting
        recommendation reception changed (either shifts the version, so the Measured report and
        Open-Mindedness stay current)."""
        version = self.store.count_reads(user_id)
        reception = self._reception_key(user_id)
        with self._lock:
            cached = self._cache.get(user_id)
            if (cached is not None and cached.reading_version == version
                    and cached.reception_version == reception):
                return cached
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
        return self.backend._serialize_report(m.corpus, m.reader_row)

    def recommendations(self, user_id: int, strategy: "str | None" = None,
                        params: "dict | None" = None) -> list:
        """RWE recommendations computed on the user's augmented feedback graph. ``params`` (the
        reader's slider-mapped hyperparameters, from ``api_server.rec_params_from_settings``) is a
        per-request override — the cached augmented model and its recommender stack are untouched,
        so preference changes never churn the model cache.

        With ``RWE_STORY_SLOT`` enabled, the DEFAULT feed (no explicit ``strategy``) additionally
        applies the conditional Story-Match slot post-pass (:meth:`_apply_story_slot`). An explicit
        strategy request stays a faithful single-model view and never gets the slot."""
        m = self._model(user_id)
        recs = self.backend._serialize_recommendations(m.corpus, m.rec, m.reader_row, strategy, params)
        if strategy is None and story_slot_enabled():
            recs, _diag = self._apply_story_slot(user_id, m, recs)
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
        deterministic and order-free: newest ``publishedAt`` first, ties by canonical URL.
        Displacement is semantic: the served card whose resolved explanation sits lowest on the
        resolver's own priority ladder leaves the feed (ties by canonical URL) — never dependent
        on feed ordering. The card is serialized by the SAME ``_serialize_rec`` as every other
        card, with the truthful provenance ``strategy="story"`` (never a fabricated RWE label)."""
        import evidence_resolver as er
        diag: dict = {"enabled": True, "fired": False}
        idx = er.story_index(self.store)
        if not recs or not idx:
            return recs, {**diag, "reason": "empty feed" if not recs else "no story clusters"}
        read_urls = {_canonical_url(str(r.get("article_id") or ""))
                     for r in self.store.get_reads(user_id)}
        served = {_canonical_url(str((r.get("article") or {}).get("url") or "")) for r in recs}
        item_of = {str(m.rec.rec_ids[j]): j for j in range(len(m.rec.rec_ids))}
        candidates: dict = {}
        for ru in read_urls:
            story = idx.get(ru)
            if not story:
                continue
            anchor_pub = next((str(c.get("publisher") or "") for c in story["coverage"]
                               if _canonical_url(str(c.get("url") or "")) == ru), "")
            for member in story["coverage"]:
                cu = _canonical_url(str(member.get("url") or ""))
                if (not cu or cu in read_urls or cu in served
                        or str(member.get("publisher") or "") == anchor_pub):
                    continue
                col = item_of.get(str(self._catalog_ids.get(cu)))
                if col is None:                      # not a recommendable corpus node -> never fabricate
                    continue
                candidates.setdefault(cu, {"col": int(col), "url": cu,
                                           "publishedAt": str(member.get("publishedAt") or "")})
        if not candidates:
            return recs, {**diag, "reason": "no qualifying story sibling in the current corpus"}
        ctx = self.explanation_context(user_id)
        types = [er.resolve(r, ctx, idx).get("type") for r in recs]
        if "story_match" in types:
            return recs, {**diag, "reason": "an organic story_match card is served (cap 1)"}
        best = max(candidates.values(), key=lambda c: (c["publishedAt"], c["url"]))
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
        diag.update(fired=True, inserted=best["url"],
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
