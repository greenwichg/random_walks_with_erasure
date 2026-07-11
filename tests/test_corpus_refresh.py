"""Tests for examples/corpus_refresh.py — atomic hot activation of a validated corpus (Commit 5).

Proves the activation contract: the Backend is built off the request path; validation OR build OR
sanity failure prevents activation (the previous Backend keeps serving); a successful build swaps a
single pointer atomically (backend + personalizer together); in-flight readers keep their old bundle;
consecutive refreshes bump the generation; the URL resolver is rebuilt with the Backend; and an
unchanged candidate signature rebuilds nothing. Activation reuses the UNCHANGED engine constructor —
no recommendation algorithm/ranking/scoring/selection/serializer is touched.

Most tests use a faithful fake Backend (fast); one real end-to-end test builds an actual Backend.
"""

import csv as _csv
import os
import pathlib
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
# a small population keeps the ONE real Backend build fast
os.environ.setdefault("RWE_N_USERS", "150")
os.environ.setdefault("RWE_MAX_ITEMS", "800")
os.environ.setdefault("RWE_SEED", "0")

import store as store_mod          # noqa: E402
import feed_source                 # noqa: E402
import corpus_refresh as cr        # noqa: E402

NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _seed_catalog(st, n_per_bucket=6, prefix="a"):
    """Insert a balanced L/C/R catalog with real URLs; returns the canonical URLs added."""
    urls = []
    for i in range(n_per_bucket):
        for pub, lean in (("Guardian", -1.5), ("AP", 0.0), ("Fox News", 1.5)):
            u = f"https://{pub.replace(' ', '').lower()}.example/{prefix}{i}"
            urls.append(u)
            st.upsert_feed_article(canonical_url=u, url=u, publisher=pub, source_publisher=pub,
                                   title=f"{pub} {prefix}{i}", description="d", body=None,
                                   published_at=(NOW - timedelta(hours=i)).isoformat(), source_feed=pub,
                                   # political=True mirrors production: every ingested article is
                                   # scored (ingest always sets the flag); these model political news.
                                   scored={"article_id": u, "outlet": pub, "lean": lean,
                                           "category": "P", "political": True})
    return urls


class FakeBackend:
    """A stand-in that satisfies the sanity checks without the heavy simulator build. Item count is
    driven from the exported CSV so `_expected_item_count` matches, and `attach_url_resolver` wires the
    real `load_url_map` output (so the URL-resolver-replacement test sees real URLs)."""

    def __init__(self, item_ids):
        self.mind = types.SimpleNamespace(dataset=types.SimpleNamespace(item_ids=list(item_ids)))
        self.eligible = [0, 1, 2, 3, 4]
        self.demo_user = 0
        self.url_by_id: dict = {}

    def attach_url_resolver(self, mapping):
        self.url_by_id = dict(mapping or {})

    def recommendations(self, u, strategy=None):
        # non-empty; first item id echoes a real corpus id so a URL resolves through url_by_id
        first = self.mind.dataset.item_ids[0] if self.mind.dataset.item_ids else "Q0"
        return [{"article": {"id": first, "url": self.url_by_id.get(first)}}]


def _fake_backend_factory(profile, provider=None):
    """Build a FakeBackend whose item count equals what catalog_from_qbias would yield: the
    bias-resolvable CSV rows, capped at max_items."""
    ids = []
    with open(profile.qbias_csv, newline="", encoding="utf-8") as f:
        for i, row in enumerate(_csv.DictReader(f)):
            if (row.get("bias_rating") or "").strip() in {"left", "center", "right"}:
                ids.append(f"Q{i}")
    if profile.max_items and len(ids) > profile.max_items:
        ids = ids[:profile.max_items]
    return FakeBackend(ids)


class FakePersonalizer:
    def __init__(self, backend, store):
        self.backend = backend
        self.store = store


def _manager(st, monkeypatch, *, fake=True):
    """A RefreshManager over a fresh app-state, seeded at generation 1 with a sentinel signature so
    the first cycle always rebuilds. Patches the Backend + Personalizer to fakes unless fake=False."""
    if fake:
        monkeypatch.setattr(cr.engine, "Backend", _fake_backend_factory)
        monkeypatch.setattr(cr.personalize, "Personalizer", FakePersonalizer)
    app = types.SimpleNamespace(store=st, active=None)
    mgr = cr.RefreshManager(app, provider="anthropic")
    seed_be = _fake_backend_factory(types.SimpleNamespace(qbias_csv=_empty_csv(), max_items=None)) if fake \
        else None
    if fake:
        mgr.seed(seed_be, FakePersonalizer(seed_be, st), "feed", "seed-sentinel", 0)
    return app, mgr


def _empty_csv():
    import tempfile
    fd, p = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    feed_source.export_candidate_csv([], p)
    return p


# --------------------------------------------------------------------------- #
# Off-thread build returns a ready Active WITHOUT activating
# --------------------------------------------------------------------------- #
def test_build_active_does_not_activate(monkeypatch):
    monkeypatch.setenv("RWE_CORPUS_MIN_ARTICLES", "1")
    st = store_mod.Store("sqlite://")
    _seed_catalog(st)
    app, mgr = _manager(st, monkeypatch)
    before = app.active
    candidate = cr.build_candidate_for(st)
    active, result, err = mgr.build_active(st, candidate, cr.corpus_health.thresholds_from_env(),
                                           generation=2)
    assert err is None and active is not None and result.eligible is True
    assert app.active is before          # building NEVER activates — the swap is a separate step


# --------------------------------------------------------------------------- #
# Validation failure -> no activation, previous Backend keeps serving
# --------------------------------------------------------------------------- #
def test_validation_failure_prevents_activation(monkeypatch):
    monkeypatch.setenv("RWE_CORPUS_MIN_ARTICLES", "9999")   # impossible floor -> ineligible
    st = store_mod.Store("sqlite://")
    _seed_catalog(st)
    app, mgr = _manager(st, monkeypatch)
    keep = app.active
    mgr.on_poll_cycle({"new": 18})
    assert app.active is keep                       # unchanged
    assert mgr.refresh_count == 0 and mgr.state == cr.RefreshState.FAILED
    assert mgr.last_error == "validation_failed"


# --------------------------------------------------------------------------- #
# Backend build failure -> no activation, previous Backend keeps serving
# --------------------------------------------------------------------------- #
def test_build_failure_prevents_activation(monkeypatch):
    monkeypatch.setenv("RWE_CORPUS_MIN_ARTICLES", "1")
    st = store_mod.Store("sqlite://")
    _seed_catalog(st)
    app, mgr = _manager(st, monkeypatch)
    keep = app.active

    def boom(profile, provider=None):
        raise RuntimeError("simulator exploded")
    monkeypatch.setattr(cr.engine, "Backend", boom)
    mgr.on_poll_cycle({"new": 18})
    assert app.active is keep
    assert mgr.refresh_count == 0 and mgr.state == cr.RefreshState.FAILED
    assert mgr.last_error.startswith("backend_build_failed")


# --------------------------------------------------------------------------- #
# Sanity: item-count mismatch prevents activation
# --------------------------------------------------------------------------- #
def test_item_count_mismatch_prevents_activation(monkeypatch):
    monkeypatch.setenv("RWE_CORPUS_MIN_ARTICLES", "1")
    st = store_mod.Store("sqlite://")
    _seed_catalog(st)
    app, mgr = _manager(st, monkeypatch)
    keep = app.active

    monkeypatch.setattr(cr.engine, "Backend", lambda p, provider=None: FakeBackend(["Q0", "Q1"]))  # wrong count
    mgr.on_poll_cycle({"new": 18})
    assert app.active is keep and mgr.refresh_count == 0
    assert mgr.last_error.startswith("item_count_mismatch")


# --------------------------------------------------------------------------- #
# Happy path: atomic swap activates a new generation
# --------------------------------------------------------------------------- #
def test_atomic_swap_activates_new_generation(monkeypatch):
    monkeypatch.setenv("RWE_CORPUS_MIN_ARTICLES", "1")
    st = store_mod.Store("sqlite://")
    _seed_catalog(st)
    app, mgr = _manager(st, monkeypatch)
    mgr.on_poll_cycle({"new": 18})
    assert mgr.refresh_count == 1 and mgr.state == cr.RefreshState.IDLE
    assert app.active.generation == 2 and app.active.source == "feed"
    assert app.active.item_count == 18                 # 6 per bucket x 3 buckets, all resolvable
    assert mgr.last_success_at is not None and mgr.last_build_ms is not None


# --------------------------------------------------------------------------- #
# Reader isolation: an in-flight bundle keeps working after the swap
# --------------------------------------------------------------------------- #
def test_reader_isolation_inflight_bundle_survives_swap(monkeypatch):
    monkeypatch.setenv("RWE_CORPUS_MIN_ARTICLES", "1")
    st = store_mod.Store("sqlite://")
    _seed_catalog(st)
    app, mgr = _manager(st, monkeypatch)
    inflight = app.active                    # a request captured the bundle before the swap
    mgr.on_poll_cycle({"new": 18})
    assert app.active is not inflight and app.active.generation == inflight.generation + 1
    # the captured (old) bundle is still fully usable — no interruption for the in-flight request
    assert inflight.backend.recommendations(inflight.backend.demo_user, "rwe-b")
    assert inflight.generation == 1


# --------------------------------------------------------------------------- #
# Unchanged candidate signature rebuilds nothing
# --------------------------------------------------------------------------- #
def test_unchanged_signature_skips_rebuild(monkeypatch):
    monkeypatch.setenv("RWE_CORPUS_MIN_ARTICLES", "1")
    st = store_mod.Store("sqlite://")
    _seed_catalog(st)
    app, mgr = _manager(st, monkeypatch)
    mgr.on_poll_cycle({"new": 18})           # first: builds + swaps
    assert mgr.refresh_count == 1
    gen = app.active.generation
    mgr.on_poll_cycle({"new": 0})            # catalog unchanged -> signature identical -> skip
    assert mgr.refresh_count == 1 and app.active.generation == gen


# --------------------------------------------------------------------------- #
# Multiple consecutive refreshes bump the generation each time
# --------------------------------------------------------------------------- #
def test_multiple_consecutive_refreshes(monkeypatch):
    monkeypatch.setenv("RWE_CORPUS_MIN_ARTICLES", "1")
    st = store_mod.Store("sqlite://")
    _seed_catalog(st, prefix="a")
    app, mgr = _manager(st, monkeypatch)
    mgr.on_poll_cycle({"new": 18})
    assert app.active.generation == 2
    _seed_catalog(st, prefix="b")            # catalog changes -> new signature
    mgr.on_poll_cycle({"new": 18})
    assert app.active.generation == 3 and mgr.refresh_count == 2
    _seed_catalog(st, prefix="c")
    mgr.on_poll_cycle({"new": 18})
    assert app.active.generation == 4 and mgr.refresh_count == 3


# --------------------------------------------------------------------------- #
# URL resolver is rebuilt with the Backend
# --------------------------------------------------------------------------- #
def test_url_resolver_replaced_on_swap(monkeypatch):
    monkeypatch.setenv("RWE_CORPUS_MIN_ARTICLES", "1")
    st = store_mod.Store("sqlite://")
    _seed_catalog(st)
    app, mgr = _manager(st, monkeypatch)
    mgr.on_poll_cycle({"new": 18})
    urls = set(app.active.backend.url_by_id.values())
    assert urls and any("guardian.example" in u for u in urls)   # real publisher URLs carried through


# --------------------------------------------------------------------------- #
# on_cycle never raises (a broken store is swallowed, marked FAILED)
# --------------------------------------------------------------------------- #
def test_on_cycle_never_raises(monkeypatch):
    class Broken:
        def list_feed_articles(self, limit):
            raise RuntimeError("db down")
    app = types.SimpleNamespace(store=Broken(), active=None)
    mgr = cr.RefreshManager(app, provider="anthropic")
    mgr.on_poll_cycle({"new": 5})            # must not raise
    assert mgr.state == cr.RefreshState.FAILED and mgr.last_error is not None


# --------------------------------------------------------------------------- #
# Decoupling guarantee: activation reuses the engine, changes no algorithm module
# --------------------------------------------------------------------------- #
def test_refresh_changes_no_recommendation_algorithm():
    # corpus_refresh REUSES the engine (api_server.Backend) but must not import a protected algorithm
    # module — so it cannot change ranking / scoring / selection / report calculations.
    for banned in ("health_report", "rwe", "simulate_users", "narrate_report"):
        assert not hasattr(cr, banned), f"corpus_refresh must not import {banned}"
    assert hasattr(cr.engine, "Backend")     # it builds via the EXISTING constructor


# --------------------------------------------------------------------------- #
# Real end-to-end: build an ACTUAL Backend from the validated candidate and swap
# --------------------------------------------------------------------------- #
def test_real_backend_build_and_swap(monkeypatch):
    monkeypatch.setenv("RWE_CORPUS_MIN_ARTICLES", "1")
    st = store_mod.Store("sqlite://")
    _seed_catalog(st, n_per_bucket=30)       # 90 bias-resolvable articles -> real eligible readers
    app = types.SimpleNamespace(store=st, active=None)
    mgr = cr.RefreshManager(app, provider="anthropic")

    candidate = cr.build_candidate_for(st)
    active, result, err = mgr.build_active(st, candidate, cr.corpus_health.thresholds_from_env(),
                                           generation=2)
    assert err is None and active is not None
    assert active.item_count == cr._expected_item_count(result, active.backend.profile.max_items)
    assert active.backend.url_by_id                        # resolver populated
    recs = active.backend.recommendations(active.backend.demo_user, "rwe-b")
    assert recs and recs[0]["article"].get("url", "").startswith("https://")   # URL passthrough intact

    old = mgr.seed(active.backend, active.personalizer, "feed", "sentinel", active.item_count)
    _seed_catalog(st, n_per_bucket=30, prefix="z")         # change the catalog
    mgr.on_poll_cycle({"new": 90})
    assert app.active.generation == old.generation + 1     # swapped to a freshly built real Backend
    assert app.active.backend is not old.backend


# --------------------------------------------------------------------------- #
# Commit 18 (D5): the refresh candidate keeps read articles past the publisher cap
# --------------------------------------------------------------------------- #
def test_candidate_read_demand_exemption(monkeypatch):
    """An article a user READ is re-added after the per-publisher cap trims it — the protected
    builder's output is unchanged for everything unread, and the read article changes the candidate
    signature (which is what makes the next poll cycle refresh automatically)."""
    st = store_mod.Store("sqlite://")
    urls = _seed_catalog(st, n_per_bucket=6)
    monkeypatch.setenv("RWE_CORPUS_MAX_PER_PUBLISHER", "2")     # tight cap: 2 per publisher

    before = cr.build_candidate_for(st)
    sig_before = cr.candidate_signature(before)
    guardian_kept = [a["canonicalUrl"] for a in before if "guardian" in a["canonicalUrl"]]
    assert len(guardian_kept) == 2
    # the OLDEST Guardian article was capped out — a user reads exactly that one
    dropped = [u for u in urls if "guardian" in u and u not in guardian_kept][-1]
    u = st.upsert_user_by_identity("google", "cap-reader").id
    st.add_read(u, dropped, {"article_id": dropped}, None)

    after = cr.build_candidate_for(st)
    kept_urls = {a["canonicalUrl"] for a in after}
    assert dropped in kept_urls                                  # exemption re-added the read article
    assert len([x for x in kept_urls if "guardian" in x]) == 3   # cap still binds for unread ones
    assert cr.candidate_signature(after) != sig_before           # signature change -> auto refresh


# --------------------------------------------------------------------------- #
# Commit 18 (D6): the hot-refresh seam fires for request-path catalog growth too
# --------------------------------------------------------------------------- #
def test_post_cycle_gate_respects_dirty_check(monkeypatch):
    """The poller's on_cycle seam runs on feed growth OR when the request path flagged the catalog
    dirty (extension read) — a quiet feed must not stall an extension article's graph entry."""
    import sources
    st = store_mod.Store("sqlite://")
    calls = []
    dirty = {"v": False}
    p = sources.MultiSourcePoller(st, scorer=object(), log=lambda *a, **k: None,
                                  on_cycle=lambda agg: calls.append(agg),
                                  dirty_check=lambda: dirty["v"])
    p._post_cycle({"new": 0})                 # quiet feed, clean catalog -> no trigger
    assert calls == []
    dirty["v"] = True
    p._post_cycle({"new": 0})                 # quiet feed, dirty catalog -> trigger (the fix)
    assert len(calls) == 1
    dirty["v"] = False
    p._post_cycle({"new": 3})                 # feed growth -> trigger (unchanged behaviour)
    assert len(calls) == 2
    # no dirty_check wired (legacy callers) -> exactly the old behaviour
    q = sources.MultiSourcePoller(st, scorer=object(), log=lambda *a, **k: None,
                                  on_cycle=lambda agg: calls.append(agg))
    q._post_cycle({"new": 0})
    assert len(calls) == 2


def test_maybe_refresh_consumes_the_dirty_flag(monkeypatch):
    monkeypatch.setenv("RWE_CORPUS_MIN_ARTICLES", "1")
    st = store_mod.Store("sqlite://")
    _seed_catalog(st)
    app, mgr = _manager(st, monkeypatch)
    mgr.mark_catalog_dirty()
    assert mgr.is_catalog_dirty() is True
    mgr.on_poll_cycle({})                     # runs _maybe_refresh
    assert mgr.is_catalog_dirty() is False    # consumed, regardless of whether a swap happened
