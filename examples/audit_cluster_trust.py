"""audit_cluster_trust.py — do the launch gates actually fire on the clusters they were built for?

Two gates ship ahead of the linkage fix: the blindspot claim is withheld from clusters the
independent signal contradicts, and the default ranking demotes them. Both key off ``geoCoherence``,
which only 11% of stories carry — so the obvious objection is that they are gates over a signal that
mostly is not there.

The answer this measures is that coverage is not uniform. Scoring needs located members, so it is
thinnest on small clusters and densest on large ones — and the gates only ever apply to large
clusters. **The top-N table below is the check on that claim.** If the biggest clusters are mostly
unscored, both gates are decorative and the thresholds need a size-based fallback instead.

Read that percentage with one caveat it cannot resolve on its own: an unscored cluster may have no
location because extraction missed it, or because the story genuinely has no geography. A phone
launch covered by 26 outlets will never carry an event country, and that is correct behaviour, not
a gap. Look at the titles in the table before concluding the coverage is too thin.

It also prints the two launch monitors, whose trigger levels are agreed in docs/CLUSTER_TRUST.md.
Both are ratios rather than counts, so they stay meaningful as the catalog grows.

    python examples/audit_cluster_trust.py
    python examples/audit_cluster_trust.py --top 20 --show 15
"""

from __future__ import annotations

import argparse

import story_service
import store as store_mod


def analyse(stories: list, *, top: int) -> dict:
    covered = sum(s["totalCoverage"] for s in stories)
    buckets: dict = {}
    for s in stories:
        b = buckets.setdefault(s["clusterTrust"], {"stories": 0, "articles": 0})
        b["stories"] += 1
        b["articles"] += s["totalCoverage"]

    # The claim under test: the gates apply to big clusters, and big clusters are the ones that
    # clear the located-member bar. Ranked by publisherCount because that is what the default sort
    # ranks by — this is the population the gates are supposed to police. "Scored" here means
    # ACTIONABLE, the same bar _cluster_trust uses; a coherence value the gate would not act on is
    # not coverage.
    biggest = sorted(stories, key=lambda s: (s["publisherCount"], s["totalCoverage"]), reverse=True)
    head = biggest[:top]
    scored_head = [s for s in head
                   if s.get("geoCoherence") is not None
                   and (s.get("locatedMembers") or 0) >= story_service.MIN_LOCATED_FOR_TRUST]

    sizes = sorted((s["totalCoverage"] for s in stories))
    p90 = sizes[int(0.9 * (len(sizes) - 1))] if sizes else 0
    largest = sizes[-1] if sizes else 0

    return {
        "stories": len(stories),
        "articles": covered,
        "buckets": buckets,
        "withheld": [s for s in stories if s.get("blindspotWithheld")],
        "topScored": len(scored_head),
        "topTotal": len(head),
        "head": head,
        "demoted": [s for s in stories if s["clusterTrust"] == story_service.TRUST_LOW],
        "unverified": [s for s in stories
                       if s["clusterTrust"] == story_service.TRUST_UNVERIFIED],
        "largestOverP90": round(largest / p90, 1) if p90 else 0.0,
        "largestShare": (largest / covered) if covered else 0.0,
        "largest": largest,
        "p90": p90,
    }


def _row(s: dict) -> str:
    """``loc`` is the column that makes the coherence number readable. A 0.50 on two located
    members is one dissenter and means almost nothing; a 0.50 on sixty is a finding."""
    coh = f"{s['geoCoherence']:.2f}" if s.get("geoCoherence") is not None else "   -"
    return (f"{s['publisherCount']:>5} {s['totalCoverage']:>5} {s.get('locatedMembers', 0):>4} "
            f"{coh:>6} {s['clusterTrust']:>10}  {s['title'][:48]}")


HEAD = f"{'pubs':>5} {'arts':>5} {'loc':>4} {'coh':>6} {'trust':>10}  title"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--top", type=int, default=20,
                    help="how many of the biggest clusters to check for coherence coverage")
    ap.add_argument("--show", type=int, default=10)
    args = ap.parse_args(argv)

    store_ = store_mod.Store(args.db)
    stories = story_service.build_stories(story_service._fetch(store_))
    res = analyse(stories, top=args.top)

    print(f"catalog: {res['stories']:,} stories, {res['articles']:,} articles in stories")
    print(f"config : coherence floor {story_service.coherence_floor()}, "
          f"unverified above {story_service.unverified_size()} members, "
          f"trust ranking {'ON' if story_service.trust_ranking() else 'OFF'}, "
          f"link quorum {story_service.link_quorum():g}")

    print("\ntrust buckets")
    print(f"{'verdict':>12} {'stories':>8} {'articles':>9} {'% arts':>7}")
    for name in (story_service.TRUST_OK, story_service.TRUST_LOW, story_service.TRUST_UNVERIFIED):
        b = res["buckets"].get(name, {"stories": 0, "articles": 0})
        share = f"{100.0 * b['articles'] / res['articles']:.1f}%" if res["articles"] else "  n/a"
        print(f"{name:>12} {b['stories']:>8,} {b['articles']:>9,} {share:>7}")

    # The pre-work measurement. Below ~80% and the gates are mostly missing their targets.
    pct = 100.0 * res["topScored"] / res["topTotal"] if res["topTotal"] else 0.0
    print(f"\ncoherence coverage on the {res['topTotal']} biggest clusters: "
          f"{res['topScored']}/{res['topTotal']} ({pct:.0f}%)")
    print("  -> gates are load-bearing" if pct >= 80.0 else
          "  -> TOO THIN: the gates need a size-based fallback, not a coherence-based one")

    print(f"\n{HEAD}")
    for s in res["head"][:args.show]:
        print(_row(s))

    print(f"\nblindspot claims withheld: {len(res['withheld'])}\n{HEAD}")
    for s in res["withheld"][:args.show]:
        print(_row(s))

    print(f"\ndemoted from the top of the default sort: {len(res['demoted'])}\n{HEAD}")
    for s in res["demoted"][:args.show]:
        print(_row(s))

    print(f"\nnot independently checkable (claims withheld, ranking untouched): "
          f"{len(res['unverified'])}\n{HEAD}")
    for s in res["unverified"][:args.show]:
        print(_row(s))

    print(f"\nlaunch monitors")
    over = res["largestShare"] >= 0.08 or res["largestOverP90"] >= 60.0
    print(f"  largest / p90 story size : {res['largestOverP90']}x  "
          f"({res['largest']} vs {res['p90']})   trigger at 60x")
    print(f"  largest share of covered : {res['largestShare']:.1%}"
          f"                trigger at 8%")
    print("  -> TRIGGERED: the linkage work is now top of the queue" if over else
          "  -> below both triggers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
