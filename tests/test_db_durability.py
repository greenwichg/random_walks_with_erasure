"""Tests for SQLite durability: pragmas, foreign-key enforcement, ephemeral detection, and the
online backup / safe restore helpers (examples/store.py + examples/db_backup.py)."""

import os
import pathlib
import re
import sqlite3
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store  # noqa: E402


def _url(tmp_path):
    return f"sqlite:///{tmp_path / 'ih.db'}"


def _file_store(tmp_path):
    return store.Store(_url(tmp_path))


def test_sqlite_durability_pragmas_applied(tmp_path):
    s = _file_store(tmp_path)
    with s.engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
        assert int(c.exec_driver_sql("PRAGMA foreign_keys").scalar()) == 1
        assert int(c.exec_driver_sql("PRAGMA busy_timeout").scalar()) == 5000
        assert int(c.exec_driver_sql("PRAGMA synchronous").scalar()) == 1        # NORMAL


def test_foreign_keys_enforced(tmp_path):
    """A child row for a non-existent user is rejected — foreign keys are ON (SQLite defaults OFF)."""
    s = _file_store(tmp_path)
    with pytest.raises(Exception):
        s.add_read(999999, "https://x.com/a", {"outlet": "x", "lean": 0.0})


def test_is_ephemeral_url_classification():
    assert store.is_ephemeral_url("sqlite://") is True
    assert store.is_ephemeral_url("sqlite:///:memory:") is True
    assert store.is_ephemeral_url("sqlite:////tmp/x.db") is True
    assert store.is_ephemeral_url("sqlite:////var/tmp/x.db") is True
    assert store.is_ephemeral_url("sqlite:////var/lib/ih/ih.db") is False
    assert store.is_ephemeral_url("sqlite:///data/ih.db") is False               # repo-relative file
    assert store.is_ephemeral_url("postgresql://h/db") is False


def test_backup_creates_valid_consistent_timestamped_copy(tmp_path):
    s = _file_store(tmp_path)
    u = s.upsert_user_by_identity("google", "bk-1", email="a@b.com")
    s.add_read(u.id, "https://x.com/a", {"outlet": "x", "lean": 1.0})

    dest = store.create_backup(_url(tmp_path), out_dir=str(tmp_path / "backups"))
    assert os.path.exists(dest)
    assert re.search(r"ih-\d{8}T\d{6}Z\.db$", dest)                              # timestamped name
    assert store.integrity_ok(dest)                                             # valid SQLite

    con = sqlite3.connect(dest)                                                 # ...and it has the data
    try:
        n = con.execute("SELECT COUNT(*) FROM reads").fetchone()[0]
        emails = [r[0] for r in con.execute("SELECT email FROM users")]
    finally:
        con.close()
    assert n == 1 and "a@b.com" in emails


def test_restore_validates_integrity_and_swaps(tmp_path):
    s = _file_store(tmp_path)
    s.upsert_user_by_identity("google", "rs-1", email="keep@b.com")
    good = store.create_backup(_url(tmp_path), out_dir=str(tmp_path / "backups"))
    path = store.sqlite_path(_url(tmp_path))

    s.engine.dispose()                                                          # release the file
    with open(path, "wb") as f:
        f.write(b"not a database")                                             # corrupt the live DB
    assert store.integrity_ok(path) is False

    saved = store.restore_database(good, path)
    assert saved and os.path.exists(saved)                                     # pre-restore snapshot kept
    assert store.integrity_ok(path)                                            # restored DB is valid
    con = sqlite3.connect(path)
    try:
        emails = [r[0] for r in con.execute("SELECT email FROM users")]
    finally:
        con.close()
    assert "keep@b.com" in emails


def test_restore_refuses_corrupt_backup_fail_safe(tmp_path):
    s = _file_store(tmp_path)
    s.upsert_user_by_identity("google", "safe-1", email="orig@b.com")
    path = store.sqlite_path(_url(tmp_path))
    s.engine.dispose()
    before = open(path, "rb").read()

    bad = str(tmp_path / "bad.db")
    with open(bad, "wb") as f:
        f.write(b"garbage, not a sqlite database")
    with pytest.raises(ValueError):
        store.restore_database(bad, path)                                      # refused up front
    assert open(path, "rb").read() == before                                  # active DB untouched


def test_storage_diagnostics(tmp_path):
    s = _file_store(tmp_path)
    s.upsert_user_by_identity("google", "diag-1")
    diag = s.storage_diagnostics()
    assert diag["journalMode"].lower() == "wal"
    assert diag["foreignKeys"] is True
    assert diag["quickCheck"] == "ok"
    assert diag["sizeBytes"] > 0
    assert "ephemeral" in diag and "backupCount" in diag
