"""personalize.py — the single personalization layer for a real user's *Measured* report.

Turns a signed-in reader's **stored reads** into a Measured Information Health Report,
Recommendations, and an AI Coach, all computed by the **unchanged** research engine over an
*augmented corpus*: the reference population plus one appended reader (the real user) whose
feedback is exactly the articles they read.

Pipeline — nothing here re-implements an algorithm or a serialiser:

    Store.get_reads(uid)                        stored scored reads (JSON verbatim)
      -> ScoredRead[]                           reconstructed B4 interface
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
from dataclasses import dataclass, fields as _dc_fields
from typing import Dict, Tuple

import numpy as np

import health_report as hr
import augmented_corpus as ac
import api_server as engine


# Field names of the B4 ScoredRead, so a stored read dict (dataclasses.asdict verbatim) is
# reconstructed by name — robust to an older/newer stored shape (extra keys ignored).
_SCORED_READ_FIELDS = {f.name for f in _dc_fields(ac.ScoredRead)}


def _scored_read_from_row(row: dict) -> ac.ScoredRead:
    """Reconstruct a B4 :class:`ScoredRead` from one stored read dict."""
    return ac.ScoredRead(**{k: v for k, v in row.items() if k in _SCORED_READ_FIELDS})


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

    def recommendations(self, user_id: int, strategy: "str | None" = None) -> list:
        """RWE recommendations computed on the user's augmented feedback graph."""
        m = self._model(user_id)
        return self.backend._serialize_recommendations(m.corpus, m.rec, m.reader_row, strategy)

    def coach_greeting(self, user_id: int) -> list:
        """Coach greeting grounded on the user's Measured report."""
        m = self._model(user_id)
        return self.backend._serialize_coach_greeting(m.corpus, m.reader_row)

    def coach_reply(self, user_id: int, message: str) -> dict:
        """Grounded coach reply + bridging suggestions from the user's augmented corpus."""
        m = self._model(user_id)
        return self.backend._serialize_coach_reply(m.corpus, m.rec, m.reader_row, message)
