"""M6.2: the network fetch no longer holds the ingest write lock.

## The measurement that selected this milestone

After M6.1 (the story-cache warm came off the lock) production sat at **16.0% lock occupancy** —
577.8 s held per hour, of which poll was 120.2 s and post-cycle 457.6 s.

The two halves scale differently, and that is the whole argument:

* **Post-cycle is now O(1) per window.** Coalescing pinned it to one pass per
  ``RWE_POST_CYCLE_MAINTENANCE_INTERVAL`` regardless of how many adapters exist. Fixed cost.
* **Poll scales linearly with source count.** A crawl source cost ~2.4 s of lock per poll (measured:
  ``pollMs`` 2335.8 and 2547.6) at four polls an hour — **9.6 s of lock-held time per source per
  hour**::

      saturation  (3600 - 458) / 9.6 = 327 sources
      50% comfort (1800 - 458) / 9.6 = 140 sources

That is the wall between two crawl sources and "hundreds". And essentially all of that 2.4 s is a
network round trip to a publisher — a socket wait holding a database WRITE lock.

## Why this had to come before worker leases

The rest of M6 is leases, a bounded pool, dormancy, an interval ceiling. None of it helps while the
fetch holds the global lock: N workers would each take that lock, wait 2.4 s on a socket, and
release — serialising exactly as one worker does. Concurrency behind a global lock is not
concurrency. This milestone is the dependency, not an alternative to them.

## What makes it safe

``SourceAdapter.collect`` (fetch + normalize + quota) touches no store; ``persist``
(``ingest_entries`` + health) is the half that does. ``poll_once`` is now their composition, so
every existing caller and every subclass override sees the contract it saw before.

The split is **opt-in per adapter** (``FETCH_IS_STORE_FREE``) and additionally requires that the
adapter has not overridden ``poll_once`` — the split lives in the base implementation, so an
override would not use it, and honouring the flag anyway would run a store-touching override
unlocked. Today exactly one adapter opts in: ``CrawlAdapter``, the class that has to reach thousands
of instances, verified store-free because the registry builds it without a ``store_`` at all.
"""
import pathlib
import sys
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import crawler  # noqa: E402
import sources  # noqa: E402


class _Store:
    def count_feed_articles(self):
        return 0


@pytest.fixture()
def poller(monkeypatch):
    monkeypatch.delenv("RWE_POST_CYCLE_MAINTENANCE_INTERVAL", raising=False)
    p = sources.MultiSourcePoller(_Store(), scorer=object(), registry=sources.SourceRegistry(),
                                  log=lambda *a, **k: None)
    monkeypatch.setattr(sources.story_service, "request_warm", lambda *a, **k: None)
    monkeypatch.setattr(sources.storage_lifecycle, "run_cleanup", lambda *a, **k: None)
    monkeypatch.setattr(p, "_record_health", lambda *a, **k: None)
    return p


def _held(poller) -> bool:
    if poller._lock.acquire(blocking=False):
        poller._lock.release()
        return False
    return True


def _adapter(poller, seen, *, store_free, entries=()):
    """A minimal adapter that records whether the lock was held during each half."""
    class _A(sources.SourceAdapter):
        FETCH_IS_STORE_FREE = store_free
        source_type = "probe"
        provider = "probe"

        @property
        def health_key(self):
            return "probe://x"

        def enabled(self):
            return True

        def interval(self):
            return 3600.0

        def fetch(self):
            seen["fetch_locked"] = _held(poller)
            return list(entries)

        def normalize(self, raw):
            seen["normalize_locked"] = _held(poller)
            return sources.SourceBatch(provider=self.provider, source_type=self.source_type,
                                       fetched_at=sources._now_iso(), entries=list(raw),
                                       raw_count=len(list(raw)))
    return _A()


# --------------------------------------------------------------------------- the point

def test_the_fetch_runs_with_the_lock_NOT_held(poller, monkeypatch):
    """The whole milestone, in one assertion. A 2.4 s socket wait must not sit inside the lock that
    serialises every other adapter's ingest."""
    seen = {}
    monkeypatch.setattr(sources.rss_ingest, "ingest_entries",
                        lambda *a, **k: seen.__setitem__("ingest_locked", _held(poller)) or {})
    poller.poll_adapter_once(_adapter(poller, seen, store_free=True))
    assert seen["fetch_locked"] is False
    assert seen["normalize_locked"] is False, "the parse rides along with the fetch"
    assert seen["ingest_locked"] is True, "the WRITE still holds the lock — that is what it is for"


def test_an_adapter_that_has_not_OPTED_IN_keeps_the_old_behaviour(poller, monkeypatch):
    """Default-deny. An adapter whose fetch might touch the store must keep the lock across the
    whole cycle until someone has actually checked it — the flag is the record of that check."""
    seen = {}
    monkeypatch.setattr(sources.rss_ingest, "ingest_entries", lambda *a, **k: {})
    poller.poll_adapter_once(_adapter(poller, seen, store_free=False))
    assert seen["fetch_locked"] is True


def test_an_override_of_poll_once_is_never_split_even_if_it_opts_in(poller, monkeypatch):
    """The subtle half of the gate. The split lives in the BASE `poll_once`; a subclass that
    overrides it would not use `collect`/`persist` at all, so honouring the flag would run a
    store-touching override unlocked. RSS, KeyedJSON and both enrichers override it."""
    seen = {}

    class _Override(sources.SourceAdapter):
        FETCH_IS_STORE_FREE = True                  # says yes...
        source_type = "probe"
        provider = "probe"

        @property
        def health_key(self):
            return "probe://x"

        def poll_once(self, store_, scorer, *, on_feed=None):   # ...but overrides the seam
            seen["poll_locked"] = _held(poller)
            return {"provider": self.provider, "sourceType": self.source_type, "new": 0,
                    "duplicates": 0, "failed": 0}

    poller.poll_adapter_once(_Override())
    assert seen["poll_locked"] is True, "an override must stay fully locked"


def test_another_adapter_can_ingest_while_a_slow_fetch_is_in_flight(poller, monkeypatch):
    """The behaviour the milestone exists to buy, rather than a proxy for it. This is what makes a
    worker pool worth building next: until now, N concurrent fetches serialised on this lock."""
    fetching, may_finish = threading.Event(), threading.Event()
    monkeypatch.setattr(sources.rss_ingest, "ingest_entries", lambda *a, **k: {})

    class _Slow(sources.SourceAdapter):
        FETCH_IS_STORE_FREE = True
        source_type = "probe"
        provider = "slow"

        @property
        def health_key(self):
            return "probe://slow"

        def fetch(self):
            fetching.set()
            may_finish.wait(timeout=5)
            return []

        def normalize(self, raw):
            return sources.SourceBatch(provider=self.provider, source_type=self.source_type,
                                       fetched_at=sources._now_iso(), entries=[], raw_count=0)

    t = threading.Thread(target=lambda: poller.poll_adapter_once(_Slow()), daemon=True)
    t.start()
    assert fetching.wait(timeout=5), "the fetch should have started"
    got = poller._lock.acquire(timeout=2)
    try:
        assert got, "another adapter must be able to ingest while a fetch is on the wire"
    finally:
        if got:
            poller._lock.release()
        may_finish.set()
        t.join(timeout=5)


# --------------------------------------------------------------------------- what must not regress

def test_poll_once_is_still_exactly_collect_plus_persist(poller, monkeypatch):
    """The contract every existing caller, subclass and test depends on. `poll_once` was not
    replaced — it became the composition, so calling it directly behaves as it always did."""
    seen = {}
    monkeypatch.setattr(sources.rss_ingest, "ingest_entries", lambda *a, **k: {"new": 3})
    a = _adapter(poller, seen, store_free=True)
    agg = a.poll_once(_Store(), object())
    assert agg["new"] == 3 and agg["failed"] == 0 and agg["provider"] == "probe"


def test_a_fetch_error_still_returns_an_aggregate_rather_than_raising(poller, monkeypatch):
    """One source's outage must not affect another — the property the original try/except carried,
    now split across two methods and therefore worth re-pinning."""
    monkeypatch.setattr(sources.rss_ingest, "ingest_entries", lambda *a, **k: {})

    class _Broken(sources.SourceAdapter):
        FETCH_IS_STORE_FREE = True
        source_type = "probe"
        provider = "broken"

        @property
        def health_key(self):
            return "probe://broken"

        def fetch(self):
            raise RuntimeError("publisher down")

    agg = poller.poll_adapter_once(_Broken())
    assert agg["failed"] == 1 and agg["ok"] == 0
    assert "publisher down" in agg["errors"][0]["error"]


def test_latencyMs_still_spans_the_WHOLE_cycle_not_just_the_locked_half(poller, monkeypatch):
    """`_Collected.started` exists for this. Health records and the aggregate are consumed as "how
    long did this source take"; reporting only the locked half would make every slow publisher look
    fast and quietly break the health signal."""
    monkeypatch.setattr(sources.rss_ingest, "ingest_entries", lambda *a, **k: {})

    class _Slow(sources.SourceAdapter):
        FETCH_IS_STORE_FREE = True
        source_type = "probe"
        provider = "slow"

        @property
        def health_key(self):
            return "probe://slow"

        def fetch(self):
            time.sleep(0.05)
            return []

    agg = poller.poll_adapter_once(_Slow())
    assert agg["latencyMs"] >= 50.0, "the fetch must still be counted in the source's latency"


def test_pollMs_stays_lock_held_and_fetchMs_carries_what_moved_out(poller, monkeypatch):
    """The measurement continuity that every number in this series depends on.

    `sum(pollMs + postCycleMs) / wall` has meant "lock occupancy" through 87.8% -> 24.9% -> 16.0%.
    It still does: `pollMs` is the locked half only, and the network half is reported separately
    rather than dropped — the same rule that `offLockWarmMs` follows."""
    events = []
    poller._log = lambda lvl, ev, **f: events.append((ev, f))
    monkeypatch.setattr(sources.rss_ingest, "ingest_entries", lambda *a, **k: {})

    class _Slow(sources.SourceAdapter):
        FETCH_IS_STORE_FREE = True
        source_type = "probe"
        provider = "slow"

        @property
        def health_key(self):
            return "probe://slow"

        def fetch(self):
            time.sleep(0.05)
            return []

    poller.poll_adapter_once(_Slow())
    f = dict(next(fields for ev, fields in events if ev == "source_poll"))
    assert f["fetchMs"] >= 50.0, "the network half is reported"
    assert f["pollMs"] < 50.0, "and is NOT counted as lock-held time"


def test_the_only_adapter_opted_in_today_is_the_one_that_has_to_scale(poller):
    """Guard on the blast radius. CrawlAdapter is the class that must reach thousands of instances
    and the one whose store-freedom was actually verified (the registry builds it with no `store_`).
    Adding another opt-in should be a deliberate act with its own check, not a default."""
    assert crawler.CrawlAdapter.FETCH_IS_STORE_FREE is True
    assert sources.SourceAdapter.FETCH_IS_STORE_FREE is False, "default-deny"
    optedin = [c.__name__ for c in vars(sources).values()
               if isinstance(c, type) and issubclass(c, sources.SourceAdapter)
               and c.__dict__.get("FETCH_IS_STORE_FREE") is True]
    assert optedin == [], f"no sources.py adapter has been opted in yet, got {optedin}"


def test_a_duck_typed_adapter_that_opts_in_does_not_CRASH_the_poll(poller, monkeypatch):
    """Found by `stress_50k.py` on its first working run, and it cost an hour of misreading.

    The M6.2 gate used `getattr(adapter, "FETCH_IS_STORE_FREE", False)` — defensive, because the
    registry accepts duck-typed adapters — beside `type(adapter).poll_once`, which is not. An
    adapter that opts in WITHOUT inheriting SourceAdapter has no `poll_once` attribute at all, so
    the gate raised AttributeError on every poll. The pool catches adapter exceptions by design, so
    the symptom was "0 polls, every source starved" — which reads as a scheduler failure rather
    than a one-word inconsistency in a condition."""
    monkeypatch.setattr(sources.rss_ingest, "ingest_entries", lambda *a, **k: {})

    class _DuckTyped:                            # no SourceAdapter base, and opts in anyway
        FETCH_IS_STORE_FREE = True
        provider, source_type = "duck", "probe"
        health_key = "probe://duck"

        def poll_once(self, store_, scorer, *, on_feed=None):
            return {"provider": self.provider, "sourceType": self.source_type,
                    "new": 1, "duplicates": 0, "failed": 0}

    agg = poller.poll_adapter_once(_DuckTyped())   # must not raise
    assert agg["new"] == 1 and agg["failed"] == 0
