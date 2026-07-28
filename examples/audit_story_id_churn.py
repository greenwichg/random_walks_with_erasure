"""audit_story_id_churn.py — how often does a story's id change under it?

``_story_id`` anchors to the cluster's representative, ``min(members, key=publishedAt)``, and the
module docstring says this keeps ids stable "as more coverage of the same event arrives". That is
true for the case it was designed against: a LATER article joining never disturbs the earliest one.

Two cases it does not cover, and both are routine rather than exotic:

* **The earliest member ages out.** The candidate set is a rolling time window, so every cluster
  eventually loses its oldest article and the representative becomes the next-oldest.
* **An EARLIER article arrives.** Ingestion is not ordered by publication time — GDELT's GKG
  backfill in particular attaches articles published hours or days earlier.

Either way the id changes, and a story id is what a saved or shared link points at. So this is a
data-integrity question, not a tidiness one, and it has never been measured.

The measurement replays the window over the catalog we already have: build at successive cutoffs,
match stories across consecutive builds by member overlap, and count how many SURVIVING stories
changed id. Matching by members rather than by id is the whole point — matching by id could only
ever report zero.

    python examples/audit_story_id_churn.py
    python examples/audit_story_id_churn.py --step-hours 12 --steps 6
"""

from __future__ import annotations

import argparse
from datetime import timedelta

import clustering
import story_service
import store as store_mod

#: Member overlap at which two stories from consecutive builds are "the same story". Jaccard over
#: article URLs. Deliberately generous: a story that gained or shed a few articles is still that
#: story, and being strict here would hide churn by declaring the pair unmatched.
MATCH_OVERLAP = 0.5


def _members(story: dict) -> frozenset:
    return frozenset(c["url"] for c in story["coverage"])


def match(before: list, after: list, *, overlap: float = MATCH_OVERLAP) -> list:
    """Pair stories across two builds by member overlap, best first. Returns ``(b, a, jaccard)``."""
    scored = []
    for i, b in enumerate(before):
        mb = _members(b)
        for j, a in enumerate(after):
            ma = _members(a)
            inter = len(mb & ma)
            if not inter:
                continue
            score = inter / len(mb | ma)
            if score >= overlap:
                scored.append((score, i, j))
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    used_b, used_a, pairs = set(), set(), []
    for score, i, j in scored:
        if i in used_b or j in used_a:
            continue
        used_b.add(i)
        used_a.add(j)
        pairs.append((before[i], after[j], score))
    return pairs


def churn(before: list, after: list) -> dict:
    """Matched stories whose id changed, with the reason separated.

    ``agedOut`` is the representative dropping off the back of the window; ``earlierArrived`` is a
    new member older than the previous representative. They have different fixes, so a single
    "ids changed" count would not be actionable."""
    pairs = match(before, after)
    changed, aged, earlier, other = [], 0, 0, 0
    for b, a, score in pairs:
        if b["id"] == a["id"]:
            continue
        mb, ma = _members(b), _members(a)
        b_rep = min(b["coverage"], key=lambda c: (c["publishedAt"] or "~", c["url"]))
        a_rep = min(a["coverage"], key=lambda c: (c["publishedAt"] or "~", c["url"]))
        if b_rep["url"] not in ma:
            aged += 1
            why = "aged out"
        elif a_rep["url"] not in mb:
            earlier += 1
            why = "earlier arrived"
        else:
            other += 1
            why = "other"
        changed.append({"was": b["title"], "now": a["title"], "articles": a["totalCoverage"],
                        "overlap": score, "why": why})
    return {"matched": len(pairs), "changed": changed,
            "agedOut": aged, "earlierArrived": earlier, "other": other}


def replay(rows: list, *, step_hours: float, steps: int, window_days: float,
           stabilize: bool = False) -> list:
    """Build the catalog at successive cutoffs, oldest first. Each build sees only the articles a
    live poller would have had at that moment, so the window rolls exactly as it does in service.

    ``stabilize`` replays the identity carry-over too, threading each build's ``url -> id`` map into
    the next exactly as the store does. Without it this tool measures the DERIVED id and therefore
    the problem; ``build_stories`` is pure and knows nothing about ``stabilize_ids``, so a run
    against a fixed production reports the same 5.1% and looks like the fix did nothing."""
    times = [clustering.parse_time(r.get("publishedAt")) for r in rows]
    stamped = [(t, r) for t, r in zip(times, rows) if t is not None]
    if not stamped:
        return []
    newest = max(t for t, _ in stamped)
    builds, prior = [], {}
    for k in range(steps, -1, -1):
        cutoff = newest - timedelta(hours=step_hours * k)
        start = cutoff - timedelta(days=window_days)
        slice_ = [r for t, r in stamped if start <= t <= cutoff]
        stories = story_service.build_stories(slice_)
        if stabilize:
            for i, pid in story_service.reassign_ids(prior, stories).items():
                stories[i] = dict(stories[i], id=pid)
            prior = {c["url"]: s["id"] for s in stories for c in s["coverage"]}
        builds.append((cutoff, stories))
    return builds


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--step-hours", type=float, default=24.0)
    ap.add_argument("--steps", type=int, default=3)
    ap.add_argument("--show", type=int, default=12)
    args = ap.parse_args(argv)

    store_ = store_mod.Store(args.db)
    rows = story_service._fetch(store_, date_from="1970-01-01T00:00:00+00:00")
    window = story_service.scan_days()
    def run(stabilize):
        builds = replay(rows, step_hours=args.step_hours, steps=args.steps,
                        window_days=window, stabilize=stabilize)
        totals = {"matched": 0, "changed": 0, "agedOut": 0, "earlierArrived": 0, "rows": [],
                  "worst": []}
        for (t0, b0), (t1, b1) in zip(builds, builds[1:]):
            c = churn(b0, b1)
            totals["matched"] += c["matched"]
            totals["changed"] += len(c["changed"])
            totals["agedOut"] += c["agedOut"]
            totals["earlierArrived"] += c["earlierArrived"]
            totals["worst"].extend(c["changed"])
            totals["rows"].append((t0, t1, len(b1), c))
        return totals

    derived = run(False)
    if not derived["rows"]:
        print("not enough catalog to replay")
        return 0
    stable = run(True)

    print(f"catalog: {len(rows):,} articles; replaying a {window:g}-day window in "
          f"{args.step_hours:g}h steps\n")
    print(f"{'from':>17} {'to':>17} {'stories':>8} {'matched':>8} {'derived':>9} "
          f"{'stabilized':>11} {'aged out':>9} {'earlier':>8}")
    for (t0, t1, n, c), (_, _, _, sc) in zip(derived["rows"], stable["rows"]):
        print(f"{t0.strftime('%m-%d %H:%M'):>17} {t1.strftime('%m-%d %H:%M'):>17} "
              f"{n:>8,} {c['matched']:>8,} {len(c['changed']):>9,} "
              f"{len(sc['changed']):>11,} {c['agedOut']:>9,} {c['earlierArrived']:>8,}")

    def rate(t):
        r = (100.0 * t["changed"] / t["matched"]) if t["matched"] else 0.0
        return r, (r * (24.0 / args.step_hours) if args.step_hours else 0.0)

    dr, dpd = rate(derived)
    sr, spd = rate(stable)
    print(f"\nid churn, DERIVED    : {derived['changed']:,} of {derived['matched']:,} "
          f"({dr:.1f}% per {args.step_hours:g}h, ~{dpd:.1f}%/day)   <- the problem")
    print(f"  representative aged out of the window : {derived['agedOut']:,}")
    print(f"  an earlier article arrived            : {derived['earlierArrived']:,}")
    print(f"id churn, STABILIZED : {stable['changed']:,} of {stable['matched']:,} "
          f"({sr:.1f}% per {args.step_hours:g}h, ~{spd:.1f}%/day)   <- with RWE_STORY_STABLE_IDS on")
    print("\n  Each remaining one is a saved or shared link that no longer resolves. The gap "
          "between\n  the two rows is what the identity table buys; the stabilized row is the "
          "residual.")
    worst = derived["worst"]

    worst.sort(key=lambda c: -c["articles"])
    print(f"\n--- the {args.show} biggest stories whose id moved ---")
    print(f"{'arts':>5} {'overlap':>8} {'why':>16}  title")
    for c in worst[:args.show]:
        print(f"{c['articles']:>5} {c['overlap']:>8.2f} {c['why']:>16}  {c['now'][:56]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
