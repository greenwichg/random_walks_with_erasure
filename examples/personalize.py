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

import threading
from dataclasses import dataclass, fields as _dc_fields
from typing import Dict

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
    recommendations, and coach."""

    reading_version: int
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

    # -- threshold gate ----------------------------------------------------
    def has_measured(self, user_id: int) -> bool:
        """Whether the user has enough stored reads for a Measured report (>= threshold).

        This is the routing switch: at/above the threshold the caller serves a Measured report
        from the augmented corpus; below it, the Initial Estimate (or demo)."""
        return self.store.count_reads(user_id) >= self.threshold

    # -- model build + cache ----------------------------------------------
    def _build_model(self, user_id: int, reading_version: int) -> PersonalModel:
        """Reconstruct the user's reads, augment the reference corpus, recompute the population
        with the unchanged engine, and build the augmented recommender stack. Expensive; called
        once per ``reading_version`` via :meth:`_model`."""
        reads = [_scored_read_from_row(r) for r in self.store.get_reads(user_id)]
        if not reads:
            raise ValueError("cannot build a measured model without stored reads")

        base = ac.bundle_from_backend(self.backend)               # read-only view of the corpus
        aug = ac.augment(base, reads, user_id=f"__real_user_{user_id}__")
        b = aug.bundle

        # UNCHANGED engine over the augmented population. source=None keeps the outlet source
        # axis (news); selective=None omits Open-Mindedness — it needs measured cross-cutting
        # reception the real user hasn't provided yet, so it is an honest n/a (as in the estimate).
        source = None if self.backend.domain == "news" else np.asarray(b.mind.titles)
        pop = hr.compute(b.mind, register=b.register, emotion=b.emotion,
                         confidence=b.confidence, source=source)

        corpus = engine._Corpus(mind=b.mind, pop=pop, register=b.register, emotion=b.emotion,
                                confidence=b.confidence,
                                outlet_lean=self.backend._build_outlet_lean(b.mind))
        rec = self.backend._build_recommenders(b.mind)            # neutral exposure (no probe)
        model = PersonalModel(reading_version=reading_version, corpus=corpus,
                              reader_row=aug.reader_row, rec=rec)

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
        """The user's cached augmented model, rebuilt when their reading_version changed."""
        version = self.store.count_reads(user_id)
        with self._lock:
            cached = self._cache.get(user_id)
            if cached is not None and cached.reading_version == version:
                return cached
            model = self._build_model(user_id, version)
            self._cache[user_id] = model      # keep only the latest version per user (bounded)
            return model

    def invalidate(self, user_id: int) -> None:
        """Drop a user's cached model (e.g. on new reads). Optional — :meth:`_model` already
        rebuilds when the reading_version changes; this just frees the entry eagerly."""
        with self._lock:
            self._cache.pop(user_id, None)

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
