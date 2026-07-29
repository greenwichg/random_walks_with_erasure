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
    # Gzipped by default: a backup is a FULL copy of the database, and at the default 12h/7d/4w
    # retention that is ~28 copies. Measured in production before this changed: 2.4 GB of backups
    # against a 93 MB database, on a 29 GB volume.
    assert re.search(r"ih-\d{8}T\d{6}Z\.db\.gz$", dest)                          # timestamped name
    assert store.is_compressed_backup(dest)
    assert store.integrity_ok(dest)                                             # valid SQLite inside

    restored = tmp_path / "roundtrip.db"                                        # ...and it has the data
    store.restore_database(dest, str(restored))
    con = sqlite3.connect(restored)
    try:
        n = con.execute("SELECT COUNT(*) FROM reads").fetchone()[0]
        emails = [r[0] for r in con.execute("SELECT email FROM users")]
    finally:
        con.close()
    assert n == 1 and "a@b.com" in emails


def test_backup_compression_is_a_kill_switch_not_a_one_way_door(tmp_path, monkeypatch):
    """``RWE_BACKUP_COMPRESS=0`` writes plain ``.db`` again, and BOTH formats stay readable.

    This is what makes the change safe to ship: reading is format-agnostic everywhere, so flipping
    the switch changes only what the next backup is written as. Every ``.db`` taken before
    compression existed — and every ``.db.gz`` taken after it is turned off — remains restorable."""
    s = _file_store(tmp_path)
    u = s.upsert_user_by_identity("google", "sw-1", email="sw@b.com")
    s.add_read(u.id, "https://x.com/sw", {"outlet": "x", "lean": 1.0})

    gz = store.create_backup(_url(tmp_path), out_dir=str(tmp_path / "b"))
    monkeypatch.setenv("RWE_BACKUP_COMPRESS", "0")
    plain = store.create_backup(_url(tmp_path), out_dir=str(tmp_path / "b"))

    assert gz.endswith(".db.gz") and plain.endswith(".db")
    assert store.integrity_ok(gz) and store.integrity_ok(plain)
    assert os.path.getsize(gz) < os.path.getsize(plain), "compression must actually shrink it"

    listed = store.list_backups(str(tmp_path / "b"))
    assert {os.path.basename(b["path"]) for b in listed} == {
        os.path.basename(gz), os.path.basename(plain)}, "both formats must be listed"
    assert {b["compressed"] for b in listed} == {True, False}

    for src in (gz, plain):                                   # both restore identically
        out = tmp_path / f"r-{os.path.basename(src)}.db"
        store.restore_database(src, str(out))
        con = sqlite3.connect(out)
        try:
            assert [r[0] for r in con.execute("SELECT email FROM users")] == ["sw@b.com"]
        finally:
            con.close()


def test_corrupt_gzip_backup_fails_the_check_rather_than_raising(tmp_path):
    """A file that is not valid gzip is not a valid backup, and must be reported as such.

    ``integrity_ok`` is the gate every restore passes through, so it has to answer False for
    unreadable input rather than propagate an exception — an exception escaping here would abort
    the caller before it could refuse the restore, which is the opposite of fail-safe."""
    bad = tmp_path / "ih-20260101T000000Z.db.gz"
    bad.write_bytes(b"not gzip, not sqlite, not a backup")
    assert store.integrity_ok(str(bad)) is False

    live = tmp_path / "live.db"
    store.Store(f"sqlite:///{live}")
    with pytest.raises(ValueError):
        store.restore_database(str(bad), str(live))


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
