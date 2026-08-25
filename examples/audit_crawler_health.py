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
import math
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


def observed_state(store_) -> dict:
    """What the scheduler has ACTUALLY done, read from the state it persists.

    The counterfactual section below is a model. This one is an observation, and it answers the
    question the whole cadence argument rests on: **do our feeds serve validators at all?** A feed
    with a stored ``etag`` or ``last_modified`` can answer 304 and its unchanged polls are nearly
    free; a feed with neither downloads a full body every time whatever the cadence, so for that
    feed a faster interval is a straight cost increase.

    This exists because the obvious way to ask — grepping the poller log for the ``notModified``
    counter — cannot work: the cycle aggregate carries the count but nothing ever logs it. Reading
    the persisted columns needs no new logging and is true of the whole history, not of whichever
    window happens to still be in the log buffer."""
    try:
        rows = [r for r in store_.list_feed_health() if is_rss_feed(r.get("feedUrl"))]
    except Exception:
        return {"tracked": 0, "rows": []}
    out = []
    for r in rows:
        st = store_.feed_schedule_state(r.get("feedUrl"))
        out.append({"name": r.get("name") or r.get("feedUrl"),
                    "etag": bool(st.get("etag")), "lastmod": bool(st.get("last_modified")),
                    "interval": st.get("interval_s"), "due": st.get("next_due_at")})
    return {"tracked": len(out), "rows": out,
            "withValidators": sum(1 for r in out if r["etag"] or r["lastmod"]),
            "scheduled": sum(1 for r in out if r["interval"])}


def is_rss_feed(feed_url: str) -> bool:
    """Whether this health row is an RSS FEED rather than a keyed-JSON/API adapter.

    The distinction is load-bearing and was missed on the first production read. ``feed_health``
    rows are keyed by feed URL for RSS (``https://…/rss.xml``) and by a synthetic adapter key for
    everything else (``gdelt://doc``, ``newsapi://top-headlines``, ``mediastack://news``). The
    per-feed scheduler lives in ``sources.RSSAdapter._ingest_scheduled`` and therefore covers ONLY
    the first kind. Adapters are scheduled by ``MultiSourcePoller._effective_interval``, which
    already backs them off on sustained failure — reporting them as "re-asked every sweep" claimed
    a problem that rule had already solved, and credited this change with fixing it."""
    return str(feed_url or "").lower().startswith(("http://", "https://"))


def equilibrium_interval(rate_per_day: float, *, lo: float, hi: float) -> float:
    """Where ``feed_schedule.advance`` settles a feed publishing ``rate_per_day`` articles.

    The law multiplies the interval by ``speedup`` when a poll finds something and by ``slowdown``
    when it does not, so the interval stops moving where the expected log-step is zero::

        p*ln(speedup) + (1 - p)*ln(slowdown) = 0
        p* = ln(slowdown) / (ln(slowdown) - ln(speedup))

    ``p*`` is the CHANGE RATE the law converges to — with the shipped 0.5/1.5 pair, 0.369. A feed
    publishing N articles a day is changed on a fraction ``N*T/86400`` of polls, so the settled
    interval is ``p* * 86400 / N``, clamped.

    Derived from the same env knobs the law reads, so tuning ``RWE_FEED_SPEEDUP`` cannot silently
    put this instrument and the mechanism it measures on different models."""
    sp, sl = feed_schedule.speedup(), feed_schedule.slowdown()
    if not (0 < sp < 1 < sl):
        return max(lo, min(86400.0 / max(rate_per_day, 1e-9), hi))   # degenerate knobs
    p_star = math.log(sl) / (math.log(sl) - math.log(sp))
    return max(lo, min(p_star * 86400.0 / max(rate_per_day, 1e-9), hi))


def scheduler_report(store_, rate_per_day: "dict | None" = None) -> dict:
    """Per-feed request volume under the uniform sweep vs the adaptive law.

    **The publish rate comes from the CATALOG, not from ``feed_health.imported``.** That column is
    assigned, not accumulated (``store.record_feed_health``: ``row.imported = ...``), so it holds
    the LAST cycle only. An earlier revision of this function keyed the settled interval on
    ``imported > 0`` and produced nonsense on the live catalog — CNN read as quiet and NPR as busy
    because of what happened in one 10-minute window. Counting the articles a publisher actually
    contributed over the whole scan window is the honest signal, and it is already loaded for the
    duplicate section.

    The settled interval is the **fixed point of the law as implemented**, not a convenient proxy
    for it — see :func:`equilibrium_interval`. An earlier revision used ``86400/N`` (the interval
    at which every poll finds exactly one article) and was wrong by 2.7x in the SLOW direction,
    which made the change read as a freshness regression it is not. A feed we cannot match to any
    catalog articles is reported as UNKNOWN rather than assumed quiet — the failure mode of
    assuming is a real feed silently pushed to the ceiling."""
    try:
        rows = store_.list_feed_health()
    except Exception:
        rows = []
    rate_per_day = rate_per_day or {}
    sweep = float(os.environ.get("RWE_POLL_INTERVAL", "600") or 600)
    lo, hi = feed_schedule.min_interval(), feed_schedule.max_interval()
    out = []
    for r in rows:
        polls = int(r.get("totalPolls") or 0)
        ok = int(r.get("totalOk") or 0)
        fails = int(r.get("consecutiveFailures") or 0)
        name = r.get("name") or ""
        rate = rate_per_day.get(name.strip().lower())
        imported = int(round(rate)) if rate is not None else -1
        if fails > 0:
            settled = hi                     # broken: the breaker walks it out regardless
        elif rate is None:
            settled = sweep                  # unknown: assume nothing, change nothing
        elif rate <= 0:
            settled = hi
        else:
            settled = equilibrium_interval(rate, lo=lo, hi=hi)
        # >1 means the sweep asks this feed MORE often than the law would; <1 means less.
        skew = (settled / sweep) if sweep else 1.0
        feed_url = r.get("feedUrl") or r.get("feed_url")
        out.append({"feed": feed_url, "rss": is_rss_feed(feed_url), "name": r.get("name"),
                    "polls": polls, "ok": ok, "imported": imported, "fails": fails,
                    "settled": settled, "skew": skew, "rate": rate,
                    "changes": bool(rate and rate > 0 and fails == 0)})
    # Only RSS rows are in scope: the scheduler cannot change an adapter's cadence.
    scoped = [r for r in out if r["rss"]]
    per_day_now = (86400.0 / sweep) * len(scoped) if scoped else 0.0
    per_day_next = sum(86400.0 / r["settled"] for r in scoped) if scoped else 0.0
    # The cost that actually matters. Today every request downloads a full document. Under the
    # scheduler only a CHANGED feed does; the rest answer 304 with no body. A feed that changes
    # is assumed to do so on most polls (it is at the floor precisely because it does).
    bodies_now = per_day_now
    bodies_next = sum((86400.0 / r["settled"]) for r in scoped if r["changes"]) if scoped else 0.0
    return {"sweep": sweep, "floor": lo, "ceiling": hi,
            "feeds": len(scoped), "adapters": len(out) - len(scoped),
            "perDayNow": per_day_now, "perDayNext": per_day_next,
            "bodiesNow": bodies_now, "bodiesNext": bodies_next, "rows": out}


def duplicate_report(store_, *, limit: int = 10) -> dict:
    """Articles the current dedup key stores twice because it is scheme- and variant-sensitive."""
    # The clustering scan window, via the same `_fetch` every other audit uses — bounded by time
    # rather than by row count, and it is the population that matters: a duplicate outside the
    # window can no longer affect a story, a feed, or a reader.
    rows = story_service._fetch(store_)
    urls = [r.get("canonicalUrl") or r.get("url") for r in rows]
    # Publisher -> articles per day over this window, the honest publish-rate signal the
    # scheduler section needs (feed_health.imported is last-cycle only).
    span_days = max(1.0, float(os.environ.get("RWE_STORIES_SCAN_DAYS", "6") or 6))
    per_pub: dict = defaultdict(int)
    for r in rows:
        pub = (r.get("publisher") or "").strip().lower()
        if pub:
            per_pub[pub] += 1
    rate_per_day = {k: v / span_days for k, v in per_pub.items()}
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
            "ratePerDay": rate_per_day,
            "examples": [v for _, v in sorted(dupes.items())[:limit]]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    ap.add_argument("--show", type=int, default=10, help="duplicate example groups to print")
    args = ap.parse_args(argv)
    st = store_mod.Store(args.db)

    d = duplicate_report(st, limit=args.show)
    s = scheduler_report(st, d["ratePerDay"])

    o = observed_state(st)
    print(f"\n=== observed scheduler state (what has actually happened) ===")
    if not o["scheduled"]:
        print("  the scheduler has not run yet on any feed — RWE_FEED_SCHEDULER off, or no poll")
        print("  cycle has completed since it was enabled. Everything below is still a MODEL.")
    else:
        print(f"  feeds carrying a validator : {o['withValidators']}/{o['tracked']}  "
              f"<- decides whether unchanged polls are free")
        print(f"  feeds the law has settled  : {o['scheduled']}/{o['tracked']}")
        print(f"\n  {'etag':>5} {'lastmod':>8} {'interval':>9}  feed")
        for r in sorted(o["rows"], key=lambda r: (r["interval"] or 0)):
            iv = "" if not r["interval"] else (f"{r['interval'] / 3600:.1f}h"
                                               if r["interval"] >= 3600
                                               else f"{r['interval'] / 60:.0f}m")
            print(f"  {'yes' if r['etag'] else '-':>5} {'yes' if r['lastmod'] else '-':>8} "
                  f"{iv:>9}  {str(r['name'])[:56]}")
        if o["withValidators"] == 0:
            print("\n  NO feed serves a validator. Every poll downloads a full body whatever the")
            print("  cadence, so the FULL BODIES estimate above is wrong and a faster floor is a")
            print("  straight cost increase. Turn RWE_FEED_SCHEDULER off or raise the floor.")
    print(f"\n=== per-feed scheduler counterfactual "
          f"(sweep {s['sweep']:.0f}s, floor {s['floor']:.0f}s, ceiling {s['ceiling']:.0f}s) ===")
    print(f"RSS feeds in scope : {s['feeds']}   "
          f"(+{s['adapters']} API adapters the scheduler does NOT touch)")
    print(f"requests/day        now {s['perDayNow']:>8,.0f}   ->  after {s['perDayNext']:>8,.0f}")
    print(f"FULL BODIES/day     now {s['bodiesNow']:>8,.0f}   ->  after {s['bodiesNext']:>8,.0f}"
          f"   <- the cost that matters")
    print("  (request count may RISE: the law polls a busy feed harder. What falls is bytes —")
    print("   an unchanged feed answers 304 with no body, which is what buys the faster cadence.)")
    if s["rows"]:
        print(f"\n  {'polls':>6} {'ok':>5} {'arts/d':>7} {'fail':>4} {'settled':>9} "
              f"{'vs sweep':>9}  feed")
        for r in sorted([x for x in s["rows"] if x["rss"]], key=lambda r: -r["skew"])[:25]:
            settled = (f"{r['settled'] / 3600:.1f}h" if r["settled"] >= 3600
                       else f"{r['settled'] / 60:.0f}m")
            rate = "     ?" if r["rate"] is None else f"{r['rate']:>6.1f}"
            print(f"  {r['polls']:>6} {r['ok']:>5} {rate:>7} {r['fails']:>4} "
                  f"{settled:>9} {r['skew']:>8.1f}x  {(r['name'] or r['feed'] or '')[:50]}")
        print("  vs sweep >1 = we currently ask it MORE often than it earns; <1 = less.")
        print("  arts/d ? = no catalog articles matched this health row's name. Reported, never")
        print("  assumed: an unmatched feed keeps the current sweep interval in this estimate.")
        dead_rss = [r for r in s["rows"] if r["fails"] > 0 and r["rss"]]
        dead_api = [r for r in s["rows"] if r["fails"] > 0 and not r["rss"]]
        if dead_rss:
            print(f"\n  {len(dead_rss)} RSS feed(s) failing — the per-feed breaker in this change "
                  f"is what backs these off:")
            for r in dead_rss[:10]:
                print(f"    {r['fails']:>3} consecutive  {(r['name'] or r['feed'] or '')[:60]}")
        if dead_api:
            print(f"\n  {len(dead_api)} API adapter(s) failing. NOT in this change's scope — "
                  f"MultiSourcePoller._effective_interval")
            print("  already backs an adapter off on sustained failure (4x its own interval at "
                  "this depth,")
            print("  capped at RWE_SOURCE_MAX_INTERVAL). Listed for visibility, not as a gap:")
            for r in dead_api[:10]:
                print(f"    {r['fails']:>3} consecutive  {(r['name'] or r['feed'] or '')[:60]}")

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
