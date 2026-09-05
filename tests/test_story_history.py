"""story_history — served builds recorded as deltas; the builder and the served list untouched."""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import rss_ingest  # noqa: E402
import store as store_mod  # noqa: E402
import story_history  # noqa: E402
import story_service  # noqa: E402


@pytest.fixture
def st():
    return store_mod.Store("sqlite:///:memory:")


def _story(sid, members, *, title=None, topic="World", left=1, center=1, right=0, tier_b=()):
    """A served Story dict in the shape ``_build_story`` emits (the fields history reads)."""
    cov = [{"url": u, "publisher": p, "headline": f"{p} on {sid}", "publishedAt": f"2026-09-0{i+1}T00:00:00+00:00",
            "lean": 0.0, "leanBucket": "center", **({"tierB": True} if u in tier_b else {})}
           for i, (u, p) in enumerate(members)]
    return {"id": sid, "title": title or f"Story {sid}", "summary": "s", "topic": topic,
            "totalCoverage": len(cov), "publisherCount": len({p for _, p in members}),
            "publishers": sorted({p for _, p in members}), "attachedCoverage": len(tier_b),
            "distribution": {"left": left, "center": center, "right": right},
            "blindspotSide": None, "blindspotWithheld": False, "clusterTrust": "ok",
            "geoCoherence": None, "countries": ["GB"], "primaryCountry": "GB",
            "earliest": cov[0]["publishedAt"], "latest": cov[-1]["publishedAt"], "image": None,
            "tags": [{"name": "westminster", "label": "Westminster", "source": "direct", "score": 0.2}],
            "coverage": cov}


def _record(st, stories, at=None):
    return story_history.record_build(st, stories, build_version="1", config_hash="cfg",
                                      registry_version="sha256:test",
                                      built_at=at or datetime.now(timezone.utc).isoformat())


def test_first_build_creates_stories_snapshots_and_membership(st):
    a = _story("st_a", [("https://x/1", "BBC"), ("https://x/2", "NPR")])
    out = _record(st, [a])
    assert out["new_stories"] == 1 and out["changed"] == 1 and out["joins"] == 2 and out["leaves"] == 0
    h = st.story_history("st_a")
    assert h["story"]["status"] == "active" and h["story"]["snapshots"] == 1
    assert h["story"]["representativeUrl"] == "https://x/1"
    assert len(h["snapshots"]) == 1 and h["snapshots"][0]["distribution"] == {"left": 1, "center": 1, "right": 0}
    assert h["snapshots"][0]["tags"] == ["westminster"]
    assert sorted(m["url"] for m in h["membership"]) == ["https://x/1", "https://x/2"]
    assert all(m["leftBuild"] is None for m in h["membership"])
    b = st.story_builds(limit=1)[0]
    assert b["buildVersion"] == "1" and b["configHash"] == "cfg" and b["stories"] == 1


def test_an_unchanged_build_writes_no_snapshot_but_stamps_the_story(st):
    a = _story("st_a", [("https://x/1", "BBC"), ("https://x/2", "NPR")])
    _record(st, [a])
    out = _record(st, [a])
    assert out["changed"] == 0 and out["joins"] == 0 and out["leaves"] == 0 and out["new_stories"] == 0
    h = st.story_history("st_a")
    assert len(h["snapshots"]) == 1 and h["story"]["lastBuildId"] == 2


def test_growth_join_leave_close_and_reopen(st):
    a = _story("st_a", [("https://x/1", "BBC"), ("https://x/2", "NPR")])
    b = _story("st_b", [("https://y/1", "CNN"), ("https://y/2", "Fox")])
    _record(st, [a, b])
    a2 = _story("st_a", [("https://x/1", "BBC"), ("https://x/2", "NPR"), ("https://x/3", "Reuters")])
    out = _record(st, [a2])                                   # b no longer served
    assert out["joins"] == 1 and out["leaves"] == 2 and out["closed_stories"] == 1 and out["changed"] == 1
    assert st.story_record("st_b")["status"] == "closed"
    assert st.story_record("st_b")["closedAt"]
    hb = st.story_history("st_b")
    assert all(m["leftBuild"] == 2 for m in hb["membership"])
    ha = st.story_history("st_a")
    assert len(ha["snapshots"]) == 2 and ha["snapshots"][-1]["totalCoverage"] == 3
    # b comes back (the window admitted it again): reopened, its members rejoin
    out = _record(st, [a2, b])
    assert st.story_record("st_b")["status"] == "active" and st.story_record("st_b")["closedAt"] is None
    assert out["joins"] == 2 and out["new_stories"] == 0


def test_merge_records_the_successor_and_split_records_the_origin(st):
    a = _story("st_a", [("https://x/1", "BBC"), ("https://x/2", "NPR")])
    b = _story("st_b", [("https://y/1", "CNN"), ("https://y/2", "Fox")])
    _record(st, [a, b])
    merged = _story("st_a", [("https://x/1", "BBC"), ("https://x/2", "NPR"),
                             ("https://y/1", "CNN"), ("https://y/2", "Fox")])
    out = _record(st, [merged])
    assert out["closed_stories"] == 1
    rb = st.story_record("st_b")
    assert rb["status"] == "merged" and rb["successorId"] == "st_a"
    # later the merged story splits: most of b's old members form a new id
    a3 = _story("st_a", [("https://x/1", "BBC"), ("https://x/2", "NPR")])
    c = _story("st_c", [("https://y/1", "CNN"), ("https://y/2", "Fox")])
    out = _record(st, [a3, c])
    assert out["new_stories"] == 1
    assert st.story_record("st_c")["originId"] == "st_a"


def test_tier_b_attachment_is_recorded_as_attached_and_never_as_a_vote(st):
    a = _story("st_a", [("https://x/1", "BBC"), ("https://x/2", "NPR"), ("https://z/1", "Blog")],
               tier_b=("https://z/1",))
    _record(st, [a])
    h = st.story_history("st_a")
    attached = [m for m in h["membership"] if m["attached"]]
    assert [m["url"] for m in attached] == ["https://z/1"]
    assert h["snapshots"][0]["attachedCoverage"] == 1


def test_disabled_records_nothing(st, monkeypatch):
    monkeypatch.setenv("RWE_STORY_HISTORY", "0")
    assert _record(st, [_story("st_a", [("https://x/1", "BBC"), ("https://x/2", "NPR")])]) is None
    assert st.story_history_stats()["stories"] == 0


def test_older_than_and_prune_keep_the_open_state(st):
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    a = _story("st_a", [("https://x/1", "BBC"), ("https://x/2", "NPR")])
    b = _story("st_b", [("https://y/1", "CNN"), ("https://y/2", "Fox")])
    _record(st, [a, b], at=old)
    _record(st, [a], at=old)                                  # b closes 40 days ago
    _record(st, [a])                                           # a still served today (no change)
    rows = st.story_history_older_than(30)
    assert [r["storyId"] for r in rows["stories"]] == ["st_b"]
    assert len(rows["membership"]) == 2 and len(rows["snapshots"]) == 2 and len(rows["builds"]) == 2
    deleted = st.prune_story_history(30)
    assert deleted == 2 + 2 + 1 + 2
    assert st.story_record("st_a")["status"] == "active" and st.story_record("st_b") is None
    assert len(st.story_history("st_a")["membership"]) == 2      # open rows untouched
    assert st.prune_story_history(0) == 0                        # 0 = keep forever


def test_snapshot_fingerprint_ignores_nothing_that_is_served():
    a = _story("st_a", [("https://x/1", "BBC"), ("https://x/2", "NPR")])
    fp = story_history.fingerprint(story_history.snapshot_of(a))
    assert fp == story_history.fingerprint(story_history.snapshot_of(dict(a)))
    changed = dict(a, blindspotSide="right")
    assert fp != story_history.fingerprint(story_history.snapshot_of(changed))


def test_the_served_list_is_identical_with_history_on_and_off(st, monkeypatch):
    """The hook is bookkeeping: same rows in, same stories out, whatever the flag says."""
    scorer = rss_ingest.make_scorer()
    E = rss_ingest.FeedEntry
    rss_ingest.ingest_entries([
        E(url="https://www.bbc.co.uk/news/articles/abc", title="Prime minister resigns after vote",
          published_at="2026-09-01T10:00:00+00:00", publisher_hint="bbc.co.uk"),
        E(url="https://www.theguardian.com/politics/pm-resigns", title="Prime minister resigns after confidence vote",
          published_at="2026-09-01T11:00:00+00:00", publisher_hint="theguardian.com"),
    ], "BBC", "https://feeds.bbci.co.uk/news/rss.xml", scorer, st, source_type="rss")
    monkeypatch.setenv("RWE_STORY_HISTORY", "0")
    story_service.clear_cache()
    off = story_service.list_stories(st, limit=10)["stories"]
    assert st.story_history_stats()["story_builds"] == 0
    monkeypatch.setenv("RWE_STORY_HISTORY", "1")
    story_service.clear_cache()
    on = story_service.list_stories(st, limit=10)["stories"]
    assert [(s["id"], s["title"], s["totalCoverage"]) for s in on] == \
        [(s["id"], s["title"], s["totalCoverage"]) for s in off]
    assert st.story_history_stats()["story_builds"] == 1
    h = st.story_history(on[0]["id"])
    assert h and len(h["membership"]) == on[0]["totalCoverage"]
    assert all(m["articleId"] for m in h["membership"])           # resolved through the aliases
    assert st.story_builds(limit=1)[0]["registryVersion"].startswith("sha256:")
