"""Commit C4 — freshness gates candidate selection; publishedAt is never fabricated for real articles.

Proves: the ``RWE_FEED_MAX_AGE_DAYS`` window (default 60, ``0`` disables) keeps stale articles out
of BOTH corpus-composition paths (the startup ``feed_source.export_catalog_csv`` export and the
hot-refresh ``corpus_validation.build_candidate``) while read articles stay exempt (graph
connectivity) and storage is untouched; and that a served article's ``publishedAt`` is the real
publication timestamp when one exists — the deterministic ``_iso_recent`` estimate survives ONLY
for demo/synthetic corpus items that have no verified URL anywhere.
"""

import csv
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import corpus_health as ch      # noqa: E402
import corpus_validation as cv  # noqa: E402
import feed_source              # noqa: E402
import store as store_mod       # noqa: E402

NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)


def _art(url, days_ago=0.0, publisher="Pub", published=True, fetched_days_ago=None):
    a = {"canonicalUrl": url, "url": url, "publisher": publisher, "title": f"t {url}",
         "scored": {"outlet": publisher, "category": "Politics", "lean": 0.0, "political": True,
                    "title": f"t {url}"},
         "publishedAt": (NOW - timedelta(days=days_ago)).isoformat() if published else None,
         "fetchedAt": (NOW - timedelta(days=(fetched_days_ago if fetched_days_ago is not None
                                             else days_ago))).isoformat()}
    return a


# --------------------------------------------------------------------------- #
# The env window.
# --------------------------------------------------------------------------- #
def test_window_defaults_to_60_days(monkeypatch):
    monkeypatch.delenv("RWE_FEED_MAX_AGE_DAYS", raising=False)
    assert ch.feed_max_age_days() == 60.0


def test_window_zero_disables_and_junk_falls_back(monkeypatch):
    monkeypatch.setenv("RWE_FEED_MAX_AGE_DAYS", "0")
    assert ch.feed_max_age_days() is None
    monkeypatch.setenv("RWE_FEED_MAX_AGE_DAYS", "-5")
    assert ch.feed_max_age_days() is None
    monkeypatch.setenv("RWE_FEED_MAX_AGE_DAYS", "ninety")
    assert ch.feed_max_age_days() == 60.0            # unparseable -> the default window
    monkeypatch.setenv("RWE_FEED_MAX_AGE_DAYS", "7.5")
    assert ch.feed_max_age_days() == 7.5


# --------------------------------------------------------------------------- #
# The shared filter.
# --------------------------------------------------------------------------- #
def test_fresh_articles_filters_by_publication_age():
    arts = [_art("https://ex.com/new", days_ago=3), _art("https://ex.com/old", days_ago=90)]
    kept = ch.fresh_articles(arts, now=NOW, max_age_days=60)
    assert [a["canonicalUrl"] for a in kept] == ["https://ex.com/new"]


def test_fresh_articles_undated_uses_fetch_time():
    fresh_undated = _art("https://ex.com/u1", published=False, fetched_days_ago=2)
    stale_undated = _art("https://ex.com/u2", published=False, fetched_days_ago=120)
    kept = ch.fresh_articles([fresh_undated, stale_undated], now=NOW, max_age_days=60)
    assert [a["canonicalUrl"] for a in kept] == ["https://ex.com/u1"]


def test_fresh_articles_unparseable_time_is_kept():
    a = _art("https://ex.com/x", days_ago=1)
    a["publishedAt"] = "not-a-date"
    a["fetchedAt"] = "also-junk"
    assert ch.fresh_articles([a], now=NOW, max_age_days=60) == [a]   # staleness can't be proven


def test_fresh_articles_exempt_and_disabled():
    old = _art("https://ex.com/read-old", days_ago=200)
    assert ch.fresh_articles([old], now=NOW, max_age_days=60,
                             exempt={"https://ex.com/read-old"}) == [old]
    assert ch.fresh_articles([old], now=NOW, max_age_days=0) == [old]   # 0/None disables


# --------------------------------------------------------------------------- #
# Hot-refresh path: corpus_validation.build_candidate.
# --------------------------------------------------------------------------- #
def test_build_candidate_excludes_stale(monkeypatch):
    monkeypatch.delenv("RWE_FEED_MAX_AGE_DAYS", raising=False)      # default 60
    arts = [_art("https://ex.com/a", days_ago=1), _art("https://ex.com/b", days_ago=61),
            _art("https://ex.com/c", days_ago=59)]
    got = {a["canonicalUrl"] for a in cv.build_candidate(arts, now=NOW)}
    assert got == {"https://ex.com/a", "https://ex.com/c"}


def test_build_candidate_gate_runs_before_publisher_cap():
    """The cap must be spent on recommendable (fresh) articles: a stale row may not consume a
    publisher slot and push a fresh article out."""
    arts = [_art("https://ex.com/stale", days_ago=90, publisher="One"),
            _art("https://ex.com/f1", days_ago=1, publisher="One"),
            _art("https://ex.com/f2", days_ago=2, publisher="One")]
    got = [a["canonicalUrl"] for a in cv.build_candidate(arts, max_per_publisher=2, now=NOW,
                                                         max_age_days=60)]
    assert got == ["https://ex.com/f1", "https://ex.com/f2"]


def test_build_candidate_disabled_keeps_everything():
    arts = [_art("https://ex.com/a", days_ago=400), _art("https://ex.com/b", days_ago=1)]
    got = {a["canonicalUrl"] for a in cv.build_candidate(arts, now=NOW, max_age_days=0)}
    assert got == {"https://ex.com/a", "https://ex.com/b"}


# --------------------------------------------------------------------------- #
# Startup path: feed_source.export_catalog_csv (through a real store).
# --------------------------------------------------------------------------- #
def _seed(st, url, days_ago, publisher="Pub"):
    a = _art(url, days_ago=days_ago, publisher=publisher)
    st.upsert_feed_article(
        canonical_url=url, url=url, publisher=publisher, source_publisher=publisher,
        title=a["title"], description="d", body=None, published_at=a["publishedAt"],
        source_feed="f", scored=a["scored"])


def test_export_catalog_csv_drops_stale_keeps_read(tmp_path, monkeypatch):
    monkeypatch.delenv("RWE_FEED_MAX_AGE_DAYS", raising=False)      # default 60
    st = store_mod.Store(f"sqlite:///{tmp_path / 'f.db'}")
    _seed(st, "https://ex.com/fresh", days_ago=2)
    _seed(st, "https://ex.com/stale", days_ago=120)
    _seed(st, "https://ex.com/stale-read", days_ago=120)
    uid = st.upsert_user_by_identity("dev", "freshness-test").id
    st.add_read(uid, "https://ex.com/stale-read",
                {"article_id": "https://ex.com/stale-read", "outlet": "Pub",
                 "category": "Politics", "lean": 0.0, "political": True})
    out = tmp_path / "corpus.csv"
    feed_source.export_catalog_csv(st, str(out))
    urls = {row["url"] for row in csv.DictReader(open(out, encoding="utf-8"))}
    assert "https://ex.com/fresh" in urls
    assert "https://ex.com/stale" not in urls                      # stale: never a candidate
    assert "https://ex.com/stale-read" in urls                     # read-demand exemption
    assert st.count_feed_articles() == 3                           # storage untouched


def test_export_catalog_csv_gate_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("RWE_FEED_MAX_AGE_DAYS", "0")
    st = store_mod.Store(f"sqlite:///{tmp_path / 'g.db'}")
    _seed(st, "https://ex.com/ancient", days_ago=500)
    out = tmp_path / "corpus.csv"
    feed_source.export_catalog_csv(st, str(out))
    urls = {row["url"] for row in csv.DictReader(open(out, encoding="utf-8"))}
    assert "https://ex.com/ancient" in urls


# --------------------------------------------------------------------------- #
# publishedAt truthfulness (the serializer + the catalog join).
# --------------------------------------------------------------------------- #
def test_serializer_never_fabricates_for_real_articles():
    import api_server
    be = object.__new__(api_server.Backend)                        # serializer-only surface
    be.url_by_id = {"Q0": "https://ex.com/real"}
    be.lean_tau = 0.5
    emotion = {"fear": 0, "outrage": 0, "analysis": 0, "positive": 0, "neutral": 1}
    real = be._article_payload(item_id="Q0", headline="h", outlet="Pub", topic="Politics",
                               lean=0.0, register=None, emotion=emotion, confidence=None,
                               outlet_lean={})
    assert real["publishedAt"] == ""                               # real article: join or nothing
    read = be._article_payload(item_id="https://ex.com/read", headline="h", outlet="Pub",
                               topic="", lean=0.0, register=None, emotion=emotion,
                               confidence=None, outlet_lean={})
    assert read["publishedAt"] == ""                               # a stored read is real too
    demo = be._article_payload(item_id="N42", headline="h", outlet="Pub", topic="", lean=0.0,
                               register=None, emotion=emotion, confidence=None, outlet_lean={})
    assert demo["publishedAt"] != ""                               # demo/synthetic keeps estimate
    datetime.fromisoformat(demo["publishedAt"])                    # ... and it parses


def test_feed_article_media_carries_real_published_at(tmp_path):
    st = store_mod.Store(f"sqlite:///{tmp_path / 'm.db'}")
    _seed(st, "https://ex.com/dated", days_ago=5)
    st.upsert_feed_article(                                        # no publication date at all
        canonical_url="https://ex.com/undated", url="https://ex.com/undated", publisher="Pub",
        source_publisher="Pub", title="t", description="d", body=None, published_at=None,
        source_feed="f", scored={"outlet": "Pub", "category": "", "lean": 0.0, "political": False})
    m = st.feed_article_media(["https://ex.com/dated", "https://ex.com/undated"])
    assert m["https://ex.com/dated"]["publishedAt"] == (NOW - timedelta(days=5)).isoformat()
    # undated rows fall back to the observed fetch time (real, never fabricated)
    got = m["https://ex.com/undated"]["publishedAt"]
    assert got
    datetime.fromisoformat(got)                                    # parseable ISO timestamp
    assert "image" not in m["https://ex.com/undated"]              # imageless row: timestamp only
