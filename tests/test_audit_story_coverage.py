"""The story-coverage auditor (examples/audit_story_coverage.py) — verdicts must name the first
broken link of the Story Match chain, so "why is no card a Story Match?" has a mechanical answer.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_story_coverage as asc   # noqa: E402
import evidence_resolver as er       # noqa: E402
import store as store_mod            # noqa: E402


def _feed(st, url, publisher, title, when="2026-07-10T09:00:00+00:00"):
    st.upsert_feed_article(
        canonical_url=er._canon(url), url=url, publisher=publisher, source_publisher=publisher,
        title=title, description="d", body=None, published_at=when, source_feed="f",
        scored={"article_id": er._canon(url), "outlet": publisher, "category": "Politics",
                "lean": 0.0, "political": True, "title": title})


def _read(st, uid, url, publisher, title):
    st.add_read(uid, er._canon(url),
                {"article_id": er._canon(url), "outlet": publisher, "category": "Politics",
                 "lean": 0.0, "political": True, "title": title})


def test_empty_catalog_reports_no_coverage(tmp_path, capsys):
    st = store_mod.Store(f"sqlite:///{tmp_path / 'a.db'}")
    uid = st.upsert_user_by_identity("dev", "t1").id
    _read(st, uid, "https://ex.com/politics/x", "AP", "Some read")
    er._INDEX_CACHE.update(key=None, index=None)
    cov = asc.audit(st, uid)
    assert cov["catalogArticles"] == 0 and cov["storyClusters"] == 0
    assert cov["verdicts"] == {"read_not_in_any_cluster": 1}
    assert cov["storyMatchPossible"] is False
    # the report states the no-coverage conclusion explicitly, in so many words
    asc.sibling_report(st, uid)
    out = capsys.readouterr().out
    assert "lacks cross-publisher coverage" in out
    assert "corpus coverage, not recommendation logic" in out


def test_report_names_freshness_exclusions(tmp_path, capsys, monkeypatch):
    """A clustered, unread, different-publisher sibling outside the candidate window must appear
    in the report as a FRESHNESS exclusion (never silently missing)."""
    monkeypatch.delenv("RWE_RECS_SOURCE", raising=False)
    st = store_mod.Store(f"sqlite:///{tmp_path / 'r.db'}")
    title = "Landmark ruling reshapes the harbor bridge project"
    _feed(st, "https://a.example.com/story/bridge", "Outlet A", title)
    _feed(st, "https://b.example.com/story/bridge", "Outlet B", title,
          when="2026-07-10T11:00:00+00:00")
    uid = st.upsert_user_by_identity("dev", "t5").id
    _read(st, uid, "https://a.example.com/story/bridge", "Outlet A", title)
    er._INDEX_CACHE.update(key=None, index=None)
    monkeypatch.setenv("RWE_FEED_MAX_AGE_DAYS", "0.001")   # everything ages out of candidacy
    asc.sibling_report(st, uid)
    out = capsys.readouterr().out
    assert "SIBLING:" in out and "Outlet B" in out
    assert "same validated cluster: yes" in out
    assert "excluded by FRESHNESS" in out


def test_topic_overlap_is_not_story_membership(tmp_path):
    """Different events on the same topic must NOT count as sibling coverage."""
    st = store_mod.Store(f"sqlite:///{tmp_path / 'b.db'}")
    _feed(st, "https://a.example.com/p/1", "Outlet A", "Hormuz strait shipping traffic plunges sharply")
    _feed(st, "https://b.example.com/p/2", "Outlet B", "Huckabee warns against testing the president")
    uid = st.upsert_user_by_identity("dev", "t2").id
    _read(st, uid, "https://a.example.com/p/1", "Outlet A",
          "Hormuz strait shipping traffic plunges sharply")
    er._INDEX_CACHE.update(key=None, index=None)
    cov = asc.audit(st, uid)
    assert cov["storyMatchPossible"] is False
    assert cov["verdicts"].get("sibling_available") is None


def test_same_event_sibling_is_detected_with_publisher_gate(tmp_path):
    st = store_mod.Store(f"sqlite:///{tmp_path / 'c.db'}")
    title = "Mayor Adams corruption ruling reshapes the race"
    _feed(st, "https://cnn.example.com/story/adams", "CNN", title)
    _feed(st, "https://fox.example.com/story/adams", "Fox News", title,
          when="2026-07-10T11:00:00+00:00")
    _feed(st, "https://cnn.example.com/story/adams-2", "CNN", title + " again",
          when="2026-07-10T12:00:00+00:00")
    uid = st.upsert_user_by_identity("dev", "t3").id
    _read(st, uid, "https://cnn.example.com/story/adams", "CNN", title)
    er._INDEX_CACHE.update(key=None, index=None)
    cov = asc.audit(st, uid)
    assert cov["multiPublisherClusters"] == 1
    assert cov["verdicts"].get("sibling_available") == 1
    assert cov["storyMatchPossible"] is True
    row = next(p for p in cov["perRead"] if p["verdict"] == "sibling_available")
    pubs = {m["publisher"] for m in row["siblings"]}
    assert pubs == {"Fox News"}                      # the same-publisher CNN sibling never counts


def test_stale_siblings_are_named(tmp_path, monkeypatch):
    """A sibling still inside the story cluster's own time window can nonetheless be outside the
    recommendation freshness window (C4) — the auditor must name that as the blocker, since the
    sibling exists in the cluster but can never be a candidate."""
    st = store_mod.Store(f"sqlite:///{tmp_path / 'd.db'}")
    title = "Landmark ruling on the harbor bridge project"
    _feed(st, "https://a.example.com/story/bridge", "Outlet A", title)
    _feed(st, "https://b.example.com/story/bridge", "Outlet B", title,
          when="2026-07-10T11:00:00+00:00")
    uid = st.upsert_user_by_identity("dev", "t4").id
    _read(st, uid, "https://a.example.com/story/bridge", "Outlet A", title)
    er._INDEX_CACHE.update(key=None, index=None)
    monkeypatch.delenv("RWE_FEED_MAX_AGE_DAYS", raising=False)   # default 60-day window
    fresh = asc.audit(st, uid)
    assert fresh["verdicts"].get("sibling_available") == 1
    # a tiny freshness window ages the sibling out of candidacy while the cluster still holds
    monkeypatch.setenv("RWE_FEED_MAX_AGE_DAYS", "0.001")
    er._INDEX_CACHE.update(key=None, index=None)
    stale = asc.audit(st, uid)
    assert stale["verdicts"].get("siblings_all_stale") == 1
    assert stale["storyMatchPossible"] is False
