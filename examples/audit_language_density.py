"""audit_language_density.py — M14 Stage 0: the run that decides whether M14 exists.

**Read-only: no writes, no ingestion, no network, no curation.** Everything here runs on catalogue
rows we already hold, so it can be run before the ToS review that still gates any probing campaign.

`docs/M14_LANGUAGE_DENSITY_DESIGN.md` §9 makes Stage 0 the hinge: three questions, each of which can
come back "no" and stop the milestone.

    1. WHO IS THE UNLABELLED QUARTER?   25.9% of the window carries no `language`, so a
                                        language-targeted strategy is blind to a quarter of the
                                        corpus. Cheap to settle by script; must be settled first.

    2. DOES THE PEER HYPOTHESIS HOLD?   `audit_source_cohort.verdict` records it as REFUTED —
                                        "English with 214 peers participates at 27%, Vietnamese with
                                        SIX peers at 30%". The Vietnamese counter-example is now
                                        known to be a tokenizer artifact (its coverage went 32 -> 0
                                        under real-word tokenization), which RE-OPENS the question
                                        rather than settling it. This run re-asks it on the stratum
                                        where it is testable.

    3. DOES Δ RANK DIFFERENTLY FROM VOLUME?
                                        If marginal cross-publisher coverage and article volume rank
                                        the pool the same way, M14's whole premise is wrong and
                                        volume-ordered admission was right all along.

**The bars are stated here, before the run, because the previous milestone printed ADOPT and was
rejected on evidence the verdict line could not see.**

    dc run --rm -T api python examples/audit_language_density.py --db "$RWE_DB_URL"
"""

from __future__ import annotations

import argparse
import os
from collections import Counter

import clustering
import source_density as sdn
import story_service
import store as store_mod


def _pct(n, d) -> str:
    return f"{n / d:>6.1%}" if d else "     —"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    ap.add_argument("--show", type=int, default=15, help="languages / publishers to list")
    ap.add_argument("--lang", default="", help="greedy-rank publishers within this language only")
    ap.add_argument("--seed-publishers", default="",
                    help="comma-separated publishers to treat as already admitted, so the ranking "
                         "reports what each candidate adds ON TOP of them")
    ap.add_argument("--top", type=int, default=20, help="publishers to rank with --lang")
    args = ap.parse_args(argv)

    st = store_mod.Store(args.db)
    rows = story_service._fetch(st)
    print(f"window            : {len(rows):,} articles")
    print(f"    The CLUSTERING window — the same rows the story builder sees, so every number below")
    print(f"    is about the corpus that actually forms stories.")

    # ----------------------------------------------------------------- 1 · the unlabelled quarter
    unlabelled = [r for r in rows if not (r.get("language") or "").strip()]
    print(f"\n=== 1 · who is the unlabelled quarter? ===")
    print(f"    `language` is populated from the feed entry and most feeds do not supply one. A")
    print(f"    language-targeted strategy is blind to these rows, so the first question is whether")
    print(f"    the blindness matters. Script bounds it; the English check narrows it further and is")
    print(f"    a HEURISTIC (>=2 English function words), not a language identifier.")
    print(f"\n  unlabelled: {len(unlabelled):,} of {len(rows):,} ({_pct(len(unlabelled), len(rows)).strip()})")
    if unlabelled:
        scripts = Counter(sdn.script_of(sdn._title(r)) for r in unlabelled)
        print(f"\n  {'script':<12} {'articles':>9} {'share':>7}")
        for name, n in scripts.most_common(8):
            print(f"  {name or '(no letters)':<12} {n:>9,} {_pct(n, len(unlabelled))}")
        latin = [r for r in unlabelled if sdn.script_of(sdn._title(r)) == "latin"]
        eng = sum(1 for r in latin if sdn.looks_english(sdn._title(r)))
        non_latin = len(unlabelled) - len(latin)
        print(f"\n  of the {len(latin):,} Latin-script rows, {eng:,} carry English function words "
              f"({_pct(eng, len(latin)).strip()})")
        print(f"  NON-LATIN and unlabelled: {non_latin:,} — the rows a language strategy would miss "
              f"entirely")
        print(f"\n  BAR: if non-Latin unlabelled is a small share of the window, the blindness is")
        print(f"       tolerable and targeting can proceed on the labelled corpus. If it is large,")
        print(f"       the language backfill has to come first — targeting a corpus you cannot see")
        print(f"       is how M10's 6-day window looked fine for two days.")

    # ----------------------------------------------------------------- 2 · strata + density
    prof = sdn.language_profile(rows)
    print(f"\n=== 2 · strata, and the peer question on the stratum where it is testable ===")
    print(f"    Strata are DERIVED from the headlines, not a language list — see source_density.")
    print(f"    `tokenizer-dead` means the typical article yields < {clustering.MIN_TITLE_TOKENS} "
          f"tokens, so pair_admits")
    print(f"    rejects it before any other test and its density is UNMEASURABLE. `fragment` means")
    print(f"    its tokens are mostly orthographic debris, so the number is untrustworthy in BOTH")
    print(f"    directions. Only `healthy` rows may be compared with each other.")
    print(f"\n  {'lang':>5} {'stratum':<15} {'arts':>7} {'pubs':>5} {'pairs':>8} "
          f"{'co-cov':>7} {'dead':>6} {'frag':>6}  top script")
    for lang, p in list(prof.items())[:args.show]:
        top = p["scripts"][0][0] if p["scripts"] else ""
        print(f"  {lang[:5]:>5} {p['stratum']:<15} {p['articles']:>7,} {p['publishers']:>5} "
              f"{p['pairs']:>8,} {p['coCoverage']:>6.1%} {p['deadShare']:>5.0%} "
              f"{p['fragmentShare']:>5.0%}  {top}")

    # `?` is excluded from the testable stratum on purpose: it is a METADATA GAP, not a language.
    # Section 1 already accounts for it, and letting a bucket that mixes English with Korean sit in
    # a table about per-language density would put a number there that means nothing.
    healthy = [p for p in prof.values() if p["stratum"] == "healthy" and p["language"] != "?"]
    healthy.sort(key=lambda p: -p["publishers"])
    print(f"\n  --- the testable stratum: {len(healthy)} language(s), `?` excluded as a metadata "
          f"gap rather than a language ---")
    if len(healthy) < 3:
        print(f"  *** TOO FEW HEALTHY LANGUAGES TO TEST THE HYPOTHESIS. With fewer than three")
        print(f"      points there is no relationship to see, and reporting one would be the")
        print(f"      four-point curve fit this design refused to do. The tokenizer fix")
        print(f"      (--unicode-fallback) has to land before the question can be asked.")
    else:
        print(f"  {'lang':>5} {'publishers':>11} {'co-coverage':>12} {'mean partners':>14}")
        for p in healthy:
            print(f"  {p['language'][:5]:>5} {p['publishers']:>11} {p['coCoverage']:>11.1%} "
                  f"{p['meanPartners']:>14.2f}")
        pubs = [p["publishers"] for p in healthy]
        depth = [p["meanPartners"] for p in healthy]
        print(f"\n    The verdict reads MEAN PARTNERS, not co-coverage. Co-coverage asks only")
        print(f"    whether an article has AT LEAST ONE cross-publisher partner, so it saturates —")
        print(f"    two overlapping publishers read 100%, the same as fifty — and a SATURATED")
        print(f"    column is flat, which a `>=` monotonicity test scores as a pass. A flat")
        print(f"    relationship is the null hypothesis, not its confirmation.")
        # A flat line satisfies `>=` at every step, so requiring VARIATION is what stops this
        # reading as a gate that cannot fail — the defect this repository keeps finding.
        spread = (max(depth) - min(depth)) if depth else 0.0
        mono = all(depth[i] >= depth[i + 1] for i in range(len(depth) - 1))
        strict = mono and spread > 0 and depth[0] > depth[-1]
        print(f"\n  BAR: mean partners rises with publisher count, and VARIES across the stratum.")
        print(f"  RESULT: publishers {pubs}, mean partners {depth}")
        if not depth or spread == 0:
            print(f"          FLAT — no relationship either way. The hypothesis is NOT supported;")
            print(f"          publisher count predicts nothing here, which is what the original")
            print(f"          refutation claimed. M14 stops unless a larger stratum says otherwise.")
        elif strict:
            print(f"          MONOTONE AND VARYING — the hypothesis survives its re-test.")
        else:
            print(f"          NOT MONOTONE — the peer hypothesis fails on the stratum where it is")
            print(f"          testable. `audit_source_cohort.verdict` already recorded it as")
            print(f"          refuted once; this is the third justification failing, and M14 stops.")

    # ----------------------------------------------------------------- 3 · Δ vs volume
    target = args.lang or (healthy[-1]["language"] if healthy else "")
    if not target:
        print("\n(no language to rank — skipping the Δ comparison)")
        return 0
    group = [r for r in rows if (r.get("language") or "?").strip() == target]
    seed = {p.strip().lower() for p in args.seed_publishers.split(",") if p.strip()}
    print(f"\n=== 3 · does Δ rank differently from volume?  (language: {target}) ===")
    print(f"    Δ is the MARGINAL number of articles that gain a cross-publisher partner when this")
    print(f"    publisher is admitted — counted in BOTH directions, so a candidate is credited for")
    print(f"    the incumbent articles it partners as well as its own. A publisher covering events")
    print(f"    nobody else covers scores 0 however much it files.")
    pairs = sdn.cross_publisher_pairs(group)
    ranked = sdn.greedy_publishers(group, seed=seed, k=args.top, pairs=pairs)
    volume = Counter(sdn._pub(r) for r in group if sdn._pub(r))

    if not ranked:
        print(f"\n  NO PUBLISHER ADDS A SINGLE CROSS-PUBLISHER PARTNER in {target}.")
        print(f"  That is the density finding in its rawest form: this language's publishers do not")
        print(f"  cover the same events as each other, so no admission ORDER helps — only more")
        print(f"  publishers covering overlapping beats would.")
        return 0

    if ranked and ranked[0]["gain"] == 0:
        print(f"\n  NOTE: the first step scores 0 and the second carries the joint gain. A")
        print(f"        cross-publisher pair needs TWO publishers, so from a cold start no single")
        print(f"        admission buys anything — which is the density constraint stated exactly.")
    print(f"\n  {'#':>3} {'publisher':<34} {'Δ':>6} {'cumul':>7} {'articles':>9} {'vol rank':>9}")
    vol_order = [p for p, _ in volume.most_common()]
    for n, step in enumerate(ranked, 1):
        pub = step["publisher"]
        vr = vol_order.index(pub) + 1 if pub in vol_order else 0
        print(f"  {n:>3} {pub[:34]:<34} {step['gain']:>6,} {step['cumulative']:>7,} "
              f"{volume.get(pub, 0):>9,} {vr:>9}")

    top_delta = [s["publisher"] for s in ranked]
    top_vol = vol_order[:len(top_delta)]
    overlap = len(set(top_delta) & set(top_vol))
    print(f"\n  BAR: the Δ ranking must differ substantially from the volume ranking. If they agree,")
    print(f"       volume-ordered admission was right and M14's premise is wrong.")
    print(f"  RESULT: {overlap} of {len(top_delta)} publishers shared between the two top-N lists "
          f"({_pct(overlap, len(top_delta)).strip()} overlap)")
    if top_delta and top_vol and top_delta[0] != top_vol[0]:
        print(f"          the volume leader is {top_vol[0]} ({volume.get(top_vol[0], 0):,} articles); "
              f"Δ picks {top_delta[0]} first")
    print(f"\nNOTHING WAS CHANGED. This measures; admitting is source_campaign.py, and the M14")
    print(f"design gates it behind these bars.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
