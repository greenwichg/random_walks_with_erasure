#!/usr/bin/env python3
"""Export one or more users' reading history to a portable, versioned JSON file.

Developer-only utility for offline notebook experimentation. It is strictly READ-ONLY: it opens the
store, reads reading history via the existing ``Store`` APIs (``list_reads`` + ``get_user``), plus
two small local read-only SELECTs for identity / user enumeration (no ``Store`` method exposes those
— the same pattern ``rec_sandbox`` and ``audit_story_coverage`` already use). It never writes to the
database and never touches the recommendation engine, ``evaluate()``, the report contract, or serving
behaviour.

Usage::

    python examples/export_reading_history.py --user user:2
    python examples/export_reading_history.py --user demo
    python examples/export_reading_history.py --all-users
    python examples/export_reading_history.py --user 2 --out my_history.json
    python examples/export_reading_history.py --all-users --out -      # stdout

Output envelope (``version`` makes notebook experiments reproducible if the format later evolves)::

    {"version": 1, "exportedAt": "...", "user": {...}, "readingHistory": [...]}     # single user
    {"version": 1, "exportedAt": "...", "users": [{"user": {...}, "readingHistory": [...]}, ...]}
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import store as store_mod  # noqa: E402

#: Bump when the export shape changes in a way a consumer must notice.
EXPORT_VERSION = 1

#: The persisted demo account, mirrored from ``rec_sandbox`` / ``seed_demo_reader``. Kept local so
#: this utility depends only on ``store`` (importing the eval engine would be far heavier).
_DEMO_PROVIDER = "dev"
_DEMO_ACCOUNT_ID = "demo@infodiet.local"


def _json_safe(obj):
    """Recursively map non-finite floats (NaN/inf) to ``None`` so the result is strict, portable
    JSON. Unknown-outlet lean is stored as ``NaN``; a classifier value could be too."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


# --------------------------------------------------------------------------- #
# Read-only identity / user lookups — no Store method exposes these; keep them local.
# --------------------------------------------------------------------------- #
def _identity_of(store, user_id: int):
    """``(provider, providerAccountId)`` for a user, or ``(None, None)``. Read-only SELECT."""
    try:
        with store.session() as s:
            row = s.scalar(store_mod.select(store_mod.Identity)
                           .where(store_mod.Identity.user_id == user_id)
                           .order_by(store_mod.Identity.id))
            return (row.provider, row.provider_account_id) if row is not None else (None, None)
    except Exception:
        return (None, None)


def _resolve_demo(store):
    """The persisted demo account's user id (read-only), or ``None`` if it isn't provisioned —
    the same lookup ``rec_sandbox._persisted_demo_user_id`` performs."""
    try:
        with store.session() as s:
            row = s.scalar(store_mod.select(store_mod.Identity).where(
                store_mod.Identity.provider == _DEMO_PROVIDER,
                store_mod.Identity.provider_account_id == _DEMO_ACCOUNT_ID))
            return int(row.user_id) if row is not None else None
    except Exception:
        return None


def _all_user_ids(store):
    """Every user id, ascending (read-only SELECT — like ``audit_story_coverage --list-users``)."""
    with store.session() as s:
        return [int(u) for u in s.scalars(
            store_mod.select(store_mod.User.id).order_by(store_mod.User.id)).all()]


def _resolve_selector(store, value: str) -> int:
    """``--user VALUE`` → a user id. Accepts ``user:N``, ``N``, or ``demo``."""
    v = (value or "").strip()
    if v.lower() == "demo":
        uid = _resolve_demo(store)
        if uid is None:
            raise SystemExit(f"no persisted demo account (provider={_DEMO_PROVIDER!r}, "
                             f"providerAccountId={_DEMO_ACCOUNT_ID!r}) in this database")
        return uid
    if v.lower().startswith("user:"):
        v = v.split(":", 1)[1]
    try:
        return int(v)
    except ValueError:
        raise SystemExit(f"could not parse --user {value!r} (use user:N, N, or 'demo')")


# --------------------------------------------------------------------------- #
# Export — reuses store.get_user + store.list_reads for all read data.
# --------------------------------------------------------------------------- #
def export_user(store, user_id: int) -> dict:
    """One user's ``{"user": ..., "readingHistory": [...]}`` block, oldest read first.

    ``store.list_reads`` already carries every field the export needs (canonical URL, the verbatim
    scored payload, the observed timestamp, and the read source); this only projects + renames."""
    provider, account = _identity_of(store, user_id)
    reads = []
    for r in reversed(store.list_reads(user_id)):     # list_reads is newest-first → reverse to chronological
        sc = r.get("scored") or {}
        reads.append({
            "readAt": r.get("observedAt") or sc.get("read_at") or r.get("createdAt"),
            "canonicalUrl": r.get("canonicalUrl"),
            "articleId": sc.get("article_id"),
            "title": sc.get("title"),
            "outlet": sc.get("outlet"),
            "category": sc.get("category"),
            "lean": sc.get("lean"),                   # NaN (unknown outlet) → null via _json_safe
            "emotion": sc.get("emotion"),             # present only when an enricher ran
            "readSource": r.get("readSource"),
        })
    return {"user": {"id": int(user_id), "provider": provider, "providerAccountId": account},
            "readingHistory": reads}


def build_export(store, *, user: "str | None" = None, all_users: bool = False) -> dict:
    """The full versioned export envelope for one selector or every user."""
    env = {"version": EXPORT_VERSION, "exportedAt": datetime.now(timezone.utc).isoformat()}
    if all_users:
        env["users"] = [export_user(store, uid) for uid in _all_user_ids(store)]
    else:
        uid = _resolve_selector(store, user)
        if store.get_user(uid) is None:
            raise SystemExit(f"no user with id {uid} in this database")
        env.update(export_user(store, uid))
    return _json_safe(env)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Read-only export of reading history to portable JSON (developer-only; for "
                    "offline notebook experiments).")
    ap.add_argument("--db", default=None, help="database URL (default: RWE_DB_URL or the repo file)")
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--user", help="a reader: user:N, N, or 'demo'")
    who.add_argument("--all-users", action="store_true", help="every user in the database")
    ap.add_argument("--out", default="reading_history.json",
                    help="output JSON path, or '-' for stdout (default: reading_history.json)")
    args = ap.parse_args(argv)

    store = store_mod.Store(args.db)
    data = build_export(store, user=args.user, all_users=args.all_users)
    text = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)   # strict, portable JSON

    if args.out == "-":
        print(text)
    else:
        pathlib.Path(args.out).write_text(text + "\n", encoding="utf-8")

    n = (sum(len(u["readingHistory"]) for u in data["users"]) if args.all_users
         else len(data["readingHistory"]))
    label = "all users" if args.all_users else f"user {data['user']['id']}"
    dest = "stdout" if args.out == "-" else args.out
    print(f"exported {n} read(s) for {label} -> {dest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
