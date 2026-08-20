"""Sidecars — the durable state that lives BESIDE the database.

`store.create_backup` snapshots one file: the SQLite database. Two small files in the same
directory are not in it and are not regenerable, so a restore that returns only the database looks
completely successful and can leave the product unusable:

  * `allowlist.txt` — the beta gate's list. The gate FAILS CLOSED on an empty list
    (web/lib/beta-access.ts -> `empty_allowlist`), so losing 116 bytes locks every user out of an
    otherwise perfect restore, including whoever is trying to fix it.
  * `score_reference.json` — the frozen scoring cohort. Losing it is not an error; the next report
    captures a new one and every reader's score silently moves to a different baseline.
"""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


bk = _load("db_backup")


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A data directory that looks like production's: a database plus its two sidecars."""
    data = tmp_path / "data"
    data.mkdir()
    db = data / "ih_beta.db"
    monkeypatch.setenv("RWE_DB_URL", f"sqlite:///{db}")
    monkeypatch.setenv("BETA_ALLOWLIST_FILE", str(data / "allowlist.txt"))
    monkeypatch.setenv("RWE_SCORE_REFERENCE", str(data / "score_reference.json"))
    store = _load("store")
    store.Store(f"sqlite:///{db}")                       # create the schema
    (data / "allowlist.txt").write_text("a@example.com\n", encoding="utf-8")
    (data / "score_reference.json").write_text(
        json.dumps({"schemaVersion": 1, "metrics": {"topic": [1.0, 2.0]}}), encoding="utf-8")
    return data


def _run(*args, env=None):
    return subprocess.run([sys.executable, str(EXAMPLES / "db_backup.py"), *args],
                          cwd=ROOT, env={**os.environ, **(env or {})},
                          capture_output=True, text=True)


def test_sidecar_paths_come_from_the_readers_own_configuration(live, monkeypatch):
    """The beta gate reads BETA_ALLOWLIST_FILE and has NO default path — manage_users.py records
    that a previous default was itself the bug. Backing up a guessed location would archive a
    stale copy while reporting success."""
    monkeypatch.setenv("BETA_ALLOWLIST_FILE", "/somewhere/else/list.txt")
    names = dict(bk.sidecar_sources(f"sqlite:///{live / 'ih_beta.db'}"))
    assert names[bk.SIDECAR_ALLOWLIST] == "/somewhere/else/list.txt"


def test_no_allowlist_configured_and_none_on_disk_means_no_sidecar(live, monkeypatch):
    monkeypatch.delenv("BETA_ALLOWLIST_FILE", raising=False)
    (live / "allowlist.txt").unlink()
    names = dict(bk.sidecar_sources(f"sqlite:///{live / 'ih_beta.db'}"))
    assert bk.SIDECAR_ALLOWLIST not in names


def test_backup_writes_both_sidecars_beside_the_database(live):
    r = _run("backup")
    assert r.returncode == 0, r.stderr
    files = sorted(p.name for p in (live / "backups").iterdir())
    assert any(f.endswith(".db.gz") for f in files)
    assert any(f.endswith(f".{bk.SIDECAR_ALLOWLIST}") for f in files), files
    assert any(f.endswith(f".{bk.SIDECAR_REFERENCE}") for f in files), files


def test_the_set_shares_one_timestamp_so_it_reads_as_a_set(live):
    """A directory listing should make it obvious which sidecars belong to which snapshot — they
    hang off the database backup's own stem."""
    _run("backup")
    names = sorted(p.name for p in (live / "backups").iterdir())
    db_name = next(n for n in names if n.endswith(".db.gz"))
    stem = bk.sidecar_prefix(db_name)
    assert stem and stem != db_name
    assert all(n.startswith(stem + ".") for n in names), names
    assert sorted(n[len(stem) + 1:] for n in names) == sorted(
        ["db.gz", bk.SIDECAR_ALLOWLIST, bk.SIDECAR_REFERENCE])


def test_a_wiped_volume_is_fully_restored_from_the_backup_alone(live, tmp_path):
    """The case the whole thing exists for: the volume is gone and only the backups survive."""
    _run("backup")
    backups = tmp_path / "offhost"
    backups.mkdir()
    for p in (live / "backups").iterdir():
        (backups / p.name).write_bytes(p.read_bytes())
    for p in live.iterdir():                              # wipe everything but the backups
        if p.is_file():
            p.unlink()
    db_backup = next(p for p in backups.iterdir() if p.name.endswith(".db.gz"))

    r = _run("restore", str(db_backup))
    assert r.returncode == 0, r.stderr
    assert (live / "ih_beta.db").exists()
    assert (live / "allowlist.txt").read_text() == "a@example.com\n"
    assert json.loads((live / "score_reference.json").read_text())["metrics"] == {"topic": [1.0, 2.0]}


def test_restoring_a_backup_that_predates_sidecars_warns_about_the_lockout(live, tmp_path):
    """A backup written before this existed restores a database and no allowlist. That is the
    silent lockout, so it must be said out loud rather than reported as a clean restore."""
    _run("backup")
    db_backup = next(p for p in (live / "backups").iterdir() if p.name.endswith(".db.gz"))
    legacy = live / "backups" / "ih_beta-legacy.db.gz"
    legacy.write_bytes(db_backup.read_bytes())            # no sidecars beside it

    r = _run("restore", str(legacy))
    assert r.returncode == 0
    assert "NOBODY" in r.stderr and bk.SIDECAR_ALLOWLIST in r.stderr
    assert bk.SIDECAR_REFERENCE in r.stdout               # the softer note, on stdout


def test_verify_refuses_a_backup_whose_allowlist_is_empty(live):
    """An empty allowlist and a missing one are the same thing to the gate: everyone denied. A
    backup carrying one must not pass as 'the file is there'."""
    (live / "allowlist.txt").write_text("", encoding="utf-8")
    _run("backup")
    db_backup = next(p for p in (live / "backups").iterdir() if p.name.endswith(".db.gz"))
    r = _run("verify", str(db_backup))
    assert r.returncode == 3, r.stdout + r.stderr
    assert "empty" in (r.stdout + r.stderr).lower()


def test_verify_passes_and_names_the_sidecars_it_found(live):
    _run("backup")
    db_backup = next(p for p in (live / "backups").iterdir() if p.name.endswith(".db.gz"))
    r = _run("verify", str(db_backup))
    assert r.returncode == 0, r.stderr
    assert bk.SIDECAR_ALLOWLIST in r.stdout and bk.SIDECAR_REFERENCE in r.stdout


def test_restore_snapshots_the_sidecars_it_replaces(live):
    """A restore must be undoable. These are exactly the files someone reaches for when the
    restore turns out to have been the mistake."""
    _run("backup")
    (live / "allowlist.txt").write_text("newer@example.com\n", encoding="utf-8")
    db_backup = next(p for p in (live / "backups").iterdir() if p.name.endswith(".db.gz"))
    _run("restore", str(db_backup))
    kept = [p for p in live.iterdir() if ".replaced-" in p.name]
    assert kept, "the replaced allowlist must be recoverable"
    assert any(p.read_text() == "newer@example.com\n" for p in kept)


def test_feed_corpus_is_deliberately_not_a_sidecar(live):
    """16 MB, and `feed_source.export_candidate_csv` regenerates it from the database. Backing it
    up would triple the artifact for nothing."""
    (live / "feed_corpus.csv").write_text("x", encoding="utf-8")
    names = dict(bk.sidecar_sources(f"sqlite:///{live / 'ih_beta.db'}"))
    assert "feed_corpus.csv" not in names


def test_sidecars_are_as_readable_as_the_snapshot_they_belong_to(live):
    """`shutil.copy2` preserves the SOURCE mode, and both live sidecars are root-owned 0600. The
    copies therefore landed unreadable to the unprivileged user that runs the off-host
    `aws s3 sync`, which skipped every sidecar and failed the sync — so the files never left the
    machine, and the hourly cron alerted. A sidecar must carry the snapshot's own mode."""
    os.chmod(live / "allowlist.txt", 0o600)
    os.chmod(live / "score_reference.json", 0o600)
    r = _run("backup")
    assert r.returncode == 0, r.stderr
    db = next(p for p in (live / "backups").iterdir() if p.name.endswith(".db.gz"))
    db_mode = db.stat().st_mode & 0o777
    for name, path in bk.sidecars_of(str(db)):
        got = os.stat(path).st_mode & 0o777
        assert got == db_mode, f"{name} is {got:o}, snapshot is {db_mode:o}"
        assert got & 0o044, f"{name} is {got:o} — the off-host sync user cannot read it"
