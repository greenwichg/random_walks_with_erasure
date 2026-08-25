"""Per-feed polling schedule: conditional GET, adaptive cadence, per-feed backoff.

The weaknesses these close, each one systemic rather than site-specific:

  * every feed was downloaded IN FULL on every cycle, so freshness was priced in bandwidth;
  * every feed was asked at ONE global interval, so publish rate was ignored in both directions;
  * per-feed ``consecutive_failures`` was persisted and then never consulted by the scheduler,
    so a permanently-broken feed was re-asked at full rate forever.

The load-bearing properties pinned here: off is byte-identical and touches no state; a 304 is a
SUCCESS and not a failure (the classic conditional-GET bug is to treat the cheapest possible
answer as an outage); unknown state always polls rather than silently dropping a feed; and the
adaptive law is bounded at both ends whatever the outcome sequence.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import feed_schedule as fs   # noqa: E402
import rss_ingest            # noqa: E402
import sources               # noqa: E402
import store as store_mod    # noqa: E402

NOW = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("RWE_FEED_SCHEDULER", "RWE_FEED_MIN_INTERVAL", "RWE_FEED_MAX_INTERVAL",
              "RWE_FEED_SPEEDUP", "RWE_FEED_SLOWDOWN", "RWE_RSS_MAX_ARTICLES"):
        monkeypatch.delenv(k, raising=False)


# --------------------------------------------------------------------------- #
# due(): the skip decision, and its fail-open direction.
# --------------------------------------------------------------------------- #
def test_unknown_state_is_always_due():
    """A feed the scheduler has never met must be polled. The opposite default would let a feed
    silently stop being collected, which is indistinguishable from a publisher going quiet."""
    assert fs.due(fs.FeedState(), now=NOW) is True


def test_unparseable_timestamp_is_due_not_skipped():
    assert fs.due(fs.FeedState(next_due_at="not-a-date"), now=NOW) is True
    assert fs.due(fs.FeedState(next_due_at=""), now=NOW) is True


def test_due_respects_a_future_deadline():
    later = (NOW + timedelta(seconds=300)).isoformat()
    assert fs.due(fs.FeedState(next_due_at=later), now=NOW) is False
    assert fs.due(fs.FeedState(next_due_at=later), now=NOW + timedelta(seconds=301)) is True


def test_naive_timestamps_are_treated_as_utc():
    """Stored strings have come from several code paths over time; a naive one must not compare
    as 'far future' and freeze a feed out."""
    naive = (NOW + timedelta(seconds=60)).replace(tzinfo=None).isoformat()
    assert fs.due(fs.FeedState(next_due_at=naive), now=NOW) is False
    assert fs.due(fs.FeedState(next_due_at=naive), now=NOW + timedelta(seconds=61)) is True


# --------------------------------------------------------------------------- #
# validators(): what we send.
# --------------------------------------------------------------------------- #
def test_validators_are_sent_only_when_held():
    assert fs.validators(fs.FeedState()) == {}
    assert fs.validators(fs.FeedState(etag='W/"abc"')) == {"If-None-Match": 'W/"abc"'}
    both = fs.validators(fs.FeedState(etag="e1", last_modified="Mon, 25 Aug 2026 10:00:00 GMT"))
    assert both == {"If-None-Match": "e1",
                    "If-Modified-Since": "Mon, 25 Aug 2026 10:00:00 GMT"}


# --------------------------------------------------------------------------- #
# advance(): the adaptive law.
# --------------------------------------------------------------------------- #
def test_change_shortens_and_quiet_lengthens():
    s = fs.FeedState(interval_s=600.0)
    assert fs.advance(s, changed=True, now=NOW).interval_s == 300.0
    assert fs.advance(s, changed=False, now=NOW).interval_s == 900.0


def test_the_law_is_bounded_at_both_ends_however_long_the_run():
    """Whatever sequence of outcomes arrives, the interval stays inside [floor, ceiling] — the two
    numbers an operator sets and an outsider could verify from our request log."""
    s = fs.FeedState(interval_s=600.0)
    for _ in range(50):
        s = fs.advance(s, changed=True, now=NOW)
    assert s.interval_s == fs.DEFAULT_MIN_INTERVAL
    for _ in range(50):
        s = fs.advance(s, changed=False, now=NOW)
    assert s.interval_s == fs.DEFAULT_MAX_INTERVAL


def test_a_new_feed_starts_somewhere_defensible():
    """No prior interval: at the floor if it brought news, at the geometric middle if it did not —
    rather than inheriting whatever the global sweep happened to be."""
    assert fs.advance(fs.FeedState(), changed=True, now=NOW).interval_s == fs.DEFAULT_MIN_INTERVAL
    quiet = fs.advance(fs.FeedState(), changed=False, now=NOW).interval_s
    assert fs.DEFAULT_MIN_INTERVAL < quiet < fs.DEFAULT_MAX_INTERVAL


def test_failure_backs_off_from_the_current_interval_and_counts_up():
    """The gap this module exists to close: a failing feed backs ITSELF off. Its consecutive
    count is the same one the health table already persisted and the scheduler never read."""
    s = fs.FeedState(interval_s=300.0)
    s1 = fs.advance(s, changed=False, failed=True, now=NOW)
    assert s1.consecutive_failures == 1 and s1.interval_s == 600.0
    s2 = fs.advance(s1, changed=False, failed=True, now=NOW)
    assert s2.consecutive_failures == 2 and s2.interval_s == 2400.0
    for _ in range(10):
        s2 = fs.advance(s2, changed=False, failed=True, now=NOW)
    assert s2.interval_s == fs.DEFAULT_MAX_INTERVAL, "a dead feed walks out to the ceiling"


def test_failure_never_discards_the_validators():
    """A transient 500 must not throw away the ETag that makes the next successful poll cheap."""
    s = fs.FeedState(etag="e1", last_modified="LM", content_sha="sha", interval_s=300.0)
    after = fs.advance(s, changed=False, failed=True, now=NOW)
    assert (after.etag, after.last_modified, after.content_sha) == ("e1", "LM", "sha")


def test_success_clears_the_failure_count():
    s = fs.FeedState(interval_s=300.0, consecutive_failures=4)
    assert fs.advance(s, changed=True, now=NOW).consecutive_failures == 0


def test_advance_carries_forward_validators_it_is_not_given():
    """A 304 need not repeat ETag; passing None must keep what we hold rather than blanking it,
    or every subsequent poll silently becomes unconditional."""
    s = fs.FeedState(etag="e1", last_modified="LM", interval_s=300.0)
    after = fs.advance(s, changed=False, etag=None, last_modified=None, now=NOW)
    assert after.etag == "e1" and after.last_modified == "LM"


def test_env_knobs_move_the_bounds(monkeypatch):
    monkeypatch.setenv("RWE_FEED_MIN_INTERVAL", "60")
    monkeypatch.setenv("RWE_FEED_MAX_INTERVAL", "300")
    s = fs.FeedState(interval_s=100.0)
    assert fs.advance(s, changed=True, now=NOW).interval_s == 60.0
    assert fs.advance(fs.FeedState(interval_s=280.0), changed=False, now=NOW).interval_s == 300.0
    monkeypatch.setenv("RWE_FEED_MIN_INTERVAL", "junk")
    assert fs.min_interval() == fs.DEFAULT_MIN_INTERVAL, "junk falls back"


def test_scheduler_defaults_off(monkeypatch):
    assert fs.enabled() is False
    monkeypatch.setenv("RWE_FEED_SCHEDULER", "1")
    assert fs.enabled() is True


# --------------------------------------------------------------------------- #
# Transport: 304 is a success, and the plain fetcher is untouched.
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, body=b"<rss/>", headers=None):
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_conditional_fetch_sends_validators_and_reports_them_back(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["headers"] = dict(req.headers)
        return _Resp(b"<rss>x</rss>", {"ETag": "e2", "Last-Modified": "LM2"})

    monkeypatch.setattr(rss_ingest.urllib.request, "urlopen", fake_urlopen)
    got = rss_ingest.fetch_feed_conditional("https://x.com/f.xml", etag="e1",
                                            last_modified="LM1")
    # urllib title-cases header keys on the Request object
    hdrs = {k.lower(): v for k, v in seen["headers"].items()}
    assert hdrs["if-none-match"] == "e1" and hdrs["if-modified-since"] == "LM1"
    assert got.not_modified is False and got.data == b"<rss>x</rss>"
    assert got.etag == "e2" and got.last_modified == "LM2"


def test_a_304_is_translated_into_not_modified_not_an_error(monkeypatch):
    """urllib RAISES on 304. A naive port marks the feed unhealthy and backs off the one feed
    behaving perfectly — this is the trap, pinned."""
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError("u", 304, "Not Modified", {}, None)

    monkeypatch.setattr(rss_ingest.urllib.request, "urlopen", fake_urlopen)
    got = rss_ingest.fetch_feed_conditional("https://x.com/f.xml", etag="e1")
    assert got.not_modified is True and got.data == b""
    assert got.etag == "e1", "validators survive a 304 that does not repeat them"


def test_real_errors_still_raise(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError("u", 500, "Boom", {}, None)

    monkeypatch.setattr(rss_ingest.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        rss_ingest.fetch_feed_conditional("https://x.com/f.xml")


def test_plain_fetch_feed_sends_no_validators(monkeypatch):
    """The unscheduled path must be untouched — its signature is a contract the whole test suite
    injects fakes against."""
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["headers"] = {k.lower() for k in req.headers}
        return _Resp()

    monkeypatch.setattr(rss_ingest.urllib.request, "urlopen", fake_urlopen)
    rss_ingest.fetch_feed("https://x.com/f.xml")
    assert "if-none-match" not in seen["headers"]
    assert "if-modified-since" not in seen["headers"]


# --------------------------------------------------------------------------- #
# Store roundtrip.
# --------------------------------------------------------------------------- #
@pytest.fixture()
def st(tmp_path):
    return store_mod.Store(f"sqlite:///{tmp_path}/t.db")


def test_schedule_state_roundtrips_and_defaults_empty(st):
    blank = st.feed_schedule_state("https://x.com/f.xml")
    assert blank["etag"] is None and blank["next_due_at"] is None
    assert blank["consecutive_failures"] == 0
    st.record_feed_schedule("https://x.com/f.xml", etag="e1", last_modified="LM",
                            content_sha="sha", next_due_at="2026-08-25T13:00:00+00:00",
                            interval_s=300.0)
    got = st.feed_schedule_state("https://x.com/f.xml")
    assert got["etag"] == "e1" and got["interval_s"] == 300.0
    assert got["next_due_at"] == "2026-08-25T13:00:00+00:00"


def test_schedule_state_can_be_written_before_any_health_row_exists(st):
    """A feed skipped on its first cycle would otherwise never acquire state and be re-polled
    forever."""
    st.record_feed_schedule("https://new.com/f.xml", next_due_at="2026-08-25T13:00:00+00:00",
                            interval_s=600.0)
    assert st.feed_schedule_state("https://new.com/f.xml")["interval_s"] == 600.0


def test_state_survives_a_health_record(st):
    st.record_feed_schedule("https://x.com/f.xml", etag="e1", interval_s=300.0,
                            next_due_at="2026-08-25T13:00:00+00:00")
    st.record_feed_health("https://x.com/f.xml", ok=True, name="X", stats={"new": 2})
    assert st.feed_schedule_state("https://x.com/f.xml")["etag"] == "e1", \
        "health and schedule share a row without clobbering each other"


# --------------------------------------------------------------------------- #
# The adapter: skip / 304 / full, end to end.
# --------------------------------------------------------------------------- #
_FEED = b"""<?xml version="1.0"?><rss><channel><title>T</title>
<item><title>Story one</title><link>https://p.example.com/a</link>
<pubDate>Mon, 25 Aug 2026 10:00:00 GMT</pubDate></item></channel></rss>"""


def _adapter(monkeypatch, st, fetches, feeds=(("P", "https://p.example.com/f.xml"),)):
    monkeypatch.setenv("RWE_FEED_SCHEDULER", "1")
    monkeypatch.setattr(rss_ingest, "load_feeds", lambda spec=None: list(feeds))
    monkeypatch.setattr(rss_ingest, "fetch_feed_conditional",
                        lambda url, **kw: fetches.pop(0))
    return sources.RSSAdapter()


def test_scheduled_poll_ingests_and_records_state(monkeypatch, st):
    ad = _adapter(monkeypatch, st,
                  [rss_ingest.FeedFetch(data=_FEED, etag="e1", last_modified="LM")])
    agg = ad.poll_once(st, rss_ingest.make_scorer())
    assert agg["feeds"] == 1 and agg["notDue"] == 0 and agg["notModified"] == 0
    state = st.feed_schedule_state("https://p.example.com/f.xml")
    assert state["etag"] == "e1" and state["next_due_at"], "validators + deadline persisted"
    assert state["interval_s"] == fs.DEFAULT_MIN_INTERVAL, "it brought news -> poll it sooner"


def test_a_feed_that_is_not_due_is_never_requested(monkeypatch, st):
    st.record_feed_schedule("https://p.example.com/f.xml", interval_s=600.0,
                            next_due_at=(datetime.now(timezone.utc)
                                         + timedelta(hours=1)).isoformat())
    called = []
    monkeypatch.setenv("RWE_FEED_SCHEDULER", "1")
    monkeypatch.setattr(rss_ingest, "load_feeds",
                        lambda spec=None: [("P", "https://p.example.com/f.xml")])
    monkeypatch.setattr(rss_ingest, "fetch_feed_conditional",
                        lambda url, **kw: called.append(url) or rss_ingest.FeedFetch())
    agg = sources.RSSAdapter().poll_once(st, rss_ingest.make_scorer())
    assert called == [], "no request at all"
    assert agg["notDue"] == 1 and agg["feeds"] == 0


def test_a_304_counts_as_a_healthy_poll_and_widens_the_interval(monkeypatch, st):
    st.record_feed_schedule("https://p.example.com/f.xml", etag="e1", interval_s=600.0)
    ad = _adapter(monkeypatch, st,
                  [rss_ingest.FeedFetch(not_modified=True, etag="e1")])
    seen = []
    agg = ad.poll_once(st, rss_ingest.make_scorer(),
                       on_feed=lambda *a: seen.append(a))
    assert agg["notModified"] == 1 and agg["ok"] == 1 and agg["failed"] == 0
    assert agg["entries"] == 0
    assert seen and seen[0][4] is None, "health sees a SUCCESS, not an error"
    assert st.feed_schedule_state("https://p.example.com/f.xml")["interval_s"] == 900.0


def test_a_failing_feed_backs_itself_off_without_touching_the_others(monkeypatch, st):
    boom = RuntimeError("connection reset")

    def fetch(url, **kw):
        if "bad" in url:
            raise boom
        return rss_ingest.FeedFetch(data=_FEED, etag="ok")

    monkeypatch.setenv("RWE_FEED_SCHEDULER", "1")
    monkeypatch.setattr(rss_ingest, "load_feeds", lambda spec=None: [
        ("Bad", "https://bad.example.com/f.xml"), ("P", "https://p.example.com/f.xml")])
    monkeypatch.setattr(rss_ingest, "fetch_feed_conditional", fetch)
    agg = sources.RSSAdapter().poll_once(st, rss_ingest.make_scorer())
    assert agg["failed"] == 1 and agg["ok"] == 1, "one feed's outage never aborts the rest"
    bad = st.feed_schedule_state("https://bad.example.com/f.xml")
    good = st.feed_schedule_state("https://p.example.com/f.xml")
    assert bad["interval_s"] > good["interval_s"], "the broken feed is the one that backs off"


def test_scheduler_off_uses_the_untouched_path(monkeypatch, st):
    """Off, `poll_once` must reach `ingest_all` — the same call the RSS path has always made —
    and must not read or write a single row of scheduling state."""
    monkeypatch.delenv("RWE_FEED_SCHEDULER", raising=False)
    monkeypatch.setattr(rss_ingest, "load_feeds",
                        lambda spec=None: [("P", "https://p.example.com/f.xml")])
    hit = {}

    def fake_ingest_all(*a, **kw):
        hit["called"] = True
        return {"feeds": 1, "ok": 1, "failed": 0, "entries": 0, "new": 0,
                "duplicates": 0, "skipped": 0, "errors": []}

    monkeypatch.setattr(rss_ingest, "ingest_all", fake_ingest_all)

    def explode(*a, **kw):
        raise AssertionError("the scheduler must not touch the store while it is off")

    monkeypatch.setattr(type(st), "feed_schedule_state", explode)
    agg = sources.RSSAdapter().poll_once(st, rss_ingest.make_scorer())
    assert hit.get("called") is True
    assert "notDue" not in agg, "no scheduler counters in the unscheduled aggregate"
