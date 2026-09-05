"""archive.py — partitions + manifests, and archive-before-delete that fails closed."""

import gzip
import json
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import archive  # noqa: E402
import archive_export  # noqa: E402
import corpus_health  # noqa: E402
import retention_policy  # noqa: E402
import rss_ingest  # noqa: E402
import storage_lifecycle  # noqa: E402
import store as store_mod  # noqa: E402
import story_history  # noqa: E402


@pytest.fixture
def st():
    return store_mod.Store("sqlite:///:memory:")


def _ingest(st, n=3, *, days_ago=0, body="FULL TEXT"):
    scorer = rss_ingest.make_scorer()
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    entries = [rss_ingest.FeedEntry(url=f"https://www.bbc.co.uk/news/articles/a{i}", title=f"Headline {i} about a vote",
                                    published_at=when, body=body, publisher_hint="bbc.co.uk")
               for i in range(n)]
    rss_ingest.ingest_entries(entries, "BBC", "https://feeds.bbci.co.uk/news/rss.xml", scorer, st,
                              source_type="rss")
    return [e.url for e in entries]


def test_write_partition_manifest_and_verify(tmp_path):
    m = archive.write_partition(str(tmp_path), "things", [{"a": 1}, {"b": float("nan")}],
                                versions={"x": "1"})
    assert m["rows"] == 2 and m["schema"] == "v1" and m["versions"] == {"x": "1"}
    part = tmp_path / "v1" / "things" / f"dt={m['day']}" / m["file"]
    assert part.exists() and not list(tmp_path.rglob("*.tmp"))
    with gzip.open(part, "rt") as f:
        lines = [json.loads(l) for l in f]
    assert lines[0] == {"a": 1}
    manifests = archive.list_manifests(str(tmp_path))
    assert len(manifests) == 1 and archive.verify(manifests[0]["path"])
    part.write_bytes(b"corrupt")
    assert not archive.verify(manifests[0]["path"])
    assert archive.write_partition(str(tmp_path), "things", [])["rows"] == 0


def test_archive_articles_drops_body_and_carries_provenance(st, tmp_path):
    urls = _ingest(st)
    m = archive.archive_articles(st, urls, root=str(tmp_path))
    assert m["rows"] == 3 and m["versions"]["scorer"] == "1"
    with gzip.open(tmp_path / "v1" / "articles" / f"dt={m['day']}" / m["file"], "rt") as f:
        rows = [json.loads(l) for l in f]
    assert all("body" not in r for r in rows)
    assert all(r["provenance"][0]["channel"] == "rss" for r in rows)
    assert all(r["articleId"] and r["licenceClass"] == "metadata_public" for r in rows)


def test_root_for_prefers_env_then_the_data_volume(tmp_path, monkeypatch):
    monkeypatch.delenv("RWE_ARCHIVE_DIR", raising=False)
    assert archive.root_for(store_mod.Store("sqlite:///:memory:")) is None
    st = store_mod.Store(f"sqlite:///{tmp_path}/ih.db")
    assert archive.root_for(st) == str(tmp_path / "archive")
    monkeypatch.setenv("RWE_ARCHIVE_DIR", "/elsewhere")
    assert archive.root_for(st) == "/elsewhere"
    with pytest.raises(archive.ArchiveUnavailable):
        monkeypatch.delenv("RWE_ARCHIVE_DIR")
        archive.archive_articles(store_mod.Store("sqlite:///:memory:"), ["x"])


def test_retention_archives_before_it_deletes(st, tmp_path, monkeypatch):
    urls = _ingest(st, n=6, days_ago=100)
    monkeypatch.setenv("RWE_ARCHIVE_ON_PRUNE", "1")
    monkeypatch.setenv("RWE_ARCHIVE_DIR", str(tmp_path))
    monkeypatch.setenv("RWE_FEED_MIN_ARTICLES", "1")
    res = corpus_health.run_retention(st, max_count=2, thresholds={"minArticles": 1, "minPublishers": 1,
                                                                    "minPerBucket": 0, "minFresh": 0,
                                                                    "freshMaxAgeDays": 365})
    assert res["pruned"] == 4 and st.count_feed_articles() == 2
    manifests = archive.list_manifests(str(tmp_path))
    assert sum(m["rows"] for m in manifests if m["kind"] == "articles") == 4


def test_retention_keeps_every_row_when_the_archive_fails(st, tmp_path, monkeypatch):
    _ingest(st, n=6, days_ago=100)
    monkeypatch.setenv("RWE_ARCHIVE_ON_PRUNE", "1")
    monkeypatch.setenv("RWE_ARCHIVE_DIR", str(tmp_path))

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(archive, "archive_articles", boom)
    events = []
    res = corpus_health.run_retention(st, max_count=2, log=lambda level, event, **f: events.append(event),
                                      thresholds={"minArticles": 1, "minPublishers": 1, "minPerBucket": 0,
                                                  "minFresh": 0, "freshMaxAgeDays": 365})
    assert res["pruned"] == 0 and st.count_feed_articles() == 6
    assert "feed_retention_archive_failed" in events


def test_tier_prune_with_a_callback_selects_then_deletes(st, monkeypatch):
    urls = _ingest(st, n=3, days_ago=100)
    seen = []
    n = st.prune_tier_articles_older_than(["bbc"], 30, before_delete=seen.append)
    assert n == 3 and len(seen) == 1 and len(seen[0]) == 3
    assert st.count_feed_articles() == 0
    _ingest(st, n=2, days_ago=100)

    def refuse(_urls):
        raise OSError("no archive")
    with pytest.raises(OSError):
        st.prune_tier_articles_older_than(["bbc"], 30, before_delete=refuse)
    assert st.count_feed_articles() == 2                      # fail closed: nothing deleted
    assert st.prune_tier_articles_older_than([], 30, before_delete=seen.append) == 0


def test_cleanup_archives_story_history_before_pruning_it(st, tmp_path, monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    a = {"id": "st_a", "title": "A", "summary": "", "topic": "", "totalCoverage": 2, "publisherCount": 2,
         "publishers": ["BBC", "NPR"], "distribution": {"left": 1, "center": 1, "right": 0},
         "coverage": [{"url": "https://x/1", "publisher": "BBC", "publishedAt": old, "headline": "h"},
                      {"url": "https://x/2", "publisher": "NPR", "publishedAt": old, "headline": "h"}]}
    story_history.record_build(st, [a], build_version="1", config_hash="c", built_at=old)
    story_history.record_build(st, [], build_version="1", config_hash="c", built_at=old)   # a closes
    monkeypatch.setenv("RWE_ARCHIVE_ON_PRUNE", "1")
    monkeypatch.setenv("RWE_ARCHIVE_DIR", str(tmp_path))
    res = storage_lifecycle.run_cleanup(st, policy=retention_policy.RetentionPolicy(story_history_days=30))
    assert res["deleted"]["story_history"] >= 5 and not res["errors"]
    kinds = {m["kind"]: m["rows"] for m in archive.list_manifests(str(tmp_path))}
    assert kinds["stories"] == 1 and kinds["story_membership"] == 2 and kinds["story_snapshots"] == 1
    assert st.story_record("st_a") is None
    # the export CLI reads the same directory back
    rc = archive_export.main(["--db", "sqlite:///:memory:", "--dir", str(tmp_path), "--stats", "--verify"])
    assert rc == 0


def test_cleanup_reports_the_new_steps_with_durations(st):
    res = storage_lifecycle.run_cleanup(st, policy=retention_policy.RetentionPolicy())
    for step in ("article_provenance", "article_aliases", "story_history", "platform_usage_events"):
        assert step in res["deleted"] and step in res["ms"]


def test_platform_tables_are_protected_and_never_pruned():
    for table in ("platform_tenants", "platform_keys", "platform_usage_daily", "publishers"):
        assert table in retention_policy.PROTECTED_TABLES
        assert not hasattr(store_mod.Store, f"prune_{table}")
    assert hasattr(store_mod.Store, "prune_platform_usage_events")   # the audit rows DO age out
