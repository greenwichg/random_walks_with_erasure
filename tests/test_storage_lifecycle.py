"""Tests for the storage lifecycle — examples/{retention_policy,storage_lifecycle}.py.

The contract that matters: derived/operational tables stay bounded, USER data is never pruned, no
row is orphaned, and a pass is incremental, fail-soft, and idempotent.
"""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import location              # noqa: E402
import retention_policy      # noqa: E402
import rss_ingest as ri      # noqa: E402
import storage_lifecycle     # noqa: E402
import store as store_mod    # noqa: E402


@pytest.fixture
def st():
    return store_mod.Store("sqlite://")


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _article(st, cu, *, countries=()):
    st.upsert_feed_article(
        canonical_url=cu, url=cu, publisher="NPR", source_publisher="NPR", title="t",
        description="", body=None, published_at=_iso(1), source_feed="rss://x",
        scored={"article_id": cu, "outlet": "NPR", "lean": 0.0, "title": "t"})
    if countries:
        st.replace_article_event_locations(
            cu, [location.EventLocation(country=c, source="gdelt-gkg") for c in countries])


# --------------------------------------------------------------------------- #
# The policy module: typed, validated, fail-safe.
# --------------------------------------------------------------------------- #
def test_policy_defaults_and_env_override(monkeypatch):
    p = retention_policy.load()
    assert p.scored_cache_days == 30 and p.analytics_event_days == 180
    assert p.rec_event_days == 365 and p.batch_limit == 5000
    assert p.catalog_enabled() is False                      # catalog retention off by default
    monkeypatch.setenv("RWE_RETENTION_MAX_COUNT", "150000")
    monkeypatch.setenv("RWE_RETENTION_SCORED_DAYS", "7")
    p = retention_policy.load()
    assert p.article_max_count == 150000 and p.catalog_enabled() is True
    assert p.scored_cache_days == 7


def test_policy_rejects_junk_and_negatives_by_keeping_the_default(monkeypatch):
    """A malformed retention value must never widen deletion — it falls back to the default."""
    for bad in ("abc", "-5", "", "  "):
        monkeypatch.setenv("RWE_RETENTION_ANALYTICS_DAYS", bad)
        assert retention_policy.load().analytics_event_days == 180
    monkeypatch.setenv("RWE_RETENTION_ANALYTICS_DAYS", "0")   # 0 is meaningful: keep forever
    assert retention_policy.load().analytics_event_days == 0


# --------------------------------------------------------------------------- #
# The leak this closes: side rows orphaned by catalog retention.
# --------------------------------------------------------------------------- #
def test_orphan_event_locations_are_reaped_and_live_ones_kept(st):
    _article(st, "https://a.example/1", countries=("US", "FR"))
    _article(st, "https://a.example/2", countries=("GB",))
    assert st.storage_stats()["rows"]["article_event_locations"] == 3
    st.delete_feed_articles(["https://a.example/1"])          # catalog retention deletes ONLY articles
    assert st.storage_stats()["rows"]["article_event_locations"] == 3   # …leaving 2 orphans behind
    assert st.prune_orphan_event_locations() == 2
    rows = st.storage_stats()["rows"]
    assert rows["article_event_locations"] == 1              # the live article's row survives
    assert st.prune_orphan_event_locations() == 0            # idempotent


# --------------------------------------------------------------------------- #
# Age-based prunes on derived tables.
# --------------------------------------------------------------------------- #
def test_scored_cache_and_analytics_prune_by_age(st):
    with st.session() as s:
        import store as _s
        s.add(_s.ScoredArticle(url="https://old.example", scored="{}",
                               created_at=datetime.now(timezone.utc) - timedelta(days=90)))
        s.add(_s.ScoredArticle(url="https://new.example", scored="{}"))
        s.add(_s.AnalyticsEvent(event="view", server_ts=_iso(400),
                                created_at=datetime.now(timezone.utc) - timedelta(days=400)))
        s.add(_s.AnalyticsEvent(event="view", server_ts=_iso(0)))
        s.commit()
    assert st.prune_scored_cache(30) == 1
    assert st.prune_analytics_events(180) == 1
    rows = st.storage_stats()["rows"]
    assert rows["scored_articles"] == 1 and rows["analytics_events"] == 1
    assert st.prune_scored_cache(0) == 0                     # 0 = keep forever, uniformly
    assert st.prune_analytics_events(0) == 0


def test_rec_events_prune_by_age_keeps_the_metric_window(st):
    uid = st.upsert_user_by_identity("dev", "u1").id
    st.record_recommendations_shown(uid, [("a-old", False)], shown_at=_iso(400))
    st.record_recommendations_shown(uid, [("a-new", True)], shown_at=_iso(10))
    assert st.prune_rec_events(365) == 1
    assert st.storage_stats()["rows"]["rec_events"] == 1     # the in-window event survives


def test_report_snapshots_capped_per_user(st):
    uid = st.upsert_user_by_identity("dev", "u1").id
    other = st.upsert_user_by_identity("dev", "u2").id
    for i in range(12):
        st.save_report(uid, {"mode": "measured", "overall": i})
    for i in range(3):
        st.save_report(other, {"mode": "measured", "overall": i})
    assert st.prune_report_snapshots(5) == 7                 # user1 trimmed 12 -> 5
    assert st.prune_report_snapshots(5) == 0                 # user2 (3) is under the cap: untouched
    assert len(st.list_report_snapshots(other, limit=100)) == 3


# --------------------------------------------------------------------------- #
# The safety contract.
# --------------------------------------------------------------------------- #
def test_cleanup_never_touches_user_data(st):
    """The invariant: a full pass leaves reads, saves, settings, and accounts exactly as they were."""
    uid = st.upsert_user_by_identity("dev", "u1").id
    st.add_read(uid, "https://x.example/read", {"article_id": "https://x.example/read", "outlet": "NPR",
                                                "lean": 0.0, "title": "t", "read_at": _iso(500)})
    st.save_article(uid, "https://x.example/saved", {"article_id": "https://x.example/saved",
                                                     "outlet": "NPR", "lean": 0.0, "title": "s"})
    st.save_settings(uid, {"weeklyReport": True})
    before = st.storage_stats()["rows"]
    storage_lifecycle.run_cleanup(st, policy=retention_policy.RetentionPolicy(
        scored_cache_days=1, analytics_event_days=1, rec_event_days=1,
        snapshots_per_user=1, notifications_per_user=1))
    after = st.storage_stats()["rows"]
    for table in ("reads", "saved_articles", "users"):       # 500-day-old read still present
        assert after[table] == before[table], f"{table} was pruned — user data must be untouchable"
    assert st.get_settings(uid)                              # settings intact


def test_protected_tables_are_declared_and_never_pruned():
    """Documentation-as-a-test: every protected table is named, and no prune method exists for one."""
    protected = retention_policy.PROTECTED_TABLES
    assert {"reads", "saved_articles", "users", "user_settings", "api_tokens"} <= set(protected)
    for table in protected:
        assert not hasattr(store_mod.Store, f"prune_{table}"), f"prune_{table} must not exist"


def test_cleanup_is_incremental_fail_soft_and_idempotent(st, monkeypatch):
    for i in range(10):
        _article(st, f"https://a.example/{i}", countries=("US",))
        st.delete_feed_articles([f"https://a.example/{i}"])   # make 10 orphans
    small = retention_policy.RetentionPolicy(batch_limit=3)
    assert storage_lifecycle.run_cleanup(st, policy=small)["deleted"]["article_event_locations"] == 3
    assert st.storage_stats()["rows"]["article_event_locations"] == 7   # bounded per pass

    # fail-soft: one table's prune raising does not abort the pass or propagate
    def boom(*a, **k):
        raise RuntimeError("disk hiccup")
    monkeypatch.setattr(store_mod.Store, "prune_scored_cache", boom)
    res = storage_lifecycle.run_cleanup(st, policy=small)
    assert "scored_articles" in res["errors"] and res["deleted"]["article_event_locations"] == 3
    monkeypatch.undo()

    while storage_lifecycle.run_cleanup(st, policy=small)["total"]:
        pass
    assert st.storage_stats()["rows"]["article_event_locations"] == 0
    assert storage_lifecycle.run_cleanup(st, policy=small)["total"] == 0   # idempotent no-op


def test_storage_stats_reports_rows_and_size(st):
    _article(st, "https://a.example/1")
    stats = st.storage_stats()
    assert stats["rows"]["feed_articles"] == 1
    assert "dbBytes" in stats                                # None for in-memory, int for a file DB
