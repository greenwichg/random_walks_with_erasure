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
from datetime import datetime, timezone
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clustering            # noqa: E402
import story_service         # noqa: E402
import store as store_mod    # noqa: E402

# One noise definition, shared with the X5b merge pass so instrument and rule cannot drift —
# the receipts (df table, the USGS residual) live at story_service.entity_noise.
_is_noise = story_service.entity_noise


def _entity_sets(ents: dict, *, noise_filter: bool = True,
                 filtered: "dict | None" = None) -> "tuple[frozenset, frozenset]":
    persons, orgs = [], []
    for kind, sink in (("person", persons), ("org", orgs)):
        for name in (ents or {}).get(kind, ()):
            if noise_filter and _is_noise(name):
                if filtered is not None:
                    filtered[name] = filtered.get(name, 0) + 1
                continue
            sink.append(name)
    return frozenset(persons), frozenset(orgs)


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
    ap.add_argument("--no-noise-filter", action="store_true",
                    help="keep media/platform/country names as evidence (the raw first-run view)")
    args = ap.parse_args(argv)

    st = store_mod.Store(args.db)
    rows = story_service._fetch(st)
    ents = st.entities_for_urls([r.get("canonicalUrl") for r in rows])
    persons, orgs = {}, {}
    filtered: dict = {}
    for idx, r in enumerate(rows):
        p, o = _entity_sets(ents.get(r.get("canonicalUrl")),
                            noise_filter=not args.no_noise_filter, filtered=filtered)
        persons[idx], orgs[idx] = p, o
    if filtered:
        top_noise = ", ".join(f"{n}({c})" for n, c in
                              sorted(filtered.items(), key=lambda kv: -kv[1])[:8])
        print(f"noise filtered      : {len(filtered):,} names / {sum(filtered.values()):,} "
              f"occurrences (media, platform chrome, country names) — top: {top_noise}")
    covered = [i for i in range(len(rows)) if persons[i] or orgs[i]]
    covered_set = frozenset(covered)
    located = sum(1 for r in rows if r.get("eventCountries"))

    print(f"window articles     : {len(rows):,}")
    print(f"entity-covered      : {len(covered):,} ({len(covered) / max(1, len(rows)):.1%})"
          f"   [event-located: {located:,} ({located / max(1, len(rows)):.1%})]")
    with_p = sum(1 for i in covered if persons[i])
    with_o = sum(1 for i in covered if orgs[i])
    print(f"  with persons      : {with_p:,}   with orgs: {with_o:,}")
    # Coverage BY AGE, because a bounded backfill covers a bounded span: the first production run
    # read 6.2% overall as a shortfall when it was ~1/3 of the window at the located rate — a 48h
    # backfill of a 6-day window CANNOT exceed ~1/3 of located coverage, and the per-bucket lines
    # make that arithmetic visible instead of leaving it to the reader.
    now = datetime.now(timezone.utc)
    buckets = [("0-24h", 0.0, 24.0), ("24-48h", 24.0, 48.0), (">48h", 48.0, float("inf"))]
    for label, lo, hi in buckets:
        idxs = []
        for i, r in enumerate(rows):
            t = clustering.parse_time(r.get("publishedAt") or "")
            if t is None:
                continue
            age = (now - t).total_seconds() / 3600.0
            if lo <= age < hi:
                idxs.append(i)
        cov = sum(1 for i in idxs if i in covered_set)
        print(f"  {label:<7} articles : {len(idxs):>7,}   entity-covered {cov:,} "
              f"({cov / max(1, len(idxs)):.1%})")
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
    #
    # Two lessons from the first production run are load-bearing here:
    # * Story coverage entries carry the DISPLAY url; rows are keyed by canonical. Joining on
    #   canonical alone silently dropped most members (150 pairs from ~1,500 stories) and the
    #   "measurement" measured the join, not the entities. Both keys index the same row now.
    # * Pairs are formed over ENTITY-COVERED members only. This is a CONDITIONAL measurement —
    #   "given both sides carry entities, do they discriminate?" — which is the question the
    #   rule design needs; forming pairs over uncovered members at 6% coverage just buried the
    #   answer under both-covered ≈ 0. Coverage itself is reported separately above.
    stories = story_service.build_stories(rows)
    url_to_idx: dict = {}
    for i, r in enumerate(rows):
        for key in (r.get("canonicalUrl"), r.get("url")):
            if key and key not in url_to_idx:
                url_to_idx[key] = i
    within = []
    story_of: dict = {}
    for sid, s in enumerate(stories):
        member_idx = sorted({url_to_idx[c["url"]] for c in s["coverage"] if c["url"] in url_to_idx})
        cov_idx = [i for i in member_idx if i in covered_set]
        for i in cov_idx:
            story_of[i] = sid
        within.extend(list(combinations(cov_idx, 2))[:args.pair_cap])

    toks = [clustering.title_tokens(r.get("title") or "") for r in rows]
    postings: dict = {}
    for i in story_of:                       # entity-covered story members — the confusable universe
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
    print("(the gap between those two lines IS the pairwise separability measurement)")

    # ----------------------------------------------------------------------- #
    # CONSENSUS-level separability — the X4-shaped question, and the one the eventual rule
    # actually asks. The 144h run showed why pairs understate the signal: two same-story
    # articles quote different people (21% of true pairs share NO entity), but the story's
    # aggregate is strongly corroborated (Leavitt: white house x32, leavitt x31 over 32
    # covered members). A growth-style rule compares cluster consensus to cluster consensus,
    # so that is what gets measured: a story's consensus = names >= 2 member votes (the
    # GEO_MIN_CONSENSUS discipline), and the two decisive rates are
    #   * member agreement — covered members sharing >= 1 of their OWN story's consensus
    #     (high = a consensus gate rarely wrongs a true member: the false-split proxy), and
    #   * confusable-story disjointness — confusable story pairs whose consensuses share
    #     nothing (high = the gate fires exactly on the borderline different-event pairs:
    #     the true-fire proxy).
    # ----------------------------------------------------------------------- #
    members_by_story: dict = {}
    for i, sid in story_of.items():
        members_by_story.setdefault(sid, []).append(i)
    consensus: dict = {}
    agree_num = agree_den = 0
    for sid, idxs in sorted(members_by_story.items()):
        if len(idxs) < 3:
            continue                        # a consensus of two members is one shared article
        votes: dict = {}
        for i in idxs:
            for name in persons[i] | orgs[i]:
                votes[name] = votes.get(name, 0) + 1
        cons = frozenset(n for n, c in votes.items() if c >= 2)
        if not cons:
            continue
        consensus[sid] = cons
        for i in idxs:
            agree_den += 1
            if (persons[i] | orgs[i]) & cons:
                agree_num += 1

    story_pairs: dict = {}
    for i, j in confusable[:args.confusable_cap]:
        key = (story_of[i], story_of[j])
        if key[0] in consensus and key[1] in consensus:
            story_pairs[key] = story_pairs.get(key, 0) + 1
    disjoint = {k: (consensus[k[0]] & consensus[k[1]]) for k in story_pairs}
    n_dis = sum(1 for ov in disjoint.values() if not ov)

    print(f"\nconsensus stories   : {len(consensus):,} (>=3 covered members, "
          f"consensus = names with >=2 member votes)")
    print(f"member agreement    : {agree_num:,}/{agree_den:,} covered members share their own "
          f"story's consensus ({agree_num / max(1, agree_den):.1%})  [false-split proxy: want HIGH]")
    print(f"confusable stories  : {len(story_pairs):,} story pairs, consensus-DISJOINT {n_dis:,} "
          f"({n_dis / max(1, len(story_pairs)):.1%})  [true-fire proxy: want HIGH]")
    print("top confusable story pairs (by linking article pairs):")
    sid_title = {sid: stories[sid]["title"] for sid in consensus}
    for (sa, sb), n in sorted(story_pairs.items(), key=lambda kv: -kv[1])[:10]:
        ov = disjoint[(sa, sb)]
        verdict = "DISJOINT" if not ov else "overlap: " + ", ".join(sorted(ov)[:3])
        print(f"  {n:>3} links  [{verdict}]")
        print(f"            {sid_title[sa][:56]}")
        print(f"            {sid_title[sb][:56]}")

    # Exhibits: the biggest stories' top names, so the numbers stay attached to real events.
    print("\ntop stories, top names (member count in parentheses; covered members in brackets):")
    for s in stories[:10]:
        member_idx = sorted({url_to_idx[c["url"]] for c in s["coverage"] if c["url"] in url_to_idx})
        names: dict = {}
        cov = 0
        for i in member_idx:
            if i in covered_set:
                cov += 1
            for name in persons[i] | orgs[i]:
                names[name] = names.get(name, 0) + 1
        top = ", ".join(f"{n}({c})" for n, c in sorted(names.items(), key=lambda kv: -kv[1])[:5])
        print(f"  {s['totalCoverage']:>4} arts [{cov:>3} cov]  {s['title'][:40]:<40}  {top or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
