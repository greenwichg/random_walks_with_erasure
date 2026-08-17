"""audit_template_gate.py — Phase B: the sole-template-evidence gate, measured against its bars.

READ-ONLY against production data: two in-memory builds of the same fetched rows — the
production-environment baseline (``template=False``) and the candidate (``template=True``) —
each built twice for the determinism bar, compared with the SAME metric definitions the
clustering-change harness owns (imported, not copied), then judged against the pre-registered
Phase B bars, verbatim from the approval:

  1. the X-Men/Paper/Mirzapur anchor exhibit resolves as Phase A's fragmentation predicted;
  2. the X-Men pair remains clustered;
  3. the three unrelated articles (DJI / The Paper / Mirzapur) detach;
  4. bad-cluster count (actionable geoCoherence below the floor) does not increase;
  5. droppedOut <= 5% of covered articles overall, with Entertainment reported separately;
  6. story count does not fall (the min_publishers cliff);
  7. largest cluster does not increase;
  8. candidate build time <= 1.5x baseline;
  9. two identical runs are byte-deterministic (both sides);
 10. null-control fragmentation: per-story pairwise-edge connectivity is computed WITH all
     edges (the null) and WITHOUT sole-template edges, and only stories whose component count
     RISES are attributed to the rule — the Phase A instrument's over-count (46 merge-pass
     lobes read as fragmentation) is the artifact this control exists to subtract.

Also reports before/after membership for every affected story and every sole-template edge.

The verdict is mechanical over bars 1-10; **any failure rejects the rule — per the approval,
no tuning around a failed bar.** Nothing is enabled: the production environment is not
touched, ``RWE_CLUSTER_TEMPLATE_GATE`` stays unset, and both builds are discarded. Run from a
container carrying the deploy environment (``dc run --rm -T api …``) or the baseline is
fiction — the standing audit warning.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clustering                        # noqa: E402
import discover                          # noqa: E402
import outlet_registry                   # noqa: E402
import story_service                     # noqa: E402
import store as store_mod                # noqa: E402
from audit_clustering_change import index_by_member, _coherence_stats   # noqa: E402

MAX_DROPPED = 0.05
MAX_TIME_RATIO = 1.5

#: The anchor exhibit's title signatures (the Phase A trace's, verbatim).
SIGS = {
    "xmen-radio": ("x-men", "cast", "d23"),
    "xmen-forbes": ("mcu", "x-men", "cast"),
    "dji": ("dji", "osmo"),
    "paper": ("paper", "season", "cast"),
    "mirzapur": ("mirzapur", "movie"),
}


def _find(arts: list, terms) -> "dict | None":
    for a in arts:
        t = (a.get("headline") or "").lower()
        if all(term in t for term in terms):
            return a
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)

    tags = {"quorum": story_service.link_quorum(), "repair": story_service.repair_quorum(),
            "merge": story_service.merge_similarity(), "veto": story_service.geo_veto() or "off"}
    print(f"environment          : quorum {tags['quorum']}  repair {tags['repair']}  "
          f"merge {tags['merge']}  geo-veto {tags['veto']}  "
          f"template-gate env: {os.environ.get('RWE_CLUSTER_TEMPLATE_GATE', '(unset)')}")
    if tags["quorum"] <= 0.0:
        print("  !! quorum 0.0 — NOT the production environment; run via `dc run --rm -T api …`.")

    st = store_mod.Store(args.db)
    rows = story_service._fetch(st)
    entities = (st.entities_for_urls([r.get("canonicalUrl") for r in rows])
                if story_service.entity_merge_min() > 0 else None)
    print(f"window articles      : {len(rows):,}")

    def timed_build(template: bool, stats=None):
        t0 = time.perf_counter()
        s = story_service.build_stories(rows, entities=entities, template=template,
                                        veto_stats=stats)
        return s, time.perf_counter() - t0

    canon = lambda stories: json.dumps(stories, sort_keys=True, default=str)
    a1, ta1 = timed_build(False)
    a2, ta2 = timed_build(False)
    tstats: dict = {}
    b1, tb1 = timed_build(True, tstats)
    b2, tb2 = timed_build(True)
    det_a, det_b = canon(a1) == canon(a2), canon(b1) == canon(b2)
    a, b = a1, b1
    ta, tb = min(ta1, ta2), min(tb1, tb2)

    a_member, b_member = index_by_member(a), index_by_member(b)
    a_by_id = {s["id"]: s for s in a}
    b_by_id = {s["id"]: s for s in b}

    # url -> topic/publisher, for the Entertainment split and the membership diffs.
    arts = [discover.feed_article_to_article(r) for r in rows]
    if story_service.exclude_wire():
        arts = [x for x in arts if not (outlet_registry.is_wire(x.get("publisher"))
                                        or outlet_registry.is_wire_url(x.get("url")))]
    if story_service.exclude_aggregator():
        arts = [x for x in arts if not outlet_registry.is_aggregator(x.get("publisher"))]
    info = {}
    for x in arts:
        for key in (x.get("id"), x.get("url")):
            if key and key not in info:
                info[key] = x

    bars = []                                # (name, passed, detail)

    # -- bars 1-3: the anchor exhibit ------------------------------------------------------- #
    found = {label: _find(arts, terms) for label, terms in SIGS.items()}
    have_all = all(found.values())
    if have_all:
        u = {label: found[label]["id"] for label in found}
        base_story = {label: a_member.get(u[label]) for label in found}
        cand_story = {label: b_member.get(u[label]) for label in found}
        welded = len({s for s in base_story.values() if s}) == 1 and all(base_story.values())
        pair_ok = (cand_story["xmen-radio"] is not None
                   and cand_story["xmen-radio"] == cand_story["xmen-forbes"])
        detached = all(cand_story[l] is None for l in ("dji", "paper", "mirzapur"))
        bars.append(("1. exhibit resolves as predicted", welded and pair_ok and detached,
                     f"baseline welded={welded}; candidate pair together={pair_ok}; "
                     f"three detached={detached}"))
        bars.append(("2. X-Men pair remains clustered", pair_ok,
                     f"pair story: {cand_story['xmen-radio']}"))
        bars.append(("3. three unrelated articles detach", detached,
                     {l: cand_story[l] for l in ("dji", "paper", "mirzapur")}))
    else:
        missing = [l for l, v in found.items() if v is None]
        bars.append(("1-3. exhibit bars", False,
                     f"exhibit articles aged out of the window: {missing} — cannot evaluate"))

    # -- bar 4: bad clusters ---------------------------------------------------------------- #
    ca, cb = _coherence_stats(a), _coherence_stats(b)
    bars.append(("4. bad-cluster count does not rise", cb["bad"] <= ca["bad"],
                 f"before {ca['bad']}/{ca['scored']} (mean {ca['mean']}), "
                 f"after {cb['bad']}/{cb['scored']} (mean {cb['mean']})"))

    # -- bar 5: droppedOut, overall + Entertainment ----------------------------------------- #
    dropped = [url for url in a_member if url not in b_member]
    covered = max(1, len(a_member))
    share = len(dropped) / covered
    ent_covered = [url for url in a_member if (info.get(url) or {}).get("topic") == "Entertainment"]
    ent_dropped = [url for url in dropped if (info.get(url) or {}).get("topic") == "Entertainment"]
    ent_share = len(ent_dropped) / max(1, len(ent_covered))
    bars.append(("5. droppedOut <= 5%", share <= MAX_DROPPED,
                 f"{len(dropped):,} of {covered:,} covered ({share:.2%}); Entertainment: "
                 f"{len(ent_dropped):,} of {len(ent_covered):,} ({ent_share:.2%})"))

    # -- bars 6-7: story count / largest ---------------------------------------------------- #
    bars.append(("6. story count does not fall", len(b) >= len(a), f"{len(a):,} -> {len(b):,}"))
    la = max((s["totalCoverage"] for s in a), default=0)
    lb = max((s["totalCoverage"] for s in b), default=0)
    bars.append(("7. largest cluster does not increase", lb <= la, f"{la} -> {lb}"))

    # -- bar 8: build time ------------------------------------------------------------------ #
    ratio = tb / max(1e-9, ta)
    bars.append(("8. build time <= 1.5x", ratio <= MAX_TIME_RATIO,
                 f"baseline {ta:.1f}s, candidate {tb:.1f}s (x{ratio:.2f}; "
                 f"runs {ta1:.1f}/{ta2:.1f} vs {tb1:.1f}/{tb2:.1f})"))

    # -- bar 9: determinism ----------------------------------------------------------------- #
    bars.append(("9. byte-deterministic (both sides)", det_a and det_b,
                 f"baseline {det_a}, candidate {det_b}"))

    # -- bar 10: null-control fragmentation -------------------------------------------------- #
    cap = story_service.desc_tokens()
    sim = clustering.DEFAULT_SIM
    ms = (story_service.desc_min_shared() if cap > 0 else story_service.min_shared_tokens())
    floor = story_service.min_title_tokens()
    idx_of = {}
    for i, x in enumerate(arts):
        for key in (x.get("id"), x.get("url")):
            if key and key not in idx_of:
                idx_of[key] = i
    toks = [story_service.article_tokens(x, cap) for x in arts]
    times = [clustering.parse_time(x["publishedAt"]) for x in arts]

    def edge_shared(i, j):
        ti, tj = toks[i], toks[j]
        if len(ti) < floor or len(tj) < floor:
            return None
        inter = ti & tj
        if len(inter) < ms or clustering.jaccard(ti, tj) < sim:
            return None
        if not clustering.within_window(times[i], times[j], clustering.DEFAULT_WINDOW_DAYS):
            return None
        return inter

    def components(members, drop_sole: bool):
        parent = {m: m for m in members}
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for xi in range(len(members)):
            for yi in range(xi + 1, len(members)):
                inter = edge_shared(members[xi], members[yi])
                if inter is None:
                    continue
                if drop_sole and inter <= story_service.TEMPLATE_TOKENS:
                    continue
                ra, rb = find(members[xi]), find(members[yi])
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
        return len({find(m) for m in members})

    marginal, sole_edges = [], []
    for s in a:
        mem = sorted({idx_of[c["url"]] for c in s["coverage"] if c["url"] in idx_of})
        if len(mem) < 2:
            continue
        for xi in range(len(mem)):
            for yi in range(xi + 1, len(mem)):
                inter = edge_shared(mem[xi], mem[yi])
                if inter is not None and inter <= story_service.TEMPLATE_TOKENS:
                    sole_edges.append((mem[xi], mem[yi], sorted(inter), s["title"]))
        null_c = components(mem, drop_sole=False)
        rule_c = components(mem, drop_sole=True)
        if rule_c > null_c:
            marginal.append((s, null_c, rule_c))
    bars.append(("10. null-control fragmentation is marginal-only",
                 len(marginal) <= 1,     # the anchor weld, and nothing else
                 f"{len(marginal)} story(ies) fragment ONLY under the rule "
                 f"(null-control subtracts merge-pass lobes); sole-template edges "
                 f"in shipped stories: {len(sole_edges)}"))

    # -- report ------------------------------------------------------------------------------ #
    print(f"\n-- bars --")
    for name, ok, detail in bars:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        print(f"         {detail}")

    print(f"\n-- affected sole-template edges (every one) --")
    for i, j, inter, title in sole_edges:
        print(f"  in '{title[:50]}' shared={inter}")
        print(f"    A: {(arts[i].get('headline') or '')[:72]}")
        print(f"    B: {(arts[j].get('headline') or '')[:72]}")

    print(f"\n-- before/after membership for every affected story --")
    a_sets = {sid: frozenset(c["url"] for c in s["coverage"]) for sid, s in a_by_id.items()}
    b_sets = {sid: frozenset(c["url"] for c in s["coverage"]) for sid, s in b_by_id.items()}
    changed = False
    for sid in sorted(set(a_sets) | set(b_sets)):
        if a_sets.get(sid) == b_sets.get(sid):
            continue
        changed = True
        side = ("only-before" if sid not in b_sets
                else "only-after" if sid not in a_sets else "changed")
        s = (a_by_id.get(sid) or b_by_id.get(sid))
        print(f"  [{side}] '{s['title'][:56]}'")
        for tag, sets_ in (("before", a_sets), ("after", b_sets)):
            urls = sets_.get(sid)
            if urls is None:
                print(f"    {tag}: (absent)")
                continue
            for url in sorted(urls):
                x = info.get(url) or {}
                print(f"    {tag}: {x.get('publisher', '?'):<20} "
                      f"{(x.get('headline') or url)[:56]}")
    if not changed:
        print("  (no story's membership differs)")
    print(f"  candidate veto counter: templateEdgeVetoed = "
          f"{tstats.get('templateEdgeVetoed', 0)}")

    print(f"\n-- verdict --")
    fails = [name for name, ok, _ in bars if not ok]
    if fails:
        print(f"  REJECT — failed bars: {', '.join(fails)}. Per the approval, the rule is "
              f"rejected without tuning around the failure.")
    else:
        print(f"  ALL BARS PASS — recommend adoption: default RWE_CLUSTER_TEMPLATE_GATE=1 in "
              f"deploy/docker-compose.yml (the same pattern as the quorum/veto knobs). "
              f"NOT done by this instrument; production is untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
