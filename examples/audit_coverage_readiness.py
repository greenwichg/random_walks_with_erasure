"""audit_coverage_readiness.py — Phase 0 of the insight-derived Coverage Comparison roadmap.

Design: docs/COVERAGE_COMPARISON_REVISED_DESIGN.md §11. **This probe is a hard gate**: the previous
roadmap (L1–L3) specified a readiness measurement, the measurement was deferred, the roadmap was
built, and docs/COVERAGE_COMPARISON_VALUE_EVALUATION.md then had to retire it. The four numbers
below decide whether the replacement is worth building, BEFORE anything is built.

It reports §11 items 1, 2, 3 and 8 — everything that can be measured without generating a single
insight:

    1. COMPARABLE-SET SIZE per L0-gated cluster, and how many clusters reach MIN_COMPARABLE.
       An UPPER BOUND: recipe and format parity can only remove members (design §4), so a cluster
       failing here can never pass. **This is the analogue of the "1 cluster out of 800" that
       killed L1–L3, and it is the gate: >= 100 clusters, or the roadmap stops.**
    2. ELIGIBILITY SENSITIVITY at min_chars 150/200/250 — the measured median description is 154
       chars, so the typical article sits just above the 200 floor and the number moves sharply.
    3. ARRIVAL RATE into the comparable scope — the input to the batch-size formula (design §9.3).
    8. SYNDICATION RATE — near-duplicate share, which moves every denominator in the feature.

Items 4–7 (generation latency, enum reliability, quantity yield, token distribution) require the
extended prompt to exist and are measured with ``benchmark_insights.py --sample-production`` after
the Phase 1 contract lands. See docs/COVERAGE_COMPARISON_REVISED_DESIGN.md §11 and the ordering
note in the implementation report.

    python examples/audit_coverage_readiness.py
    python examples/audit_coverage_readiness.py --show 10     # sample the clusters

Read-only: it opens the store, builds the same default story view the product serves, and writes
nothing. It calls the SAME comparable-set functions production will (``coverage_insights``), so the
numbers describe the rule that will actually ship.
"""

from __future__ import annotations

import argparse
import statistics
from collections import Counter

import article_insights
import coverage_comparison as cc
import coverage_insights as ci
import ingest                       # canonical_url — coverage carries the PUBLISHER url
import story_service
import store as store_mod

#: Design §11.2 — the floors to test the eligibility rule at.
_MIN_CHARS_SWEEP = (150, 200, 250)


def _catalog_text(store_, canon_urls: set) -> dict:
    """canonical_url -> ``(generator text, title+description)``.

    Two texts, because the comparable-set rules ask two different questions: input parity is about
    what the model would see, wire detection is about the copy the outlet ran (design §4).

    The join is CANONICAL (design §10): coverage members carry the publisher's own URL, the catalog
    is keyed by canonical URL, and joining on the raw member URL resolves ~8% of members — the exact
    defect that invalidated the first audit run."""
    from sqlalchemy import select
    if not canon_urls:
        return {}
    out: dict = {}
    with store_._Session() as s:
        rows = s.execute(select(store_mod.FeedArticle.canonical_url, store_mod.FeedArticle.title,
                                store_mod.FeedArticle.description, store_mod.FeedArticle.body)
                         .where(store_mod.FeedArticle.canonical_url.in_(list(canon_urls)))).all()
    for canon, title, desc, body in rows:
        gen = article_insights.article_text(
            {"headline": title, "description": desc, "body": body})
        out[canon] = (gen, f"{title or ''} {desc or ''}".strip())
    return out


def _cands(members: list, texts: dict, insights: "dict | None" = None) -> list:
    """Coverage members as comparable-set candidates, resolved through the canonical join.

    With ``insights`` supplied, each candidate also carries its stored facets, which is what turns
    the structural upper bound into the real comparable set."""
    out = []
    for m in members:
        canon = _canon(m)
        gen, dedup = texts.get(canon, ("", ""))
        c = ci.candidate(m, gen, dedup)
        if insights is not None:
            c = ci.with_insight(c, insights.get(canon))
        out.append(c)
    return out


def _canon(m: dict) -> str:
    return ingest.canonical_url(str(m.get("id") or m.get("url") or ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--show", type=int, default=8, help="sample clusters to print")
    ap.add_argument("--with-insights", action="store_true",
                    help="measure the TRUE comparable set using stored facets (recipe + format "
                         "parity), not the structural upper bound. Needs generated insights.")
    args = ap.parse_args(argv)

    st = store_mod.Store(args.db)
    stories = story_service.default_story_view(st, build_inline=True)
    window = story_service.scan_days()
    members_total = sum(len(s.get("coverage") or []) for s in stories)
    print(f"stories in the served view : {len(stories):,}")
    print(f"clustered articles         : {members_total:,}   (window {window:g} days)")
    print(f"rule: MIN_COMPARABLE={ci.min_comparable()}  input_parity={ci.input_parity():.2f}  "
          f"grace={ci.time_grace_hours():g}h  syndication>={ci.syndication_sim():.2f}  "
          f"min_chars={article_insights.min_chars()}")

    urls = {_canon(m) for s in stories for m in (s.get("coverage") or []) if m.get("url")}
    texts = _catalog_text(st, urls)
    print(f"members resolved in catalog: {len(texts):,} / {len(urls):,} distinct urls")
    if not texts:
        print("  no member rows resolved — cannot assess readiness")
        return 1

    insights = None
    if args.with_insights:
        insights = st.get_insights(sorted(urls))
        with_facets = sum(1 for v in insights.values() if (v or {}).get("facets"))
        print(f"members with stored facets : {with_facets:,} / {len(urls):,} "
              f"({with_facets / max(1, len(urls)):.1%})")
        if not with_facets:
            print("  no facets generated yet — run without --with-insights for the upper bound")
            return 1

    # ---- L0-gated clusters: the only ones any card can render for -----------------------------
    gated = []
    for s in stories:
        mem = list(s.get("coverage") or [])
        if not mem:
            continue
        art = {"publisher": mem[0].get("publisher"), "url": mem[0].get("url"),
               "leanBucket": mem[0].get("leanBucket"), "register": mem[0].get("register")}
        if cc.gate(art, s, member=mem[0]) is None:
            gated.append(s)
    print(f"\nclusters past the L0 gates : {len(gated):,}")

    # ---- 1. Comparable-set size (upper bound) -------------------------------------------------
    floor = article_insights.min_chars()
    sizes: list = []
    reach = 0
    samples: list = []
    for s in gated:
        mem = list(s.get("coverage") or [])
        cands = _cands(mem, texts, insights)
        eligible = [c for c in cands if c["inputChars"] >= floor]
        # Per-member comparable sets; a cluster "reaches" when ANY member could render a card.
        best, best_target = 0, None
        for t in eligible:
            peers = (ci.comparable_set(t, eligible) if insights is not None
                     else ci.comparable_stage1(t, eligible))
            units = ci.support_units(peers)
            if units > best:
                best, best_target = units, t
        sizes.append(best)
        if best >= ci.min_comparable():
            reach += 1
            if len(samples) < args.show:
                samples.append((s, best, len(mem), len(eligible), best_target))

    share = reach / max(1, len(gated))
    headline = ("TRUE set: recipe + format parity applied" if insights is not None
                else "UPPER BOUND: recipe+format parity can only reduce")
    print(f"\n== 1. comparable-set size ({headline}) ==")
    if sizes:
        srt = sorted(sizes)
        print(f"  support units per cluster : p10 {srt[len(srt) // 10]}  "
              f"median {statistics.median(srt):.0f}  p90 {srt[len(srt) * 9 // 10]}  "
              f"max {srt[-1]}")
    print(f"  clusters reaching >= {ci.min_comparable():<2}   : {reach:,} / {len(gated):,} ({share:.1%})")
    print(f"  GATE (design §13 phase 0)  : {'PASS' if reach >= 100 else 'FAIL'}  (needs >= 100)")
    if samples:
        print("  samples:")
        for s, units, n_mem, n_elig, t in samples:
            print(f"     units={units:>2} members={n_mem:>3} eligible={n_elig:>3}  "
                  f"{(s.get('title') or '')[:56]}")

    # ---- 2. Eligibility sensitivity -----------------------------------------------------------
    print("\n== 2. eligibility sensitivity (design §11.2) ==")
    lens = sorted(len(gen) for gen, _ in texts.values())
    print(f"  generator input length    : p10 {lens[len(lens) // 10]:,}  "
          f"median {statistics.median(lens):,.0f}  p90 {lens[len(lens) * 9 // 10]:,}")
    for mc in _MIN_CHARS_SWEEP:
        ok = sum(1 for n in lens if n >= mc)
        n_reach = 0
        for s in gated:
            mem = list(s.get("coverage") or [])
            elig = [c for c in _cands(mem, texts) if c["inputChars"] >= mc]
            if any(ci.support_units(ci.comparable_stage1(t, elig)) >= ci.min_comparable()
                   for t in elig):
                n_reach += 1
        print(f"  min_chars={mc:<4} eligible {ok:>6,} ({ok / len(lens):.1%})   "
              f"clusters reaching: {n_reach:,}")

    # ---- 3. Arrival rate ----------------------------------------------------------------------
    print("\n== 3. arrival rate (design §9.3 input) ==")
    per_day = members_total / max(0.5, window)
    print(f"  clustered articles/day    : {per_day:,.0f}")
    eligible_total = sum(1 for n in lens if n >= floor)
    elig_per_day = eligible_total / max(0.5, window)
    print(f"  …eligible at min_chars={floor:<4}: {elig_per_day:,.0f}/day")
    interval = 600.0
    need = int(-(-elig_per_day * interval // 86400) * 1.5) + 1
    print(f"  required RWE_INSIGHTS_BATCH at a {interval:g}s cycle, 1.5x headroom: {need}")
    print(f"  (today's default is {article_insights.DEFAULT_BATCH} → "
          f"{article_insights.DEFAULT_BATCH * 86400 / interval:,.0f}/day capacity)")

    # ---- 8. Syndication -----------------------------------------------------------------------
    print("\n== 8. syndication rate (design §11.8) ==")
    collapsed_from, collapsed_to, wire_clusters = 0, 0, 0
    biggest = Counter()
    for s in gated:
        mem = list(s.get("coverage") or [])
        cands = _cands(mem, texts)
        groups = ci.syndication_groups(cands)
        multi = [g for g in groups if len(g) > 1]
        collapsed_from += len(cands)
        collapsed_to += len(groups)
        if multi:
            wire_clusters += 1
            biggest[max(len(g) for g in multi)] += 1
    if collapsed_from:
        print(f"  members in gated clusters : {collapsed_from:,}")
        print(f"  after wire collapse       : {collapsed_to:,} "
              f"({1 - collapsed_to / collapsed_from:.1%} folded)")
        print(f"  clusters with wire copy   : {wire_clusters:,} / {len(gated):,} "
              f"({wire_clusters / max(1, len(gated)):.1%})")
        if biggest:
            print("  largest wire group size   : "
                  + ", ".join(f"{k}x:{v}" for k, v in sorted(biggest.items(), reverse=True)[:6]))

    print("\n  Read: item 1 is the gate. Items 4-7 (latency, enum reliability, quantity yield,")
    print("  token distribution) need the Phase 1 contract and run via benchmark_insights.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
