"""audit_corpus_boundary.py — does the Tier A boundary hold, and what binds today?

Read-only. No network, no writes, no curation. This is the production side of M1
(`docs/SCALE_ROADMAP.md`): the unit tests pin the properties, this reports the live numbers and
runs the two bars that can only be run against a real catalog.

## What it answers

**1. What actually binds the clustering corpus right now.** ``story_service._fetch`` bounds its
candidate set by a 6-day window AND by ``RWE_STORIES_MAX_SCAN`` rows, newest-first. The row cap has
been silent since it existed — ``search_feed_articles`` returns ``(rows, total)`` and ``_fetch``
discarded the total — so "is the window we are clustering the window we asked for?" has never been
answered on production. It is the first thing printed.

**2. The boundary is a no-op with nothing configured.** Byte-identical, on the bar this repo already
uses for a clustering-neutral change (`docs/PERFORMANCE.md`, the candidate-walk rewrite and the
``_merge_duplicates`` size bound): every id, title, coverage count, publisher count, blindspot side,
trust verdict and ORDERED member-URL list.

**3. Containment under load, WITH a control arm.** Synthetic Tier B rows — copied from real ones, so
they carry real headlines and would genuinely cluster — are injected into the window. The tiered arm
must leave the story set byte-identical. The **control arm** then runs the same injection with
tiering off, and must produce a DIFFERENT story set.

Without the control arm bar 3 is vacuous: rows that would never have clustered anyway prove nothing
about containment. `docs/PERFORMANCE.md` records exactly this trap from the merge-bound work — a
recall test that "looked exactly like the bound breaking recall" was exercising a switched-off code
path, and running the failure against the unmodified tree first is the only reason it did not become
an hour of debugging correct code.

    dc run --rm -T api python examples/audit_corpus_boundary.py --db "$RWE_DB_URL"

``--inject`` defaults to 40,000 rather than the roadmap's stated 100,000. The TIERED arm is cheap at
any size (injected rows are filtered before the build), but the CONTROL arm has to cluster them, and
the build is quadratic: 40k roughly doubles the corpus and settles the property, while 100k on a
2-vCPU box whose sustainable budget is 0.40 vCPU is a multi-minute build competing with the live
service. Pass ``--inject 100000`` when the box has headroom; ``--no-control`` skips the expensive
arm and says so in the verdict, because a bar that did not run is not a bar that passed.
"""

from __future__ import annotations

import argparse
import copy
import os
import time

import corpus
import story_service
import store as store_mod
from pagination import OffsetPagination

#: Host the synthetic rows are attributed to. A ``.example`` domain is reserved by RFC 2606 and
#: cannot collide with a real outlet, so the injection can never be confused for live coverage.
SYNTHETIC_HOST = "tier-b-probe.example"


def fingerprint(stories: list) -> list:
    """The byte-identical bar. Member ORDER is part of it because member order decides DSU union
    order, which decides group roots, which decides story ids — a comparison that sorted the
    members would pass on a build that had silently re-identified every story."""
    return [(s["id"], s["title"], len(s["coverage"]),
             len({c["publisher"] for c in s["coverage"]}),
             s["blindspotSide"], s["clusterTrust"],
             tuple(c["url"] for c in s["coverage"]))
            for s in stories]


def synth(rows: list, n: int) -> list:
    """``n`` synthetic Tier B rows, COPIED from real ones.

    Copying rather than generating is the whole point twice over: the row shape is correct by
    construction (no guessing which of the 24 keys ``feed_article_to_article`` reads), and the
    headlines are real ones that already cluster in this catalog — so the control arm is guaranteed
    to have something to find. A synthetic corpus that clusters differently from the real one has
    misled this repo's performance work three separate times."""
    out = []
    for i in range(n):
        src = rows[i % len(rows)]
        r = copy.deepcopy(src)
        url = f"https://{SYNTHETIC_HOST}/{i}"
        r["canonicalUrl"] = r["url"] = url
        r["publisher"] = "Tier B Probe"
        scored = dict(r.get("scored") or {})
        scored["outlet"] = "Tier B Probe"
        scored["article_id"] = url
        r["scored"] = scored
        out.append(r)
    return out


def _build(rows: list, *, entities=None, verdicts=None) -> "tuple[list, float]":
    """One build, timed. ``band_out`` is deliberately NOT passed: it is the sink the serving path
    flushes back to the store, and this instrument is read-only. Every arm gets the same inputs, so
    omitting it cannot separate them."""
    t0 = time.perf_counter()
    stories = story_service.build_stories(rows, entities=entities, event_verdicts=verdicts)
    return stories, (time.perf_counter() - t0) * 1000.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    ap.add_argument("--inject", type=int, default=40_000,
                    help="synthetic Tier B rows to inject (default %(default)s; the roadmap bar is "
                         "100000 — see the module docstring for why that is not the default)")
    ap.add_argument("--no-control", action="store_true",
                    help="skip the expensive control arm (the verdict will say the bar did not run)")
    args = ap.parse_args(argv)

    st = store_mod.Store(args.db)
    failures = []

    # ---------------------------------------------------------------- 1. what binds today
    date_from = story_service._window_start()
    cap = story_service.max_scan_default()
    raw, total = st.search_feed_articles(
        date_from=date_from, sort="newest",
        pagination=OffsetPagination.from_params(cap, 0, max_limit=cap))
    report = {}
    corpus.select(raw, total=total, cap=cap, window_start=date_from,
                  log=lambda *a, **k: None, report_out=report)

    print("=== what bounds the clustering corpus right now ===")
    print(f"  window matched      : {report['window']:,} articles")
    print(f"  scanned (after cap) : {report['scanned']:,}")
    print(f"  kept  (Tier A)      : {report['kept']:,}")
    print(f"  row cap             : {report['cap']:,}")
    print(f"  Tier A budget       : {report['budget']:,}   <- build stops fitting the poll cycle")
    print(f"  binding constraint  : {report['binding']}")
    print(f"  tiering configured  : {report['tiering']}")
    # A shorter effective window is only a TRUNCATION when the cap bound. Otherwise it just means
    # the catalog holds nothing older, which is a different fact entirely — and reading one as the
    # other is exactly the misdiagnosis this instrument exists to prevent.
    why = ("TRUNCATED by the row cap" if report["capBound"]
           else "the catalog holds nothing older — NOT a truncation")
    print(f"  requested window    : {report['requestedWindowHours']} h  (from {report['requestedFrom']})")
    print(f"  effective window    : {report['effectiveWindowHours']} h  <- {why}")
    if report["capBound"]:
        print(f"  *** CAP BOUND: {report['window'] - report['cap']:,} articles in the window were "
              f"never scanned. Story yield now tracks ingestion RATE — adding sources will produce "
              f"FEWER stories. This is the M2 trigger.")
        failures.append("the row cap is truncating the clustering window")
    else:
        head = report["budget"] - report["kept"]
        print(f"  headroom to budget  : {head:,} articles "
              f"({report['kept'] / max(1, report['budget']) * 100:.1f}% of Tier A budget used)")

    # ---------------------------------------------------------------- 2. no-op with nothing set
    rows = story_service._fetch(st)
    ents = story_service._entities_for(st, rows)
    verdicts, _band = story_service._event_inputs(st)
    base, base_ms = _build(rows, entities=ents, verdicts=verdicts)
    base_fp = fingerprint(base)

    print(f"\n=== BAR 1 — the boundary is a no-op with nothing configured ===")
    selected = corpus.select(raw, total=total, cap=cap, window_start=date_from,
                             log=lambda *a, **k: None)
    print(f"  rows in  : {len(raw):,}      rows out : {len(selected):,}")
    print(f"  identity : {'SAME LIST OBJECT' if selected is raw else 'A COPY'}")
    print(f"  stories  : {len(base):,}   build {base_ms:,.0f} ms")
    if selected is not raw:
        print("  *** FAIL: select() returned a new list while tiering is off. Off must cost nothing.")
        failures.append("select() copied the row list while switched off")
    else:
        print("  PASS")

    # ---------------------------------------------------------------- 3. containment + control
    injected = synth(rows, args.inject)
    print(f"\n=== BAR 2 — containment: {args.inject:,} synthetic Tier B rows ===")
    os.environ["RWE_CORPUS_TIER_B"] = SYNTHETIC_HOST
    try:
        kept = corpus.select(rows + injected, total=total + args.inject, cap=cap + args.inject,
                             window_start=date_from, log=lambda *a, **k: None)
        tiered, tiered_ms = _build(kept, entities=ents, verdicts=verdicts)
        tiered_fp = fingerprint(tiered)
    finally:
        os.environ.pop("RWE_CORPUS_TIER_B", None)

    print(f"  corpus after select : {len(kept):,}   (was {len(rows):,} + {args.inject:,} injected)")
    print(f"  stories             : {len(tiered):,}   build {tiered_ms:,.0f} ms "
          f"(baseline {base_ms:,.0f} ms)")
    print(f"  NOTE: do not read those two timings as a speed-up. Every arm runs in one process, so")
    print(f"  the later ones inherit warm registry memos. The COUNTS are what this bar asserts.")
    if tiered_fp == base_fp:
        print("  PASS — story set BYTE-IDENTICAL to the baseline")
    else:
        moved = sum(1 for a, b in zip(base_fp, tiered_fp) if a != b)
        print(f"  *** FAIL: {moved} stories differ, plus {abs(len(base_fp) - len(tiered_fp))} "
              f"added/removed. Tier B altered the partition, which it must never do.")
        failures.append("Tier B rows changed the Tier A story set")

    print(f"\n=== BAR 3 — the control arm: would those rows have changed anything? ===")
    if args.no_control:
        print("  SKIPPED (--no-control). Bar 2 is UNINTERPRETED without this: rows that would")
        print("  never have clustered prove nothing about containment.")
        failures.append("the control arm did not run, so bar 2 is uninterpreted")
    else:
        n_all = len(rows) + args.inject
        print(f"  clustering {n_all:,} articles with tiering OFF — the expensive arm, and the build")
        print(f"  is quadratic{'; expect minutes, not seconds' if n_all > 60_000 else ''}.")
        control, control_ms = _build(rows + injected, entities=ents, verdicts=verdicts)
        control_fp = fingerprint(control)
        print(f"  stories             : {len(control):,}   build {control_ms:,.0f} ms")
        if control_fp != base_fp:
            # Report WHAT moved, not just that something did. A story-count delta of 0 is common
            # and looks like nothing happened, when in fact every cluster grew members.
            common = sum(1 for a, b in zip(sorted(base_fp), sorted(control_fp)) if a != b)
            print(f"  PASS — admitting them DOES move the story set: "
                  f"{len(control) - len(base):+,} stories, {common:,} of "
                  f"{min(len(base_fp), len(control_fp)):,} compared entries differ. Bar 2 measured "
                  f"something real.")
        else:
            print("  *** FAIL: admitting the injected rows changed nothing, so bar 2 is vacuous.")
            failures.append("the injected rows do not cluster; bar 2 proves nothing")

    print("\n" + "=" * 70)
    if failures:
        print("VERDICT: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("VERDICT: PASS — the clustering corpus is a selected projection, the boundary contains")
    print("Tier B, and the control arm confirms the bar was not vacuous.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
