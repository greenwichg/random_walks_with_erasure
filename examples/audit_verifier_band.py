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


#: V1 exhibit signatures — (label-hint, side-A title terms, side-B title terms, draft label,
#: rubric rule). Terms of length <= 3 match on word boundaries. Draft labels come from the
#: RATIFIED rubric table (docs/EVENT_IDENTITY_RUBRIC.md) and are clearly marked drafts in the
#: emitted sheet; absent exhibits are skipped, never fabricated.
V1_EXHIBITS = (
    ("xmen-pair", ("x-men", "cast", "d23"), ("mcu", "x-men", "cast"), "same_event", "5"),
    ("xmen-paper", ("x-men", "cast", "d23"), ("paper", "season", "cast"), "different_event", "5"),
    ("paper-mirzapur", ("paper", "season", "cast"), ("mirzapur", "movie"), "different_event", "5"),
    ("dji-mirzapur", ("dji", "osmo"), ("mirzapur", "movie"), "different_event", "5"),
    ("batwara-vishwanath", ("batwara", "collection", "day 2"),
     ("vishwanath", "collection", "day 2"), "different_event", "5,6"),
    ("vishwanath-jana", ("vishwanath", "trails"), ("jana nayagan", "day 21"),
     "different_event", "1"),
    ("batwara-days", ("batwara", "day 3"), ("batwara", "day 2"), "same_event", "2"),
    ("remains", ("human remains", "palomar"), ("human remains", "scarborough"),
     "different_event", "7"),
    ("shootings", ("shooting", "lexington"), ("shooting", "portland"), "different_event", "7"),
    ("hayden-family", ("hayden", "panettiere", "dead"),
     ("hayden", "panettiere", "life", "photos"), "same_event", "4-family"),
    ("tennis-previews", ("preview", "head-to-head", "odds"),
     ("preview", "head-to-head", "odds"), "different_event", "3b,5"),
    ("uk-alert-family", ("alert", "domestic abuse"), ("alert", "burnham"),
     "same_event", "4-family,5"),
    # Production 2026-08-25: the recall-genre weld (see story_service.RECALL_TOKENS) — a
    # frozen-fruit-bars recall bridged into the Prestige eye-drops recall story on pure
    # recall-shape vocabulary.
    ("recall-fruitbar", ("frozen fruit", "recalled"), ("eye drops", "recalled"),
     "different_event", "5"),
    # Production 2026-08-25: the comparative-bridge weld (see story_service.min_support) — a
    # Guardian article on The Odyssey's box-office record served inside the Spider-Man Brand New
    # Day story. Unlike every exhibit above it, this pair has NO lexical case to answer: the two
    # articles share zero tokens (j=0.000). They were joined through a round-up headline covering
    # both films, so the defect is in the linkage GRAPH, not in the vocabulary — registered here
    # so the verifier band scores it alongside the rest.
    ("odyssey-spiderman", ("odyssey", "nolan"), ("spider-man", "box office"),
     "different_event", "7"),
)


def _sig_match(title: str, terms) -> bool:
    import re as _re
    t = (title or "").lower()
    for term in terms:
        if len(term) <= 3:
            if not _re.search(r"(?<![a-z0-9])" + _re.escape(term) + r"(?![a-z0-9])", t):
                return False
        elif term not in t:
            return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--emit-pairs", default=None, metavar="PATH",
                    help="also write the V1 golden-pairs labeling sheet (JSONL) here; a "
                         "companion PATH.keys file maps pair_id -> urls so the sheet itself "
                         "stays publisher-blind per the rubric protocol")
    ap.add_argument("--per-class", type=int, default=80,
                    help="band pairs sampled per class for the sheet (index-spread)")
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
    c1_pairs, c1_rel = [], Counter()
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
            si, sj = story_of.get(i), story_of.get(j)
            rel = ("intra-story" if si is not None and si == sj
                   else "cross-story" if si is not None and sj is not None else "unstoried")
            c1_rel[rel] += 1
            c1_pairs.append((i, j, sorted(inter)[:6], rel))

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
    c2_pairs = []
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
                c2_pairs.append((i, j, s))

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
    c3_pairs = []
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
                    c3_pairs.append((i, j, sorted(cons[i] & cons[j])))

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
    c4_pairs, capped, seen4 = [], False, set()
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
                    c4_pairs.append((i, j, sorted(art_names[i] & art_names[j])[:3]))
            if capped:
                break
        if capped:
            break

    # -- report ------------------------------------------------------------------------------ #
    c1, c2, c3, c4 = len(c1_pairs), len(c2_pairs), len(c3_pairs), len(c4_pairs)
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

    show("class 1 (router-flagged edges)", c1_pairs, lambda t: (
        print(f"  [{t[3]}] shared~{t[2]}"),
        print(f"    A: {(arts[t[0]].get('headline') or '')[:72]}"),
        print(f"    B: {(arts[t[1]].get('headline') or '')[:72]}")))
    show("class 2 (near-threshold merges)", c2_pairs, lambda t: (
        print(f"  [sim {t[2]:.3f}] '{baseline[t[0]]['title'][:48]}'"),
        print(f"            vs '{baseline[t[1]]['title'][:48]}'")))
    show("class 3 (single shared name)", c3_pairs, lambda t: (
        print(f"  [{t[2]}] '{baseline[t[0]]['title'][:48]}'"),
        print(f"            vs '{baseline[t[1]]['title'][:48]}'")))
    show("class 4 (entity-anchored, low lexical)", c4_pairs, lambda t: (
        print(f"  [{t[2]}]"),
        print(f"    A: {(arts[t[0]].get('headline') or '')[:72]}"),
        print(f"    B: {(arts[t[1]].get('headline') or '')[:72]}")))

    # -- V1 golden-pairs sheet (opt-in; the same enumeration, so the sampling frame can never
    # drift from the measurement) --------------------------------------------------------------- #
    if args.emit_pairs:
        import hashlib
        import json

        def side(i: int) -> dict:
            """Exactly the verifier's inputs — publisher deliberately absent (rubric protocol)."""
            a = arts[i]
            e = ents.get(a.get("id") or a.get("url")) or {}
            names = sorted({n for kind in ("person", "org") for n in e.get(kind, ())
                            if n and not story_service.entity_noise(n)})
            return {"headline": a.get("headline") or "",
                    "dek": " ".join((a.get("description") or "").split())[:400],
                    "publishedAt": a.get("publishedAt") or "",
                    "entities": names[:8],
                    "countries": sorted(story_service._member_countries(a))}

        def rep_of(si: int) -> int:
            mem = story_members[si]
            return min(mem, key=lambda m: (arts[m].get("publishedAt") or "~",
                                           arts[m].get("id") or ""))

        def spread(lst, n):
            if len(lst) <= n:
                return list(lst)
            stride = len(lst) // n
            return list(lst[::stride][:n])

        rows_out, keys_out, seen_ids = [], [], set()

        def emit(i: int, j: int, klass: str, draft: str = "", rule: str = ""):
            ua, ub = sorted((arts[i].get("id") or arts[i].get("url") or "",
                             arts[j].get("id") or arts[j].get("url") or ""))
            pid = "p_" + hashlib.sha1(f"{ua}\x00{ub}".encode()).hexdigest()[:16]
            if pid in seen_ids:
                return
            seen_ids.add(pid)
            rows_out.append({"pair_id": pid, "class": klass, "a": side(i), "b": side(j),
                             "draft_label": draft, "draft_rule": rule, "label": "",
                             "rubric_version": "v1", "notes": ""})
            keys_out.append({"pair_id": pid, "url_a": ua, "url_b": ub})

        # exhibits first (draft-labeled from the RATIFIED rubric; absent ones skipped)
        n_ex = 0
        for label, ta, tb, draft, rule in V1_EXHIBITS:
            ia = next((k for k, a in enumerate(arts) if _sig_match(a.get("headline"), ta)), None)
            jb = next((k for k, a in enumerate(arts)
                       if _sig_match(a.get("headline"), tb) and k != ia), None)
            if ia is not None and jb is not None:
                emit(ia, jb, f"exhibit:{label}", draft, rule)
                n_ex += 1

        pc = max(1, args.per_class)
        for i, j, _sh, _rel in spread(sorted(c1_pairs), pc):
            emit(i, j, "router_edge")
        for si, sj, _s in spread(sorted(c2_pairs), pc):
            emit(rep_of(si), rep_of(sj), "near_merge")
        for si, sj, _n in spread(sorted(c3_pairs), pc):
            emit(rep_of(si), rep_of(sj), "single_name")
        for i, j, _n in spread(sorted(c4_pairs), pc):
            emit(i, j, "entity_anchor")

        # controls: intra-story pairs OUTSIDE the band (presumed same_event) ...
        banded = {(min(i, j), max(i, j)) for i, j, _s, _r in c1_pairs} | \
                 {(min(i, j), max(i, j)) for i, j, _n in c4_pairs}
        controls = []
        for mem in story_members:
            capm = mem[:6]
            for x in range(len(capm)):
                for y in range(x + 1, len(capm)):
                    p = (capm[x], capm[y])
                    if p not in banded:
                        controls.append(p)
        for i, j in spread(controls, max(1, (3 * pc) // 4)):
            emit(i, j, "intra_control")
        # ... and deterministic far negatives (different stories, zero token overlap)
        negs, step = [], max(1, len(arts) // 200)
        for i in range(0, len(arts), step):
            j = (i + 7919) % len(arts)
            a, b = min(i, j), max(i, j)
            if a == b:
                continue
            if story_of.get(a) is not None and story_of.get(a) == story_of.get(b):
                continue
            if not (toks[a] & toks[b]):
                negs.append((a, b))
        for i, j in spread(negs, pc // 2):
            emit(i, j, "random_negative")

        with open(args.emit_pairs, "w", encoding="utf-8") as f:
            for r in rows_out:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(args.emit_pairs + ".keys", "w", encoding="utf-8") as f:
            for r in keys_out:
                f.write(json.dumps(r) + "\n")
        by_class = Counter(r["class"].split(":")[0] for r in rows_out)
        print(f"\n-- V1 sheet emitted: {args.emit_pairs} --")
        print(f"  {len(rows_out):,} pairs ({n_ex} exhibits matched of {len(V1_EXHIBITS)}); "
              f"by class: {dict(by_class)}")
        print(f"  keys sidecar (urls; keep away from the labeler): {args.emit_pairs}.keys")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
