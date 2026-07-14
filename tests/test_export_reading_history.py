"""Tests for examples/export_reading_history.py — the read-only reading-history exporter.

Verifies the versioned envelope, the per-read field projection (reusing store.list_reads), oldest-first
ordering, NaN-lean → null (strict portable JSON), the demo / user:N / --all-users selectors, that
``main`` writes a file and returns 0, and that the whole thing is read-only (the DB is unchanged)."""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import store as store_mod                # noqa: E402
import export_reading_history as exp     # noqa: E402


def _scored(article_id, outlet, category, title, lean, emotion=None):
    return {"article_id": article_id, "outlet": outlet, "category": category, "title": title,
            "lean": lean, "political": True, "emotion": emotion}


def _seed(tmp_path):
    st = store_mod.Store(f"sqlite:///{tmp_path/'exp.db'}")
    demo = st.upsert_user_by_identity("dev", "demo@infodiet.local", display_name="Demo Reader").id
    st.add_read(demo, "https://npr.org/a",
                _scored("https://npr.org/a", "NPR", "Politics", "t1", -1.0, "calm"),
                "2026-07-01T00:00:00+00:00", read_source="seed")
    st.add_read(demo, "https://foxnews.com/b",
                _scored("https://foxnews.com/b", "Fox News", "Politics", "t2", 2.0),
                "2026-07-02T00:00:00+00:00", read_source="seed")
    st.add_read(demo, "https://blog.example/c",   # unknown outlet -> NaN lean, no emotion
                _scored("https://blog.example/c", "blog.example", "Politics", "t3", float("nan")),
                "2026-07-03T00:00:00+00:00", read_source="extension")
    other = st.upsert_user_by_identity("dev", "someone-else").id
    st.add_read(other, "https://cnn.com/d",
                _scored("https://cnn.com/d", "CNN", "Politics", "t4", -1.0),
                "2026-07-01T00:00:00+00:00", read_source="seed")
    return st, demo, other


def test_single_user_shape_fields_and_order(tmp_path):
    st, demo, _ = _seed(tmp_path)
    env = exp.build_export(st, user=f"user:{demo}")
    assert env["version"] == exp.EXPORT_VERSION and env["exportedAt"]
    assert env["user"] == {"id": demo, "provider": "dev", "providerAccountId": "demo@infodiet.local"}
    h = env["readingHistory"]
    assert [r["readAt"] for r in h] == ["2026-07-01T00:00:00+00:00", "2026-07-02T00:00:00+00:00",
                                        "2026-07-03T00:00:00+00:00"]                     # oldest-first
    assert set(h[0]) == {"readAt", "canonicalUrl", "articleId", "title", "outlet", "category",
                         "lean", "emotion", "readSource"}
    assert (h[0]["outlet"] == "NPR" and h[0]["category"] == "Politics" and h[0]["lean"] == -1.0
            and h[0]["emotion"] == "calm" and h[0]["readSource"] == "seed"
            and h[0]["canonicalUrl"] == "https://npr.org/a")
    assert h[2]["lean"] is None and h[2]["emotion"] is None and h[2]["readSource"] == "extension"  # NaN->null


def test_strict_portable_json_and_demo_selector(tmp_path):
    st, demo, _ = _seed(tmp_path)
    env = exp.build_export(st, user="demo")                     # 'demo' resolves the persisted account
    text = json.dumps(env, allow_nan=False)                     # raises if any NaN survived
    assert json.loads(text)["user"]["id"] == demo


def test_all_users(tmp_path):
    st, demo, other = _seed(tmp_path)
    env = exp.build_export(st, all_users=True)
    assert env["version"] == exp.EXPORT_VERSION and "user" not in env
    counts = {u["user"]["id"]: len(u["readingHistory"]) for u in env["users"]}
    assert counts == {demo: 3, other: 1}


def test_main_writes_file_and_is_read_only(tmp_path):
    st, demo, _ = _seed(tmp_path)
    dburl = f"sqlite:///{tmp_path/'exp.db'}"
    before = store_mod.Store(dburl).count_reads(demo)
    out = tmp_path / "hist.json"
    assert exp.main(["--db", dburl, "--user", f"user:{demo}", "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["version"] == 1 and doc["user"]["id"] == demo and len(doc["readingHistory"]) == 3
    assert store_mod.Store(dburl).count_reads(demo) == before   # read-only: DB unchanged


def test_errors_on_missing_user_and_missing_demo(tmp_path):
    st = store_mod.Store(f"sqlite:///{tmp_path/'empty.db'}")
    with pytest.raises(SystemExit):
        exp.build_export(st, user="user:999")                   # no such user
    with pytest.raises(SystemExit):
        exp.build_export(st, user="demo")                       # no persisted demo account
