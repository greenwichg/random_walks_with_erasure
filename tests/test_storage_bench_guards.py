"""The two guards in `storage_bench.py` that already caught a wrong measurement, tested by
breaking the product they guard.

A benchmark cannot be checked by reading its output — a fast number and an absent workload look the
same. Both guards below were written after the harness produced a confident, entirely fictional
result, and each test here mutates the harness so the guard has to fire.

* `_fill` verifies its own row count. `feed_articles.created_at` is NOT NULL with a *Python-side*
  default, so SQLite has no default to supply and `INSERT OR IGNORE` discards every row in silence.
  The first ladder ran green against an empty table.
* `--calibrate` compares bytes-per-article between the bulk fill and the real `ingest_entries`. The
  first `scored` payload was a plausible-looking shorthand at half the real size, and it understated
  storage growth by 23%.
"""
from __future__ import annotations

import os
import pathlib
import random
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "examples"))

import storage_bench  # noqa: E402
import store as store_mod  # noqa: E402


@pytest.fixture()
def empty_db(tmp_path):
    path = str(tmp_path / "bench.db")
    store_mod.Store(f"sqlite:///{path}").engine.dispose()      # creates the schema + indexes
    return path


# --------------------------------------------------------------------------- the fill guard

def test_fill_inserts_the_rows_it_claims(empty_db):
    seconds = storage_bench._fill(empty_db, 0, 500, 50, random.Random(1))
    con = sqlite3.connect(empty_db)
    try:
        assert con.execute("SELECT COUNT(*) FROM feed_articles").fetchone()[0] == 500
        # The score cache too: the real path writes one per article, and leaving it out would
        # understate growth by exactly what the cache costs.
        assert con.execute("SELECT COUNT(*) FROM scored_articles").fetchone()[0] == 500
    finally:
        con.close()
    assert seconds >= 0.0


def test_fill_raises_when_the_insert_is_silently_rejected(empty_db, monkeypatch):
    """Drop the NOT NULL column with no SQL default and every insert becomes a no-op that
    `INSERT OR IGNORE` swallows. Without the count check this returns a timing and no data."""
    columns = tuple(c for c in storage_bench._FEED_COLUMNS if c != "created_at")
    monkeypatch.setattr(storage_bench, "_FEED_COLUMNS", columns)
    monkeypatch.setattr(storage_bench, "_rows",
                        lambda rng, start, count, sources: [
                            tuple(r[:-1]) for r in
                            _original_rows(rng, start, count, sources)])
    with pytest.raises(RuntimeError, match="being rejected"):
        storage_bench._fill(empty_db, 0, 200, 20, random.Random(2))


_original_rows = storage_bench._rows


def test_every_row_matches_the_column_list():
    """A column added to `_FEED_COLUMNS` without a value in `_rows` (or the reverse) is a
    misalignment SQLite reports as a parameter-count error at fill time — but only if the two are
    checked against each other, which nothing else does."""
    rows = _original_rows(random.Random(3), 0, 5, 5)
    assert rows and all(len(r) == len(storage_bench._FEED_COLUMNS) for r in rows)


# --------------------------------------------------------------------------- the calibration guard

@pytest.fixture()
def small_calibration(monkeypatch):
    monkeypatch.setattr(storage_bench, "CALIBRATION_ARTICLES", 300)
    for key in ("RWE_CORPUS_TIER_B", "RWE_CORPUS_SHADOW"):
        monkeypatch.delenv(key, raising=False)


def test_calibration_passes_for_the_shipped_row_shape(small_calibration):
    result = storage_bench._calibrate(50, random.Random(4))
    assert result["verdict"] == "PASS", result
    assert result["drift"] <= storage_bench.CALIBRATION_TOLERANCE


def test_calibration_fails_when_the_bulk_rows_stop_matching_the_real_ones(
        small_calibration, monkeypatch):
    """The guard flips. Shrink the `scored` payload the bulk path writes — the exact defect the
    first version shipped — and the calibration must refuse the run rather than report a smaller,
    flattering bytes-per-article."""
    monkeypatch.setattr(storage_bench, "_scored_json", lambda publisher, url, title: "{}")
    result = storage_bench._calibrate(50, random.Random(5))
    assert result["verdict"] == "FAIL", result
    assert result["drift"] > storage_bench.CALIBRATION_TOLERANCE


def test_main_exits_nonzero_on_a_failed_calibration(monkeypatch, tmp_path):
    """A FAIL that only appeared in the JSON would be a diagnostic nothing acts on."""
    monkeypatch.setattr(storage_bench, "run_ladder",
                        lambda *a, **k: {"rungs": [], "calibration": {"verdict": "FAIL",
                                                                      "drift": 0.99},
                                         "host": {}})
    assert storage_bench.main(["--rungs", "10", "--json", str(tmp_path / "o.json")]) == 1


# --------------------------------------------------------------------------- the RSS sampler

def test_rss_peak_reports_a_peak_at_least_as_large_as_the_baseline():
    with storage_bench._rss_peak(interval=0.01) as peak:
        ballast = [os.urandom(1024) for _ in range(2000)]      # noqa: F841 — keep it resident
    assert peak["peakMb"] > 0.0
    assert peak["deltaMb"] >= 0.0
