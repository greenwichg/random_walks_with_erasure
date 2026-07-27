"""refresh_publisher_metadata.py — fill or refresh the publisher metadata cache from Wikipedia.

The background poller already does this a few publishers per cycle, which is the right pace for
steady state but slow for a cold start. This is the same code path, run in bulk and on demand.

**Idempotent.** Freshness is per row and per status (see ``publisher_metadata.TTL_DAYS``), so a
second run right after a first does nothing and makes no requests. That is a property of
``pending()``, not of a flag, which is what makes it safe to put on a cron or rerun after a
failure — there is no "already done" state to corrupt.

    python examples/refresh_publisher_metadata.py                 # everything due, in one pass
    python examples/refresh_publisher_metadata.py --limit 20      # a bounded batch
    python examples/refresh_publisher_metadata.py --publisher "BBC News"   # one outlet
    python examples/refresh_publisher_metadata.py --force         # ignore freshness, re-ask
    python examples/refresh_publisher_metadata.py --dry-run       # list what WOULD be fetched
    python examples/refresh_publisher_metadata.py --stats         # coverage, no requests
"""

from __future__ import annotations

import argparse
import time

import publisher_metadata
import publisher_wiki
import sources
import store as store_mod

#: Courtesy pause between publishers. Wikimedia's capacity is not the constraint — being visibly
#: well-behaved is. Serial requests with a small gap is what their etiquette guidance asks for.
DEFAULT_DELAY = 0.25


def _fetch_json(url: str) -> dict:
    """Wikimedia through the shared HTTP chassis: retry/backoff discipline, and the descriptive
    User-Agent their policy requires (requests without one are refused outright)."""
    return sources._get_json(url, headers={"User-Agent": publisher_wiki.USER_AGENT}, timeout=20.0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--limit", type=int, default=0, help="max publishers (0 = all that are due)")
    ap.add_argument("--publisher", default=None, help="refresh exactly one outlet")
    ap.add_argument("--force", action="store_true", help="ignore freshness and re-ask")
    ap.add_argument("--dry-run", action="store_true", help="list candidates, make no requests")
    ap.add_argument("--stats", action="store_true", help="print cache coverage and exit")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    args = ap.parse_args(argv)

    store_ = store_mod.Store(args.db)

    if args.stats:
        stats = store_.publisher_metadata_stats()
        total_publishers = len(store_.catalog_publishers())
        print(f"catalog publishers : {total_publishers:,}")
        print(f"cached rows        : {stats['total']:,}")
        for status, n in sorted(stats["byStatus"].items(), key=lambda kv: -kv[1]):
            print(f"  {status:<10} {n:,}")
        return 0

    if args.publisher:
        candidates = [{"publisher": args.publisher, "articles": 0}]
    elif args.force:
        rows = store_.catalog_publishers()
        candidates = rows[:args.limit] if args.limit else rows
    else:
        limit = args.limit or len(store_.catalog_publishers()) or 1
        candidates = publisher_metadata.pending(store_, limit=limit)

    if not candidates:
        print("nothing due — every publisher's metadata is fresh")
        return 0

    if args.dry_run:
        print(f"{len(candidates):,} publisher(s) would be fetched:")
        for c in candidates:
            print(f"  {c['articles']:>6}  {c['publisher']}")
        return 0

    counts: dict = {}
    for i, c in enumerate(candidates):
        row = publisher_metadata.enrich_publisher(store_, c["publisher"], fetch_json=_fetch_json)
        status = (row or {}).get("status", "error")
        counts[status] = counts.get(status, 0) + 1
        detail = row.get("wikipediaTitle") or row.get("error") or ""
        print(f"{status:<10} {c['publisher']}  {detail}"[:110])
        if args.delay and i < len(candidates) - 1:
            time.sleep(args.delay)

    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
