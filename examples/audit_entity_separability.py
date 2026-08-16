"""audit_entity_separability.py — can GKG entities tell same-event pairs from confusable ones?

The X5 rule-design measurement (docs/STORY_ENTITY_EVIDENCE_PLAN.md). X4 drove country-level
evidence to its two ceilings — coverage (18.7% located) and granularity (two US court cases both
read {US}) — and person/org evidence is the candidate that dissolves both. But it has its own
suspected failure written down BEFORE this instrument existed: ubiquitous names. "donald trump"
appears in both court cases, so naive entity-disjointness may separate nothing, and rarity
weighting at the token level is a measured, reverted failure (``story_service.use_idf``). Whether
entity-level evidence discriminates is an EMPIRICAL question about the real catalog, so this
instrument measures it instead of arguing it:

* **Coverage** — what share of the window carries any person/org at all (the reach bound).
* **Ubiquity** — the df table for the top names. If the mass sits in a few names, disjointness
  needs a ubiquity floor; if it is flat, it does not.
* **Separability** — the number the rule design actually rests on: among WITHIN-story pairs
  (production stories, presumed same-event) versus CONFUSABLE cross-story pairs (different
  stories whose titles share >= MIN_SHARED_TOKENS content tokens — the borderline the clusterer
  nearly merged), what share have a shared person / org / any / RARE shared entity? A usable
  signal shows a wide gap between those two columns; no gap means rung 2 stops here.

READ-ONLY: no writes, no clustering change, deterministic (first-K pair sampling by index, no
randomness, no clock). An instrument, like the ``--desc-tokens`` audit — it produces the numbers
a rule proposal must cite, not a verdict.
"""

from __future__ import annotations

import argparse
import os
import sys
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clustering            # noqa: E402
import story_service         # noqa: E402
import store as store_mod    # noqa: E402


def _entity_sets(ents: dict) -> "tuple[frozenset, frozenset]":
    persons = frozenset((ents or {}).get("person", ()))
    orgs = frozenset((ents or {}).get("org", ()))
    return persons, orgs


def pair_stats(pairs, persons, orgs, rare) -> dict:
    """Overlap counts for a pair iterable — one pass, no scoring, so the numbers mean exactly
    what they say. ``rare`` is the set of non-ubiquitous names (persons and orgs pooled)."""
    out = {"pairs": 0, "bothCovered": 0, "sharedPerson": 0, "sharedOrg": 0, "sharedAny": 0,
           "sharedRare": 0}
    for i, j in pairs:
        out["pairs"] += 1
        pi, oi = persons[i], orgs[i]
        pj, oj = persons[j], orgs[j]
        if not (pi | oi) or not (pj | oj):
            continue
        out["bothCovered"] += 1
        sp = bool(pi & pj)
        so = bool(oi & oj)
        if sp:
            out["sharedPerson"] += 1
        if so:
            out["sharedOrg"] += 1
        if sp or so:
            out["sharedAny"] += 1
        if ((pi | oi) & (pj | oj)) & rare:
            out["sharedRare"] += 1
    return out


def _fmt(stats: dict) -> str:
    n = stats["bothCovered"]
    if not n:
        return f"{stats['pairs']:,} pairs, 0 with entities on both sides"
    return (f"{stats['pairs']:,} pairs, {n:,} both-covered | shared person {stats['sharedPerson'] / n:.1%}, "
            f"org {stats['sharedOrg'] / n:.1%}, any {stats['sharedAny'] / n:.1%}, "
            f"rare {stats['sharedRare'] / n:.1%}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--pair-cap", type=int, default=50,
                    help="within-story pairs sampled per story (first-K by index, deterministic)")
    ap.add_argument("--confusable-cap", type=int, default=3000,
                    help="cross-story confusable pairs examined (first-K, deterministic)")
    ap.add_argument("--ubiquity", type=float, default=0.05,
                    help="a name in more than this share of entity-covered articles is ubiquitous")
    ap.add_argument("--top", type=int, default=20, help="df table size")
    args = ap.parse_args(argv)

    st = store_mod.Store(args.db)
    rows = story_service._fetch(st)
    ents = st.entities_for_urls([r.get("canonicalUrl") for r in rows])
    persons, orgs = {}, {}
    for idx, r in enumerate(rows):
        p, o = _entity_sets(ents.get(r.get("canonicalUrl")))
        persons[idx], orgs[idx] = p, o
    covered = [i for i in range(len(rows)) if persons[i] or orgs[i]]
    located = sum(1 for r in rows if r.get("eventCountries"))

    print(f"window articles     : {len(rows):,}")
    print(f"entity-covered      : {len(covered):,} ({len(covered) / max(1, len(rows)):.1%})"
          f"   [event-located: {located:,} ({located / max(1, len(rows)):.1%})]")
    with_p = sum(1 for i in covered if persons[i])
    with_o = sum(1 for i in covered if orgs[i])
    print(f"  with persons      : {with_p:,}   with orgs: {with_o:,}")
    if not covered:
        print("nothing to measure — run gdelt_entity_backfill.py first")
        return 0

    # Ubiquity: document frequency over entity-covered articles, persons and orgs separately.
    df: dict = {}
    for i in covered:
        for name in persons[i]:
            df[("person", name)] = df.get(("person", name), 0) + 1
        for name in orgs[i]:
            df[("org", name)] = df.get(("org", name), 0) + 1
    floor = max(2, int(args.ubiquity * len(covered)))
    rare = frozenset(name for (kind, name), n in df.items() if n < floor)
    print(f"\ndistinct names      : {len(df):,} (ubiquity floor: df >= {floor} "
          f"-> {sum(1 for n in df.values() if n >= floor):,} ubiquitous)")
    print(f"{'df':>6}  kind    name")
    for (kind, name), n in sorted(df.items(), key=lambda kv: -kv[1])[:args.top]:
        print(f"{n:>6}  {kind:<6}  {name[:56]}")

    # Separability. Within-story pairs come from the PRODUCTION build (presumed same-event);
    # confusable pairs share >= MIN_SHARED_TOKENS content tokens across DIFFERENT stories.
    stories = story_service.build_stories(rows)
    url_to_idx = {r.get("canonicalUrl"): i for i, r in enumerate(rows)}
    within = []
    story_of: dict = {}
    for sid, s in enumerate(stories):
        member_idx = sorted(url_to_idx[c["url"]] for c in s["coverage"] if c["url"] in url_to_idx)
        for i in member_idx:
            story_of[i] = sid
        within.extend(list(combinations(member_idx, 2))[:args.pair_cap])

    toks = [clustering.title_tokens(r.get("title") or "") for r in rows]
    postings: dict = {}
    for i in story_of:                       # covered articles only — the confusable universe
        for t in toks[i]:
            postings.setdefault(t, []).append(i)
    confusable, seen = [], set()
    for i in sorted(story_of):
        counts: dict = {}
        for t in toks[i]:
            for j in postings.get(t, ()):
                if j > i and story_of.get(j) != story_of[i]:
                    counts[j] = counts.get(j, 0) + 1
        for j, shared in sorted(counts.items()):
            if shared >= clustering.MIN_SHARED_TOKENS and (i, j) not in seen:
                seen.add((i, j))
                confusable.append((i, j))
        if len(confusable) >= args.confusable_cap:
            break

    print(f"\nwithin-story pairs  : {_fmt(pair_stats(within, persons, orgs, rare))}")
    print(f"confusable pairs    : {_fmt(pair_stats(confusable[:args.confusable_cap], persons, orgs, rare))}")
    print("(the gap between those two lines IS the separability measurement)")

    # Exhibits: the biggest stories' top names, so the numbers stay attached to real events.
    print("\ntop stories, top names (member count in parentheses):")
    for s in stories[:10]:
        member_idx = [url_to_idx[c["url"]] for c in s["coverage"] if c["url"] in url_to_idx]
        names: dict = {}
        for i in member_idx:
            for name in persons[i] | orgs[i]:
                names[name] = names.get(name, 0) + 1
        top = ", ".join(f"{n}({c})" for n, c in sorted(names.items(), key=lambda kv: -kv[1])[:5])
        print(f"  {s['totalCoverage']:>4} arts  {s['title'][:44]:<44}  {top or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
