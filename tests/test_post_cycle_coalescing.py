"""Coalescing the catalog-wide post-cycle steps to one pass per polling window.

## The measurement that motivated it

Production, 6 h window against 12 h uptime, 150,000-row catalog:

    post-cycle 18,176.9 s + poll 797.6 s / 21,600 s  =  87.8% LOCK OCCUPANCY
    per post-cycle: cleanup 38-50%, refresh 36-50%, warm 11-17%

`poll_adapter_once` holds `self._lock` across BOTH `poll_once` and `_post_cycle`, and both timings
are taken inside it, so that is lock-HELD time rather than queueing. Retention and the hot refresh
each cost a function of catalog size, not of what the adapter brought — kait8 paid 216 s of it for
**2 new articles** while GNews paid 90 s for 10 — and with ~11 adapter threads the catalog paid for
a full pass every time any one of them found a single article.

## What this changes, and what it deliberately does not

Scheduling only. Nothing moves off the lock, no thread is added, and no step is removed. Narrowing
the lock itself is M6; this is the cheap prerequisite.

The tests below are the three ways a coalescing bug hides, each one a failure this codebase has
already had in some form: work silently dropped, a latency promise quietly downgraded, and a
"fix" that cannot be turned off without a rollback.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import sources  # noqa: E402


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture()
def rig(monkeypatch):
    """A poller whose expensive steps are counters, over a controllable clock."""
    monkeypatch.delenv("RWE_POST_CYCLE_MAINTENANCE_INTERVAL", raising=False)
    calls = {"cleanup": 0, "refresh": 0, "warm": 0}

    monkeypatch.setattr(sources.storage_lifecycle, "run_cleanup",
                        lambda *a, **k: calls.__setitem__("cleanup", calls["cleanup"] + 1))
    monkeypatch.setattr(sources.story_service, "request_warm",
                        lambda *a, **k: calls.__setitem__("warm", calls["warm"] + 1))

    clock = _Clock()
    monkeypatch.setattr(sources.time, "monotonic", clock)

    dirty = {"flag": False}
    poller = sources.MultiSourcePoller(
        object(), registry=sources.SourceRegistry(),
        log=lambda *a, **k: None,
        on_cycle=lambda agg: calls.__setitem__("refresh", calls["refresh"] + 1),
        dirty_check=lambda: dirty["flag"])
    return poller, calls, clock, dirty


def _ingest(poller, n=1):
    return poller._post_cycle({"new": n})


def _quiet(poller):
    return poller._post_cycle({"new": 0})


# --------------------------------------------------------------------------- the point of the change

def test_eleven_adapters_in_one_window_pay_for_ONE_pass_not_eleven(rig):
    """The measured waste: every adapter that found a single article triggered a full catalog-wide
    retention pass and a full hot refresh, each superseded by the next before a reader saw it."""
    poller, calls, _clock, _dirty = rig
    for _ in range(11):
        _ingest(poller)
    assert calls["cleanup"] == 1, "retention is catalog-wide; once per window is what it means"
    assert calls["refresh"] == 1, "every rebuild but the last is superseded"


def test_the_locked_phase_no_longer_warms_but_REPORTS_that_a_warm_is_wanted(rig):
    """The warm moved out of the locked phase (M6). `_post_cycle` now answers one question for its
    caller — "did content arrive?" — and `_post_cycle_unlocked` does the work after the lock drops.

    Un-coalesced on purpose, unchanged by the move: the warm and breaking detection are the
    latency-sensitive half, and a breaking story is worth telling people about now."""
    poller, calls, _clock, _dirty = rig
    for _ in range(11):
        assert _ingest(poller) is True, "content arrived — the caller must be told to warm"
    assert calls["warm"] == 0, "and it must NOT have warmed while holding the lock"

    for _ in range(11):
        poller._post_cycle_unlocked({"new": 1})
    assert calls["warm"] == 11, "every ingesting cycle still warms — just off the lock"


def test_a_cycle_that_brought_nothing_reports_no_warm_wanted(rig):
    """A warm rebuilds the story cache from a catalog that did not change. The old code reached the
    same conclusion by returning early; the split has to preserve it explicitly."""
    poller, calls, clock, _dirty = rig
    _ingest(poller)
    clock.advance(sources._maintenance_interval() + 1)
    assert _quiet(poller) is False, "no content, no warm"


def test_the_next_window_pays_again(rig):
    """A throttle that never re-armed would be a leak, not a fix."""
    poller, calls, clock, _dirty = rig
    _ingest(poller)
    clock.advance(sources._maintenance_interval() + 1)
    _ingest(poller)
    assert calls["cleanup"] == 2 and calls["refresh"] == 2


def test_the_first_pass_after_start_is_never_delayed(rig):
    """`_last_maintenance` starts at None rather than 0.0 so "never run" is distinguishable from
    "ran at process start" — otherwise a fresh process would defer its first retention pass."""
    poller, calls, _clock, _dirty = rig
    assert poller._last_maintenance is None
    _ingest(poller)
    assert calls["cleanup"] == 1


# --------------------------------------------------------------------------- the three ways this hides a bug

def test_a_DEFERRED_pass_is_not_lost_when_ingestion_goes_quiet(rig):
    """The failure that would have been invisible. If the last cycle of a window is skipped and
    ingestion then stops, those rows wait for a cycle that never brings anything — precisely the
    "a quiet feed stalls that article's graph entry indefinitely" failure `dirty_check` exists to
    prevent. `_maintenance_pending` outlives the skip and a due pass runs on a quiet cycle."""
    poller, calls, clock, _dirty = rig
    _ingest(poller)                                   # window 1: pass runs
    _ingest(poller)                                   # deferred — this window's rows are pending
    assert calls["cleanup"] == 1
    clock.advance(sources._maintenance_interval() + 1)
    _quiet(poller)                                    # nothing new, but a pass is owed
    assert calls["cleanup"] == 2, "the deferred window must not be stranded"
    assert calls["refresh"] == 2


def test_a_quiet_cycle_with_nothing_owed_does_no_work_at_all(rig):
    """The mirror. Re-running retention on an unchanged catalog every poll would replace one waste
    with another, and `postCycleMs: 0.0` on a `new: 0` cycle is the property that made the original
    measurement legible."""
    poller, calls, clock, _dirty = rig
    _ingest(poller)
    clock.advance(sources._maintenance_interval() + 1)
    _quiet(poller)
    _quiet(poller)
    assert calls["cleanup"] == 1 and calls["refresh"] == 1


def test_a_dirty_nudge_BYPASSES_the_throttle(rig):
    """D6's latency bound is a promise rather than a probability: a request-path producer created
    an article the poller's counters never see, and asking for a check must get a real one. Nudges
    come from reads, not from the eleven pollers, so they cannot reintroduce the per-cycle cost."""
    poller, calls, _clock, dirty = rig
    _ingest(poller)
    assert calls["refresh"] == 1
    _ingest(poller)                                   # throttled
    assert calls["refresh"] == 1
    dirty["flag"] = True
    _quiet(poller)                                    # no new rows, but the request path asked
    assert calls["refresh"] == 2, "a dirty nudge must not wait out the window"


def test_setting_the_interval_to_zero_restores_the_OLD_behaviour_exactly(rig, monkeypatch):
    """An off switch that is not a rollback. If coalescing turns out to be wrong on production, the
    operator sets one variable rather than redeploying a revert — and the variable is read per call,
    so it retunes a running process."""
    poller, calls, _clock, _dirty = rig
    monkeypatch.setenv("RWE_POST_CYCLE_MAINTENANCE_INTERVAL", "0")
    for _ in range(5):
        _ingest(poller)
    assert calls["cleanup"] == 5 and calls["refresh"] == 5


# --------------------------------------------------------------------------- the guard on the guard

def test_the_default_interval_is_a_real_throttle_not_zero():
    """If the default were 0 every test above would still pass while the change did nothing —
    the shape of vacuous guard this repo keeps finding in its own instruments."""
    assert sources._maintenance_interval() == 600.0


def test_feed_growth_inside_a_spent_window_is_DEFERRED_not_dropped(rig):
    """The one behaviour change with a product cost, pinned so it is visible rather than implied.

    A dirty nudge bypasses the throttle and consumes the window; feed growth arriving right after
    it therefore waits. That is coalescing working as designed — but it means new articles reach
    the recommendation corpus up to one window later than before, which is the deliberate trade
    this change makes for the lock. `test_post_cycle_gate_respects_dirty_check` in
    tests/test_corpus_refresh.py failed on exactly this line until it disabled the throttle."""
    poller, calls, clock, dirty = rig
    dirty["flag"] = True
    _quiet(poller)                                    # the nudge runs a pass and spends the window
    assert calls["refresh"] == 1
    dirty["flag"] = False
    _ingest(poller, 3)                                # growth arrives inside the spent window
    assert calls["refresh"] == 1, "deferred"
    assert poller._maintenance_pending is True, "and REMEMBERED — a deferral is not a drop"
    clock.advance(sources._maintenance_interval() + 1)
    _quiet(poller)
    assert calls["refresh"] == 2, "the deferred growth is picked up by the next window"


def test_the_off_switch_can_actually_REACH_the_container():
    """`RWE_POST_CYCLE_MAINTENANCE_INTERVAL=0` is only an off switch if compose passes it. The
    stack's `environment:` is an explicit allowlist with no `env_file:`, and this exact omission has
    now shipped four times — most recently in this same series, where the crawl flag reached
    `ingest` and its `RWE_CORPUS_SHADOW` precondition did not. A rollback lever that cannot be
    pulled is worse than none, because it is believed."""
    import yaml
    doc = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text())
    carriers = [n for n, s in doc["services"].items()
                if "RWE_POST_CYCLE_MAINTENANCE_INTERVAL" in (s.get("environment") or {})]
    assert set(carriers) == {"api", "ingest"}, f"both pollers must carry it, got {carriers}"
