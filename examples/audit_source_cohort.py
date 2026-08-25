"""audit_source_cohort.py — which of the outlets we ALREADY ingest earn their place in Tier A?

Read-only. No network, no writes, no ingestion, no curation. This is Stage 4 (Evaluation) of
`docs/SCALE_ROADMAP.md`, run on the cohort that is already inside the catalog.

## Why the first cohort is not outside

The expansion looks like it needs discovery and shadow ingest first. It does not, because **the
broad ingestion already happened and nobody evaluated it.** `rss_ingest.ingest_entries` has no
admission gate — an unknown outlet ingests anyway, `unknown_outlet` is observational — and GDELT
delivers arbitrary-domain URLs. Measured on the live catalog: **4,083 outlet identities, 3,729 of
them unrated**, about half the window's articles from outlets that cannot vote on lean.

Every one of those is in Tier A today, by grandfathering. So the first cohort is not a list of
publishers to add. It is the highest-volume outlets we already carry, measured for the first time,
against the question M1 and M2 built the machinery to ask: **does this outlet belong in the O(n²)
builder at all?**

That ordering also de-risks what comes next. The evaluation stage gets exercised on real data with
zero crawl, zero ToS exposure and zero new code in the serving path, before it is ever pointed at a
genuinely new source.

## The five measurements

``unique articles/stories``  volume, and what share of it actually reaches an admitted story. An
                            outlet whose articles always sit as singletons contributes to Search and
                            Discover and nothing to coverage.
``syndication``             share of its headlines whose exact title-token set also appears under a
                            DIFFERENT publisher. A proxy, not proof — but it is the production
                            tokenizer's own notion of "same headline", and it is what separates an
                            outlet covering an event from one republishing someone else's copy.
``reliability``             registry resolution, lean, kind, credibility, host stability, date
                            coverage.
``clustering impact``       the counterfactual: move the cohort to Tier B and read the production
                            bars — stories, covered articles, **articles that LOST their story**,
                            largest cluster, coherence, ratified exhibits.
``promotion``               a per-outlet verdict with its reason.

## The bar that decides

Tier A promotion needs a **lean rating** — without one an outlet inflates story size while
contributing nothing to the blindspot claim, which is `SOURCE_COVERAGE_AUDIT.md`'s central finding —
**and** the counterfactual bars. Tier B needs neither, because a Tier B row cannot alter the
partition. That asymmetry is why Tier B scales to 50,000 and Tier A does not.

    dc run --rm -T api python examples/audit_source_cohort.py --db "$RWE_DB_URL"
"""

from __future__ import annotations

import argparse
import os
from collections import Counter, defaultdict

import audit_clustering_change as ach
import clustering
import outlet_registry
import story_service
import store as store_mod

#: Articles in the window below which an outlet is not worth a verdict. The same floor the offline
#: validation prefilter used, and for the same measured reason: 3,442 of 4,083 identities sit below
#: it with a MEDIAN of one article, so they are noise rather than candidates.
VOLUME_FLOOR = 10

#: Share of an outlet's headlines that may duplicate another publisher's before it reads as a
#: republisher rather than a newsroom. Deliberately generous — genuine same-headline collisions do
#: happen on wire-fed stories — so a flagged outlet is a strong signal rather than a marginal one.
SYNDICATION_CEILING = 0.35

#: Share of an outlet's articles that must reach an admitted story for it to be carrying coverage.
#: Below this it is a Search/Discover source, which is exactly what Tier B is for — but ONLY if the
#: outlet had a fair chance to cluster. See :data:`PEER_FLOOR` and :func:`verdict`.
PARTICIPATION_FLOOR = 0.10

#: Outlets publishing in the same language before participation is a meaningful measurement of an
#: outlet rather than of the corpus around it.
#:
#: Clustering is title-token Jaccard, so an outlet can only join a story with a publisher writing in
#: its OWN language. Below this many peers, "0% participation" says nothing about the outlet: it
#: says the corpus has nobody for it to agree with. Three, because `min_publishers = 2` means a
#: story needs two distinct publishers, and an outlet needs at least one peer besides itself plus
#: margin for the peers not covering the same events on the same days.
PEER_FLOOR = 3


def _identity(reg, row) -> str:
    pub = (row.get("publisher") or "").strip()
    o = reg.resolve(pub) if pub else None
    return o.canonical if o else pub.lower()


def _host(row) -> str:
    return outlet_registry._host_of(row.get("canonicalUrl") or row.get("url") or "")


def outlet_table(rows: list, stories: list, reg) -> dict:
    """Per outlet identity, every measurement except the counterfactual."""
    by_id = defaultdict(list)
    for r in rows:
        by_id[_identity(reg, r)].append(r)

    # Which canonical URLs reached an admitted story, and which story.
    member = ach.index_by_member(stories)

    # Title-token set -> the distinct publishers carrying it. The production tokenizer, so "same
    # headline" means what clustering means by it rather than a second, private definition.
    carriers = defaultdict(set)
    for r in rows:
        toks = clustering.title_tokens(r.get("title") or "")
        if toks:
            carriers[toks].add((r.get("publisher") or "").strip().lower())

    out = {}
    for key, arts in by_id.items():
        o = reg.resolve(key)
        urls = [a.get("canonicalUrl") or a.get("url") for a in arts]
        in_story = [u for u in urls if u in member]
        hosts = Counter(_host(a) for a in arts if _host(a))
        langs = Counter((a.get("language") or "").strip().lower() for a in arts
                        if (a.get("language") or "").strip())
        dup = 0
        for a in arts:
            toks = clustering.title_tokens(a.get("title") or "")
            if toks and len(carriers[toks]) > 1:
                dup += 1
        dated = sum(1 for a in arts if (a.get("publishedAt") or "").strip())
        out[key] = {
            "articles": len(arts),
            "inStory": len(in_story),
            "stories": len({member[u] for u in in_story}),
            "participation": len(in_story) / max(1, len(arts)),
            "syndication": dup / max(1, len(arts)),
            "hosts": len(hosts),
            "topHost": hosts.most_common(1)[0][0] if hosts else "",
            "hostStability": (hosts.most_common(1)[0][1] / max(1, len(arts))) if hosts else 0.0,
            "language": langs.most_common(1)[0][0] if langs else "",
            "dated": dated / max(1, len(arts)),
            "tracked": o is not None,
            "rated": bool(o is not None and o.lean == o.lean),   # NaN != NaN
            "kind": (o.kind if o else None),
            "credibility": (o.credibility if o else None),
            "canonical": (o.canonical if o else key),
        }
    return out


def verdict(s: dict, peers: int = 99) -> "tuple[str, str]":
    """``(verdict, reason)`` for one outlet. ``peers`` is how many outlets in the corpus publish in
    this outlet's language — see STRANDED below.

    Ordered so the disqualifying reasons are read first: a syndicator that also participates heavily
    is the WORST case, not a mixed one, because its participation is other publishers' coverage
    counted twice.

    **The participation criterion is gated on peers, and the first production run is why.** It
    demoted 178 outlets, and the reasons block named Index.hu, PerthNow, cooperativa.cl,
    nettavisen.no, iltalehti.fi, digi24.ro — real newsrooms, every one at ``dated 100%``,
    ``host 100%``, ``syndication 0%`` and participation 0%. Title-token Jaccard cannot match a
    Hungarian headline to an English one, so a legitimate Hungarian newsroom scores zero **by
    construction**. Six Vietnamese outlets in the same corpus score 20-46% because they cluster with
    EACH OTHER.

    So participation measures *whether an outlet has linguistic peers in our corpus*, not whether it
    is valuable. Ungated it was a language filter wearing a quality filter's clothes — the same
    class of error as a coherence bar that is structurally blind to sources carrying no event
    geography. It also contradicted a ratified decision: 9GAG and DEV Community were measured
    individually in `SOURCE_COVERAGE_AUDIT.md` Part 3 and kept."""
    if s["kind"] in ("wire", "aggregator"):
        return "ALREADY EXCLUDED", f"registry kind={s['kind']} — never enters clustering"
    if s["syndication"] > SYNDICATION_CEILING:
        return "TIER B", (f"{s['syndication']:.0%} of headlines also run under another publisher — "
                          f"republisher, not a newsroom")
    if s["hostStability"] < 0.5 and s["hosts"] > 1:
        return "TIER B", f"only {s['hostStability']:.0%} of articles on its main host — unstable identity"
    if s["participation"] < PARTICIPATION_FLOOR:
        if peers < PEER_FLOOR:
            return "STRANDED", (f"{s['participation']:.0%} participation, but only {peers} outlet(s) "
                                f"in the corpus publish in {s['language'] or 'its language'} — "
                                f"participation cannot measure it. NOT a quality verdict.")
        return "TIER B", (f"{s['participation']:.0%} of its articles reach a story despite {peers} "
                          f"peers in {s['language'] or 'its language'} — a Search/Discover source")
    if s["rated"]:
        return "TIER A (keep)", "carries a lean, participates in stories — it can vote and it does"
    if s["tracked"]:
        return "RATE", (f"{s['participation']:.0%} participation — already has a registry row, "
                        f"needs a sourced LEAN before it can vote")
    return "CURATE", (f"{s['participation']:.0%} participation, {s['syndication']:.0%} syndication, "
                      f"but no registry row — curate one so it can eventually vote")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    ap.add_argument("--floor", type=int, default=VOLUME_FLOOR,
                    help="articles in the window below which an outlet gets no verdict")
    ap.add_argument("--show", type=int, default=30, help="candidates to list")
    args = ap.parse_args(argv)

    st = store_mod.Store(args.db)
    reg = outlet_registry.default_registry()
    rows = story_service._fetch(st)
    ents = story_service._entities_for(st, rows)
    verdicts_in, _band = story_service._event_inputs(st)
    base = story_service.build_stories(rows, entities=ents, event_verdicts=verdicts_in)
    table = outlet_table(rows, base, reg)

    tracked = {k: v for k, v in table.items() if v["tracked"]}
    print(f"window            : {len(rows):,} articles, {len(base):,} stories")
    print(f"outlet identities : {len(table):,}   tracked {len(tracked):,}   "
          f"untracked {len(table) - len(tracked):,}")
    print(f"  above the {args.floor}-article floor: "
          f"{sum(1 for v in table.values() if v['articles'] >= args.floor):,}")

    # ---------------------------------------------------------------- does language explain it?
    #
    # The first production run demoted 178 outlets on low participation, and the reasons block was
    # a list of real newsrooms in languages nobody else in the corpus writes. This section is what
    # decides whether that reading is right, instead of leaving it as a story about a list of names.
    #
    # The prediction, stated before the numbers: participation should track PEER COUNT, not
    # outlet quality — so a language with many outlets should show high participation and a
    # language with one should show ~0, whatever the outlets are.
    peers: Counter = Counter(v["language"] for v in table.values()
                             if v["language"] and v["articles"] >= args.floor)
    by_lang = defaultdict(lambda: [0, 0])
    for v in table.values():
        if v["articles"] >= args.floor:
            by_lang[v["language"] or "(none)"][0] += v["articles"]
            by_lang[v["language"] or "(none)"][1] += v["inStory"]
    print(f"\n=== does LANGUAGE explain participation? (outlets above the floor) ===")
    print("    Prediction: participation tracks how many PEERS an outlet has, not its quality —")
    print("    clustering is title-token Jaccard, so an outlet can only join a story with a")
    print("    publisher writing in its own language.")
    # `language` comes from the feed entry, and plenty of feeds do not supply one. Say so BEFORE
    # the table: a breakdown dominated by "(none)" cannot test the hypothesis either way, and
    # reporting it as though it had is exactly the failure this audit series keeps finding in its
    # own instruments — a gate that cannot fire reading as a gate that passed.
    above = [v for v in table.values() if v["articles"] >= args.floor]
    known = sum(1 for v in above if v["language"])
    cover = known / max(1, len(above))
    print(f"\n  language known for {known} of {len(above)} outlets above the floor ({cover:.0%})")
    if cover < 0.5:
        print("  *** TOO SPARSE TO CONCLUDE. `language` is populated from the feed entry and most")
        print("      feeds here do not supply one, so this section can neither confirm nor refute")
        print("      the peer hypothesis. Everything with an unknown language is treated as")
        print("      STRANDED — the fail-safe direction, since STRANDED demotes nobody.")
    print(f"\n  {'lang':>6} {'outlets':>8} {'arts':>7} {'inStory':>8} {'part':>6}")
    for lang, (arts, ins) in sorted(by_lang.items(), key=lambda kv: -kv[1][0])[:15]:
        n = peers.get(lang, 0) if lang != "(none)" else 0
        print(f"  {lang[:6]:>6} {n:>8} {arts:>7,} {ins:>8,} {ins / max(1, arts):>5.0%}")

    # ---------------------------------------------------------------- the cohort
    cand = {k: v for k, v in table.items()
            if v["articles"] >= args.floor and not v["rated"]}
    print(f"\n=== THE COHORT: {len(cand):,} unrated outlets at or above the floor ===")
    print("    Every one of these is in Tier A today, by grandfathering. None has ever been")
    print("    measured. Ranked by articles reaching an admitted story — the only volume that")
    print("    is coverage rather than catalog.")
    print(f"\n  {'arts':>6} {'story':>6} {'part':>6} {'synd':>6} {'host':>6} {'dated':>6}  outlet")
    ranked = sorted(cand.items(), key=lambda kv: -kv[1]["inStory"])
    for key, s in ranked[:args.show]:
        v, _why = verdict(s, peers.get(s["language"], 0))
        print(f"  {s['articles']:>6} {s['inStory']:>6} {s['participation']:>5.0%} "
              f"{s['syndication']:>5.0%} {s['hostStability']:>5.0%} {(s['language'] or '?')[:5]:>5}  "
              f"{s['canonical'][:30]:<30} {v}")

    # ---------------------------------------------------------------- verdict census
    census = Counter(verdict(s, peers.get(s["language"], 0))[0] for s in cand.values())
    print(f"\n=== verdicts ===")
    for name, n in census.most_common():
        arts = sum(s["articles"] for s in cand.values()
                   if verdict(s, peers.get(s["language"], 0))[0] == name)
        print(f"  {n:>5} outlets  {arts:>7,} articles  {name}")

    demote = {k for k, s in cand.items()
              if verdict(s, peers.get(s["language"], 0))[0] == "TIER B"}
    print(f"\n=== the reasons, for the {len(demote)} Tier B verdicts (read these) ===")
    for key in sorted(demote, key=lambda k: -cand[k]["inStory"])[:args.show]:
        print(f"  {cand[key]['canonical'][:36]:<36} "
              f"{verdict(cand[key], peers.get(cand[key]['language'], 0))[1]}")

    # ---------------------------------------------------------------- counterfactual
    print(f"\n=== clustering impact: move those {len(demote)} outlets to Tier B ===")
    print("    Filtering the rows directly rather than through the SQL prefilter: the cap is not")
    print("    binding, so the two are equivalent for the BUILD, and this keeps the audit off the")
    print("    query path entirely.")
    keep = [r for r in rows if _identity(reg, r) not in demote]
    after = story_service.build_stories(keep, entities=ents, event_verdicts=verdicts_in)
    mb, ma = ach.index_by_member(base), ach.index_by_member(after)
    cov_b = sum(len(s["coverage"]) for s in base)
    cov_a = sum(len(s["coverage"]) for s in after)
    moved_urls = {r.get("canonicalUrl") or r.get("url") for r in rows
                  if _identity(reg, r) in demote}

    print(f"  rows removed       : {len(rows) - len(keep):,}")
    print(f"  stories            : {len(base):,} -> {len(after):,}")
    print(f"  largest cluster    : {max((len(s['coverage']) for s in base), default=0)} -> "
          f"{max((len(s['coverage']) for s in after), default=0)}")
    print(f"  covered articles   : {cov_b:,} -> {cov_a:,}")
    lost = [u for u in mb if u not in ma and u not in moved_urls]
    print(f"  OTHER articles that LOST their story: {len(lost):,}"
          f"   <- the bar; the removed rows themselves do not count")
    print(f"  other articles that changed story   : "
          f"{sum(1 for u, s in mb.items() if u not in moved_urls and ma.get(u) and ma[u] != s):,}")
    bb, ab = ach._coherence_stats(base), ach._coherence_stats(after)
    print(f"  independent signal : {bb['bad']}/{bb['scored']} bad (mean {bb['mean']}) -> "
          f"{ab['bad']}/{ab['scored']} bad (mean {ab['mean']})")
    cb = sum(1 for s in base if s.get("blindspotSide"))
    ca = sum(1 for s in after if s.get("blindspotSide"))
    print(f"  BLINDSPOT CLAIMS   : {cb:,} -> {ca:,}")

    print("\n=== ratified exhibits ===")
    for label, truth, b, a in ach._exhibit_outcomes(rows, mb, ma):
        def fmt(v):
            return "not in window" if v is None else ("together" if v else "separated")
        print(f"  {label:<22} {truth:<16} {fmt(b):<14} -> {fmt(a):<14}"
              f"{'' if b == a else '   <-- MOVED'}")

    print("\nNOTHING WAS CHANGED. This selects and measures a cohort; moving an outlet is a")
    print("separate, explicit decision (RWE_CORPUS_TIER_B) taken on these numbers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
