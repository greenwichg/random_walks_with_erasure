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
"""

from __future__ import annotations

import argparse

import clustering
import story_service
import store as store_mod


def build(rows: list, *, min_shared: int, min_tokens: int, idf: bool = False) -> list:
    return story_service.build_stories(rows, min_shared=min_shared, min_tokens=min_tokens, idf=idf)


def index_by_member(stories: list) -> dict:
    """article id -> the story id it landed in, so membership can be diffed."""
    out = {}
    for s in stories:
        for c in s["coverage"]:
            out[c["url"]] = s["id"]
    return out


def compare(store_, *, before: tuple, after: tuple, show: int = 10,
            before_idf: bool = False, after_idf: bool = False) -> dict:
    rows = story_service._fetch(store_)
    a = build(rows, min_shared=before[0], min_tokens=before[1], idf=before_idf)
    b = build(rows, min_shared=after[0], min_tokens=after[1], idf=after_idf)

    a_by_id = {s["id"]: s for s in a}
    a_member = index_by_member(a)
    b_member = index_by_member(b)

    # A "split" = an old story whose members now live in more than one story (or in none).
    fates: dict = {}
    for url, old in a_member.items():
        fates.setdefault(old, set()).add(b_member.get(url))
    split = [(sid, dests) for sid, dests in fates.items() if len(dests) > 1 or dests == {None}]
    split.sort(key=lambda kv: -a_by_id[kv[0]]["totalCoverage"])

    return {
        "articles": len(rows),
        "beforeStories": len(a),
        "afterStories": len(b),
        "beforeLargest": max((s["totalCoverage"] for s in a), default=0),
        "afterLargest": max((s["totalCoverage"] for s in b), default=0),
        "splitCount": len(split),
        # Coverage retention: a change that "improves" the numbers by quietly dropping articles out
        # of stories is not an improvement. droppedOut counts articles that were in a story and now
        # are in none.
        "beforeCovered": len(a_member),
        "afterCovered": len(b_member),
        "droppedOut": len([u for u in a_member if u not in b_member]),
        "newlyCovered": len([u for u in b_member if u not in a_member]),
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
    args = ap.parse_args(argv)

    after = (args.min_shared if args.min_shared is not None else story_service.min_shared_tokens(),
             args.min_tokens if args.min_tokens is not None else story_service.min_title_tokens())
    res = compare(store_mod.Store(args.db),
                  before=(args.before_min_shared, args.before_min_tokens),
                  after=after, show=args.show, after_idf=args.idf)

    print(f"articles in window : {res['articles']:,}")
    print(f"before  (shared>={args.before_min_shared}, tokens>={args.before_min_tokens}): "
          f"{res['beforeStories']:,} stories, largest {res['beforeLargest']}")
    print(f"after   (shared>={after[0]}, tokens>={after[1]}{', idf' if args.idf else ''}): "
          f"{res['afterStories']:,} stories, largest {res['afterLargest']}")
    print(f"clusters changed   : {res['splitCount']:,}")
    print(f"articles in a story: {res['beforeCovered']:,} -> {res['afterCovered']:,} "
          f"(dropped out {res['droppedOut']:,}, newly covered {res['newlyCovered']:,})")
    if res["split"]:
        print(f"\n{'arts':>5} {'pubs':>5} {'->':>4}  title")
        for s in res["split"]:
            dest = "gone" if s["dissolved"] and s["pieces"] == 0 else f"{s['pieces']}"
            print(f"{s['articles']:>5} {s['publishers']:>5} {dest:>4}  {s['title'][:64]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
