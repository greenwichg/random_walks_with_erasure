"""search_index.py — the catalogue's full-text index (``feed_articles_fts``): status, rebuild, probe.

The index is created and kept in step automatically (``store.Store._ensure_search_fts``: FTS5 +
triggers). This CLI is for the two moments an operator needs to touch it:

    python examples/search_index.py status                 # ready? indexed vs catalogue rows
    python examples/search_index.py rebuild                # after a VACUUM (rowids may renumber)
    python examples/search_index.py query "trump apple"    # what term search returns, ranked

``--db`` defaults to ``RWE_DB_URL``. On the production host:

    cd /opt/ih && sudo docker exec -i deploy-api-1 python examples/search_index.py status
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="SQLAlchemy URL (default: RWE_DB_URL)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("rebuild")
    q = sub.add_parser("query")
    q.add_argument("text")
    q.add_argument("--limit", type=int, default=10)
    args = ap.parse_args(argv)
    st = store.Store(args.db or store.default_db_url())
    if args.cmd == "status":
        out = st.search_index_status()
        out["indexErrors"] = [e for e in getattr(st, "index_errors", []) if e[0] == st.FTS_TABLE]
        out["drift"] = (out["indexed"] is not None and out["indexed"] != out["catalogue"])
        print(json.dumps(out, indent=1))
        return 0 if out["ready"] and not out["drift"] else 1
    if args.cmd == "rebuild":
        if not getattr(st, "fts_ready", False):
            print(json.dumps({"error": "index unavailable", "indexErrors": st.index_errors}))
            return 1
        print(json.dumps({"indexed": st.rebuild_search_index()}))
        return 0
    rows, total = st.search_feed_articles(q=args.text, terms=True, sort="relevance",
                                          include_provisional=False)
    print(json.dumps({"query": args.text, "match": store.Store.fts_match_expression(args.text),
                      "total": total,
                      "results": [{"publisher": r["publisher"], "title": r["title"],
                                   "publishedAt": r["publishedAt"]} for r in rows[:args.limit]]},
                     indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
