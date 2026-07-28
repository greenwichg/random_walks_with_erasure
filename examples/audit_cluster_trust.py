"""audit_cluster_trust.py — do the launch gates actually fire on the clusters they were built for?

Two gates ship ahead of the linkage fix: the blindspot claim is withheld from clusters the
independent signal contradicts, and the default ranking demotes them. Both key off ``geoCoherence``,
which only 11% of stories carry — so the obvious objection is that they are gates over a signal that
mostly is not there.

The answer this measures is that coverage is not uniform. Scoring needs located members, so it is
thinnest on small clusters and densest on large ones — and the gates only ever apply to large
clusters. **The top-N table below is the check on that claim.** Measured on the live catalog it
holds where it matters: the largest cluster carries 72 located members and is caught.

Two things the table has already shown that the aggregate hid:

* Unscored is not one thing. A cluster with NO located members may simply have no geography — a
  phone launch covered by 26 outlets has no event country, and that is correct. A cluster with a
  few locations but too few to act on is an extraction-depth problem. Only the second counts
  against the gate, so only the second is in the denominator.
* The blindspot gate and the coherence signal barely overlap. Blindspots arise in SMALL clusters
  (few publishers, so a side goes uncovered); coherence needs FOUR located members, which small
  clusters rarely have. On the live catalog the gate withholds nothing, because the clusters it
  distrusts are large enough to have coverage on every side. ``blindspot claims withheld`` is the
  number that says so — read it as a measurement, not as a target.

It also prints the two launch monitors, whose trigger levels are agreed in docs/CLUSTER_TRUST.md.
Both are ratios rather than counts, so they stay meaningful as the catalog grows.

    python examples/audit_cluster_trust.py
    python examples/audit_cluster_trust.py --top 20 --show 15
"""

from __future__ import annotations

import argparse

import publisher_identity
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
    # Unscored splits into two different problems and only one of them is ours. A cluster with NO
    # located members may simply have no geography — a phone launch covered by 26 outlets has no
    # event country, and that is correct behaviour rather than a gap. A cluster with some locations
    # but too few to act on is a depth problem in extraction. Reporting them together produced a
    # "TOO THIN" verdict on a catalog whose largest clusters are in fact well covered.
    geoless_head = [s for s in head if not (s.get("locatedMembers") or 0)]
    locatable_head = [s for s in head if (s.get("locatedMembers") or 0)]

    sizes = sorted((s["totalCoverage"] for s in stories))
    p90 = sizes[int(0.9 * (len(sizes) - 1))] if sizes else 0
    largest = sizes[-1] if sizes else 0

    return {
        "stories": len(stories),
        "articles": covered,
        "buckets": buckets,
        "withheld": [s for s in stories if s.get("blindspotWithheld")],
        "claims": len([s for s in stories if s.get("blindspotSide")]),
        "claimSupport": claim_support(stories),
        "topScored": len(scored_head),
        "topTotal": len(head),
        "topGeoless": len(geoless_head),
        "topLocatable": len(locatable_head),
        "head": head,
        "demoted": [s for s in stories if s["clusterTrust"] == story_service.TRUST_LOW],
        "unverified": [s for s in stories
                       if s["clusterTrust"] == story_service.TRUST_UNVERIFIED],
        "largestOverP90": round(largest / p90, 1) if p90 else 0.0,
        "largestShare": (largest / covered) if covered else 0.0,
        "largest": largest,
        "p90": p90,
    }


def rated_publishers(story: dict) -> int:
    """Distinct publishers in this story that carry a lean rating — the sample the blindspot claim
    is actually computed from. Unrated outlets cast no vote, so they are not it."""
    return len({c["publisher"] for c in story["coverage"] if c.get("leanBucket")})


def claim_support(stories: list) -> dict:
    """How many rated publishers stand behind each blindspot claim.

    The same defect the coherence gate had before ``MIN_LOCATED_FOR_TRUST``: a ratio acted on
    without a sample-size floor. With ONE rated publisher the distribution is 1.0 in that bucket,
    two buckets are empty, and ``_blindspot`` fires — so the story asserts "nobody on the left
    covered this" on the evidence of a single outlet. That is absence of evidence reported as
    evidence of absence, and it is structural: any story with fewer than three lean-diverse rated
    publishers has an empty bucket by arithmetic, not by editorial fact."""
    buckets: dict = {}
    for s in stories:
        if not s.get("blindspotSide"):
            continue
        n = rated_publishers(s)
        key = str(n) if n < 4 else "4+"
        buckets[key] = buckets.get(key, 0) + 1
    return buckets


def blocked_by_ratings(stories: list, *, min_rated: int) -> dict:
    """Stories that have the OUTLETS for a coverage-gap claim but not the RATINGS, and which
    unrated outlets would unlock them.

    Curating seven outlets moved claims 61 -> 62, and the reason is that adding a rating does two
    opposite things: it can lift a story from two rated publishers to three (enabling a claim) and
    it can fill an empty lean bucket in a story that already had three (removing one). Volume alone
    cannot predict which — an outlet with 60 articles spread over 60 stories that each already have
    four rated publishers unlocks nothing.

    So the worklist is not "unrated outlets by article count". It is "unrated outlets appearing in
    stories that are exactly ONE rating short", because those are the only ones a single row can
    convert. Stories two or three short need coordinated curation and are counted separately."""
    keys = publisher_identity.groups({c["publisher"] for s in stories for c in s["coverage"]})
    short: dict = {}
    unlock: dict = {}
    for idx, story in enumerate(stories):
        outlets = {keys.get(c["publisher"], c["publisher"]) for c in story["coverage"]}
        rated = {keys.get(c["publisher"], c["publisher"])
                 for c in story["coverage"] if c.get("leanBucket")}
        if len(outlets) < min_rated or len(rated) >= min_rated:
            continue
        gap = min_rated - len(rated)
        short[gap] = short.get(gap, 0) + 1
        if gap == 1:
            for c in story["coverage"]:
                k = keys.get(c["publisher"], c["publisher"])
                if k not in rated:
                    # Keyed on the story's INDEX, not its title. Titles are not unique — two
                    # stories can share one, and keying on it silently undercounts the outlet.
                    unlock.setdefault(c["publisher"], set()).add(idx)
    return {"short": short,
            "blocked": sum(short.values()),
            "unlock": sorted(((len(v), n) for n, v in unlock.items()), reverse=True)}


def _row(s: dict) -> str:
    """``loc`` is the column that makes the coherence number readable. A 0.50 on two located
    members is one dissenter and means almost nothing; a 0.50 on sixty is a finding."""
    coh = f"{s['geoCoherence']:.2f}" if s.get("geoCoherence") is not None else "   -"
    return (f"{s['publisherCount']:>5} {s['totalCoverage']:>5} {s.get('locatedMembers', 0):>4} "
            f"{coh:>6} {s['clusterTrust']:>10}  {s['title'][:48]}")


HEAD = f"{'pubs':>5} {'arts':>5} {'loc':>4} {'coh':>6} {'trust':>10}  title"

#: Locatable clusters required before the coverage percentage gets a pass/fail verdict. Below this
#: one cluster swings it far enough to invent a regression.
MIN_COVERAGE_DENOM = 20


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

    # The pre-work measurement, scored over the clusters the gate could ever reach. A cluster with
    # no located members at all is excluded from the denominator rather than counted against the
    # gate: it is a story without geography, not a gate without coverage.
    denom = res["topLocatable"]
    pct = 100.0 * res["topScored"] / denom if denom else 0.0
    print(f"\ncoherence coverage on the {res['topTotal']} biggest clusters: "
          f"{res['topScored']} actionable of {denom} with any location "
          f"({pct:.0f}%), {res['topGeoless']} with no geography at all")
    # A verdict needs a denominator that can carry one. At n=14 a single cluster moves this by 7
    # points, and it has already flipped 13/16 (81%) to 11/14 (79%) on catalog churn alone —
    # reported as a regression when nothing regressed. Same defect as a coherence ratio read at two
    # located members, in a bar of this tool's own.
    if denom < MIN_COVERAGE_DENOM:
        print(f"  -> not enough locatable clusters to judge (n={denom}); re-run with "
              f"--top {max(40, args.top * 2)}")
    elif pct >= 80.0:
        print("  -> gates are load-bearing on the clusters they can see")
    else:
        print("  -> TOO THIN: extraction depth, not gate design — raise location coverage or fall "
              "back on size (RWE_STORY_UNVERIFIED_SIZE)")
    print("     The percentage is a proxy. The direct question is whether the BIGGEST clusters "
          "carry a\n     score, and the table below answers it.")

    print(f"\n{HEAD}")
    for s in res["head"][:args.show]:
        print(_row(s))

    print(f"\nblindspot claims: {res['claims']:,} of {res['stories']:,} stories, by how many "
          f"RATED publishers stand behind each")
    print(f"{'rated pubs':>11} {'claims':>8} {'% of claims':>12}")
    for key in ("1", "2", "3", "4+"):
        n = res["claimSupport"].get(key, 0)
        share = f"{100.0 * n / res['claims']:.1f}%" if res["claims"] else "  n/a"
        print(f"{key:>11} {n:>8,} {share:>12}")
    print("  -> a claim resting on 1-2 rated publishers is arithmetic, not an editorial finding:\n"
          "     with fewer than three lean-diverse rated outlets a bucket is empty by construction.")

    blocked = blocked_by_ratings(stories, min_rated=story_service.min_rated_for_blindspot())
    print(f"\nstories with the OUTLETS but not the RATINGS: {blocked['blocked']:,}")
    for gap in sorted(blocked["short"]):
        print(f"  {blocked['short'][gap]:>5} are {gap} rating short")
    print("  Only the 1-short ones can be converted by a single registry row; the rest need\n"
          "  coordinated curation. Volume is the wrong worklist — this is the right one:")
    print(f"  {'unlocks':>8}  unrated outlet")
    for n, name in blocked["unlock"][:args.show]:
        print(f"  {n:>8}  {name}")

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
