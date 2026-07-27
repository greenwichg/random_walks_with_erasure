"""backfill_published_at.py — one-time migration: rewrite ``feed_articles.published_at`` to UTC.

``published_at`` is a TEXT column and ``store._search_order`` sorts it **lexicographically**, so a
stored offset made string order disagree with real time. ``rss_ingest._to_iso`` now normalises every
new row to ``+00:00``; this fixes the rows written before that.

    2026-07-27T12:00:00-04:00   is 16:00Z but sorts BELOW
    2026-07-27T16:00:00+00:00   which is the same instant

The clustering candidate set is chosen newest-first from this column, so until the existing rows are
converted, US-Eastern articles keep losing their place in it.

**Safe by construction**: only rewrites values that parse, only when the normalised form differs,
never touches any other column, and re-running is a no-op (UTC in, UTC out). Idempotent.

    python examples/backfill_published_at.py --dry-run     # report only
    python examples/backfill_published_at.py               # apply
"""

from __future__ import annotations

import argparse
import collections
import sys
from datetime import datetime

import rss_ingest
import store as store_mod


def normalise(value: str):
    """The UTC form of a stored timestamp, or ``None`` when it cannot be parsed (left untouched)."""
    s = (value or "").strip()
    if not s:
        return None
    try:
        return rss_ingest.to_utc_iso(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except ValueError:
        return rss_ingest._to_iso(s)      # last resort: the RFC 822 path


def run(store_, *, dry_run: bool = False, batch: int = 1000) -> dict:
    """Convert every non-UTC ``published_at`` in place. Returns a counts summary."""
    stats = collections.Counter()
    offsets = collections.Counter()
    pending: list = []

    with store_.session() as s:
        rows = s.query(store_mod.FeedArticle.canonical_url,
                       store_mod.FeedArticle.published_at).all()

    for url, raw in rows:
        stats["total"] += 1
        if not raw:
            stats["null"] += 1
            continue
        offsets[raw[-6:] if raw[-6] in "+-" else "naive"] += 1
        fixed = normalise(raw)
        if fixed is None:
            stats["unparseable"] += 1
            continue
        if fixed == raw:
            stats["already_utc"] += 1
            continue
        pending.append((url, fixed))

    stats["to_convert"] = len(pending)
    if dry_run or not pending:
        return {"counts": dict(stats), "offsets": dict(offsets.most_common(10)),
                "sample": pending[:5], "applied": 0}

    applied = 0
    for i in range(0, len(pending), batch):
        chunk = pending[i:i + batch]
        with store_.session() as s:
            for url, fixed in chunk:
                row = s.get(store_mod.FeedArticle, url)
                if row is not None:
                    row.published_at = fixed
                    applied += 1
    return {"counts": dict(stats), "offsets": dict(offsets.most_common(10)),
            "sample": pending[:5], "applied": applied}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None, help="RWE_DB_URL override")
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--batch", type=int, default=1000, help="rows per transaction")
    args = ap.parse_args(argv)

    result = run(store_mod.Store(args.db), dry_run=args.dry_run, batch=args.batch)
    c = result["counts"]
    print(f"rows            {c.get('total', 0)}")
    print(f"  null          {c.get('null', 0)}")
    print(f"  already UTC   {c.get('already_utc', 0)}")
    print(f"  unparseable   {c.get('unparseable', 0)} (left untouched)")
    print(f"  to convert    {c.get('to_convert', 0)}")
    print(f"offsets seen    {result['offsets']}")
    for url, fixed in result["sample"]:
        print(f"  e.g. {url[:60]} -> {fixed}")
    print(f"applied         {result['applied']}{' (dry run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
