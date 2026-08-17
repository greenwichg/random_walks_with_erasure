"""audit_verifier_band.py — V0 of the same-event verifier: how big is the ambiguous band?

READ-ONLY instrument, the sizing gate for the verifier design (docs/EVENT_IDENTITY_RUBRIC.md
carries the V1 labeling spec). No verifier exists and nothing here calls a model; this measures
the TRIAGE BAND — the decisions the deterministic stack would route to a second-stage verdict —
so the budget, queue latency, and cost are set from measurement rather than hope. The design's
fail-closed contract means everything counted here serves TODAY's baseline decision unchanged.

Band classes measured (deterministic sampling and caps throughout):

  1. router-flagged growth edges — edges whose evidence is low-specificity: shared tokens
     inside the adopted+candidate template lexicons (the box-office extension, registered
     2026-08-17), or distinctive tokens absent from one side's headline-lead subject zone
     (mutual lead-5 anchoring). Edges the adopted gate already vetoes are EXCLUDED — no
     question is needed for a decided edge.
  2. near-threshold duplicate-merge proposals — story pairs whose profile similarity lands in
     [MERGE_BAND_LO, merge_similarity()) with the gap window satisfied: proposals the lexical
     pass almost made.
  3. the X5b single-name band — story pairs sharing exactly ONE discriminative corroborated
     entity name (below min_names=2, the USGS floor): the death-vs-retrospective class.
  4. entity-anchored, lexically-invisible pairs — article pairs sharing >= 2 non-noise entity
     names with token Jaccard < 0.15 across different (or no) stories: the cross-language /
     paraphrase recall band. Entity coverage is ~24%, and enumeration is capped; the count is
     reported as a floor when the cap engages.

Outputs: per-class counts (intra/cross-story split where meaningful), unique-question totals,
a per-day rate over the scan window, and a cost/latency projection with every assumption
printed (pinned model pricing; the verdict store means each pair is judged once, ever, so the
steady state is NEW pairs only — the per-day figure). Sample pairs per class for hand-reading.

Run from a container carrying the deploy environment (``dc run --rm -T api …``) or the
baseline is fiction — the standing audit warning.
"""

from __future__ import annotations

import argparse
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

#: Candidate box-office lexicon extension (registered 2026-08-17, Phase A2 — verbatim, eight
#: tokens; used here as a ROUTER signal, not a veto).
BOX_OFFICE = frozenset(("box", "office", "collection", "crore", "lakh", "lakhs", "gross", "day"))
LEAD_K = 5                       # headline subject zone: first K content tokens
MERGE_BAND_LO = 0.25             # near-threshold duplicate-merge band floor
CLASS4_JACCARD_MAX = 0.15
CLASS4_NAME_DF_CAP = 20          # entity names on more articles than this are non-discriminative
CLASS4_PAIR_CAP = 5000
SHOW = 4

# Cost assumptions, printed with the projection. Pinned claude-opus-4-8 API pricing.
PRICE_IN, PRICE_OUT = 5.00, 25.00          # $ per 1M tokens
TOK_IN, TOK_OUT = 700, 150                 # per pair question (prompt + 2x headline/dek; JSON out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)

    tags = {"quorum": story_service.link_quorum(), "repair": story_service.repair_quorum(),
            "merge": story_service.merge_similarity(), "veto": story_service.geo_veto() or "off",
            "template": story_service.template_gate()}
    print(f"environment          : quorum {tags['quorum']}  repair {tags['repair']}  "
          f"merge {tags['merge']}  geo-veto {tags['veto']}  template-gate {tags['template']}")
    if tags["quorum"] <= 0.0:
        print("  !! quorum 0.0 — NOT the production environment; run via `dc run --rm -T api …`.")

    st = store_mod.Store(args.db)
    rows = story_service._fetch(st)
    ents = st.entities_for_urls([r.get("canonicalUrl") for r in rows])
    days = story_service.scan_days()
    arts = [discover.feed_article_to_article(r) for r in rows]
    if story_service.exclude_wire():
        arts = [a for a in arts if not (outlet_registry.is_wire(a.get("publisher"))
                                        or outlet_registry.is_wire_url(a.get("url")))]
    if story_service.exclude_aggregator():
        arts = [a for a in arts if not outlet_registry.is_aggregator(a.get("publisher"))]
    print(f"window articles      : {len(rows):,} ({len(arts):,} post-exclusion; "
          f"scan window {days:g}d)")

    cap = story_service.desc_tokens()
    sim = clustering.DEFAULT_SIM
    ms = story_service.desc_min_shared() if cap > 0 else story_service.min_shared_tokens()
    floor = story_service.min_title_tokens()
    LEX = story_service.TEMPLATE_TOKENS | BOX_OFFICE
    toks = [story_service.article_tokens(a, cap) for a in arts]
    leads = [clustering.description_tokens(a.get("headline") or "", LEAD_K) for a in arts]
    times = [clustering.parse_time(a["publishedAt"]) for a in arts]

    baseline = story_service.build_stories(rows, entities=story_service._entities_for(st, rows))
    by_url: dict = {}
    for i, a in enumerate(arts):
        for k in (a.get("id"), a.get("url")):
            if k and k not in by_url:
                by_url[k] = i
    story_of: dict = {}
    story_members: list = []
    for si, s in enumerate(baseline):
        mem = sorted({by_url[c["url"]] for c in s["coverage"] if c["url"] in by_url})
        story_members.append(mem)
        for m in mem:
            story_of.setdefault(m, si)

    # -- class 1: router-flagged edges ------------------------------------------------------ #
    postings: dict = {}
    for i, t in enumerate(toks):
        for tok in t:
            postings.setdefault(tok, []).append(i)
    c1, c1_rel, samples1 = 0, Counter(), []
    total_edges = 0
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
            inter = ti & toks[j]
            if (clustering.jaccard(ti, toks[j]) < sim
                    or not clustering.within_window(times[i], times[j],
                                                    clustering.DEFAULT_WINDOW_DAYS)):
                continue
            total_edges += 1
            if inter <= story_service.TEMPLATE_TOKENS:
                continue                      # already vetoed by the adopted gate — decided
            distinctive = inter - LEX
            flagged = (not distinctive
                       or not (distinctive & leads[i]) or not (distinctive & leads[j]))
            if not flagged:
                continue
            c1 += 1
            si, sj = story_of.get(i), story_of.get(j)
            rel = ("intra-story" if si is not None and si == sj
                   else "cross-story" if si is not None and sj is not None else "unstoried")
            c1_rel[rel] += 1
            if len(samples1) < SHOW * 3:
                samples1.append((i, j, sorted(inter)[:6], rel))

    # -- class 2: near-threshold duplicate-merge proposals ---------------------------------- #
    all_members = [[arts[m] for m in mem] for mem in story_members]
    profiles = [story_service._profile(g) for g in all_members]
    weights = clustering.idf_weights(profiles)
    total_w = [sum(weights.get(t, 1.0) for t in p) for p in profiles]
    prof_postings: dict = {}
    for i, p in enumerate(profiles):
        for t in p:
            prof_postings.setdefault(t, []).append(i)
    common = max(2, len(profiles) // 2)
    msim = story_service.merge_similarity()
    gap_max = story_service.merge_max_gap_hours()
    bound = 1.0 + MERGE_BAND_LO
    c2, samples2 = 0, []
    for i in range(len(profiles)):
        seen: set = set()
        for t in profiles[i]:
            if len(prof_postings[t]) > common:
                continue
            for j in prof_postings[t]:
                if j > i:
                    seen.add(j)
        for j in sorted(seen):
            ti_, tj_ = total_w[i], total_w[j]
            if (ti_ if ti_ < tj_ else tj_) * bound < MERGE_BAND_LO * (ti_ + tj_):
                continue
            inter = profiles[i] & profiles[j]
            if not inter:
                continue
            w = sum(weights.get(t, 1.0) for t in inter)
            den = ti_ + tj_ - w
            s = (w / den) if den else 0.0
            if MERGE_BAND_LO <= s < msim and \
                    story_service._gap_hours(all_members[i], all_members[j]) <= gap_max:
                c2 += 1
                if len(samples2) < SHOW:
                    samples2.append((i, j, s))

    # -- class 3: the X5b single-name band --------------------------------------------------- #
    def eprofile(ms_):
        votes: dict = {}
        for m in ms_:
            e = ents.get(m.get("id") or m.get("url")) or {}
            seen_ = {n for kind in ("person", "org") for n in e.get(kind, ())
                     if n and not story_service.entity_noise(n)}
            for n in seen_:
                votes[n] = votes.get(n, 0) + 1
        return {n: c for n, c in votes.items() if c >= 2}
    eprofiles = [eprofile(g) for g in all_members]
    ent_postings: dict = {}
    for i, p in enumerate(eprofiles):
        for n in p:
            ent_postings.setdefault(n, []).append(i)
    disc = frozenset(n for n, sids in ent_postings.items()
                     if len(sids) <= story_service.ENTITY_MERGE_MAX_STORY_DF)
    cons = [frozenset(p) & disc for p in eprofiles]
    c3, samples3 = 0, []
    seen_pairs: set = set()
    for n in sorted(disc):
        sids = ent_postings[n]
        for x in range(len(sids)):
            for y in range(x + 1, len(sids)):
                i, j = sids[x], sids[y]
                if (i, j) in seen_pairs:
                    continue
                seen_pairs.add((i, j))
                if len(cons[i] & cons[j]) == 1 and \
                        story_service._gap_hours(all_members[i], all_members[j]) <= gap_max:
                    c3 += 1
                    if len(samples3) < SHOW:
                        samples3.append((i, j, sorted(cons[i] & cons[j])))

    # -- class 4: entity-anchored, lexically-invisible article pairs (capped floor) ---------- #
    art_names: list = []
    for a in arts:
        e = ents.get(a.get("id") or a.get("url")) or {}
        art_names.append(frozenset(n for kind in ("person", "org") for n in e.get(kind, ())
                                   if n and not story_service.entity_noise(n)))
    name_postings: dict = {}
    for i, names in enumerate(art_names):
        for n in names:
            name_postings.setdefault(n, []).append(i)
    c4, capped, seen4 = 0, False, set()
    samples4: list = []
    for n, plist in sorted(name_postings.items()):
        if len(plist) > CLASS4_NAME_DF_CAP:
            continue
        for x in range(len(plist)):
            for y in range(x + 1, len(plist)):
                i, j = plist[x], plist[y]
                if (i, j) in seen4:
                    continue
                seen4.add((i, j))
                if len(seen4) >= CLASS4_PAIR_CAP:
                    capped = True
                    break
                if len(art_names[i] & art_names[j]) < 2:
                    continue
                si, sj = story_of.get(i), story_of.get(j)
                if si is not None and si == sj:
                    continue                  # already together — no question
                if clustering.jaccard(toks[i], toks[j]) < CLASS4_JACCARD_MAX and \
                        clustering.within_window(times[i], times[j],
                                                 clustering.DEFAULT_WINDOW_DAYS):
                    c4 += 1
                    if len(samples4) < SHOW:
                        samples4.append((i, j, sorted(art_names[i] & art_names[j])[:3]))
            if capped:
                break
        if capped:
            break

    # -- report ------------------------------------------------------------------------------ #
    print(f"\n-- band composition (window totals; the verdict store makes steady state "
          f"NEW pairs only) --")
    print(f"  1. router-flagged edges          : {c1:,} of {total_edges:,} edges "
          f"({dict(c1_rel)})")
    print(f"  2. near-threshold dup merges     : {c2:,} story pairs "
          f"[{MERGE_BAND_LO}, {msim})")
    print(f"  3. X5b single-name band          : {c3:,} story pairs (shared names == 1)")
    print(f"  4. entity-anchored low-lexical   : {c4:,} article pairs"
          + ("  (CAPPED — a floor)" if capped else ""))
    total_q = c1 + c2 + c3 + c4
    per_day = total_q / max(1.0, days)
    print(f"  total question-pairs             : {total_q:,}  (~{per_day:,.0f}/day over "
          f"the {days:g}d window)")

    cost_pair = (TOK_IN * PRICE_IN + TOK_OUT * PRICE_OUT) / 1e6
    print(f"\n-- cost/latency projection (assumptions printed, not hidden) --")
    print(f"  model claude-opus-4-8 @ ${PRICE_IN}/M in, ${PRICE_OUT}/M out; "
          f"~{TOK_IN} in + {TOK_OUT} out tokens/pair -> ${cost_pair:.4f}/pair")
    print(f"  window backfill (once): ${total_q * cost_pair:,.2f}   "
          f"(batch API: ${total_q * cost_pair / 2:,.2f})")
    print(f"  steady state: ~${per_day * cost_pair:,.2f}/day at ~{per_day:,.0f} new pairs/day")
    print(f"  queue latency at 30 req/min: {per_day / 30 / 60:.1f}h/day of worker time")

    def show(title, items, fmt):
        print(f"\n-- samples: {title} --")
        for it in items[:SHOW]:
            fmt(it)

    show("class 1 (router-flagged edges)", samples1, lambda t: (
        print(f"  [{t[3]}] shared~{t[2]}"),
        print(f"    A: {(arts[t[0]].get('headline') or '')[:72]}"),
        print(f"    B: {(arts[t[1]].get('headline') or '')[:72]}")))
    show("class 2 (near-threshold merges)", samples2, lambda t: (
        print(f"  [sim {t[2]:.3f}] '{baseline[t[0]]['title'][:48]}'"),
        print(f"            vs '{baseline[t[1]]['title'][:48]}'")))
    show("class 3 (single shared name)", samples3, lambda t: (
        print(f"  [{t[2]}] '{baseline[t[0]]['title'][:48]}'"),
        print(f"            vs '{baseline[t[1]]['title'][:48]}'")))
    show("class 4 (entity-anchored, low lexical)", samples4, lambda t: (
        print(f"  [{t[2]}]"),
        print(f"    A: {(arts[t[0]].get('headline') or '')[:72]}"),
        print(f"    B: {(arts[t[1]].get('headline') or '')[:72]}")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
