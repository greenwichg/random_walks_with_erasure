"""audit_source_discovery.py — M7: find candidate sources, and price the crawl before running it.

**Stages 1 and 2 of `docs/SCALE_ROADMAP.md`.** Read-only: no writes, no ingestion, no curation.

## Offline by default, and that is the safety property

Stage 1 (discovery) needs **no network at all** — the crawl exhaust is already in the catalog. Stage
2 (validation) is the first thing in this entire roadmap that touches a publisher, and the roadmap
is explicit that it "does not start without an explicit go-ahead".

So a bare run does Stage 1, applies the three offline gates, and **prints what Stage 2 would cost**
— hosts, requests, wall time. That number is what a ToS review is actually asking about, and it
belongs in front of a human before any request, not in a post-hoc report.

`--probe` performs the network pass. Without it `source_validation.validate` is called with no
fetcher, and it has none of its own: every network gate reports `UNKNOWN`, never `PASS`. An offline
run cannot be mistaken for a validated one, structurally rather than by convention.

## What `--probe` does NOT do

It does not ingest. It does not write a feed row, a catalog row, or a tier assignment. It reads
`robots.txt`, one landing page and at most one feed per host, and prints a verdict. **Admitting a
source is a separate, human step** — M9 emits config; M7 emits a worklist.

    # Stage 1 only. No request is made. This is the run to look at first.
    dc run --rm -T api python examples/audit_source_discovery.py --db "$RWE_DB_URL"

    # Stage 2. Touches publishers. Requires the ToS/robots review to have happened.
    dc run --rm -T api python examples/audit_source_discovery.py --db "$RWE_DB_URL" \\
        --probe --limit 5
"""

from __future__ import annotations

import argparse
import os
from collections import Counter

import crawler
import outlet_registry
import source_discovery as sd
import source_validation as sv
import story_service
import store as store_mod


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    ap.add_argument("--floor", type=int, default=sd.VOLUME_FLOOR,
                    help="articles on a host before it is worth a request (default: %(default)s)")
    ap.add_argument("--show", type=int, default=30, help="candidates to list")
    ap.add_argument("--probe", action="store_true",
                    help="STAGE 2: make network requests to candidate hosts. Reads robots.txt, one "
                         "landing page and at most one feed per host. Requires the ToS/robots "
                         "review; without this flag no request is made and every network gate "
                         "reports UNKNOWN.")
    ap.add_argument("--limit", type=int, default=0,
                    help="with --probe, stop after this many hosts (0 = all). Start small.")
    ap.add_argument("--interval", type=float, default=crawler.DEFAULT_MIN_INTERVAL,
                    help="seconds between requests to the same host (default: %(default)s)")
    args = ap.parse_args(argv)

    st = store_mod.Store(args.db)
    reg = outlet_registry.default_registry()
    rows = story_service._fetch(st)

    cands = sd.candidates(rows, reg, floor=args.floor)
    work = sd.worklist(cands)
    stats = sd.census(cands)
    cost = sd.probe_cost(cands, seconds_per_request=args.interval)

    print(f"window        : {len(rows):,} articles")
    print(f"hosts seen    : {stats.get('total', 0):,}")
    print(f"  already tracked by the registry : {stats.get('tracked', 0):,}")
    print(f"  aggregator / proxy hosts        : {stats.get('proxy', 0):,}")
    print(f"  below the {args.floor}-article floor          : {stats.get('belowFloor', 0):,}")
    print(f"  CANDIDATES                      : {stats.get('eligible', 0):,}")

    # ------------------------------------------------------------------ the language gap
    #
    # `rss_ingest.parse_feed` did not read a feed's declared <language> until it was taught to, so
    # every RSS-ingested row carried NULL and the only values present came from the other adapters.
    #
    # The fix is NOT forward-only, which is a correction to what this section first claimed.
    # `store.upsert_feed_article` backfills a field that was empty (`if language and not
    # row.language`), so every re-poll fills in the articles a feed is still serving. Measured: `rss`
    # reached 12% within minutes of the deploy, far more than new ingestion could explain. So the
    # share should climb fast and then PLATEAU below 100% — rows that aged out of their feed before
    # the fix landed are never revisited and keep NULL until retention removes them.
    by_type: dict = {}
    for r in rows:
        t = (r.get("sourceType") or "(none)").strip() or "(none)"
        known, total = by_type.get(t, (0, 0))
        by_type[t] = (known + (1 if (r.get("language") or "").strip() else 0), total + 1)
    print(f"\n=== language coverage, by source ===")
    print("    RSS carried NO language at all until parse_feed was taught to read the feed's own")
    print("    <language>. A re-poll BACKFILLS an empty field, so the fix reaches articles a feed")
    print("    is still serving — not just new ones. Expect `rss` to climb fast and then plateau")
    print("    below 100%: rows that aged out of their feed before the fix are never revisited.")
    print("    A source at 0% is not broken — the keyed adapters take language from the QUERY, so")
    print("    0% means that adapter's LANGUAGE combo axis is unset. Setting it would populate the")
    print("    field AND narrow what the API returns, which is a tradeoff, not a free win.")
    print(f"\n  {'source':<12} {'known':>8} {'rows':>8} {'share':>7}")
    for t, (known, total) in sorted(by_type.items(), key=lambda kv: -kv[1][1]):
        print(f"  {t[:12]:<12} {known:>8,} {total:>8,} {known / max(1, total):>6.0%}")

    print(f"\n=== what Stage 2 would cost ===")
    print("    Stated BEFORE any request, because 'how much of a publisher's bandwidth are we")
    print("    about to spend' is the question a ToS review is actually asking.")
    print(f"  {cost['hosts']:,} hosts x 2 requests = {cost['requests']:,} requests")
    print(f"  at {args.interval:g}s politeness  = {cost['minutes']:.1f} minutes of crawling")

    print(f"\n=== the candidates ===")
    print("    Rejections are listed too. A discovery run that silently dropped them could not be")
    print("    audited, and the rejection counts are the cheapest evidence the gates do anything.")
    print("    These are hosts we ALREADY INGEST — this is the crawl-exhaust channel, so a high")
    print("    article count means we carry it heavily without a registry row, not that it is new.")
    print("    `lang` is what OUR catalog recorded, and `?` is a gap in our metadata rather than a")
    print("    fact about the source; gate 6 reads it as UNKNOWN and the feed settles it.")
    print("    There is deliberately no `dated` column: for a time-windowed fetch every row carries")
    print("    a date by construction, so it would read 100% for everything. Gate 4 asks the feed.")
    print(f"\n  {'arts':>6} {'lang':>5}  {'host':<34} why")
    for c in cands[:args.show]:
        mark = " " if c["eligible"] else "x"
        print(f" {mark}{c['articles']:>6} {(c['language'] or '?')[:5]:>5}  "
              f"{c['host'][:34]:<34} {c['reason']}")

    if not args.probe:
        print(f"\n=== STAGE 2 NOT RUN ===")
        print("  No request was made. Every network gate reports UNKNOWN, which is not a pass:")
        print("  claiming a publisher's robots.txt permits us without having read it is exactly the")
        print("  shape of error this audit series keeps finding in its own instruments.")
        print("\n  Before --probe, the roadmap asks for a ToS / robots review that has never been")
        print("  done. CRAWLER_DESIGN.md records that no live crawl has ever run and that")
        print("  crawler.py's configured patterns are unverified guesses. This is the first thing")
        print("  in the whole roadmap that touches a publisher.")
        if work:
            print(f"\n  When authorised, start small:  --probe --limit 5")
        return 0

    # ---------------------------------------------------------------- Stage 2
    targets = work[:args.limit] if args.limit else work
    print(f"\n=== STAGE 2: PROBING {len(targets):,} HOSTS ===")
    print(f"    User-Agent: {crawler.USER_AGENT}")
    print(f"    robots.txt is fail-CLOSED: absent or unparseable is a refusal, not permission.")
    print(f"    Rate limited to {args.interval:g}s per host. No ingestion, no writes.")

    limiter = crawler.RateLimiter(default_interval=args.interval)
    robots = crawler.RobotsPolicy()
    results, spent = [], 0
    for c in targets:
        r = sv.validate(c, fetch=crawler._fetch_text, robots=robots, limiter=limiter)
        results.append(r)
        spent += r["requests"]
        print(f"\n  {r['verdict']:<11} {c['host']}")
        for g in r["gates"]:
            if g.status != sv.PASS:
                print(f"      gate {g.number} {g.name}: {g.status} — {g.detail}")
        if r["feed"]:
            print(f"      feed: {r['feed']}")

    census = Counter(r["verdict"] for r in results)
    print(f"\n=== results ===")
    for name, n in census.most_common():
        print(f"  {n:>5} hosts  {name}")
    print(f"\n  requests actually spent : {spent:,}")
    print(f"  waited for politeness   : {limiter.waited_seconds:.1f}s")

    admitted = [r for r in results if r["verdict"] == "ADMIT"]
    print(f"\n=== what to do with the {len(admitted)} ADMIT verdicts ===")
    print("  Nothing automatic. M7 emits a worklist, not an ingestion. Admitting a source means")
    print("  adding its feed and putting the outlet in RWE_CORPUS_SHADOW, where M8 measures it for")
    print("  14 days and M9 decides — and M9 emits config for a human rather than applying it.")
    for r in admitted:
        print(f"    {r['host']:<34} {r['feed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
