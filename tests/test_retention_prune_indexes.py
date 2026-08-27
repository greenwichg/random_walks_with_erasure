"""M3 / D3 — every bounded prune filters on an indexed column.

Three of the five prunes in `storage_lifecycle.run_cleanup` had no index on the column their
`WHERE` tests, so each full-scanned its table on **every** pass — including the overwhelming
majority that delete nothing, because proving there is nothing to delete is exactly what the scan
was doing.

Measured A/B in one process at 400,000 catalogue rows / 400,000 score-cache rows / 200,000 analytics
rows / 200,000 rec-events, alternating index-present and index-absent passes to control for page
cache (`docs/STORAGE_50K_DESIGN.md` §4 D3):

    pass total        216.1 ms  ->  21.4 ms   (10.1x)
      scored_articles 102.8 ms  ->   0.7 ms
      analytics_events 21.3 ms  ->   0.5 ms
      rec_events       15.2 ms  ->   0.5 ms
      storage_stats    72.3 ms  ->  17.3 ms   (COUNT(*) can use the smaller index b-tree)

The assertions here are on the **query plan**, not on time. A timing test would be flaky, and would
also pass for a table small enough that a scan is fast — which is every test database, and exactly
why this went unnoticed.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "examples"))

import store as store_mod  # noqa: E402

#: (table, filter column, the prune that uses it). Each row is a `WHERE <column> < :cutoff` that
#: `storage_lifecycle.run_cleanup` runs once per pass.
_PRUNE_FILTERS = [
    ("scored_articles", "created_at", "prune_scored_cache"),
    ("analytics_events", "created_at", "prune_analytics_events"),
    ("rec_events", "shown_at", "prune_rec_events"),
]


def _plan(db_path: str, table: str, column: str) -> str:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            f"EXPLAIN QUERY PLAN SELECT rowid FROM {table} WHERE {column} < ? LIMIT 5000",
            ("2000-01-01",)).fetchall()
    finally:
        con.close()
    return " | ".join(r[3] for r in rows)


@pytest.fixture()
def fresh_db(tmp_path):
    path = str(tmp_path / "fresh.db")
    store_mod.Store(f"sqlite:///{path}").engine.dispose()
    return path


@pytest.mark.parametrize("table,column,prune", _PRUNE_FILTERS)
def test_prune_filter_is_index_backed(fresh_db, table, column, prune):
    plan = _plan(fresh_db, table, column)
    assert "SCAN" not in plan, (
        f"{prune} filters {table}.{column} with a full table scan ({plan}) — it runs on every "
        f"cleanup pass, including the ones that delete nothing")
    assert "SEARCH" in plan, plan


def test_the_migration_upgrades_a_pre_existing_database(tmp_path):
    """The case that actually matters: production's database already exists, so a model change
    alone would never reach it. `Store.__init__` must add the indexes in place."""
    path = str(tmp_path / "legacy.db")
    store_mod.Store(f"sqlite:///{path}").engine.dispose()

    con = sqlite3.connect(path)
    for name in ("ix_scored_created_at", "ix_analytics_created_at", "ix_rec_events_shown_at"):
        con.execute(f"DROP INDEX IF EXISTS {name}")
    con.commit()
    con.close()
    for table, column, _ in _PRUNE_FILTERS:                # the "before" state, asserted
        assert "SCAN" in _plan(path, table, column)

    store_mod.Store(f"sqlite:///{path}").engine.dispose()  # reopening is the migration

    for table, column, prune in _PRUNE_FILTERS:
        assert "SCAN" not in _plan(path, table, column), f"{prune} was not upgraded in place"


def test_creating_the_indexes_twice_is_harmless(fresh_db):
    """`IF NOT EXISTS`, and the store is constructed on every process start."""
    for _ in range(3):
        st = store_mod.Store(f"sqlite:///{fresh_db}")
        assert st.storage_diagnostics()["indexErrors"] == []
        st.engine.dispose()


def test_index_failures_are_reported_rather_than_swallowed(fresh_db, monkeypatch):
    """`index_errors` was written by `_ensure_search_indexes` and read by NOBODY.

    A missing index degrades silently by definition — the query still returns the right rows, just
    slowly — so a report is the only signal it can ever give. This asserts the channel exists, by
    feeding the creator a statement that cannot succeed.
    """
    st = store_mod.Store(f"sqlite:///{fresh_db}")
    assert st.storage_diagnostics()["indexErrors"] == []

    st._create_indexes(["CREATE INDEX IF NOT EXISTS ix_bogus ON no_such_table(nope)"])
    reported = st.storage_diagnostics()["indexErrors"]
    assert reported and reported[0]["index"] == "ix_bogus", reported
    assert "no_such_table" in reported[0]["error"], reported
    st.engine.dispose()


def test_a_failing_index_never_blocks_the_rest(fresh_db):
    """One transaction per statement: a failure must not roll back the indexes created before it,
    which is the fault the per-statement loop was written to fix."""
    st = store_mod.Store(f"sqlite:///{fresh_db}")
    st._create_indexes([
        "CREATE INDEX IF NOT EXISTS ix_probe_a ON scored_articles(url)",
        "CREATE INDEX IF NOT EXISTS ix_bogus ON no_such_table(nope)",
        "CREATE INDEX IF NOT EXISTS ix_probe_b ON rec_events(article_id)",
    ])
    con = sqlite3.connect(fresh_db)
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_probe_%'")}
    finally:
        con.close()
    assert names == {"ix_probe_a", "ix_probe_b"}, (
        f"a failing statement took its neighbours with it: {names}")
    assert [e["index"] for e in st.storage_diagnostics()["indexErrors"]] == ["ix_bogus"]
    st.engine.dispose()
