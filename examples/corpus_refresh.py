"""corpus_refresh.py — Atomic hot activation of a validated recommendation corpus (Commit 5).

Activation ONLY. It consumes Commit 4's candidate + validation *exactly as they exist*, builds a NEW
``Backend`` entirely off the request path, runs sanity checks, and swaps a single pointer so new
requests immediately use it while in-flight requests finish on the old one. It changes no
recommendation algorithm, ranking, score, selection, or serializer — it replaces the DATA SOURCE
behind the unchanged engine (a fresh ``Backend`` built by the existing constructor from the validated
candidate), nothing more.

Guarantees:
  * **Off-thread build** — the heavy ``Backend`` build runs in the poller thread, never in a request.
  * **Atomic swap** — ``app.active = new`` is a single attribute assignment, so under the GIL a reader
    sees a fully-old or fully-new bundle, never a partial one; no lock is taken on the read path.
  * **Consistent bundle** — ``backend`` and ``personalizer`` swap *together* (the personalizer captures
    its backend), so a measured reader never mixes an old personalizer with a new backend.
  * **Rollback by construction** — the pointer is swapped ONLY after a full build + all sanity checks
    pass. Any failure simply never swaps: the current ``Backend`` keeps serving; retry next cycle.
  * **Idempotent** — a cycle whose candidate signature is unchanged rebuilds nothing.

This module owns activation. It does not own — and never modifies — candidate construction, corpus
validation, publisher balancing, retention, or feed health; it consumes their outputs.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import api_server as engine        # Backend + DatasetProfile — the UNCHANGED engine, reused verbatim
import feed_source                 # export_candidate_csv + load_url_map (the single qbias serializer)
import corpus_validation           # Commit 4: build_candidate + validate_corpus (consumed as-is)
import corpus_health               # thresholds_from_env (shared floors + ceilings)
import obs_metrics                 # OBS1 counters — the gate's skip/build split lands in /api/metrics
import personalize                 # Personalizer — rebuilt over the new backend on every swap


class RefreshState:
    IDLE = "idle"
    BUILDING = "building"
    SWAPPING = "swapping"
    FAILED = "failed"


@dataclasses.dataclass(frozen=True)
class Active:
    """The immutable, atomically-swapped serving bundle. ``backend`` and ``personalizer`` always
    belong to the same generation, so a request that reads one field reads a consistent pair."""
    backend: "engine.Backend"
    personalizer: "personalize.Personalizer"
    generation: int
    built_at: str
    source: str                 # "feed" | "static"
    candidate_sig: str
    item_count: int


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int_env(name: str) -> Optional[int]:
    v = os.environ.get(name)
    return int(v) if v and v.lstrip("-").isdigit() else None


def _sizing_kwargs() -> dict:
    """The population sizing for a rebuilt Backend, read from the SAME env the startup build uses
    (``RWE_N_USERS`` / ``RWE_MAX_ITEMS`` / ``RWE_SEED``). Unset keys fall through to
    ``DatasetProfile.synthetic``'s defaults — identical to the startup profile — so a hot-rebuilt
    corpus is sized exactly like the one built at boot."""
    kw = {}
    for env, key in (("RWE_N_USERS", "n_users"), ("RWE_MAX_ITEMS", "max_items"), ("RWE_SEED", "seed")):
        v = _int_env(env)
        if v is not None:
            kw[key] = v
    return kw


def candidate_signature(candidate: list) -> str:
    """A stable content signature of a candidate (composition + order). Two cycles that produce the
    same candidate get the same signature, so an unchanged catalog rebuilds nothing."""
    h = hashlib.sha1()
    for a in candidate:
        h.update((corpus_validation.ch._canonical(a) or "").encode("utf-8", "replace"))
        h.update(b"\x00")
    return f"{len(candidate)}:{h.hexdigest()[:16]}"


def build_candidate_for(store_, thresholds: Optional[dict] = None) -> list:
    """The current candidate — Commit 4's builder over the live catalog, consumed unchanged — plus the
    read-demand exemption (Commit 18): an article a user actually read is re-added if the per-publisher
    cap trimmed it, so composition balancing never disconnects a reader from the corpus. The protected
    builder itself is untouched (a bounded post-pass over its output). Because the candidate is built
    from the store, an extension-created article changes the candidate *signature*; the poller's
    hot-refresh seam normally runs only on feed growth, so the request path also marks the manager
    ``catalog_dirty`` (D6) — together bounding an extension article's graph latency to one poll
    interval even on quiet feeds."""
    th = thresholds or corpus_health.thresholds_from_env()
    articles = store_.list_feed_articles(limit=10_000_000)
    candidate = corpus_validation.build_candidate(articles, max_per_publisher=th["maxPerPublisher"] or None)
    read_urls = store_.distinct_read_urls()
    if read_urls:
        kept = {a.get("canonicalUrl") for a in candidate}
        candidate = candidate + [a for a in articles
                                 if a.get("canonicalUrl") in read_urls
                                 and a.get("canonicalUrl") not in kept]
    return candidate


def cheap_check_enabled() -> bool:
    """Whether an unchanged catalog fingerprint may skip the candidate build outright.
    ON — ``RWE_REFRESH_CHEAP_CHECK=0`` restores a full candidate build on every cycle.

    The candidate build loads the ENTIRE catalog (``list_feed_articles(limit=10_000_000)``) —
    measured in production (2026-08-01) at **4,495 ms** at 47k rows, paid on every post-cycle just
    to learn whether anything changed, and growing with catalog age forever because catalog
    retention defaults off. The fingerprint (row count + newest ``fetched_at``) answers the same
    question for the no-change case in one aggregate query.

    **Honest sizing, from the same production evidence:** generation 17 within ~75 minutes of boot
    means most ingest-bearing cycles genuinely rebuild — the gate cannot open on those, so its
    steady-state saving is the minority of cycles (dirty nudges, cap-absorbed ingests, retention-
    only cycles). It is shipped because it is cheap insurance that also *arrests the growth path*
    (a no-change cycle's cost no longer scales with catalog size), and because its two counters
    (``corpus_refresh_cheap_skip_total`` / ``corpus_refresh_candidate_built_total``) finally
    measure the skip fraction the estimate depends on, in /api/metrics, instead of modelling it.

    Why skipping is safe here and would not be in general: the candidate is time-dependent
    (:func:`corpus_validation.build_candidate` ages articles out at 60 days), so "same catalog"
    does not strictly imply "same candidate" over long spans. But this check only runs inside the
    poller's post-cycle, which itself only runs when items were ingested (fingerprint changed →
    gate open) or the request path marked the catalog dirty (bypasses the gate below). A skip can
    therefore only defer aging-out until the next real write — minutes in practice, and exactly the
    latency bound D6 already accepts for quiet feeds."""
    v = os.environ.get("RWE_REFRESH_CHEAP_CHECK", "").strip().lower()
    return v not in {"0", "false", "no", "off"}


def initial_signature(store_) -> str:
    """Signature of the startup catalog's candidate, so the first poll cycle skips a rebuild when the
    catalog hasn't changed since boot."""
    try:
        return candidate_signature(build_candidate_for(store_))
    except Exception:
        return "unknown"


def _expected_item_count(result, max_items: Optional[int]) -> int:
    """How many items the (unchanged) qbias builder is expected to yield from the validated candidate:
    the bias-resolvable rows (``sum(perBucket)``, since ``catalog_from_qbias`` drops rows whose lean
    won't resolve), capped at ``max_items`` (it randomly subsamples past that). Verified empirically."""
    bias_resolvable = sum((result.metrics.get("perBucket") or {}).values())
    return min(bias_resolvable, max_items) if max_items else bias_resolvable


class RefreshManager:
    """Owns the active serving bundle and performs validated, atomic hot swaps.

    One instance per app, created at startup and seeded with the boot Backend (generation 1). The
    poller drives it via :meth:`on_poll_cycle`; nothing on the request path calls it (the request path
    only *reads* ``app.active``)."""

    def __init__(self, app_state, *, provider: str = "anthropic",
                 log: Optional[Callable[..., None]] = None):
        self.app = app_state                 # the FastAPI _State (holds .store and .active)
        self.provider = provider
        self._log = log or (lambda *a, **k: None)
        self._lock = threading.Lock()        # serializes state transitions + the swap (NOT the read path)
        self.polling_enabled = False
        # Commit 18 (D6): set when a request-path producer (an extension read) created a catalog
        # article between poll cycles. The poller's hot-refresh seam normally fires only when a feed
        # cycle ingested something new; this flag lets a quiet-feed cycle still trigger the rebuild,
        # bounding an extension article's graph latency to one poll interval. Consumed (cleared) by
        # ``_maybe_refresh`` — a plain bool write, safe cross-thread under the GIL.
        self.catalog_dirty = False
        # diagnostics
        self.state = RefreshState.IDLE
        self.generation = 0
        self.refresh_count = 0
        # The catalog fingerprint the last-built candidate was constructed against — read BEFORE
        # that build, so a write landing mid-build makes the next cycle rebuild rather than skip.
        # Deliberately NOT set by `seed`: the boot candidate is built before seeding and a write in
        # that gap would otherwise be skipped over, so the first poll cycle always pays one full
        # check — exactly what it paid before the gate existed.
        self.last_catalog_fp = None
        self.last_candidate_sig: Optional[str] = None
        self.last_validation_at: Optional[str] = None
        self.last_success_at: Optional[str] = None
        self.last_failed_at: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_build_ms: Optional[float] = None
        self.last_candidate_ms: Optional[float] = None
        self.last_activation_ms: Optional[float] = None
        self.last_failures: list = []

    # -- startup seed ------------------------------------------------------- #
    def seed(self, backend, personalizer, source: str, candidate_sig: str, item_count: int) -> "Active":
        """Install the boot Backend as generation 1 (no build, no validation — it was built by the
        lifespan exactly as before)."""
        active = Active(backend=backend, personalizer=personalizer, generation=1, built_at=_now_iso(),
                        source=source, candidate_sig=candidate_sig, item_count=item_count)
        self.app.active = active
        self.generation = 1
        self.last_candidate_sig = candidate_sig
        return active

    # -- the off-thread Backend builder ------------------------------------ #
    def _sanity_check(self, be, result, max_items) -> Optional[str]:
        """Reject a Backend that built but is degenerate, BEFORE it can be activated."""
        if len(be.eligible) == 0:
            return "no_eligible_readers"
        expected = _expected_item_count(result, max_items)
        got = len(be.mind.dataset.item_ids)
        if got != expected:
            return f"item_count_mismatch:{got}!={expected}"
        if not be.url_by_id:
            return "url_resolver_empty"
        try:
            recs = be.recommendations(be.demo_user, "rwe-b")
        except Exception as e:      # a build that can't serve a single recommendation is not eligible
            return f"recommendations_failed:{type(e).__name__}"
        if not recs:
            return "recommendations_empty"
        return None

    def build_active(self, store_, candidate: list, thresholds: dict, *, generation: int):
        """Validate the candidate, build a Backend from it off-thread, run sanity checks, and (only on
        success) return a ready ``Active``. Returns ``(active_or_None, result, error_or_None)`` — it
        never activates and never raises for an expected failure."""
        feed_health = store_.list_feed_health()
        result = corpus_validation.validate_corpus(candidate, feed_health, thresholds=thresholds)
        self.last_validation_at = result.generatedAt
        self.last_failures = [f["code"] for f in result.failures]
        if not result.eligible:
            return None, result, "validation_failed"

        csv_path = None
        try:
            import tempfile
            fd, csv_path = tempfile.mkstemp(prefix="ih_refresh_", suffix=".csv")
            os.close(fd)
            feed_source.export_candidate_csv(candidate, csv_path)
            profile = engine.DatasetProfile.synthetic(qbias_csv=csv_path, **_sizing_kwargs())
            t0 = time.perf_counter()
            be = engine.Backend(profile, provider=self.provider)
            be.attach_url_resolver(feed_source.load_url_map(csv_path))   # resolver rebuilt WITH the backend
            # The country map is an ENRICHMENT, unlike the URL map above (which `_sanity_check`
            # requires — an empty one rejects the build). Losing it costs the For You country
            # preference its input and nothing else, so it must never be the reason a corpus
            # refresh fails and the catalog goes stale. Fail-soft, and say so in the log.
            try:
                be.attach_country_resolver(feed_source.load_country_map(csv_path))
            except Exception as e:                       # noqa: BLE001 — enrichment, never fatal
                self._log(logging.WARNING, "country_map_unavailable", error=repr(e))
            # Story membership: the per-story quota's input, rebuilt WITH the backend so the two
            # can never describe different catalogs. Same fail-soft posture as the country map.
            try:
                be.attach_story_resolver(*feed_source.load_story_maps(store_, csv_path))
            except Exception as e:                       # noqa: BLE001 — enrichment, never fatal
                self._log(logging.WARNING, "story_map_unavailable", error=repr(e))
            self.last_build_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        except Exception as e:
            return None, result, f"backend_build_failed:{type(e).__name__}"
        finally:
            if csv_path:
                try:
                    os.unlink(csv_path)     # the CSV is fully consumed by build + load_url_map
                except OSError:
                    pass

        err = self._sanity_check(be, result, profile.max_items)
        if err is not None:
            return None, result, err

        personalizer = personalize.Personalizer(be, store_)   # fresh cache, wraps the NEW backend
        active = Active(backend=be, personalizer=personalizer, generation=generation, built_at=_now_iso(),
                        source="feed", candidate_sig=candidate_signature(candidate),
                        item_count=len(be.mind.dataset.item_ids))
        return active, result, None

    # -- the poller seam (runs in the poller thread; off the request path) -- #
    @staticmethod
    def _catalog_fp(store_):
        """The store's cheap catalog fingerprint (one aggregate query), or ``None`` when it cannot
        be read — and ``None`` never matches anything, so an unreadable fingerprint always falls
        through to the full check rather than to a skip."""
        try:
            return store_.catalog_fingerprint()
        except Exception:
            return None

    def mark_catalog_dirty(self) -> None:
        """A request-path producer (extension read) created a catalog article: make sure the NEXT
        poll cycle runs the refresh check even if the feeds themselves bring nothing new (D6)."""
        self.catalog_dirty = True

    def is_catalog_dirty(self) -> bool:
        """The poller's quiet-cycle check (see ``sources.MultiSourcePoller._post_cycle``)."""
        return bool(self.catalog_dirty)

    def on_poll_cycle(self, agg: dict) -> None:
        """Called by the poller after each ingest/retention cycle. Rebuilds + swaps only when the
        candidate actually changed and validates. Never raises (guarded) and never blocks readers."""
        try:
            self._maybe_refresh()
        except Exception as e:      # a refresh fault must never break the poll loop
            self._log(logging.ERROR, "corpus_refresh_error", error=repr(e))
            with self._lock:
                self.state = RefreshState.FAILED
                self.last_failed_at = _now_iso()
                self.last_error = repr(e)

    def _maybe_refresh(self) -> None:
        store_ = self.app.store
        if store_ is None:
            return
        was_dirty = self.catalog_dirty
        self.catalog_dirty = False           # consuming the nudge: this check covers everything so far
        # THE CHEAP GATE (P1-1): an unchanged catalog fingerprint cannot have changed the candidate
        # (modulo the bounded aging deferral argued on `cheap_check_enabled`), so skip the
        # full-catalog load outright. A dirty nudge BYPASSES the gate: it is the request path
        # explicitly asking for a check, and honouring it with a real one keeps D6's latency bound
        # a promise rather than a probability. Fail-open on every uncertainty — an unreadable
        # fingerprint, no recorded baseline, no active bundle — because the failure mode of
        # skipping wrongly (a stale corpus) is worse than the 4.5 s it saves.
        fp = self._catalog_fp(store_)
        if (cheap_check_enabled() and not was_dirty and self.app.active is not None
                and fp is not None and self.last_catalog_fp is not None
                and fp == self.last_catalog_fp):
            obs_metrics.incr("corpus_refresh_cheap_skip_total")
            self._log(logging.INFO, "corpus_refresh_check", rebuilt=False, skipped="fingerprint")
            return
        th = corpus_health.thresholds_from_env()
        t0 = time.perf_counter()
        candidate = build_candidate_for(store_, th)
        sig = candidate_signature(candidate)
        # The candidate is built on every cycle THE GATE LETS THROUGH — it is how the signature is
        # computed — and it loads the entire catalog (`list_feed_articles(limit=10_000_000)`). The
        # fingerprint recorded here is the PRE-build read from above, so a write landing during the
        # multi-second build reopens the gate next cycle instead of being skipped over.
        self.last_candidate_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        self.last_candidate_sig = sig
        self.last_catalog_fp = fp
        obs_metrics.incr("corpus_refresh_candidate_built_total")
        current = self.app.active
        if current is not None and sig == current.candidate_sig:
            self._log(logging.INFO, "corpus_refresh_check", candidateMs=self.last_candidate_ms,
                      items=len(candidate), rebuilt=False)
            return              # catalog composition unchanged — nothing to rebuild

        with self._lock:
            self.state = RefreshState.BUILDING
        gen = (current.generation + 1) if current is not None else 1
        active, result, err = self.build_active(store_, candidate, th, generation=gen)

        if err is not None or active is None:
            with self._lock:
                self.state = RefreshState.FAILED
                self.last_failed_at = _now_iso()
                self.last_error = err
            self._log(logging.WARNING, "corpus_refresh_skipped", reason=err,
                      generation=gen, failures=self.last_failures)
            return

        t0 = time.perf_counter()
        with self._lock:
            self.state = RefreshState.SWAPPING
            self.app.active = active                     # <-- THE ATOMIC SWAP (single assignment)
            self.generation = active.generation
            self.refresh_count += 1
            self.last_success_at = active.built_at
            self.last_activation_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            self.last_error = None
            self.state = RefreshState.IDLE
        self._log(logging.INFO, "corpus_refresh_activated", generation=active.generation,
                  items=active.item_count, candidateMs=self.last_candidate_ms,
                  buildMs=self.last_build_ms,
                  activationMs=self.last_activation_ms, sig=active.candidate_sig)

    # -- diagnostics -------------------------------------------------------- #
    def snapshot(self) -> dict:
        a = self.app.active
        with self._lock:
            return {
                "state": self.state,
                "source": a.source if a else "static",
                "generation": a.generation if a else 0,
                "activeVersion": a.generation if a else 0,
                "activeSignature": a.candidate_sig if a else None,
                "candidateVersion": self.last_candidate_sig,
                "itemCount": a.item_count if a else 0,
                "builtAt": a.built_at if a else None,
                "refreshCount": self.refresh_count,
                "lastSuccessAt": self.last_success_at,
                "lastFailedAt": self.last_failed_at,
                "lastValidationAt": self.last_validation_at,
                "lastError": self.last_error,
                "lastFailures": self.last_failures,
                "buildMs": self.last_build_ms,
                "activationMs": self.last_activation_ms,
                "pollingEnabled": self.polling_enabled,
                "catalogDirty": bool(self.catalog_dirty),
            }
