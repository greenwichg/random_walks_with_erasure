"""audit_publisher_concentration.py — is articles-per-publisher actually a false-merge detector?

Written to CHALLENGE a proposed heuristic, not to justify it. The claim under test is that a cluster
averaging many articles per publisher is a press-release template rather than a story, and that
rejecting such clusters would measurably improve the catalog.

The claim originally rested on ten hand-read rows from ``audit_clustering_change.py``'s dropped-
coverage table — clusters that one specific change happened to disturb. That is a biased sample: it
contains the clusters a rarity-weighting experiment moved, not a random draw. Generalising "every
real story sits at 1.0-2.2, every template above 5" from it was unsound.

This measures the whole catalog instead, and tests the heuristic against an INDEPENDENT signal.
``geoCoherence`` — the share of a cluster's located members that agree on where the event happened —
is computed from provider-extracted locations and knows nothing about publishers. So it can answer
the question the heuristic cannot answer about itself:

    precision : of the clusters a gate would REMOVE, how many are independently bad?
    recall    : of the clusters independently known to be bad, how many would it CATCH?

A heuristic that removes mostly-good clusters, or catches few of the bad ones, is not worth having
however plausible its story sounds.

    python examples/audit_publisher_concentration.py
    python examples/audit_publisher_concentration.py --incoherent-below 0.7 --show 25
"""

from __future__ import annotations

import argparse

import story_service
import store as store_mod

#: Candidate thresholds to evaluate. A gate rejects any cluster at or above its value.
THRESHOLDS = (2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0)


def per_publisher(story: dict) -> float:
    pubs = story.get("publisherCount") or 0
    return (story["totalCoverage"] / pubs) if pubs else float("inf")


def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.1f}%" if d else "  n/a"


def _percentiles(values: list) -> dict:
    if not values:
        return {}
    s = sorted(values)
    def at(p):
        return s[min(len(s) - 1, int(p / 100.0 * len(s)))]
    return {"p50": at(50), "p75": at(75), "p90": at(90), "p95": at(95), "p99": at(99),
            "max": s[-1]}


def analyse(stories: list, *, incoherent_below: float, min_located: int) -> dict:
    total_articles = sum(s["totalCoverage"] for s in stories)

    # The independent quality signal. Only clusters with enough located members carry one, so the
    # scored subset is much smaller than the catalog — that limit is reported, not hidden.
    scored = [s for s in stories
              if s.get("geoCoherence") is not None and (s.get("locatedMembers") or 0) >= min_located]
    bad = [s for s in scored if s["geoCoherence"] < incoherent_below]

    rows = []
    for t in THRESHOLDS:
        removed = [s for s in stories if per_publisher(s) >= t]
        removed_scored = [s for s in scored if per_publisher(s) >= t]
        caught_bad = [s for s in bad if per_publisher(s) >= t]
        rows.append({
            "threshold": t,
            "storiesRemoved": len(removed),
            "articlesRemoved": sum(s["totalCoverage"] for s in removed),
            # Of the removed clusters that CARRY a coherence score, how many are actually bad?
            "removedScored": len(removed_scored),
            "removedBad": len(caught_bad),
            "precision": (len(caught_bad) / len(removed_scored)) if removed_scored else None,
            "recall": (len(caught_bad) / len(bad)) if bad else None,
        })

    return {
        "stories": len(stories),
        "articles": total_articles,
        "perPublisher": _percentiles([per_publisher(s) for s in stories]),
        "scored": len(scored),
        "bad": len(bad),
        "rows": rows,
        "badClusters": sorted(bad, key=lambda s: s["geoCoherence"]),
        "highConcentration": sorted(stories, key=per_publisher, reverse=True),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--incoherent-below", type=float, default=0.7,
                    help="geoCoherence under this counts as an independently BAD cluster")
    ap.add_argument("--min-located", type=int, default=3,
                    help="located members required before a coherence score is trusted")
    ap.add_argument("--show", type=int, default=20)
    args = ap.parse_args(argv)

    store_ = store_mod.Store(args.db)
    stories = story_service.build_stories(story_service._fetch(store_))
    res = analyse(stories, incoherent_below=args.incoherent_below, min_located=args.min_located)

    print(f"catalog: {res['stories']:,} stories, {res['articles']:,} articles in stories")
    p = res["perPublisher"]
    print("articles-per-publisher distribution: "
          + "  ".join(f"{k}={v:.2f}" for k, v in p.items()))
    print(f"\nindependent quality signal: {res['scored']:,} stories carry a geoCoherence with "
          f">= {args.min_located} located members")
    print(f"  of those, {res['bad']:,} are BAD (coherence < {args.incoherent_below})")

    print(f"\n{'thresh':>7} {'stories':>8} {'articles':>9} {'% arts':>7} "
          f"{'scored':>7} {'bad':>5} {'precision':>10} {'recall':>8}")
    for r in res["rows"]:
        prec = f"{r['precision']:.0%}" if r["precision"] is not None else "   n/a"
        rec = f"{r['recall']:.0%}" if r["recall"] is not None else "   n/a"
        print(f"{r['threshold']:>7.1f} {r['storiesRemoved']:>8,} {r['articlesRemoved']:>9,} "
              f"{_pct(r['articlesRemoved'], res['articles']):>7} "
              f"{r['removedScored']:>7} {r['removedBad']:>5} {prec:>10} {rec:>8}")

    print(f"\n--- the {args.show} most concentrated clusters (what a gate would remove first) ---")
    print(f"{'a/p':>6} {'arts':>5} {'pubs':>5} {'coh':>6}  title")
    for s in res["highConcentration"][:args.show]:
        coh = f"{s['geoCoherence']:.2f}" if s.get("geoCoherence") is not None else "  -"
        print(f"{per_publisher(s):>6.1f} {s['totalCoverage']:>5} {s['publisherCount']:>5} "
              f"{coh:>6}  {s['title'][:60]}")

    print(f"\n--- the {args.show} WORST clusters by independent coherence (what actually needs "
          f"fixing) ---")
    print(f"{'coh':>6} {'a/p':>6} {'arts':>5} {'pubs':>5}  title")
    for s in res["badClusters"][:args.show]:
        print(f"{s['geoCoherence']:>6.2f} {per_publisher(s):>6.1f} {s['totalCoverage']:>5} "
              f"{s['publisherCount']:>5}  {s['title'][:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
