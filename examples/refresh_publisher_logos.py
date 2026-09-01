"""refresh_publisher_logos.py — resolve outlet logos from their own sites, in bulk and on demand.

The background poller does this a few publishers per cycle (``sources.PublisherLogoResolver``),
which is the right pace for steady state and the wrong one for a cold start over a catalog of
hundreds of outlets. Same code path, same manners (robots.txt is a refusal when absent, one
per-host rate limit, the crawler's User-Agent), run to completion here.

**Idempotent.** Freshness is per row and per verdict (``publisher_logo.TTL_DAYS``): a found logo
is re-verified after 90 days, a site that exposes nothing is re-asked after 30, a transport error
after one. A second run right after a first does nothing and makes no requests — a property of
``pending()``, not of a flag.

    python examples/refresh_publisher_logos.py                  # everything due, in one pass
    python examples/refresh_publisher_logos.py --limit 50       # a bounded batch
    python examples/refresh_publisher_logos.py --publisher "Fox News"
    python examples/refresh_publisher_logos.py --force          # ignore freshness, re-resolve
    python examples/refresh_publisher_logos.py --dry-run        # list what WOULD be fetched
    python examples/refresh_publisher_logos.py --stats          # coverage, no requests
"""

from __future__ import annotations

import argparse

import crawler
import publisher_logo
import store as store_mod


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--limit", type=int, default=0, help="max publishers (0 = all that are due)")
    ap.add_argument("--publisher", default=None, help="resolve exactly one outlet")
    ap.add_argument("--force", action="store_true", help="ignore freshness and re-resolve")
    ap.add_argument("--dry-run", action="store_true", help="list candidates, make no requests")
    ap.add_argument("--stats", action="store_true", help="print cache coverage and exit")
    args = ap.parse_args(argv)

    store_ = store_mod.Store(args.db)

    if args.stats:
        stats = store_.publisher_logo_stats()
        print(f"catalog publishers : {len(store_.catalog_publishers()):,}")
        print(f"resolved rows      : {stats['total']:,}")
        for status, n in sorted(stats["byStatus"].items(), key=lambda kv: -kv[1]):
            print(f"  {status:<8} {n:,}")
        return 0

    if args.publisher:
        candidates = [{"publisher": args.publisher, "articles": 0}]
    elif args.force:
        rows = store_.catalog_publishers()
        candidates = rows[:args.limit] if args.limit else rows
    else:
        limit = args.limit or len(store_.catalog_publishers()) or 1
        candidates = publisher_logo.pending(store_, limit=limit)

    if not candidates:
        print("nothing due — every publisher's logo verdict is fresh")
        return 0

    if args.dry_run:
        print(f"{len(candidates):,} publisher(s) would be resolved:")
        for c in candidates:
            print(f"  {c['articles']:>6}  {c['publisher']}  ({publisher_logo.site_for(store_, c['publisher']) or 'no host'})")
        return 0

    # One policy and one limiter for the whole run: robots.txt read once per host, and the
    # per-host gap honoured across publishers that share an origin.
    policy, limiter = crawler.RobotsPolicy(), crawler.RateLimiter()
    counts: dict = {}
    for c in candidates:
        row = publisher_logo.resolve_publisher(store_, c["publisher"],
                                               fetch_bytes=publisher_logo.default_fetch_bytes,
                                               policy=policy, limiter=limiter)
        status = row.get("status", "error")
        counts[status] = counts.get(status, 0) + 1
        detail = (f"{row.get('width')}x{row.get('height')} {row.get('url')}" if status == "ok"
                  else row.get("reason") or "")
        print(f"{status:<6} {c['publisher']}  {detail}"[:120])

    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
