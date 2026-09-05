#!/usr/bin/env python3
"""archive_export.py — write the archive on demand, and read back what it holds.

    python examples/archive_export.py --db "$RWE_DB_URL" --stats                     # what the archive holds
    python examples/archive_export.py --db "$RWE_DB_URL" --verify                    # re-hash every partition
    python examples/archive_export.py --db "$RWE_DB_URL" --publishers                # snapshot the publisher table
    python examples/archive_export.py --db "$RWE_DB_URL" --story-history-older-than 30 [--prune]
    python examples/archive_export.py --db "$RWE_DB_URL" --articles-older-than 30    # archive only, never deletes

``--dir`` overrides ``RWE_ARCHIVE_DIR`` (default: ``archive/`` beside the SQLite file, which
``backup-offhost.sh`` ships to ``s3://<bucket>/archive/``). Retention's own archive-before-delete
is the switch ``RWE_ARCHIVE_ON_PRUNE=1``; this CLI is the operator's manual path and the nightly
snapshot of the tables retention never touches.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import archive  # noqa: E402
import store  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="SQLAlchemy URL (default: RWE_DB_URL)")
    ap.add_argument("--dir", default=None, help="archive root (default: RWE_ARCHIVE_DIR or beside the DB)")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--publishers", action="store_true")
    ap.add_argument("--story-history-older-than", type=float, default=None, metavar="DAYS")
    ap.add_argument("--prune", action="store_true", help="with --story-history-older-than: delete after archiving")
    ap.add_argument("--articles-older-than", type=float, default=None, metavar="DAYS")
    ap.add_argument("--limit", type=int, default=5000, help="rows per table per pass")
    args = ap.parse_args(argv)

    st = store.Store(args.db or store.default_db_url())
    root = args.dir or archive.root_for(st)
    if not root:
        print("no archive location: pass --dir or set RWE_ARCHIVE_DIR", file=sys.stderr)
        return 2
    out: dict = {"root": root}

    if args.stats:
        manifests = archive.list_manifests(root)
        by_kind: dict = {}
        for m in manifests:
            k = by_kind.setdefault(m["kind"], {"partitions": 0, "rows": 0, "bytes": 0})
            k["partitions"] += 1
            k["rows"] += int(m.get("rows") or 0)
            k["bytes"] += int(m.get("bytes") or 0)
        out["kinds"] = by_kind
        out["partitions"] = len(manifests)
    if args.verify:
        bad = [m["path"] for m in archive.list_manifests(root) if not archive.verify(m["path"])]
        out["verified"] = not bad
        out["corrupt"] = bad
    if args.publishers:
        out["publishers"] = archive.archive_publishers(st, root=root)
    if args.story_history_older_than is not None:
        rows = st.story_history_older_than(args.story_history_older_than, limit=args.limit)
        out["storyHistory"] = archive.archive_story_history(st, rows, root=root)
        if args.prune:
            out["storyHistoryPruned"] = st.prune_story_history(args.story_history_older_than,
                                                               args.limit)
    if args.articles_older_than is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=args.articles_older_than)).isoformat()
        urls = [r["canonicalUrl"] for r in st.list_retention_rows()
                if (r.get("publishedAt") or r.get("fetchedAt") or "") < cutoff][: args.limit]
        out["articles"] = archive.archive_articles(st, urls, root=root)
    print(json.dumps(out, indent=1, default=str))
    return 0 if out.get("verified", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
