"""The ingestion-freshness audit — the instrument that says whether we capture the latest news.

Its job is to be able to say NO honestly: an unknown timestamp must read as unknown (never as a
zero lag), an outlet's silence must be judged against its OWN publish rate, and every verdict
line must be derivable from a number the tables print.

Mutation ledger (each check went red against the listed break of audit_freshness.py):
  - lag_minutes returns 0 for an undated row            -> "undated is unknown, not zero" fails
  - negative lag not clamped                            -> "clock skew reads as zero" fails
  - archive counted on created_at instead of published  -> "archive means published before window" fails
  - STALE floor dropped (any gap x3)                    -> "a weekly column is not stale" still passes,
                                                           "a wire quiet for 7 h is stale" passes,
                                                           but "a busy wire quiet for 2 h" fails
  - overflow flag ignores totalPolls > 1                -> "first poll is never overflow" fails
  - re-ingestions/day computed from imported            -> "re-ingestion is the duplicate count" fails
  - GDELT held to the sweep interval                    -> "GDELT is judged on its own interval" fails
  - window_rows filtered on published_at                -> "window is by first-seen" fails
  - scheduler state read off list_feed_health alone     -> "scheduler state comes from the store's
                                                           accessor" fails (0/1 scheduled)
  - suspect threshold applied to created_at age         -> "suspect dates group by feed" fails
"""
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_freshness as af   # noqa: E402
import store as store_mod      # noqa: E402

NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(hours=24)


def _row(pub, published_min_ago, created_min_ago, stype="rss", fetched_min_ago=None):
    return {"publisher": pub, "sourceType": stype,
            "published": None if published_min_ago is None else NOW - timedelta(minutes=published_min_ago),
            "created": NOW - timedelta(minutes=created_min_ago),
            "fetched": NOW - timedelta(minutes=(fetched_min_ago if fetched_min_ago is not None
                                               else created_min_ago))}


# --------------------------------------------------------------------------- #
# lag — the number everything else rests on
# --------------------------------------------------------------------------- #
def test_undated_is_unknown_not_zero():
    assert af.lag_minutes(_row("A", None, 5)) is None
    s = af.lag_report([_row("A", None, 5), _row("A", 20, 5)], since=SINCE)["all"]
    assert s["undated"] == 1 and s["dated"] == 1
    assert s["medianMin"] == 15          # the one dated row, not an average with a phantom zero


def test_clock_skew_reads_as_zero_never_negative():
    # Published "after" first sight, inside the ingest clamp's allowance.
    assert af.lag_minutes(_row("A", published_min_ago=2, created_min_ago=6)) == 0.0


def test_archive_means_published_before_the_window():
    rows = [_row("A", 60 * 24 * 9, 10), _row("A", 30, 10)]
    s = af.lag_report(rows, since=SINCE)["all"]
    assert s["archive"] == 1
    # Both were FIRST SEEN inside the window — that is what makes the old one an archive admission.
    assert s["n"] == 2


def test_lag_buckets_count_strictly_slower_than_the_bound():
    rows = [_row("A", 61, 0), _row("A", 60, 0), _row("A", 361, 0), _row("A", 1441, 0)]
    s = af.lag_report(rows, since=SINCE)["all"]
    assert (s["over60"], s["over360"], s["over1440"]) == (3, 2, 1)


def test_percentile_is_nearest_rank_and_empty_is_none():
    assert af.percentile([], 0.5) is None
    assert af.percentile([5], 0.9) == 5
    assert af.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.9) == 9


# --------------------------------------------------------------------------- #
# outlet flags — silence judged against the outlet's own rate
# --------------------------------------------------------------------------- #
def _outlets(rows, hours=24.0):
    return {o["publisher"]: o for o in af.outlet_report(rows, now=NOW, since=SINCE, hours=hours,
                                                         poll_interval_s=600.0)}


def test_a_busy_wire_quiet_for_seven_hours_is_stale():
    # 48 rows/day -> mean gap 30 min; 3 gaps = 1.5 h, floor 6 h; quiet 7 h -> STALE.
    rows = [_row("Wire", 7 * 60 + i * 30 + 2, 7 * 60 + i * 30) for i in range(48)]
    assert "STALE" in _outlets(rows)["Wire"]["flags"]


def test_a_busy_wire_quiet_for_two_hours_is_not_stale():
    # Same rate, quiet 2 h: past 3 gaps (1.5 h) but under the 6 h floor -> not stale.
    rows = [_row("Wire", 2 * 60 + i * 30 + 2, 2 * 60 + i * 30) for i in range(44)]
    assert "STALE" not in _outlets(rows)["Wire"]["flags"]


def test_a_weekly_column_is_not_stale_on_tuesday():
    # 1 row in a 7-day window -> mean gap 168 h; quiet 20 h is nothing.
    rows = [_row("Column", 20 * 60 + 1, 20 * 60)]
    assert "STALE" not in _outlets(rows, hours=24 * 7)["Column"]["flags"]


def test_laggy_is_a_median_beyond_two_sweeps():
    slow = [_row("Slow", 300 + i, i) for i in range(5)]            # 5 h behind, every row
    fast = [_row("Fast", 5 + i, i) for i in range(5)]
    o = _outlets(slow + fast)
    assert "LAGGY" in o["Slow"]["flags"]
    assert "LAGGY" not in o["Fast"]["flags"]


def test_archive_and_undated_flags_use_shares():
    arch = [_row("Arch", 60 * 24 * 10, i) for i in range(3)] + [_row("Arch", 5, 0)]   # 75% old
    und = [_row("Und", None, i) for i in range(2)] + [_row("Und", 5, 0)]              # 67% undated
    o = _outlets(arch + und)
    assert "ARCHIVE" in o["Arch"]["flags"]
    assert "UNDATED" in o["Und"]["flags"]
    assert "ARCHIVE" not in o["Und"]["flags"] and "UNDATED" not in o["Arch"]["flags"]


def test_outlets_sort_by_volume_and_carry_their_sources():
    rows = [_row("B", 1, 0, "crawl"), _row("A", 1, 0), _row("A", 2, 0, "gdelt")]
    out = af.outlet_report(rows, now=NOW, since=SINCE, hours=24, poll_interval_s=600)
    assert [o["publisher"] for o in out] == ["A", "B"]
    assert out[0]["sources"] == ["gdelt", "rss"]


# --------------------------------------------------------------------------- #
# cadence — held to the interval each source actually runs on
# --------------------------------------------------------------------------- #
def _health(url, ok_min_ago, *, fails=0, imported=0, duplicate=0, polls=5, latency=1000.0,
            name=None):
    return {"feedUrl": url, "name": name or url, "healthy": fails < 3, "consecutiveFailures": fails,
            "totalPolls": polls, "imported": imported, "duplicate": duplicate,
            "lastLatencyMs": latency,
            "lastSuccessAt": None if ok_min_ago is None else (NOW - timedelta(minutes=ok_min_ago)).isoformat()}


def test_source_kind_splits_rss_crawl_and_api_keys():
    assert af.source_kind("https://x.com/rss") == "rss"
    assert af.source_kind("crawl://daily-maverick") == "crawl"
    assert af.source_kind("gdelt://doc") == "api"
    assert af.source_kind(None) == "api"


def test_gdelt_is_judged_on_its_own_interval():
    # 50 min since success: off-schedule against a 600 s sweep (2x = 20 min), on schedule against
    # GDELT's 1800 s (2x = 60 min).
    c = af.cadence_report([_health("gdelt://doc", 50)], now=NOW, poll_interval_s=600,
                          crawl_interval_s=900, gdelt_interval_s=1800)
    assert c["api"]["notOnSchedule"] == 0
    c2 = af.cadence_report([_health("newsapi://top", 50)], now=NOW, poll_interval_s=600,
                           crawl_interval_s=900, gdelt_interval_s=1800)
    assert c2["api"]["notOnSchedule"] == 1


def test_rss_sweep_is_the_sum_of_serial_latencies_and_gap_is_the_newest_success():
    h = [_health("https://a/rss", 4, latency=800), _health("https://b/rss", 5, latency=12000),
         _health("https://c/rss", None)]
    c = af.cadence_report(h, now=NOW, poll_interval_s=600, crawl_interval_s=900)["rss"]
    assert c["tracked"] == 3 and c["neverSucceeded"] == 1
    assert c["lastSweepS"] == pytest.approx(12.8)
    assert c["gapSinceLastSuccessS"] == pytest.approx(4 * 60)


def test_first_poll_is_never_overflow_but_a_repeat_all_new_poll_is():
    first = _health("https://a/rss", 1, imported=30, duplicate=0, polls=1)
    repeat = _health("https://b/rss", 1, imported=30, duplicate=0, polls=7)
    mixed = _health("https://c/rss", 1, imported=30, duplicate=2, polls=7)
    c = af.cadence_report([first, repeat, mixed], now=NOW, poll_interval_s=600,
                          crawl_interval_s=900)["rss"]
    assert [r["feedUrl"] for r in c["overflowSuspects"]] == ["https://b/rss"]


def test_failing_and_off_schedule_rows_are_listed_worst_first():
    h = [_health("https://a/rss", 30, fails=1), _health("https://b/rss", 200, fails=4),
         _health("https://c/rss", 3)]
    c = af.cadence_report(h, now=NOW, poll_interval_s=600, crawl_interval_s=900)["rss"]
    assert [r["feedUrl"] for r in c["failingRows"]] == ["https://b/rss", "https://a/rss"]
    assert c["notOnSchedule"] == 2 and c["unhealthy"] == 1


# --------------------------------------------------------------------------- #
# re-ingestion — the duplicate count, extrapolated, plus the catalog's own evidence
# --------------------------------------------------------------------------- #
def test_reingestion_is_the_duplicate_count_per_day():
    h = [_health("https://a/rss", 1, imported=3, duplicate=27),
         _health("https://b/rss", 1, imported=5, duplicate=15),
         _health("crawl://x", 1, imported=9, duplicate=99)]         # not RSS: not a sweep cost
    r = af.reingest_report(h, [], poll_interval_s=600)
    assert r["feeds"] == 2
    assert r["duplicateLastCycle"] == 42 and r["importedLastCycle"] == 8
    assert r["duplicatesPerDay"] == pytest.approx(42 * 144)
    assert r["duplicateShare"] == pytest.approx(0.84)


def test_retouched_rows_are_those_still_listed_an_hour_after_first_sight():
    rows = [_row("A", 5, 300, fetched_min_ago=10),     # created 5 h ago, touched 10 min ago
            _row("A", 5, 300, fetched_min_ago=290),    # touched 10 min after creation: not retouched
            _row("A", 5, 30)]
    r = af.reingest_report([], rows, poll_interval_s=600)
    assert r["retouched"] == 1
    assert r["longestRetouchH"] == pytest.approx(290 / 60.0)


# --------------------------------------------------------------------------- #
# the window — first-seen, from a real store
# --------------------------------------------------------------------------- #
def test_window_is_by_first_seen_not_by_publication_date():
    d = tempfile.mkdtemp()
    st = store_mod.Store(f"sqlite:///{d}/t.db")

    def add(url, published_min_ago, created_min_ago):
        st.upsert_feed_article(canonical_url=url, url=url, publisher="P", source_publisher=None,
                               title="t", description="", body=None,
                               published_at=(NOW - timedelta(minutes=published_min_ago)).isoformat(),
                               source_feed="f", scored={"lean": 0.0}, source_type="rss")
        with st.session() as s:
            s.get(store_mod.FeedArticle, url).created_at = (
                NOW - timedelta(minutes=created_min_ago)).replace(tzinfo=None)

    add("https://p/old-but-new-to-us", published_min_ago=60 * 24 * 30, created_min_ago=10)
    add("https://p/fresh", published_min_ago=15, created_min_ago=5)
    add("https://p/seen-yesterday", published_min_ago=10, created_min_ago=60 * 30)
    rows = af.window_rows(st, SINCE)
    assert sorted(r["created"] is not None for r in rows) == [True, True]
    assert {r["published"] < SINCE for r in rows} == {True, False}    # the archive row is IN
    # and the timestamps come back aware, so lag arithmetic never mixes naive and aware.
    assert all(r["created"].tzinfo is not None and r["published"].tzinfo is not None for r in rows)


def test_findings_are_derived_from_the_printed_numbers():
    rows = [_row("A", 8 + i, i) for i in range(20)]
    lag = af.lag_report(rows, since=SINCE)
    outlets = af.outlet_report(rows, now=NOW, since=SINCE, hours=24, poll_interval_s=600)
    h = [_health("https://a/rss", 2, imported=2, duplicate=18, latency=400_000)]
    cadence = af.cadence_report(h, now=NOW, poll_interval_s=600, crawl_interval_s=900)
    re_ = af.reingest_report(h, rows, poll_interval_s=600)
    text = "\n".join(af.findings(lag, outlets, cadence, re_, hours=24, poll_interval_s=600))
    assert "within one sweep" in text
    assert "last RSS sweep took 400 s" in text and "real cadence is 1000 s" in text
    assert "90% of entries processed per sweep were already held" in text
    # And the renderer accepts exactly what run() assembles.
    out = af.render(af.config_in_effect(), lag, outlets, cadence, re_,
                    af.findings(lag, outlets, cadence, re_, hours=24, poll_interval_s=600),
                    hours=24, top=5, poll_interval_s=600)
    assert "=== findings ===" in out and "A" in out


def test_scheduler_state_comes_from_the_stores_accessor_not_the_health_listing():
    # The production defect this pins: list_feed_health carries no scheduler columns, so an audit
    # reading only that listing reports "0/N scheduled" on a deployment where the scheduler runs.
    d = tempfile.mkdtemp()
    st = store_mod.Store(f"sqlite:///{d}/t.db")
    st.record_feed_health("https://a/rss", ok=True, name="A", latency_ms=5, stats={})
    st.record_feed_schedule("https://a/rss", etag='"x"', next_due_at=NOW.isoformat(),
                            interval_s=300.0)
    st.record_feed_health("crawl://b", ok=True, name="B", latency_ms=5, stats={})
    health = af.scheduler_state(st, st.list_feed_health())
    rss = next(r for r in health if r["feedUrl"] == "https://a/rss")
    assert rss["intervalS"] == 300.0 and rss["hasValidator"] is True
    assert "intervalS" not in next(r for r in health if r["feedUrl"] == "crawl://b")
    c = af.cadence_report(health, now=NOW, poll_interval_s=600, crawl_interval_s=900)["rss"]
    assert c["scheduled"] == 1 and c["withValidators"] == 1


def test_suspect_dates_group_by_feed_and_name_the_outlets():
    old = 60 * 24 * 400          # published 400 days before first sight
    rows = [dict(_row("CNN", old + i, i), sourceFeed="https://cnn/rss", url=f"https://cnn/{i}")
            for i in range(3)]
    rows.append(dict(_row("BBC", 5, 0), sourceFeed="https://bbc/rss", url="https://bbc/1"))
    # Old by CREATION age but honestly dated: created 20 h ago, published 20 h ago -> not suspect.
    rows.append(dict(_row("AP", 20 * 60, 20 * 60), sourceFeed="https://ap/rss", url="https://ap/1"))
    s = af.suspect_dates_report(rows)
    assert len(s) == 1
    assert s[0]["sourceFeed"] == "https://cnn/rss" and s[0]["rows"] == 3
    assert s[0]["publishers"] == ["CNN"] and s[0]["example"] == "https://cnn/0"
    assert s[0]["medianLagDays"] == pytest.approx(400, abs=0.01)


def test_render_shows_each_outlets_sources_and_the_suspect_section():
    rows = [dict(_row("BBC", 8, 0, "rss"), sourceFeed="f", url="u"),
            dict(_row("BBC", 8, 0, "gnews"), sourceFeed="g", url="u2")]
    lag = af.lag_report(rows, since=SINCE)
    outlets = af.outlet_report(rows, now=NOW, since=SINCE, hours=24, poll_interval_s=600)
    cadence = af.cadence_report([], now=NOW, poll_interval_s=600, crawl_interval_s=900)
    re_ = af.reingest_report([], rows, poll_interval_s=600)
    out = af.render(af.config_in_effect(), lag, outlets, cadence, re_, [], hours=24, top=5,
                    poll_interval_s=600, suspect=[])
    assert "gnews+rss" in out
    assert "=== 4. suspect publication dates" in out and "  none" in out


def test_empty_window_says_so_instead_of_reporting_a_healthy_zero():
    lag = af.lag_report([], since=SINCE)
    v = af.findings(lag, [], {}, af.reingest_report([], [], poll_interval_s=600), hours=24,
                    poll_interval_s=600)
    assert len(v) == 1 and "nothing was first-seen" in v[0]
