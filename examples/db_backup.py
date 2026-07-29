"""Backup / restore / status for the Information Health SQLite store.

A consistent **online** backup (the server keeps running) and a **safe** restore (the backup is
integrity-checked before it replaces the live database, and the current DB is snapshotted first).
No new dependency — pure stdlib sqlite3, driven by the same ``store`` helpers the engine uses.

    # back up the configured database (RWE_DB_URL, or the default file) to a timestamped file
    python examples/db_backup.py backup                     # -> <db-dir>/backups/ih_beta-<ts>.db.gz
    python examples/db_backup.py backup --out /backups      # or a chosen directory

    # verify ANY backup, compressed or not — exit 0 = intact (use this from scripts)
    python examples/db_backup.py verify /backups/ih_beta-<ts>.db.gz

    # inspect storage + list backups
    python examples/db_backup.py status

    # restore (STOP the engine first): validates integrity, snapshots current, then swaps
    python examples/db_backup.py restore /backups/ih_beta-20260706T101500Z.db

The database is chosen exactly like the engine: ``--db`` > ``RWE_DB_URL`` > the default repo file.
The backup directory is ``--out`` > ``RWE_BACKUP_DIR`` > ``backups/`` beside the database file.

Backups are gzipped by default (``RWE_BACKUP_COMPRESS=0`` to disable). Reading is format-agnostic
everywhere — ``verify`` and ``restore`` accept ``.db`` and ``.db.gz`` alike, so backups written
before compression existed stay restorable forever.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import sibling store
import store


def _db_url(args) -> str:
    return args.db or store.default_db_url()


def cmd_backup(args) -> int:
    url = _db_url(args)
    try:
        dest = store.create_backup(url, out_dir=args.out)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"backup failed: {e}", file=sys.stderr)
        return 1
    size = os.path.getsize(dest)
    note = ""
    if store.is_compressed_backup(dest):
        src = store.sqlite_path(url)
        if src and os.path.exists(src):
            raw = os.path.getsize(src)
            note = f", gzip {raw / size:.1f}x from {raw:,}" if size else ""
    print(f"backup ok: {dest} ({size:,} bytes{note}, integrity check passed)")
    return 0


def cmd_verify(args) -> int:
    """Integrity-check a backup FILE, compressed or not.

    Exists because the scripts used to verify by opening the backup as ``sqlite:///<path>`` and
    grepping ``status`` output for "quickCheck ok" — which cannot work on a ``.db.gz`` and, worse,
    reported failure through a grep rather than an exit code. ``store.integrity_ok`` already handles
    both formats; this exposes it with a contract a shell can trust."""
    path = args.backup
    if not os.path.exists(path):
        print(f"verify FAILED: no such file: {path}", file=sys.stderr)
        return 1
    size = os.path.getsize(path)
    if not store.integrity_ok(path):
        print(f"verify FAILED: {path} ({size:,} bytes) did not pass PRAGMA integrity_check",
              file=sys.stderr)
        return 2
    kind = "gzip" if store.is_compressed_backup(path) else "plain"
    print(f"verify ok: {path} ({size:,} bytes, {kind}, integrity check passed)")
    return 0


def cmd_status(args) -> int:
    url = _db_url(args)
    s = store.Store(url)
    diag = s.storage_diagnostics()
    print("storage:")
    for k in ("url", "backend", "ephemeral", "journalMode", "synchronous", "foreignKeys",
              "busyTimeoutMs", "quickCheck", "sizeBytes"):
        if k in diag:
            print(f"  {k:<14} {diag[k]}")
    out = args.out or store.default_backup_dir(url)
    backups = store.list_backups(out)
    print(f"backups in {out}: {len(backups)}")
    for b in backups[:5]:
        print(f"  {b['modifiedAt']}  {b['sizeBytes']:>12,}  {b['path']}")
    return 0


def cmd_restore(args) -> int:
    url = _db_url(args)
    path = store.sqlite_path(url)
    if path is None:
        print("cannot restore into an in-memory / non-file database", file=sys.stderr)
        return 1
    try:
        saved = store.restore_database(args.backup, path)
    except (FileNotFoundError, ValueError) as e:
        print(f"restore refused (active database untouched): {e}", file=sys.stderr)
        return 1
    print(f"restore ok: {args.backup} -> {path}")
    if saved:
        print(f"  previous database saved to {saved}")
    print("  restart the engine to pick up the restored database.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="database URL (default: RWE_DB_URL or the repo file)")
    sub = ap.add_subparsers(dest="command", required=True)

    b = sub.add_parser("backup", help="create a consistent, timestamped backup (server can stay up)")
    b.add_argument("--out", default=None, help="backup directory (default: RWE_BACKUP_DIR or beside the DB)")
    b.set_defaults(func=cmd_backup)

    st = sub.add_parser("status", help="show storage diagnostics + list backups")
    st.add_argument("--out", default=None, help="backup directory to list")
    st.set_defaults(func=cmd_status)

    v = sub.add_parser("verify", help="integrity-check a backup file (.db or .db.gz); exit 0 = intact")
    v.add_argument("backup", help="path to a backup file")
    v.set_defaults(func=cmd_verify)

    r = sub.add_parser("restore", help="restore from a backup (STOP the engine first)")
    r.add_argument("backup", help="path to a backup file produced by `backup`")
    r.set_defaults(func=cmd_restore)

    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
