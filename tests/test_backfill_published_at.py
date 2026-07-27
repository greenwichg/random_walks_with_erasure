"""Tests for examples/backfill_published_at.py — the one-time UTC migration.

The catalog was written with source offsets preserved (``…-04:00``, ``…+05:30``, and some naive
values). ``published_at`` is TEXT and is sorted lexicographically to pick the clustering candidate
set, so until those rows are normalised the newest-first window keeps mis-ordering them. This proves
the migration converts exactly what it should, leaves everything else alone, and is idempotent.
"""

import pathlib
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import backfill_published_at as bf   # noqa: E402
import store as store_mod            # noqa: E402


def _add(st, cu, published_at):
    st.upsert_feed_article(
        canonical_url=cu, url=cu, publisher="P", source_publisher="P", title="t",
        description="", body=None, published_at=published_at, source_feed="f",
        scored={"article_id": cu, "outlet": "P", "category": "politics", "lean": 0.0, "title": "t"})


def _stored(st, cu):
    return st.get_feed_article(cu)["publishedAt"]


def test_converts_offsets_to_utc_preserving_the_instant():
    st = store_mod.Store("sqlite://")
    _add(st, "https://a.example/1", "2026-07-27T12:00:00-04:00")   # 16:00Z
    _add(st, "https://a.example/2", "2026-07-27T12:00:00+05:30")   # 06:30Z
    res = bf.run(st, backup=False)
    assert res["applied"] == 2
    assert _stored(st, "https://a.example/1") == "2026-07-27T16:00:00+00:00"
    assert _stored(st, "https://a.example/2") == "2026-07-27T06:30:00+00:00"


def test_naive_timestamps_are_read_as_utc():
    st = store_mod.Store("sqlite://")
    _add(st, "https://a.example/3", "2026-07-27T12:00:00")
    bf.run(st, backup=False)
    assert _stored(st, "https://a.example/3") == "2026-07-27T12:00:00+00:00"


def test_already_utc_rows_are_untouched():
    st = store_mod.Store("sqlite://")
    _add(st, "https://a.example/4", "2026-07-27T12:00:00+00:00")
    res = bf.run(st, backup=False)
    assert res["applied"] == 0 and res["counts"]["already_utc"] == 1
    assert _stored(st, "https://a.example/4") == "2026-07-27T12:00:00+00:00"


def test_null_and_unparseable_are_left_alone():
    st = store_mod.Store("sqlite://")
    _add(st, "https://a.example/5", None)
    _add(st, "https://a.example/6", "not a timestamp")
    res = bf.run(st, backup=False)
    assert res["applied"] == 0
    assert res["counts"]["null"] == 1 and res["counts"]["unparseable"] == 1
    assert _stored(st, "https://a.example/5") is None
    assert _stored(st, "https://a.example/6") == "not a timestamp"


def test_dry_run_writes_nothing():
    st = store_mod.Store("sqlite://")
    _add(st, "https://a.example/7", "2026-07-27T12:00:00-04:00")
    res = bf.run(st, dry_run=True, backup=False)
    assert res["applied"] == 0 and res["counts"]["to_convert"] == 1
    assert _stored(st, "https://a.example/7") == "2026-07-27T12:00:00-04:00"   # unchanged


def test_idempotent():
    st = store_mod.Store("sqlite://")
    _add(st, "https://a.example/8", "2026-07-27T12:00:00-04:00")
    assert bf.run(st, backup=False)["applied"] == 1
    second = bf.run(st, backup=False)
    assert second["applied"] == 0 and second["counts"]["already_utc"] == 1


def test_migration_makes_string_order_match_time_order():
    """The property the migration exists to restore."""
    st = store_mod.Store("sqlite://")
    raw = {"https://a.example/x1": "2026-07-27T12:00:00-04:00",   # 16:00Z
           "https://a.example/x2": "2026-07-27T15:00:00+00:00",   # 15:00Z
           "https://a.example/x3": "2026-07-27T19:00:00+05:30"}   # 13:30Z
    for cu, ts in raw.items():
        _add(st, cu, ts)

    before = sorted(raw.values())
    assert before[-1] != "2026-07-27T12:00:00-04:00", "pre-migration: newest does not sort last"

    bf.run(st, backup=False)
    after = sorted(_stored(st, cu) for cu in raw)
    by_time = sorted(after, key=lambda s: datetime.fromisoformat(s))
    assert after == by_time
    assert after[-1] == "2026-07-27T16:00:00+00:00"


# --------------------------------------------------------------------------- #
# The migration snapshots itself — no operator step, no "remember to back up first".
# --------------------------------------------------------------------------- #
def test_takes_a_snapshot_before_writing(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'live.db'}"
    st = store_mod.Store(url)
    _add(st, "https://a.example/9", "2026-07-27T12:00:00-04:00")

    taken = []
    monkeypatch.setattr(bf, "snapshot", lambda u: taken.append(u) or str(tmp_path / "snap.db"))
    res = bf.run(st)
    assert taken == [url], "no pre-migration snapshot was taken"
    assert res["backup"] and res["applied"] == 1


def test_a_failed_snapshot_aborts_before_any_write(tmp_path, monkeypatch):
    """If the safety net cannot be created, the migration must not start."""
    url = f"sqlite:///{tmp_path / 'live.db'}"
    st = store_mod.Store(url)
    _add(st, "https://a.example/10", "2026-07-27T12:00:00-04:00")

    def boom(_u):
        raise OSError("disk full")

    monkeypatch.setattr(bf, "snapshot", boom)
    try:
        bf.run(st)
        assert False, "should have refused to run"
    except RuntimeError as e:
        assert "aborting before any write" in str(e)
    assert _stored(st, "https://a.example/10") == "2026-07-27T12:00:00-04:00"   # untouched


def test_no_snapshot_when_there_is_nothing_to_convert(tmp_path, monkeypatch):
    """An already-migrated database must not litter the backups directory on every re-run."""
    url = f"sqlite:///{tmp_path / 'live.db'}"
    st = store_mod.Store(url)
    _add(st, "https://a.example/11", "2026-07-27T12:00:00+00:00")
    monkeypatch.setattr(bf, "snapshot", lambda u: (_ for _ in ()).throw(AssertionError("snapshotted")))
    res = bf.run(st)
    assert res["applied"] == 0 and res["backup"] is None


def test_real_snapshot_writes_a_restorable_file(tmp_path):
    """End-to-end through the REAL store.create_backup path the scheduler uses."""
    url = f"sqlite:///{tmp_path / 'live.db'}"
    st = store_mod.Store(url)
    _add(st, "https://a.example/12", "2026-07-27T12:00:00-04:00")
    res = bf.run(st)
    assert res["applied"] == 1
    path = pathlib.Path(res["backup"])
    assert path.exists() and path.stat().st_size > 0
    assert store_mod.integrity_ok(str(path))
    # the snapshot holds the PRE-migration value; the live DB holds the converted one
    assert store_mod.Store(f"sqlite:///{path}").get_feed_article(
        "https://a.example/12")["publishedAt"] == "2026-07-27T12:00:00-04:00"
    assert _stored(st, "https://a.example/12") == "2026-07-27T16:00:00+00:00"
