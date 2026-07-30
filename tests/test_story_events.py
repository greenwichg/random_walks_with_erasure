"""Breaking-story detection — the notification platform's first PRODUCER (A5).

The property under test is the one the whole design turns on: `compute_freshness` returns a *level*
that oscillates, and a reader must be told once, on the *edge*. These assert that a story crossing
into "Breaking" emits exactly one event however many times it crosses, plus the quality bar, the
TTL, the kill switch, and the fail-soft posture the ingest loop depends on.
"""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import story_events as se          # noqa: E402
import store as store_mod          # noqa: E402

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    """Detection is OFF by default in production; every test here needs it on except the one that
    asserts the default."""
    monkeypatch.setenv("RWE_BREAKING_NOTIFICATIONS", "1")


def _store():
    return store_mod.Store("sqlite://")


def _story(sid="st_1", *, publishers=4, hours_ago=1.0, articles=3, title="Court issues ruling"):
    """A story dict shaped as `story_service.list_stories` returns one — enough of it for
    `compute_freshness`, which reads `coverage[].publishedAt` and `latest`."""
    latest = NOW - timedelta(hours=hours_ago)
    coverage = [{"publisher": f"P{i}", "url": f"https://x/{sid}/{i}",
                 "publishedAt": (latest - timedelta(minutes=10 * i)).isoformat()}
                for i in range(articles)]
    return {"id": sid, "title": title, "topic": "Politics",
            "publisherCount": publishers, "publishers": [f"P{i}" for i in range(publishers)],
            "latest": latest.isoformat(), "updatedAt": latest.isoformat(), "coverage": coverage}


def _with_stories(monkeypatch, stories):
    monkeypatch.setattr(se.story_service, "list_stories", lambda *a, **k: {"stories": list(stories)})


def _events(st):
    return st.recent_notification_events(now=NOW.isoformat())


def test_a_story_becoming_breaking_emits_exactly_one_event(monkeypatch):
    st = _store()
    _with_stories(monkeypatch, [_story()])
    assert se.detect_breaking_stories(st, now=NOW) == 1
    events = _events(st)
    assert len(events) == 1
    assert events[0]["sourceType"] == "story_breaking" and events[0]["sourceId"] == "st_1"
    assert events[0]["category"] == "breaking"
    assert events[0]["payload"]["title"] == "Court issues ruling"
    assert events[0]["payload"]["publisherCount"] == 4
    assert events[0]["payload"]["band"] == "Breaking"


def test_the_same_story_breaking_again_emits_nothing(monkeypatch):
    """THE property. The band is recomputed every poll cycle and oscillates as the burst window
    slides; the reader must hear about it once. Nothing here remembers that — the event row does."""
    st = _store()
    _with_stories(monkeypatch, [_story()])
    assert se.detect_breaking_stories(st, now=NOW) == 1

    for cycle in range(1, 6):                    # five more poll cycles, still Breaking
        assert se.detect_breaking_stories(st, now=NOW + timedelta(minutes=cycle)) == 0, cycle
    assert len(_events(st)) == 1


def test_a_story_that_cools_and_re_erupts_still_emits_nothing(monkeypatch):
    """The oscillation made explicit: Breaking -> not Breaking -> Breaking again. The middle cycle
    is what a level check would treat as "new" the third time."""
    st = _store()
    _with_stories(monkeypatch, [_story()])
    assert se.detect_breaking_stories(st, now=NOW) == 1

    _with_stories(monkeypatch, [_story(hours_ago=30)])            # cooled: outside the burst window
    assert se.detect_breaking_stories(st, now=NOW + timedelta(hours=1)) == 0

    _with_stories(monkeypatch, [_story()])                        # a second wave of coverage
    assert se.detect_breaking_stories(st, now=NOW + timedelta(hours=2)) == 0, "already announced"
    assert len(_events(st)) == 1


def test_a_story_below_the_publisher_bar_is_not_announced(monkeypatch):
    """A single-source "breaking story" is a rumour. Staying silent costs less than the credibility
    of announcing one."""
    st = _store()
    _with_stories(monkeypatch, [_story(publishers=2)])
    assert se.detect_breaking_stories(st, now=NOW) == 0
    assert _events(st) == []

    monkeypatch.setenv("RWE_BREAKING_MIN_PUBLISHERS", "2")
    assert se.detect_breaking_stories(st, now=NOW) == 1, "the bar is configurable"


def test_a_story_that_is_not_breaking_is_not_announced(monkeypatch):
    st = _store()
    _with_stories(monkeypatch, [_story(hours_ago=48)])            # old: Archived, not Breaking
    assert se.detect_breaking_stories(st, now=NOW) == 0


def test_the_event_carries_a_ttl_so_stale_breaking_stops_being_delivered(monkeypatch):
    """The TTL is what makes the per-day cap a cap rather than a queue: a story held back by
    yesterday's ceiling expires instead of arriving tomorrow as news."""
    st = _store()
    _with_stories(monkeypatch, [_story()])
    se.detect_breaking_stories(st, now=NOW)
    expires = _events(st)[0]["expiresAt"]
    assert expires == (NOW + timedelta(hours=6)).isoformat(), "6h default"

    assert st.recent_notification_events(now=(NOW + timedelta(hours=7)).isoformat()) == []


def test_the_ttl_is_configurable(monkeypatch):
    st = _store()
    monkeypatch.setenv("RWE_BREAKING_TTL_HOURS", "2")
    _with_stories(monkeypatch, [_story()])
    se.detect_breaking_stories(st, now=NOW)
    assert _events(st)[0]["expiresAt"] == (NOW + timedelta(hours=2)).isoformat()


def test_detection_is_off_by_default(monkeypatch):
    """The only Phase A commit that changes what a reader sees, and deploying it must not."""
    monkeypatch.delenv("RWE_BREAKING_NOTIFICATIONS", raising=False)
    st = _store()
    _with_stories(monkeypatch, [_story()])
    assert se.enabled() is False
    assert se.detect_breaking_stories(st, now=NOW) == 0
    assert _events(st) == []

    for off in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("RWE_BREAKING_NOTIFICATIONS", off)
        assert se.enabled() is False, off
        assert se.detect_breaking_stories(st, now=NOW) == 0


def test_several_stories_breaking_at_once_each_get_their_own_event(monkeypatch):
    st = _store()
    _with_stories(monkeypatch, [_story("st_a"), _story("st_b"), _story("st_c")])
    assert se.detect_breaking_stories(st, now=NOW) == 3
    assert {e["sourceId"] for e in _events(st)} == {"st_a", "st_b", "st_c"}


def test_a_story_build_failure_costs_detection_and_nothing_else(monkeypatch):
    """The caller is the ingest poll loop. Ingestion matters far more than an alert about it, so this
    must never raise."""
    st = _store()

    def boom(*a, **k):
        raise RuntimeError("story build failed")

    monkeypatch.setattr(se.story_service, "list_stories", boom)
    logged = []
    assert se.detect_breaking_stories(st, now=NOW, log=lambda e, **f: logged.append(e)) == 0
    assert "breaking_detect_failed" in logged


def test_one_malformed_story_does_not_stop_the_others(monkeypatch):
    """Story dicts come from a builder, not a schema. A bad one is skipped; the batch continues."""
    st = _store()
    _with_stories(monkeypatch, [{"id": "broken"}, _story("st_ok"), None])
    assert se.detect_breaking_stories(st, now=NOW) == 1
    assert [e["sourceId"] for e in _events(st)] == ["st_ok"]


def test_a_story_without_an_id_is_skipped(monkeypatch):
    st = _store()
    bad = _story(); bad["id"] = ""
    _with_stories(monkeypatch, [bad])
    assert se.detect_breaking_stories(st, now=NOW) == 0


def test_detection_logs_what_it_announced(monkeypatch):
    """The operator's signal that the producer is alive — and, if it is noisy, why."""
    st = _store()
    _with_stories(monkeypatch, [_story("st_1")])
    seen = []
    se.detect_breaking_stories(st, now=NOW, log=lambda e, **f: seen.append((e, f)))
    assert seen[0][0] == "breaking_story_detected"
    assert seen[0][1]["storyId"] == "st_1" and seen[0][1]["publisherCount"] == 4


def test_both_pollers_hook_detection():
    """A producer wired to one chassis and not the other would simply never fire in whichever
    deployment ran the other. Both seams are asserted at source level, because no unit test would
    notice the missing one."""
    for path in ("examples/feed_service.py", "examples/sources.py"):
        src = (ROOT / path).read_text()
        assert "story_events.detect_breaking_stories" in src, f"{path} has no detection hook"
