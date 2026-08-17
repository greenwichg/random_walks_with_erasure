"""audit_template_edges.py — Phase A of the sole-template-edge rule: measure before any rule.

READ-ONLY instrument. The candidate fix under test (NOT implemented anywhere): a clustering
edge may not rest SOLELY on announcement-template vocabulary — the pairwise gate would
additionally require >= 1 shared token outside a curated template lexicon. Template tokens
would keep counting toward Jaccard (recall inside real stories unharmed); they would lose only
the power to be the entire case for a join. The production-confirmed anchor exhibit
(2026-08-17): "'X-Men' cast, release date revealed at D23" welded to "The Paper Season 2
Cast, Release Date and Trailer Revealed" (j=0.444, shared = {cast, date, release, revealed} —
100% template), then to Mirzapur and DJI Osmo, five articles in one story titled by the
earliest filer. The real X-Men edge shares {men, d23} beyond the template and survives the
candidate rule; all three false edges die.

This instrument measures the rule's exposure WITHOUT wiring it: the census of edges whose
shared-token set is a subset of the lexicon, how many shipped stories contain one, the
intra-story false-split exposure, per-story fragmentation if such edges were removed, and 12
deterministically-spread samples for hand-reading.

**Pre-registered kill criterion (Phase A, fixed before any number was seen): if more than 2%
of intra-story pairs are sole-template — the rule would be cutting real stories, not bridges —
or the hand-read shows the sampled sole-template edges are mostly genuine, the lexicon is
wrong and the fix dies here.** The verdict line applies the 2% arm mechanically and DEFERS the
hand-read arm explicitly — the X6 lesson: the instrument counts, the human reads, and Phase B
exists only after both.

The lexicon is the registered enumeration, verbatim — twelve tokens, no post-hoc additions:
per-token participation is reported so any future trim/extension is measured, not guessed.

No re-clustering: story membership comes from ONE baseline build (the production
environment's, discarded after measurement); fragmentation is a connectivity analysis of each
story's own member edge-graph. Run from a container carrying the deploy environment
(``dc run --rm -T api …``) or the baseline is fiction — the standing audit warning.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from bisect import bisect_right
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clustering                # noqa: E402
import discover                  # noqa: E402
import outlet_registry           # noqa: E402
import story_service             # noqa: E402
import store as store_mod        # noqa: E402

#: The registered candidate lexicon (2026-08-17 Phase A spec, verbatim). Announcement-template
#: vocabulary: tokens that name the SHAPE of an entertainment/product reveal headline rather
#: than its subject. Deliberately excludes words with plausible subject duty ("movie", "film",
#: "price", "review") — the census below measures; it does not curate further.
TEMPLATE = frozenset(("cast", "release", "date", "trailer", "revealed", "everything",
                      "know", "season", "episode", "premiere", "teaser", "specs"))

PAIRS_PER_STORY = 45             # X6 (a)-population convention: 10 members' cross pairs
HAND_READ = 12


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)

    tags = {"quorum": story_service.link_quorum(), "repair": story_service.repair_quorum(),
            "merge": story_service.merge_similarity(), "veto": story_service.geo_veto() or "off"}
    print(f"environment          : quorum {tags['quorum']}  repair {tags['repair']}  "
          f"merge {tags['merge']}  geo-veto {tags['veto']}")
    if tags["quorum"] <= 0.0:
        print("  !! quorum 0.0 — NOT the production environment; run via `dc run --rm -T api …`.")

    st = store_mod.Store(args.db)
    rows = story_service._fetch(st)

    # The exclusion-mirrored article list — the exact population the builder clusters.
    arts = [discover.feed_article_to_article(r) for r in rows]
    if story_service.exclude_wire():
        arts = [a for a in arts if not (outlet_registry.is_wire(a.get("publisher"))
                                        or outlet_registry.is_wire_url(a.get("url")))]
    if story_service.exclude_aggregator():
        arts = [a for a in arts if not outlet_registry.is_aggregator(a.get("publisher"))]

    cap = story_service.desc_tokens()
    sim = clustering.DEFAULT_SIM
    ms = story_service.desc_min_shared() if cap > 0 else story_service.min_shared_tokens()
    floor = story_service.min_title_tokens()
    toks = [story_service.article_tokens(a, cap) for a in arts]
    times = [clustering.parse_time(a["publishedAt"]) for a in arts]
    weights = clustering.idf_weights(toks) if story_service.use_idf() else None
    print(f"window articles      : {len(rows):,} ({len(arts):,} post-exclusion)   "
          f"gate: sim>={sim} shared>={ms} floor>={floor} idf={weights is not None}")
    print(f"lexicon ({len(TEMPLATE)} tokens)  : {', '.join(sorted(TEMPLATE))}")

    def pair_gate(i: int, j: int) -> "frozenset | None":
        """The production pairwise gate; returns the shared set when the pair is an edge."""
        ti, tj = toks[i], toks[j]
        if len(ti) < floor or len(tj) < floor:
            return None
        inter = ti & tj
        if len(inter) < ms:
            return None
        if clustering.weighted_jaccard(ti, tj, weights) < sim:
            return None
        if not clustering.within_window(times[i], times[j], clustering.DEFAULT_WINDOW_DAYS):
            return None
        return inter

    # Story membership from ONE production-environment build (measured, then discarded).
    baseline = story_service.build_stories(rows, entities=story_service._entities_for(st, rows))
    by_url: dict = {}
    for idx, a in enumerate(arts):
        for key in (a.get("id"), a.get("url")):
            if key and key not in by_url:
                by_url[key] = idx
    story_of: dict = {}
    story_members: list = []
    for si, s in enumerate(baseline):
        mem = sorted({by_url[c.get("url")] for c in s["coverage"] if c.get("url") in by_url})
        story_members.append(mem)
        for idx in mem:
            story_of.setdefault(idx, si)

    # -- 1. full edge census (the production candidate walk: bisect + C-level tally) -------- #
    postings: dict = {}
    for i, t in enumerate(toks):
        for tok in t:
            postings.setdefault(tok, []).append(i)
    total_edges = sole_edges = 0
    relation = Counter()                  # intra / cross / unstoried, for sole-template edges
    token_part = Counter()                # lexicon token -> sole-template edges containing it
    sole_sample: list = []                # every sole-template edge (i, j, shared) for sampling
    stories_with_sole: set = set()
    for i in range(len(arts)):
        ti = toks[i]
        if len(ti) < floor:
            continue
        shared_counts: Counter = Counter()
        for tok in ti:
            plist = postings[tok]
            tail = plist[bisect_right(plist, i):]
            if tail:
                shared_counts.update(tail)
        for j, overlap in shared_counts.items():
            if overlap < ms or len(toks[j]) < floor:
                continue
            inter = pair_gate(i, j)
            if inter is None:
                continue
            total_edges += 1
            if inter <= TEMPLATE:
                sole_edges += 1
                si, sj = story_of.get(i), story_of.get(j)
                rel = ("intra-story" if si is not None and si == sj
                       else "cross-story" if si is not None and sj is not None
                       else "unstoried")
                relation[rel] += 1
                if si is not None and si == sj:
                    stories_with_sole.add(si)
                for tok in inter:
                    token_part[tok] += 1
                sole_sample.append((i, j, inter, rel))

    print(f"\n-- 1. edge census --")
    print(f"  edges passing the production gate : {total_edges:,}")
    print(f"  sole-template edges               : {sole_edges:,}  "
          f"({sole_edges / max(1, total_edges):.1%} of edges)")
    for rel in ("intra-story", "cross-story", "unstoried"):
        print(f"    {rel:<12}: {relation[rel]:,}")
    print(f"  per-token participation (sole-template edges containing it):")
    for tok, n in token_part.most_common():
        print(f"    {tok:<12}{n:>7,}   (window df {len(postings.get(tok, [])):,})")

    # -- 2. stories touched ----------------------------------------------------------------- #
    print(f"\n-- 2. shipped stories containing a sole-template intra-story edge --")
    print(f"  {len(stories_with_sole):,} of {len(baseline):,} stories "
          f"({len(stories_with_sole) / max(1, len(baseline)):.1%})")

    # -- 3. intra-story false-split exposure (THE KILL METRIC) ------------------------------ #
    sampled = sole_pairs = edge_pairs = sole_edge_pairs = 0
    for mem in story_members:
        capm = mem[:10]
        taken = 0
        for x in range(len(capm)):
            for y in range(x + 1, len(capm)):
                sampled += 1
                inter = pair_gate(capm[x], capm[y])
                if inter is not None:
                    edge_pairs += 1
                    if inter <= TEMPLATE:
                        sole_edge_pairs += 1
                if inter is not None and inter <= TEMPLATE:
                    sole_pairs += 1
                taken += 1
                if taken >= PAIRS_PER_STORY:
                    break
            if taken >= PAIRS_PER_STORY:
                break
    share = sole_pairs / max(1, sampled)
    print(f"\n-- 3. intra-story false-split exposure --")
    print(f"  sampled intra-story pairs         : {sampled:,}")
    print(f"  sole-template pairs (kill metric) : {sole_pairs:,}  ({share:.2%})   bar: <= 2.00%")
    print(f"  [context] intra-story EDGES {edge_pairs:,}; sole-template among them "
          f"{sole_edge_pairs:,} ({sole_edge_pairs / max(1, edge_pairs):.1%})")

    # Fragmentation: connectivity of each story's FULL member edge-graph without sole edges.
    frag_stories, dropped_articles, named = 0, 0, []
    for si, mem in enumerate(story_members):
        if len(mem) < 2:
            continue
        parent = {m: m for m in mem}
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for x in range(len(mem)):
            for y in range(x + 1, len(mem)):
                inter = pair_gate(mem[x], mem[y])
                if inter is not None and not (inter <= TEMPLATE):
                    ra, rb = find(mem[x]), find(mem[y])
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
        comps: dict = {}
        for m in mem:
            comps.setdefault(find(m), []).append(m)
        if len(comps) > 1:
            frag_stories += 1
            lost = 0
            for piece in comps.values():
                pubs = {arts[m].get("publisher") for m in piece}
                if len(piece) < 2 or len(pubs) < 2:
                    lost += len(piece)
            dropped_articles += lost
            named.append((si, len(mem), len(comps), lost, baseline[si]["title"]))
    print(f"  stories fragmenting without sole-template edges: {frag_stories:,} "
          f"of {len(baseline):,}; articles falling below admission: {dropped_articles:,}")
    named.sort(key=lambda t: (-t[3], -t[1]))
    for si, m, pieces, lost, title in named[:12]:
        print(f"    [{m} members -> {pieces} pieces, {lost} below admission] {title[:64]}")

    # -- 4. hand-read: 12 sole-template edges, deterministically spread ---------------------- #
    print(f"\n-- 4. hand-read sample ({HAND_READ} sole-template edges, index-spread) --")
    sole_sample.sort(key=lambda e: (e[0], e[1]))
    stride = max(1, len(sole_sample) // HAND_READ)
    for i, j, inter, rel in sole_sample[::stride][:HAND_READ]:
        print(f"  [{rel}] shared={sorted(inter)}")
        print(f"    A: {(arts[i].get('headline') or '')[:76]}")
        print(f"    B: {(arts[j].get('headline') or '')[:76]}")

    # -- 5. verdict (mechanical arm only; the hand-read arm is the reader's) ----------------- #
    print(f"\n-- 5. verdict --")
    if share > 0.02:
        print(f"  KILL — {share:.2%} of intra-story pairs are sole-template (> 2% bar): the "
              f"rule would cut real stories, not bridges. Phase B is not justified.")
    else:
        print(f"  mechanical bar MET ({share:.2%} <= 2%). Phase B is justified ONLY if the "
              f"hand-read above shows the sampled edges are bridges, not real stories — "
              f"that arm is deliberately not automated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
