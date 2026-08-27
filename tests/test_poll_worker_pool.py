"""M6.3: a bounded worker pool with per-source leases, replacing thread-per-adapter.

## Why now, and not earlier

Thread-per-adapter tied thread count to SOURCE count. That was invisible while the ingest lock
serialised everything — N threads all queued on the same lock, so N did not matter. M6.2 took the
network fetch off that lock and moved the measured ceiling for crawl sources from ~327 to ~2,200,
at which point the old model means 2,200 threads and 2,200 simultaneous outbound connections. The
second is a politeness problem as much as a resource one.

A pool decouples the two: sources live in a due-time table, a fixed number of workers lease them,
and concurrency is capped by the pool rather than by how many publishers exist.

## What these tests pin

A scheduler rewrite on the ingest path has a small number of ways to be quietly wrong, and every
one of them is a correctness bug rather than a slowdown:

1. **A source is never polled by two workers at once.** Under thread-per-adapter the adapter's own
   thread WAS the lease and this was free. In a pool it has to be stated, or a slow fetch overlaps
   its own next cycle.
2. **No source starves.** With fewer workers than sources, a busy neighbour must not be able to
   overtake a waiting source indefinitely.
3. **Concurrency is actually bounded.** A cap that does not cap is worse than none, because it is
   believed.
4. **A raising source is rescheduled, not dropped**, and does not take its worker down with it.
5. **`RWE_POLL_WORKERS=0` is exactly the old model** — an off switch that is not a rollback.
6. **`stop()` does not hang** on workers parked waiting for the next due time.
"""
import pathlib
import sys
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import sources  # noqa: E402


class _Store:
    def count_feed_articles(self):
        return 0


class _Probe(sources.SourceAdapter):
    """An adapter that records when it was polled and how long it overlapped others."""
    source_type = "probe"

    def __init__(self, name, *, interval=0.2, work=0.02, boom=False):
        self.provider = name
        self._interval = interval
        self._work = work
        self._boom = boom
        self.polls = 0

    @property
    def health_key(self):
        return f"probe://{self.provider}"

    def enabled(self):
        return True

    def interval(self):
        return self._interval


@pytest.fixture()
def poller(monkeypatch):
    monkeypatch.delenv("RWE_POST_CYCLE_MAINTENANCE_INTERVAL", raising=False)
    monkeypatch.setattr(sources.story_service, "request_warm", lambda *a, **k: None)
    monkeypatch.setattr(sources.storage_lifecycle, "run_cleanup", lambda *a, **k: None)
    p = sources.MultiSourcePoller(_Store(), scorer=object(), registry=sources.SourceRegistry(),
                                  log=lambda *a, **k: None)
    monkeypatch.setattr(p, "_record_health", lambda *a, **k: None)
    yield p
    p.stop(join_timeout=2.0)


def _run_pool(poller, adapters, workers, monkeypatch, seconds=0.6, tracker=None):
    """Start a pool over `adapters` and let it run briefly."""
    monkeypatch.setenv("RWE_POLL_WORKERS", str(workers))
    for a in adapters:
        poller.registry.register(a)
    monkeypatch.setattr(poller, "poll_adapter_once",
                        tracker or (lambda a: setattr(a, "polls", a.polls + 1)))
    poller.start()
    time.sleep(seconds)
    poller.stop(join_timeout=2.0)


# --------------------------------------------------------------------------- the lease

def test_one_source_is_never_polled_by_two_workers_at_once(poller, monkeypatch):
    """The invariant thread-per-adapter got for free. `leased` is what replaces "this adapter has
    its own thread"; without it a slow fetch overlaps its own next cycle, and a publisher sees two
    concurrent requests from us — the politeness contract broken by our own scheduler.

    **Deterministic, not racy.** The first draft raced six workers against a 50 ms poll and hoped
    for an overlap. It PASSED with the lease disabled — a vacuous test for the one invariant that
    most needed a real one. This version pins the first poll open with an Event, so every other
    worker is guaranteed to attempt a claim while the source is demonstrably in flight."""
    started, in_poll, may_finish = [], threading.Event(), threading.Event()
    guard = threading.Lock()

    def _poll(adapter):
        with guard:
            started.append(adapter.provider)
        in_poll.set()
        may_finish.wait(timeout=5)

    # FOUR sources, not one. The pool caps workers at `min(requested, len(adapters))`, so a
    # single-source fixture starts a single worker — and a lease test with one worker cannot
    # observe a double claim at all. The first two drafts of this test were unfalsifiable for
    # exactly that reason: they passed with the lease disabled. The three filler sources exist to
    # buy real workers, poll instantly, and then sit 600 s in the future so the only thing any
    # freed worker can see is the blocked one.
    monkeypatch.setenv("RWE_POLL_WORKERS", "4")
    poller.registry.register(_Probe("solo", interval=0.01))
    for i in range(3):
        poller.registry.register(_Probe(f"filler{i}", interval=600.0))

    def _dispatch(adapter):
        if adapter.provider == "solo":
            _poll(adapter)

    monkeypatch.setattr(poller, "poll_adapter_once", _dispatch)
    poller.start()
    try:
        assert in_poll.wait(timeout=5), "the first poll should have started"
        time.sleep(0.3)                     # ample time for the other five to try to claim it
        with guard:
            assert started == ["solo"], f"the source was claimed more than once: {started}"
    finally:
        may_finish.set()
        poller.stop(join_timeout=3.0)


def test_concurrency_is_bounded_by_the_POOL_not_by_the_source_count(poller, monkeypatch):
    """The milestone in one measurement. Twelve sources, three workers: at most three fetches may be
    on the wire at once. Under thread-per-adapter this number would be twelve."""
    peak, live = [0], [0]
    guard = threading.Lock()

    def _poll(adapter):
        with guard:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.03)
        with guard:
            live[0] -= 1

    sources_ = [_Probe(f"s{i}", interval=0.01) for i in range(12)]
    _run_pool(poller, sources_, workers=3, monkeypatch=monkeypatch, tracker=_poll)
    assert peak[0] <= 3, f"pool of 3 ran {peak[0]} concurrent polls"
    assert peak[0] >= 2, "and it must actually use the pool, not serialise"


def test_no_source_starves_when_workers_are_scarce(poller, monkeypatch):
    """Earliest-due-first is what makes this true. With 8 sources and 2 workers every source must
    get served; a scheduler that always took the first ready entry could let a busy neighbour
    overtake a waiting source indefinitely."""
    sources_ = [_Probe(f"s{i}", interval=0.02) for i in range(8)]
    _run_pool(poller, sources_, workers=2, monkeypatch=monkeypatch, seconds=1.0)
    never = [a.provider for a in sources_ if a.polls == 0]
    assert never == [], f"starved sources: {never}"


# --------------------------------------------------------------------------- isolation

def test_a_raising_source_is_RESCHEDULED_and_does_not_kill_its_worker(poller, monkeypatch):
    """Isolation is per LEASE, not per worker. Under thread-per-adapter a raising adapter cost one
    source its polling. In a pool, a worker that died would cost every source it would have served
    next — so the failure has to be contained at the lease and the source put back on the clock."""
    good = _Probe("good", interval=0.02)
    bad = _Probe("bad", interval=0.02)

    def _poll(adapter):
        adapter.polls += 1
        if adapter.provider == "bad":
            raise RuntimeError("publisher exploded")

    # 2.5 s, not 0.6: `_release` floors the next due time at `max(1.0, wait)`, inherited verbatim
    # from `_run_adapter`'s `self._stop.wait(max(1.0, wait))` so a misconfigured interval cannot
    # spin. A shorter window cannot observe a retry at all, and asserting one would be asserting
    # against the clock rather than against the scheduler.
    _run_pool(poller, [good, bad], workers=1, monkeypatch=monkeypatch, seconds=2.5, tracker=_poll)
    assert bad.polls >= 2, "a raising source must be retried, not dropped from the table"
    assert good.polls >= 2, "and must not have taken the only worker down with it"


def test_stop_does_not_hang_on_workers_parked_until_the_next_due_time(poller, monkeypatch):
    """A pool with nothing due sleeps until the next due time. With a realistic interval that is
    minutes, so `stop()` without a notify would present as a hang rather than a shutdown — the same
    shape of fault as the deadlock M6.1's canary was built for."""
    monkeypatch.setenv("RWE_POLL_WORKERS", "2")
    poller.registry.register(_Probe("slow", interval=600.0))
    monkeypatch.setattr(poller, "poll_adapter_once", lambda a: None)
    poller.start()
    time.sleep(0.15)                                   # let both workers park in _claim
    t0 = time.perf_counter()
    poller.stop(join_timeout=3.0)
    assert time.perf_counter() - t0 < 2.0, "stop() must wake parked workers, not wait them out"
    assert not poller.running


# --------------------------------------------------------------------------- the off switch

def test_zero_workers_is_EXACTLY_the_old_thread_per_adapter_model(poller, monkeypatch):
    """An off switch that is not a rollback. A scheduler change on the ingest path should be
    revertible with one variable, and 0 is the default so deploying M6.3 changes nothing at all."""
    monkeypatch.setenv("RWE_POLL_WORKERS", "0")
    a, b = _Probe("a", interval=600.0), _Probe("b", interval=600.0)
    for x in (a, b):
        poller.registry.register(x)
    monkeypatch.setattr(poller, "poll_adapter_once", lambda ad: None)
    poller.start()
    names = sorted(t.name for t in poller._threads)
    poller.stop(join_timeout=2.0)
    assert names == ["src-probe", "src-probe"], f"expected one thread per adapter, got {names}"


def test_the_default_is_off_so_the_deploy_is_a_no_op(monkeypatch):
    """At ~11 adapters a pool and thread-per-adapter schedule identically, so the safe default for a
    scheduler rewrite is the model already running in production."""
    monkeypatch.delenv("RWE_POLL_WORKERS", raising=False)
    assert sources._poll_workers() == 0


def test_the_pool_never_starts_more_workers_than_there_are_sources(poller, monkeypatch):
    """Idle workers are not free — each one parks on the condition variable and wakes on every
    notify. Asking for 50 workers over 2 sources should give 2."""
    monkeypatch.setenv("RWE_POLL_WORKERS", "50")
    for x in (_Probe("a", interval=600.0), _Probe("b", interval=600.0)):
        poller.registry.register(x)
    monkeypatch.setattr(poller, "poll_adapter_once", lambda ad: None)
    poller.start()
    n = len(poller._threads)
    poller.stop(join_timeout=2.0)
    assert n == 2, f"expected 2 workers for 2 sources, got {n}"


# --------------------------------------------------------------------------- scheduling parity

def test_every_source_is_due_IMMEDIATELY_at_start(poller, monkeypatch):
    """Thread-per-adapter polls once and then sleeps its interval. The pool must meter sources, not
    delay them: a first pass that waited out an interval would make every deploy lose a cycle."""
    sources_ = [_Probe(f"s{i}", interval=600.0) for i in range(4)]
    _run_pool(poller, sources_, workers=4, monkeypatch=monkeypatch, seconds=0.4)
    counts = [a.polls for a in sources_]
    assert all(c == 1 for c in counts), f"every source should have polled exactly once: {counts}"


def test_backoff_is_the_SAME_function_the_per_adapter_loop_uses(poller, monkeypatch):
    """`_release` calls `_effective_interval`, reused verbatim. That function encodes the
    sustained-failure rule GDELT's ~40% load shedding forced; a pool that re-derived its own backoff
    would drift from the path it has to stay equivalent to."""
    a = _Probe("a", interval=100.0)
    poller._consecutive[a.health_key] = 99             # deep in backoff
    lease = sources._Lease(adapter=a, due=0.0, leased=True)
    poller._release(lease)
    expected = poller._effective_interval(a)
    assert lease.leased is False, "the slot must be handed back"
    assert lease.due - time.monotonic() == pytest.approx(expected, rel=0.2)
    assert expected > a.interval(), "and the guard test itself must be in the backoff regime"


def test_the_pool_switch_can_actually_REACH_the_container():
    """`RWE_POLL_WORKERS` is both the enable and the rollback for a scheduler change on the ingest
    path. It is only either of those if compose passes it.

    This omission has now shipped FOUR times in this series — the crawl flag reaching `ingest`
    without its `RWE_CORPUS_SHADOW` precondition, the coalescing interval, the warm's off switch,
    and this one. Each time the code was correct and the lever was inert. Pinned by name rather than
    by a source sweep, for the reason `test_rec_flags_deployable.py` records: most engine-read
    RWE_* vars are legitimately absent from compose, so a general sweep needs an allowlist of
    deliberate absences that would itself rot."""
    import yaml
    doc = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text())
    carriers = [n for n, s in doc["services"].items()
                if "RWE_POLL_WORKERS" in (s.get("environment") or {})]
    assert set(carriers) == {"api", "ingest"}, f"both pollers must carry it, got {carriers}"
