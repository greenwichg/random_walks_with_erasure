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
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import sibling store
import store


def _db_url(args) -> str:
    return args.db or store.default_db_url()


# --------------------------------------------------------------------------- #
# Sidecars — durable state that lives BESIDE the database, not inside it.
# --------------------------------------------------------------------------- #
#: A restore that returns the database and nothing else looks completely successful and can leave
#: the product unusable. Two small files qualify:
#:
#:   allowlist.txt        the beta gate's list. Hand-curated through scripts/manage_users.py and
#:                        reconstructible from nothing. The gate FAILS CLOSED on an empty list
#:                        (web/lib/beta-access.ts: `empty_allowlist` -> denied), so losing 116
#:                        bytes locks every user out of an otherwise perfect 500 GB restore —
#:                        including whoever is trying to fix it.
#:   score_reference.json the frozen scoring cohort. Losing it is not an error: the next report
#:                        captures a new one from whatever corpus is live then, and every reader's
#:                        score silently moves to a different baseline with nothing to show for it.
#:
#: Deliberately NOT included: feed_corpus.csv (16 MB and regenerated from the database by
#: `feed_source.export_candidate_csv`), and the backups directory itself.
SIDECAR_ALLOWLIST = "allowlist.txt"
SIDECAR_REFERENCE = "score_reference.json"


def sidecar_sources(db_url: str) -> "list[tuple[str, str]]":
    """``[(name, live path)]`` for each sidecar — resolved the way the READER of that file resolves
    it, never by assuming a location.

    The beta gate reads ``BETA_ALLOWLIST_FILE`` and has no default path on purpose
    (scripts/manage_users.py records that a previous default was itself the bug), so an unset
    variable means the deployment keeps its list somewhere else — or in ``BETA_ALLOWLIST`` — and
    guessing would back up a stale copy while reporting success."""
    data_dir = os.path.dirname(store.sqlite_path(db_url) or "") or "."
    out = []

    allow = (os.environ.get("BETA_ALLOWLIST_FILE") or "").strip()
    if not allow:
        candidate = os.path.join(data_dir, SIDECAR_ALLOWLIST)
        allow = candidate if os.path.exists(candidate) else ""
    if allow:
        out.append((SIDECAR_ALLOWLIST, allow))

    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import score_reference
        out.append((SIDECAR_REFERENCE, score_reference.path()))
    except Exception:                              # noqa: BLE001 — fall back to the conventional spot
        out.append((SIDECAR_REFERENCE, os.path.join(data_dir, SIDECAR_REFERENCE)))
    return out


def sidecar_prefix(backup_path: str) -> str:
    """The shared stem a backup's sidecars hang off, so a set is obvious in a directory listing and
    sorts together: ``ih_beta-<ts>.db.gz`` -> ``ih_beta-<ts>``."""
    p = backup_path[:-3] if backup_path.endswith(".gz") else backup_path
    return p[:-3] if p.endswith(".db") else p


def sidecars_of(backup_path: str) -> "list[tuple[str, str]]":
    """``[(name, path)]`` for the sidecars stored alongside an existing backup."""
    prefix = sidecar_prefix(backup_path)
    found = []
    for name in (SIDECAR_ALLOWLIST, SIDECAR_REFERENCE):
        candidate = f"{prefix}.{name}"
        if os.path.exists(candidate):
            found.append((name, candidate))
    return found


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

    # Sidecars travel with the database or the restore is a trap: see SIDECAR_* above.
    prefix = sidecar_prefix(dest)
    for name, src in sidecar_sources(url):
        target = f"{prefix}.{name}"
        if not os.path.exists(src):
            print(f"  sidecar {name}: not present at {src} — nothing to copy")
            continue
        try:
            shutil.copy2(src, target)
        except OSError as e:
            # Loud, and a non-zero exit: a backup missing its allowlist restores into a product
            # nobody can sign in to, and the scheduler must not record that as a good cycle.
            print(f"backup INCOMPLETE: could not copy {name} from {src}: {e}", file=sys.stderr)
            return 3
        print(f"  sidecar {name}: {os.path.getsize(target):,} bytes -> {target}")
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
    found = dict(sidecars_of(path))
    for name in (SIDECAR_ALLOWLIST, SIDECAR_REFERENCE):
        if name in found:
            n = os.path.getsize(found[name])
            state = f"{n:,} bytes" if n else "EMPTY — restoring this would be the same as losing it"
            print(f"  sidecar {name}: {state}")
        else:
            print(f"  sidecar {name}: absent from this backup")
    # An EMPTY allowlist is indistinguishable from a missing one to the gate, which then denies
    # everyone — so it fails the check rather than passing as a file that exists.
    if SIDECAR_ALLOWLIST in found and not os.path.getsize(found[SIDECAR_ALLOWLIST]):
        print(f"verify FAILED: {found[SIDECAR_ALLOWLIST]} is empty", file=sys.stderr)
        return 3
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
        kept = ",".join(n for n, _ in sidecars_of(b["path"])) or "none"
        print(f"  {b['modifiedAt']}  {b['sizeBytes']:>12,}  {b['path']}  sidecars: {kept}")
    print("\nsidecars live now (durable state that is NOT inside the database):")
    for name, src in sidecar_sources(_db_url(args)):
        state = f"{os.path.getsize(src):,} bytes" if os.path.exists(src) else "absent"
        print(f"  {name:<22} {state:>14}  {src}")
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

    stored = dict(sidecars_of(args.backup))
    for name, live in sidecar_sources(url):
        src = stored.get(name)
        if src is None:
            if name == SIDECAR_ALLOWLIST:
                print(f"  WARNING: this backup has no {name}. The beta gate fails CLOSED on an "
                      f"empty list, so unless BETA_ALLOWLIST is set in the environment, NOBODY "
                      f"will be able to sign in — including you. Restore it by hand before "
                      f"starting the engine.", file=sys.stderr)
            else:
                print(f"  note: this backup has no {name}; it will be re-captured, which moves "
                      f"every reader's score to a new baseline.")
            continue
        # Snapshot whatever is there now, exactly as the database restore does: a restore must be
        # undoable, and these are the files someone reaches for when the restore was a mistake.
        if os.path.exists(live):
            keep = f"{live}.replaced-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            try:
                shutil.copy2(live, keep)
                print(f"  previous {name} saved to {keep}")
            except OSError as e:
                print(f"  could not snapshot the current {name} ({e}); leaving it in place",
                      file=sys.stderr)
                continue
        try:
            os.makedirs(os.path.dirname(live) or ".", exist_ok=True)
            shutil.copy2(src, live)
            print(f"  restored {name} -> {live}")
        except OSError as e:
            print(f"  FAILED to restore {name} -> {live}: {e}", file=sys.stderr)
            return 3
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
