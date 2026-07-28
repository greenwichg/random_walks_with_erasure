"""audit_clustering_change.py — measure a clustering-threshold change against the LIVE catalog.

Clustering thresholds are an empirical question about the real headline mix, not something to
settle on hand-picked examples. This runs the current window's articles through two parameter sets
and reports what actually moves: story count, the clusters that split, and the boilerplate titles
that stop merging.

The defaults compare "no admission gates" (the pre-2026-07-27 behaviour: ratio only) against
whatever the code is configured with now.

    python examples/audit_clustering_change.py                       # before/after summary
    python examples/audit_clustering_change.py --min-shared 4        # try a candidate value
    python examples/audit_clustering_change.py --show 20             # list the biggest splits
    python examples/audit_clustering_change.py --link-quorum 0.3     # cluster-aware linkage

The last one is the change this instrument now exists for. It prints a VERDICT against bars fixed
in advance (``--max-dropped``), because the previous tightening change looked good on its headline
numbers and cost 10.5% of covered articles — a number nobody would have accepted if asked first.
"""

from __future__ import annotations

import argparse

import clustering
import story_service
import store as store_mod

#: Reject above this share of covered articles falling out of stories entirely. Set from the IDF
#: experiment, which measured 10.5% and was reverted.
MAX_DROPPED = 0.05


def build(rows: list, *, min_shared: int, min_tokens: int, idf: bool = False,
          quorum: float = 0.0) -> list:
    return story_service.build_stories(rows, min_shared=min_shared, min_tokens=min_tokens, idf=idf,
                                       quorum=quorum)


def index_by_member(stories: list) -> dict:
    """article id -> the story id it landed in, so membership can be diffed."""
    out = {}
    for s in stories:
        for c in s["coverage"]:
            out[c["url"]] = s["id"]
    return out


def _coherence_stats(stories: list) -> dict:
    """geoCoherence over the clusters that carry one. The scored subset is a minority of the
    catalog (three located members are required), so these are reported WITH their denominator —
    a mean over 91 of 925 stories is not a statement about the catalog."""
    scored = [s["geoCoherence"] for s in stories if s.get("geoCoherence") is not None]
    floor = story_service.coherence_floor()
    return {
        "scored": len(scored),
        "bad": len([c for c in scored if c < floor]),
        "mean": round(sum(scored) / len(scored), 3) if scored else None,
    }


def verdict(res: dict, *, max_dropped: float = MAX_DROPPED) -> dict:
    """Adopt / reject against the bars, computed rather than eyeballed.

    Two rejection rules, both learned from measurements already taken:

    * dropped coverage over ``max_dropped`` — the IDF experiment's failure mode.
    * story count FALLING — the ``min_publishers`` cliff. Splitting a 4-article/2-publisher cluster
      into 2+2 can leave two single-publisher fragments, and both are then dropped. Oversplitting
      does not merely shrink stories, it deletes them, and a raw article count hides that.
    """
    covered = res["beforeCovered"] or 1
    dropped = res["droppedOut"] / covered
    fails = []
    if dropped > max_dropped:
        fails.append(f"dropped {dropped:.1%} of covered articles (bar {max_dropped:.0%})")
    if res["afterStories"] < res["beforeStories"]:
        fails.append(f"story count fell {res['beforeStories']:,} -> {res['afterStories']:,} "
                     f"(min_publishers cliff)")
    return {"droppedShare": dropped, "fails": fails, "adopt": not fails}


def compare(store_, *, before: tuple, after: tuple, show: int = 10,
            before_idf: bool = False, after_idf: bool = False,
            before_quorum: float = 0.0, after_quorum: float = 0.0) -> dict:
    rows = story_service._fetch(store_)
    a = build(rows, min_shared=before[0], min_tokens=before[1], idf=before_idf,
              quorum=before_quorum)
    b = build(rows, min_shared=after[0], min_tokens=after[1], idf=after_idf, quorum=after_quorum)

    a_by_id = {s["id"]: s for s in a}
    a_member = index_by_member(a)
    b_member = index_by_member(b)

    # A "split" = an old story whose members now live in more than one story (or in none).
    fates: dict = {}
    for url, old in a_member.items():
        fates.setdefault(old, set()).add(b_member.get(url))
    split = [(sid, dests) for sid, dests in fates.items() if len(dests) > 1 or dests == {None}]
    split.sort(key=lambda kv: -a_by_id[kv[0]]["totalCoverage"])

    # Where the dropped articles CAME FROM. droppedOut on its own cannot tell a regression from a
    # fix: an article leaving a 101-article/4-publisher press-release template is the change working,
    # and an article leaving a 48-publisher wire story is the change costing real coverage. Both
    # decrement the same counter. Attributing each loss to the cluster it left makes the mix visible,
    # and articles-per-publisher is the tell — a template is one outlet repeating itself (25 articles
    # per publisher), a real story is many outlets covering one event (~1.4).
    lost: dict = {}
    for url, old in a_member.items():
        if url not in b_member:
            lost[old] = lost.get(old, 0) + 1
    dropped_from = sorted(lost.items(), key=lambda kv: -kv[1])

    return {
        "articles": len(rows),
        "beforeStories": len(a),
        "afterStories": len(b),
        "beforeLargest": max((s["totalCoverage"] for s in a), default=0),
        "afterLargest": max((s["totalCoverage"] for s in b), default=0),
        "splitCount": len(split),
        # Whether the INDEPENDENT signal improved. A change that splits clusters without moving
        # this has rearranged the catalog rather than corrected it.
        "beforeCoherence": _coherence_stats(a),
        "afterCoherence": _coherence_stats(b),
        # Coverage retention: a change that "improves" the numbers by quietly dropping articles out
        # of stories is not an improvement. droppedOut counts articles that were in a story and now
        # are in none.
        "beforeCovered": len(a_member),
        "afterCovered": len(b_member),
        "droppedOut": len([u for u in a_member if u not in b_member]),
        "newlyCovered": len([u for u in b_member if u not in a_member]),
        "droppedFrom": [{
            "lost": n,
            "articles": a_by_id[sid]["totalCoverage"],
            "publishers": a_by_id[sid]["publisherCount"],
            "perPublisher": a_by_id[sid]["totalCoverage"] / max(1, a_by_id[sid]["publisherCount"]),
            "title": a_by_id[sid]["title"],
        } for sid, n in dropped_from[:show]],
        "split": [{
            "articles": a_by_id[sid]["totalCoverage"],
            "publishers": a_by_id[sid]["publisherCount"],
            "pieces": len([d for d in dests if d]),
            "dissolved": None in dests,
            "title": a_by_id[sid]["title"],
        } for sid, dests in split[:show]],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--before-min-shared", type=int, default=1)
    ap.add_argument("--before-min-tokens", type=int, default=1)
    ap.add_argument("--min-shared", type=int, default=None, help="candidate (default: configured)")
    ap.add_argument("--min-tokens", type=int, default=None, help="candidate (default: configured)")
    ap.add_argument("--show", type=int, default=10)
    ap.add_argument("--idf", action="store_true",
                    help="score the AFTER side with rarity-weighted similarity")
    ap.add_argument("--link-quorum", type=float, default=0.0,
                    help="cluster-aware linkage on the AFTER side: fraction of cross-pairs that "
                         "must agree before two clusters merge (0 = single linkage)")
    ap.add_argument("--max-dropped", type=float, default=MAX_DROPPED,
                    help="reject the change above this share of covered articles dropped")
    args = ap.parse_args(argv)

    after = (args.min_shared if args.min_shared is not None else story_service.min_shared_tokens(),
             args.min_tokens if args.min_tokens is not None else story_service.min_title_tokens())
    res = compare(store_mod.Store(args.db),
                  before=(args.before_min_shared, args.before_min_tokens),
                  after=after, show=args.show, after_idf=args.idf,
                  after_quorum=args.link_quorum)

    tag = (", idf" if args.idf else "") + (f", quorum {args.link_quorum:g}"
                                           if args.link_quorum > 0 else "")
    print(f"articles in window : {res['articles']:,}")
    print(f"before  (shared>={args.before_min_shared}, tokens>={args.before_min_tokens}): "
          f"{res['beforeStories']:,} stories, largest {res['beforeLargest']}")
    print(f"after   (shared>={after[0]}, tokens>={after[1]}{tag}): "
          f"{res['afterStories']:,} stories, largest {res['afterLargest']}")
    print(f"clusters changed   : {res['splitCount']:,}")
    print(f"articles in a story: {res['beforeCovered']:,} -> {res['afterCovered']:,} "
          f"(dropped out {res['droppedOut']:,}, newly covered {res['newlyCovered']:,})")

    bc, ac = res["beforeCoherence"], res["afterCoherence"]
    print(f"independent signal : {bc['bad']}/{bc['scored']} bad (mean {bc['mean']}) -> "
          f"{ac['bad']}/{ac['scored']} bad (mean {ac['mean']})")

    v = verdict(res, max_dropped=args.max_dropped)
    print(f"\nVERDICT: {'ADOPT' if v['adopt'] else 'REJECT'} "
          f"(dropped {v['droppedShare']:.1%} of covered articles)")
    for f in v["fails"]:
        print(f"  - {f}")
    if res["droppedFrom"]:
        print("\nwhere the dropped articles came from  (high a/p = one outlet repeating a template)")
        print(f"{'lost':>5} {'arts':>5} {'pubs':>5} {'a/p':>6}  title")
        for d in res["droppedFrom"]:
            print(f"{d['lost']:>5} {d['articles']:>5} {d['publishers']:>5} "
                  f"{d['perPublisher']:>6.1f}  {d['title'][:60]}")
    if res["split"]:
        print(f"\n{'arts':>5} {'pubs':>5} {'->':>4}  title")
        for s in res["split"]:
            dest = "gone" if s["dissolved"] and s["pieces"] == 0 else f"{s['pieces']}"
            print(f"{s['articles']:>5} {s['publishers']:>5} {dest:>4}  {s['title'][:64]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
