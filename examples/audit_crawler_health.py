"""audit_crawler_health.py — measure the ingestion path against the LIVE catalog. Read-only.

Two questions, both answered from stored rows rather than by asking any publisher, so this is safe
to run at any time and costs one process and a few SELECTs.

    python examples/audit_crawler_health.py --db "$RWE_DB_URL"            # both sections
    python examples/audit_crawler_health.py --db "$RWE_DB_URL" --show 25  # more duplicate examples

**1. What the per-feed scheduler WOULD do** (``feed_schedule``, off by default). Replays the law
against each feed's recorded health — polls, successes, articles imported — and reports the
request volume the current uniform sweep spends versus what an adaptive cadence would spend, plus
which feeds are being asked far more often than they publish and which are failing every cycle
while nothing backs them off. The counterfactual for ``RWE_FEED_SCHEDULER``.

**2. How much duplicate coverage the dedup key is missing.** ``ingest.canonical_url`` keeps the
SCHEME and normalises only host case, ``www.``, the query string and the trailing slash. So
``http://x.com/a`` and ``https://x.com/a`` are two different articles to this catalog, as are a
story and its ``/amp`` variant, and a story and its ``m.`` mobile host. With seven providers
feeding one catalog that is a real duplicate surface, and this section SIZES it before anyone
changes a key that story ids, the feedback ledger and the score cache are all built on.

Nothing here modifies a row. Section 2 in particular only *counts* — widening the dedup key is a
migration, not an edit, and the number below is what decides whether it is worth planning.
"""

from __future__ import annotations

import argparse
import os
import re
from collections import defaultdict

import feed_schedule
import ingest
import story_service
import store as store_mod

#: An ``/amp`` path segment, an ``.amp`` extension, or an ``amp.`` host — the three shapes
#: publishers actually serve. Identification by URL STRUCTURE, not by publisher: no name appears
#: here and none should, or this becomes the site-specific rule the exercise forbids.
_AMP_PATH = re.compile(r"(?:^|/)amp(?:/|$)|\.amp$|\.amp\.html?$", re.IGNORECASE)
_MOBILE_HOST = re.compile(r"^(?:m|mobile|amp)\.", re.IGNORECASE)


def _variant_key(canonical: str) -> str:
    """The canonical URL reduced further: scheme dropped, an ``m.``/``amp.`` host prefix dropped,
    an ``/amp`` path segment dropped. Two articles sharing this key but not their canonical URL are
    the SAME article stored twice."""
    u = canonical
    for prefix in ("https://", "http://"):
        if u.startswith(prefix):
            u = u[len(prefix):]
            break
    host, _, path = u.partition("/")
    host = _MOBILE_HOST.sub("", host)
    path = "/" + path if path else ""
    path = _AMP_PATH.sub("/", path)
    path = re.sub(r"/{2,}", "/", path).rstrip("/")
    return f"{host}{path}"


def scheduler_report(store_) -> dict:
    """Per-feed request volume under the uniform sweep vs the adaptive law."""
    try:
        rows = store_.list_feed_health()
    except Exception:
        rows = []
    sweep = float(os.environ.get("RWE_POLL_INTERVAL", "600") or 600)
    lo, hi = feed_schedule.min_interval(), feed_schedule.max_interval()
    out = []
    for r in rows:
        polls = int(r.get("totalPolls") or 0)
        ok = int(r.get("totalOk") or 0)
        imported = int(r.get("imported") or 0)
        fails = int(r.get("consecutiveFailures") or 0)
        # Where the law would settle this feed. A feed importing on most polls converges to the
        # floor; one that never imports converges to the ceiling; a failing one walks to the
        # ceiling regardless. Settled interval, not a step-by-step replay: the question is the
        # steady state, and the transient is a few cycles either way.
        if fails > 0:
            settled = hi
        elif polls and ok and imported > 0:
            settled = lo
        else:
            settled = hi
        # >1 means the sweep asks this feed MORE often than the law would; <1 means less.
        skew = (settled / sweep) if sweep else 1.0
        out.append({"feed": r.get("feedUrl") or r.get("feed_url"), "name": r.get("name"),
                    "polls": polls, "ok": ok, "imported": imported, "fails": fails,
                    "settled": settled, "skew": skew,
                    "changes": bool(imported > 0 and fails == 0)})
    per_day_now = (86400.0 / sweep) * len(out) if out else 0.0
    per_day_next = sum(86400.0 / r["settled"] for r in out) if out else 0.0
    # The cost that actually matters. Today every request downloads a full document. Under the
    # scheduler only a CHANGED feed does; the rest answer 304 with no body. A feed that changes
    # is assumed to do so on most polls (it is at the floor precisely because it does).
    bodies_now = per_day_now
    bodies_next = sum((86400.0 / r["settled"]) for r in out if r["changes"]) if out else 0.0
    return {"sweep": sweep, "floor": lo, "ceiling": hi, "feeds": len(out),
            "perDayNow": per_day_now, "perDayNext": per_day_next,
            "bodiesNow": bodies_now, "bodiesNext": bodies_next, "rows": out}


def duplicate_report(store_, *, limit: int = 10) -> dict:
    """Articles the current dedup key stores twice because it is scheme- and variant-sensitive."""
    # The clustering scan window, via the same `_fetch` every other audit uses — bounded by time
    # rather than by row count, and it is the population that matters: a duplicate outside the
    # window can no longer affect a story, a feed, or a reader.
    rows = story_service._fetch(store_)
    urls = [r.get("canonicalUrl") or r.get("url") for r in rows]
    groups = defaultdict(set)
    for u in urls:
        if not u:
            continue
        groups[_variant_key(ingest.canonical_url(u))].add(u)
    dupes = {k: sorted(v) for k, v in groups.items() if len(v) > 1}
    by_reason = {"scheme": 0, "amp": 0, "mobile": 0, "other": 0}
    for members in dupes.values():
        schemes = {m.split("://", 1)[0].lower() for m in members if "://" in m}
        if len(schemes) > 1:
            by_reason["scheme"] += len(members) - 1
        elif any(_AMP_PATH.search(m) for m in members):
            by_reason["amp"] += len(members) - 1
        elif any(_MOBILE_HOST.search(m.split("://", 1)[-1]) for m in members):
            by_reason["mobile"] += len(members) - 1
        else:
            by_reason["other"] += len(members) - 1
    extra = sum(len(v) - 1 for v in dupes.values())
    return {"articles": len(urls), "groups": len(dupes), "redundant": extra,
            "share": (extra / len(urls)) if urls else 0.0, "byReason": by_reason,
            "examples": [v for _, v in sorted(dupes.items())[:limit]]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    ap.add_argument("--show", type=int, default=10, help="duplicate example groups to print")
    args = ap.parse_args(argv)
    st = store_mod.Store(args.db)

    s = scheduler_report(st)
    print(f"\n=== per-feed scheduler counterfactual "
          f"(sweep {s['sweep']:.0f}s, floor {s['floor']:.0f}s, ceiling {s['ceiling']:.0f}s) ===")
    print(f"feeds tracked      : {s['feeds']}")
    print(f"requests/day        now {s['perDayNow']:>8,.0f}   ->  after {s['perDayNext']:>8,.0f}")
    print(f"FULL BODIES/day     now {s['bodiesNow']:>8,.0f}   ->  after {s['bodiesNext']:>8,.0f}"
          f"   <- the cost that matters")
    print("  (request count may RISE: the law polls a busy feed harder. What falls is bytes —")
    print("   an unchanged feed answers 304 with no body, which is what buys the faster cadence.)")
    if s["rows"]:
        print(f"\n  {'polls':>6} {'ok':>5} {'imp':>5} {'fail':>4} {'settled':>9} {'vs sweep':>9}  feed")
        for r in sorted(s["rows"], key=lambda r: -r["skew"])[:20]:
            settled = (f"{r['settled'] / 3600:.1f}h" if r["settled"] >= 3600
                       else f"{r['settled'] / 60:.0f}m")
            print(f"  {r['polls']:>6} {r['ok']:>5} {r['imported']:>5} {r['fails']:>4} "
                  f"{settled:>9} {r['skew']:>8.1f}x  {(r['name'] or r['feed'] or '')[:50]}")
        print("  vs sweep >1 = we currently ask it MORE often than it earns; <1 = less.")
        dead = [r for r in s["rows"] if r["fails"] > 0]
        if dead:
            print(f"\n  {len(dead)} feed(s) currently failing and re-asked every sweep — the "
                  f"per-feed breaker is what backs these off:")
            for r in dead[:10]:
                print(f"    {r['fails']:>3} consecutive  {(r['name'] or r['feed'] or '')[:60]}")

    d = duplicate_report(st, limit=args.show)
    print(f"\n=== dedup-key blind spots (canonical_url keeps scheme, /amp, m.) ===")
    print(f"articles in window : {d['articles']:,}")
    print(f"redundant rows     : {d['redundant']:,} ({d['share'] * 100:.2f}% of the window) "
          f"across {d['groups']:,} groups")
    print(f"  by cause         : scheme {d['byReason']['scheme']:,}  "
          f"amp {d['byReason']['amp']:,}  mobile {d['byReason']['mobile']:,}  "
          f"other {d['byReason']['other']:,}")
    for group in d["examples"]:
        print("\n  same article, stored separately:")
        for m in group:
            print(f"    {m[:110]}")
    if not d["groups"]:
        print("  none — the dedup key is not losing anything in this window, and widening it "
              "would be a migration with no measured benefit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
