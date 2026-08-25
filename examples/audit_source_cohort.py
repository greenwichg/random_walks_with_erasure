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
import discover
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

#: Participation below which an outlet is REPORTED as a possible Search/Discover source. It does
#: not demote — two justifications for acting on this number have now failed against the data. See
#: :func:`verdict`.
PARTICIPATION_FLOOR = 0.10

#: Same-language outlets, reported beside participation so the two can be read together.
#:
#: This was a GATE — below three peers, low participation was excused as "stranded". The measurement
#: killed it: English at 214 peers participates at 27%, Vietnamese at SIX peers at 30%. Peer count
#: does not predict participation, so the number is now context for a human reading the table and
#: nothing decides on it.
PEER_FLOOR = 3


def _identity(reg, row) -> str:
    pub = (row.get("publisher") or "").strip()
    o = reg.resolve(pub) if pub else None
    return o.canonical if o else pub.lower()


def _host(row) -> str:
    return outlet_registry._host_of(row.get("canonicalUrl") or row.get("url") or "")


def member_key(row) -> str:
    """The key a story's coverage entry carries for this row.

    **This is the bug that invalidated the first two production runs of this script, and it is
    worth the docstring.** ``audit_clustering_change.index_by_member`` indexes on ``c["url"]``, and
    ``_coverage`` fills that from the article's DISPLAY url — ``_absolute_url(row["url"] or
    row["canonicalUrl"])``. This script looked up ``canonicalUrl``, which ``ingest.canonical_url``
    has already lower-cased, stripped of ``www.``, of the query string and of the trailing slash.

    For any article whose feed URL carries any of those — which is most of them — the two strings
    differ and the lookup MISSES. Measured on a three-row fixture: **0 of 3 hits**. On production it
    reported 292 in-story articles against a window that actually had 6,121 covered, so every
    participation figure was low by roughly 20x, and the outlets it appeared to separate were
    separated by URL formatting rather than by clustering.

    The expression below is the one ``discover.feed_article_to_article`` uses, called on the same
    module, so the two cannot drift apart without the import failing."""
    return discover._absolute_url(row.get("url") or row.get("canonicalUrl"))


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
        urls = [member_key(a) for a in arts]
        in_story = [u for u in urls if u and u in member]
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

    **Participation is REPORTED AND NEVER ACTED ON, and three production runs are why.**

    It first demoted 178 outlets — Index.hu, PerthNow, cooperativa.cl, nettavisen.no — and the
    proposed explanation was linguistic: title-token Jaccard cannot match a Hungarian headline to
    an English one, so an outlet with no same-language peers scores zero by construction. That
    reading was itself built on a broken membership lookup (see :func:`member_key`).

    With the lookup fixed, the peer hypothesis is **refuted by its own measurement**: English with
    214 peers participates at 27%, Vietnamese with SIX peers at 30%. Peer count does not predict
    participation, so the gate that was built on it was unjustified too.

    What remains true is that the criterion keeps flagging things that are not defects. Its list
    contains real newsrooms (The Hankyoreh, cooperativa.cl, BelTA, dailymemphian.com) and outlets a
    ratified decision already examined and KEPT — 9GAG and DEV Community were measured individually
    in `SOURCE_COVERAGE_AUDIT.md` Part 3, and `Nature` / `Space.com` are the research kind that
    audit decided stays. Two proposed justifications have now failed. Until a third survives
    contact with the data, low participation is an observation, not a verdict.

    **Only the two language-independent criteria demote**: syndication and host instability. Both
    measure something the outlet is actually doing wrong — republishing another masthead's copy, or
    having no stable identity — rather than something the corpus around it is not doing."""
    if s["kind"] in ("wire", "aggregator"):
        return "ALREADY EXCLUDED", f"registry kind={s['kind']} — never enters clustering"
    if s["syndication"] > SYNDICATION_CEILING:
        return "TIER B", (f"{s['syndication']:.0%} of headlines also run under another publisher — "
                          f"republisher, not a newsroom")
    if s["hostStability"] < 0.5 and s["hosts"] > 1:
        return "TIER B", f"only {s['hostStability']:.0%} of articles on its main host — unstable identity"
    if s["participation"] < PARTICIPATION_FLOOR:
        return "LOW PARTICIPATION", (
            f"{s['participation']:.0%} of its articles reach a story ({peers} peers in "
            f"{s['language'] or 'unknown'}) — REPORTED, NOT ACTED ON. See the note on this verdict.")
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

    # ---------------------------------------------------------------- reconciliation
    #
    # Every participation figure below depends on ONE dictionary lookup landing, and the first two
    # production runs of this script were invalid because it did not (see `member_key`). So the
    # totals are reconciled against the build's own covered count BEFORE anything is reported: the
    # per-outlet in-story counts must sum to the number of coverage entries, because every covered
    # article belongs to exactly one outlet. A mismatch means the key is wrong again, and the run
    # says so instead of printing 20x-low numbers with a straight face.
    covered = sum(len(s["coverage"]) for s in base)
    summed = sum(v["inStory"] for v in table.values())
    if summed != covered:
        print(f"*** LOOKUP BROKEN: per-outlet in-story sums to {summed:,}, but the build covered "
              f"{covered:,} articles.")
        print("    Every participation figure would be wrong. See member_key() — this is the exact")
        print("    failure that invalidated the runs of 2026-08-25. Refusing to report.")
        return 1

    tracked = {k: v for k, v in table.items() if v["tracked"]}
    print(f"window            : {len(rows):,} articles, {len(base):,} stories, "
          f"{covered:,} covered   [membership reconciled]")
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
    print("    The prediction was that participation tracks PEER COUNT. Measured 2026-08-25 with")
    print("    a corrected membership lookup, it does NOT: en has 214 peers at 27%, vi has SIX at")
    print("    30%. Kept because the table is still worth reading — every non-Latin-script")
    print("    language sits at exactly 0%, which is a question about the tokenizer, not a finding.")
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

    # ONLY the defect-based verdicts. Participation does not demote -- see verdict().
    demote = {k for k, s in cand.items()
              if verdict(s, peers.get(s["language"], 0))[0] == "TIER B"}
    print(f"\n=== the reasons, for the {len(demote)} Tier B verdicts (read these) ===")
    if not demote:
        print("  none — no outlet is republishing another masthead's copy or carrying an unstable")
        print("  identity. Low participation is reported above and deliberately does not demote.")
    for key in sorted(demote, key=lambda k: -cand[k]["inStory"])[:args.show]:
        print(f"  {cand[key]['canonical'][:36]:<36} "
              f"{verdict(cand[key], peers.get(cand[key]['language'], 0))[1]}")

    # ---------------------------------------------------------------- counterfactual
    # ---------------------------------------------------------------- the benefit side
    #
    # Every previous version of this script measured COST precisely and BENEFIT not at all, which
    # is why the five-outlet cohort could not be adjudicated: 29 collateral losses against an
    # unquantified good is not a trade, it is half a trade.
    #
    # For a syndicator the benefit IS countable. `SOURCE_COVERAGE_AUDIT` states the rationale
    # already used for aggregators: "an aggregator's articles ARE other outlets' articles, so
    # counting one as a publisher double-counts coverage the cluster already holds." So: how many
    # of a demoted outlet's in-story articles carried the SAME title-token set as another member of
    # that same story? Each one is a publisher-count inflation the story should never have had.
    story_members = defaultdict(list)
    for s in base:
        for c in s["coverage"]:
            story_members[s["id"]].append(c)
    tok_of = {member_key(r): clustering.title_tokens(r.get("title") or "") for r in rows}
    mb = ach.index_by_member(base)
    double = 0
    for r in rows:
        if _identity(reg, r) not in demote:
            continue
        k = member_key(r)
        sid = mb.get(k)
        if not sid:
            continue
        mine = tok_of.get(k)
        if mine and any(c["url"] != k and tok_of.get(c["url"]) == mine
                        for c in story_members[sid]):
            double += 1
    print(f"\n=== the benefit side: publisher counts these outlets inflated ===")
    print(f"  in-story articles carrying a title IDENTICAL to another member of the SAME story:"
          f" {double:,}")
    print("    Each is a story counting one event's coverage twice. That is the rationale")
    print("    EXCLUDED_KINDS already applies to aggregators, measured here rather than assumed.")

    print(f"\n=== clustering impact: move those {len(demote)} outlets to Tier B ===")
    print("    Filtering the rows directly rather than through the SQL prefilter: the cap is not")
    print("    binding, so the two are equivalent for the BUILD, and this keeps the audit off the")
    print("    query path entirely.")
    print("    Reported PER CRITERION as well as together, because syndication and host")
    print("    instability are different defects: one says 'this is someone else's copy', the")
    print("    other says 'we cannot tell who this is'. If the cost lands on one of them, the")
    print("    other can ship alone.")

    def counterfactual(label: str, drop: set):
        if not drop:
            print(f"\n  --- {label}: no outlets")
            return
        keep = [r for r in rows if _identity(reg, r) not in drop]
        after = story_service.build_stories(keep, entities=ents,
                                            event_verdicts=verdicts_in)
        ma = ach.index_by_member(after)
        moved = {member_key(r) for r in rows if _identity(reg, r) in drop}
        lost = [u for u in mb if u not in ma and u not in moved]
        cb = sum(1 for s in base if s.get("blindspotSide"))
        ca = sum(1 for s in after if s.get("blindspotSide"))
        bb, ab = ach._coherence_stats(base), ach._coherence_stats(after)
        print(f"\n  --- {label}: {len(drop)} outlets, {len(rows) - len(keep):,} rows")
        print(f"      stories            : {len(base):,} -> {len(after):,}")
        print(f"      largest cluster    : {max((len(s['coverage']) for s in base), default=0)} -> "
              f"{max((len(s['coverage']) for s in after), default=0)}")
        print(f"      covered articles   : {sum(len(s['coverage']) for s in base):,} -> "
              f"{sum(len(s['coverage']) for s in after):,}")
        print(f"      OTHER articles that LOST their story: {len(lost):,}   <- the bar")
        print(f"      other articles that changed story   : "
              f"{sum(1 for u, s in mb.items() if u not in moved and ma.get(u) and ma[u] != s):,}")
        print(f"      independent signal : {bb['bad']}/{bb['scored']} (mean {bb['mean']}) -> "
              f"{ab['bad']}/{ab['scored']} (mean {ab['mean']})")
        print(f"      BLINDSPOT CLAIMS   : {cb:,} -> {ca:,}")
        return ma

    synd = {k for k in demote if cand[k]["syndication"] > SYNDICATION_CEILING}
    host = demote - synd
    counterfactual("SYNDICATION only", synd)
    counterfactual("HOST INSTABILITY only", host)
    ma = counterfactual("ALL of them together", demote)

    print("\n=== ratified exhibits (all of them together) ===")
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
