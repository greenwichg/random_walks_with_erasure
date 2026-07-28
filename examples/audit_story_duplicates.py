"""audit_story_duplicates.py — how many separate stories are the same event?

Splitting the mega-cluster exposed a defect that had been hiding inside it: one event covered under
disjoint vocabulary becomes several stories. A Seattle mass shooting came out as four clusters —
"At Least 2 Killed in Shooting at Food Festival in Seattle", "Two dead, five injured in shooting
near Seattle's Space Needle", "Mass shooting reported at Seattle Center", "Multiple people have
been shot after gunfire erupts near Seattle" — because those headlines share **one or two** tokens
against ``MIN_SHARED_TOKENS = 3``. No linkage rule can join them; they never clear the pairwise
gate. This is a RECALL limit of title-token Jaccard, and it is the opposite failure from chaining.

So this measures it with a signal the clusterer does not use. Each story gets a profile built from
**every member's headline AND description** — the median story is 2 articles, and at ~37 headline
words plus ~160 description characters each, that is several times the text one headline offers.
Rare-word weighting then does the comparing, because "trump" is not evidence and "hormuz" is.

**This instrument cannot confirm its own findings.** Same-event is a judgement about the world, and
nothing here can make it. What it produces is CANDIDATES with their titles printed, so precision
comes from reading them. Treat the count at any threshold as an upper bound until that is done.

    python examples/audit_story_duplicates.py
    python examples/audit_story_duplicates.py --min-sim 0.25 --show 40
"""

from __future__ import annotations

import argparse

import clustering
import discover
import story_service
import store as store_mod

#: Candidate thresholds on weighted profile similarity. Profiles are far larger token sets than
#: headlines, so these sit well below clustering's own 0.28 — the two numbers are not comparable.
THRESHOLDS = (0.15, 0.20, 0.25, 0.30, 0.40)

#: Profile tokens two stories must share before similarity is scored at all. Same role as
#: clustering's MIN_SHARED_TOKENS, raised because profiles carry many more tokens.
MIN_SHARED = 4

#: Hours of separation beyond which two stories are not the same event. Coverage of one event
#: arrives in a burst; two clusters a week apart that read alike are a recurring topic, not a
#: duplicate.
DEFAULT_MAX_GAP_HOURS = 48.0


def profile(story: dict, desc: dict) -> frozenset:
    """Every member's headline plus its description, as one token set.

    Deliberately NOT the story title alone — that is the input the clusterer already failed on, so
    reusing it would measure nothing new. Descriptions are present on ~77% of rows and carry the
    vocabulary the headlines happen to omit: the words "Space Needle" appear in one headline of the
    four Seattle clusters and in most of their descriptions."""
    toks: set = set()
    for c in story["coverage"]:
        toks |= clustering.title_tokens(c["headline"])
        toks |= clustering.title_tokens(desc.get(c["url"]) or "")
    return frozenset(toks)


def _gap_hours(a: dict, b: dict) -> float:
    """Hours between two stories' coverage windows; 0 when they overlap."""
    ae, al = clustering.parse_time(a["earliest"]), clustering.parse_time(a["latest"])
    be, bl = clustering.parse_time(b["earliest"]), clustering.parse_time(b["latest"])
    if not (ae and al and be and bl):
        return 0.0                                  # missing timestamps never block a match
    if ae <= bl and be <= al:
        return 0.0
    delta = (be - al) if be > al else (ae - bl)
    return abs(delta.total_seconds()) / 3600.0


def _geo(a: dict, b: dict) -> str:
    """Whether the two stories' strongest event countries agree — an INDEPENDENT corroboration,
    since it comes from provider-extracted locations rather than from any text. Only available when
    both carry votes, which is the minority case, so it annotates rather than decides."""
    av, bv = a.get("countryVotes") or {}, b.get("countryVotes") or {}
    if not av or not bv:
        return "-"
    return "same" if max(av, key=av.get) == max(bv, key=bv.get) else "diff"


def find_pairs(stories: list, desc: dict, *, min_sim: float, max_gap_hours: float,
               min_shared: int = MIN_SHARED) -> list:
    """Candidate same-event pairs, strongest first. Deterministic."""
    profiles = [profile(s, desc) for s in stories]
    weights = clustering.idf_weights(profiles)

    postings: dict = {}
    for i, toks in enumerate(profiles):
        for t in toks:
            postings.setdefault(t, []).append(i)

    pairs = []
    for i in range(len(stories)):
        shared: dict = {}
        for t in profiles[i]:
            for j in postings[t]:
                if j > i:
                    shared[j] = shared.get(j, 0) + 1
        for j, overlap in shared.items():
            if overlap < min_shared:
                continue
            if _gap_hours(stories[i], stories[j]) > max_gap_hours:
                continue
            score = clustering.weighted_jaccard(profiles[i], profiles[j], weights)
            if score >= min_sim:
                pairs.append((score, i, j))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
    return pairs


def analyse(stories: list, desc: dict, *, max_gap_hours: float) -> dict:
    """Counts at every threshold, plus the article volume involved.

    Article volume is the number that matters. Twelve duplicate pairs sounds negligible until you
    see they hold 300 articles — the same lesson the trust buckets taught."""
    low = min(THRESHOLDS)
    pairs = find_pairs(stories, desc, min_sim=low, max_gap_hours=max_gap_hours)
    rows = []
    for t in THRESHOLDS:
        at = [p for p in pairs if p[0] >= t]
        touched = {i for _, i, j in at} | {j for _, i, j in at}
        rows.append({
            "threshold": t,
            "pairs": len(at),
            "stories": len(touched),
            "articles": sum(stories[i]["totalCoverage"] for i in touched),
            # Groups, not pairs: four clusters of one event are six pairs but ONE duplicate event,
            # so a pair count overstates how much is actually wrong.
            "groups": _groups(at),
            "geoSame": len([p for p in at if _geo(stories[p[1]], stories[p[2]]) == "same"]),
            "geoDiff": len([p for p in at if _geo(stories[p[1]], stories[p[2]]) == "diff"]),
        })
    return {"stories": len(stories),
            "articles": sum(s["totalCoverage"] for s in stories),
            "rows": rows, "pairs": pairs}


def _groups(pairs: list) -> int:
    """Connected components over the candidate pairs — how many distinct events are duplicated."""
    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for _, i, j in pairs:
        a, b = find(i), find(j)
        if a != b:
            parent[max(a, b)] = min(a, b)
    return len({find(x) for x in parent})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--min-sim", type=float, default=0.25,
                    help="threshold for the listed pairs (counts are reported at all thresholds)")
    ap.add_argument("--max-gap-hours", type=float, default=DEFAULT_MAX_GAP_HOURS)
    ap.add_argument("--show", type=int, default=25)
    args = ap.parse_args(argv)

    store_ = store_mod.Store(args.db)
    rows = story_service._fetch(store_)
    # The same serialisation build_stories uses, so these urls match the coverage rows exactly.
    desc = {a["url"]: (a.get("description") or "")
            for a in (discover.feed_article_to_article(r) for r in rows)}
    stories = story_service.build_stories(rows)
    res = analyse(stories, desc, max_gap_hours=args.max_gap_hours)

    print(f"catalog: {res['stories']:,} stories, {res['articles']:,} articles in stories")
    print(f"window : same-event candidates must be within {args.max_gap_hours:g}h\n")
    print(f"{'sim':>6} {'pairs':>7} {'events':>7} {'stories':>8} {'articles':>9} {'% arts':>7} "
          f"{'geo same':>9} {'geo diff':>9}")
    for r in res["rows"]:
        share = f"{100.0 * r['articles'] / res['articles']:.1f}%" if res["articles"] else "  n/a"
        print(f"{r['threshold']:>6.2f} {r['pairs']:>7,} {r['groups']:>7,} {r['stories']:>8,} "
              f"{r['articles']:>9,} {share:>7} {r['geoSame']:>9} {r['geoDiff']:>9}")

    print(f"\n--- candidate pairs at sim >= {args.min_sim:g} (READ THESE — the count is an upper "
          f"bound until they are) ---")
    shown = [p for p in res["pairs"] if p[0] >= args.min_sim][:args.show]
    for score, i, j in shown:
        a, b = stories[i], stories[j]
        print(f"\n  sim {score:.2f}  gap {_gap_hours(a, b):.0f}h  geo {_geo(a, b)}")
        print(f"    {a['totalCoverage']:>4} arts  {a['title'][:66]}")
        print(f"    {b['totalCoverage']:>4} arts  {b['title'][:66]}")
    if not shown:
        print("  (none)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
