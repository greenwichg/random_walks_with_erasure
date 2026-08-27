"""M6, first piece: the story-cache warm no longer holds the ingest write lock.

## What was wrong

`poll_adapter_once` held `self._lock` across the poll AND the whole post-cycle, and the post-cycle
called `story_service.request_warm`. That is non-blocking only when `warm_coalesce_window() > 0`,
and it defaults to **0** — off by measured decision, not oversight (`story_service:2829`: production
warms sit ~60 s apart so there is no burst to merge, and *delaying* a warm costs more than it
saves). With the window at 0 it calls `warm_cache` inline, so every ingesting cycle ran a full
clustering build while holding the lock that serialises every other adapter's ingest.

Measured on production 2026-08-26, after the catalog-wide steps were coalesced: `warmMs` 14-20 s on
every ingesting cycle, against a 13,624 ms full build at 27,764 articles. The same number. It was
the largest single contributor to the 24.9% lock occupancy that remained.

## Why this is not "turn coalescing on"

Coalescing *delays* the warm, and that delay is exactly what the two measurements rejected. This
keeps the warm synchronous and immediate, and only stops it blocking other adapters — the part that
was never justified. `warm_cache` reads the catalog and builds an in-process cache; it never needed
the WRITE lock.

## The two properties, and why each is a test rather than a comment

A concurrency change is only as good as its narrowest claim. "The warm does not hold the lock" and
"the thing that writes still does" are both checkable at runtime by asking the lock, so neither is
left as prose.
"""
import pathlib
import sys
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import sources        # noqa: E402
import story_events   # noqa: E402


@pytest.fixture()
def poller(monkeypatch):
    monkeypatch.delenv("RWE_POST_CYCLE_MAINTENANCE_INTERVAL", raising=False)
    return sources.MultiSourcePoller(object(), registry=sources.SourceRegistry(),
                                     log=lambda *a, **k: None)


def _lock_is_held(poller) -> bool:
    """Ask the lock directly. Same thread, so this is a non-reentrant probe: `threading.Lock` is not
    owner-aware, and a successful acquire proves nothing was holding it."""
    if poller._lock.acquire(blocking=False):
        poller._lock.release()
        return False
    return True


# --------------------------------------------------------------------------- the point

def test_the_warm_runs_with_the_lock_NOT_held(poller, monkeypatch):
    """The whole change, in one assertion. A 13.6 s clustering build must not sit inside the lock
    that serialises every other adapter's ingest."""
    seen = {}
    monkeypatch.setattr(sources.story_service, "request_warm",
                        lambda *a, **k: seen.__setitem__("held", _lock_is_held(poller)))
    poller._post_cycle_unlocked({"new": 1})
    assert seen["held"] is False


def test_the_breaking_detector_DOES_still_hold_it(poller, monkeypatch):
    """The other half, and the reason this is not simply "move the block outside the lock".
    `detect_breaking_stories` WRITES event rows. It reads the cache the warm just built, so it has
    to stay after it — and it re-enters the lock for its own duration rather than racing an
    adapter's ingest. Brief and explicit beats moving a writer off the write lock."""
    seen = {}
    monkeypatch.setattr(sources.story_service, "request_warm", lambda *a, **k: None)
    monkeypatch.setattr(story_events, "detect_breaking_stories",
                        lambda *a, **k: seen.__setitem__("held", _lock_is_held(poller)))
    poller._post_cycle_unlocked({"new": 1})
    assert seen["held"] is True


def test_the_warm_happens_AFTER_the_lock_is_released_not_before_it_is_taken(poller, monkeypatch):
    """Ordering, not just exclusion. The warm must observe the rows this cycle just ingested, so it
    belongs after the locked phase — moving it earlier would rebuild a cache that is stale on
    arrival, which is the failure the inline call was written to avoid in the first place."""
    order = []
    monkeypatch.setattr(sources.storage_lifecycle, "run_cleanup",
                        lambda *a, **k: order.append("cleanup(locked)"))
    monkeypatch.setattr(sources.story_service, "request_warm",
                        lambda *a, **k: order.append(f"warm(locked={_lock_is_held(poller)})"))

    class _Adapter:
        provider, source_type = "probe", "test"

        def poll_once(self, *a, **k):
            order.append(f"poll(locked={_lock_is_held(poller)})")
            return {"new": 3}

    poller.store = type("S", (), {"count_feed_articles": lambda self: 0})()
    poller.poll_adapter_once(_Adapter())
    assert order == ["poll(locked=True)", "cleanup(locked)", "warm(locked=False)"]


# --------------------------------------------------------------------------- what must not regress

def test_a_failing_warm_never_breaks_the_poll_loop(poller, monkeypatch):
    """Fail-soft was the contract before the move and has to survive it: a warm that cannot be built
    is a slow next request, never a broken poll loop."""
    monkeypatch.setattr(sources.story_service, "request_warm",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    poller._post_cycle_unlocked({"new": 1})          # must not raise


def test_a_failing_breaking_detector_RELEASES_the_lock(poller, monkeypatch):
    """The failure mode a `with` block exists to prevent, asserted anyway because this one is fatal:
    an exception inside the re-acquired lock that escaped without releasing would deadlock every
    adapter thread in the process, and the symptom would be "ingestion stopped", not "an error"."""
    monkeypatch.setattr(sources.story_service, "request_warm", lambda *a, **k: None)
    monkeypatch.setattr(story_events, "detect_breaking_stories",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    poller._post_cycle_unlocked({"new": 1})
    assert _lock_is_held(poller) is False, "the lock must be free after the exception"


def test_another_thread_can_ingest_while_a_warm_is_in_flight(poller, monkeypatch):
    """The behaviour the change exists to buy, rather than a proxy for it. Under the old code this
    would block for the full duration of the build; now it does not."""
    warming, may_finish = threading.Event(), threading.Event()

    def _slow_warm(*a, **k):
        warming.set()
        may_finish.wait(timeout=5)

    monkeypatch.setattr(sources.story_service, "request_warm", _slow_warm)
    t = threading.Thread(target=lambda: poller._post_cycle_unlocked({"new": 1}), daemon=True)
    t.start()
    assert warming.wait(timeout=5), "the warm should have started"

    got = poller._lock.acquire(timeout=2)
    try:
        assert got, "an adapter must be able to take the ingest lock mid-warm"
    finally:
        if got:
            poller._lock.release()
        may_finish.set()
        t.join(timeout=5)


def test_a_lock_that_cannot_be_retaken_LOGS_instead_of_hanging(poller, monkeypatch):
    """The deadlock made real, safely.

    `_post_cycle_unlocked` must not be called while the lock is already held — `threading.Lock` is
    not reentrant, so a bare `with self._lock:` would block that adapter thread forever. This is not
    theoretical: reverting the warm back inside the lock, to check these tests actually flip, HUNG
    the test run rather than failing it. A deadlock here presents as "ingestion stopped" with no
    error logged anywhere, which is the worst shape a fault can take in this loop.

    A timed acquire turns the impossible wait into a line an operator can search for."""
    monkeypatch.setattr(sources, "_BREAKING_LOCK_TIMEOUT_S", 0.2)
    monkeypatch.setattr(sources.story_service, "request_warm", lambda *a, **k: None)
    ran = []
    monkeypatch.setattr(story_events, "detect_breaking_stories", lambda *a, **k: ran.append(1))

    events = []
    poller._log = lambda lvl, ev, **f: events.append(ev)

    poller._lock.acquire()                       # simulate the caller holding it
    try:
        poller._post_cycle_unlocked({"new": 1})  # must RETURN, not hang
    finally:
        poller._lock.release()

    assert "breaking_detect_lock_timeout" in events, "the impossible wait must be reported"
    assert ran == [], "and the writer must not run without the lock it needs"


def test_the_timeout_is_generous_enough_not_to_fire_on_ordinary_contention(poller):
    """Guard on the guard. A timeout short enough to trip while another adapter is mid-ingest would
    convert a working system into a stream of ERROR lines — and post-cycle maintenance passes were
    measured at ~96 s, so this has to clear that."""
    assert sources._BREAKING_LOCK_TIMEOUT_S >= 120.0
