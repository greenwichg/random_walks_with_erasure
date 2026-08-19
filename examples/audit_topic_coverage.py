#!/usr/bin/env python3
"""Why are some articles uncategorized? — a read-only census of `classify_topic` misses.

`ingest.classify_topic` returns "" when it cannot place an article in the taxonomy. That is a
deliberate answer, not a failure (a guessed topic is worse than an admitted unknown), and the
report now drops those rows rather than drawing a nameless bar. But a reader whose Reading
distribution shows 10% uncategorized is telling us something about the classifier, and the only
honest way to know what is to look at the articles it gave up on.

This instrument answers four questions, in order:

  1. HOW MANY — of the catalog, and of one reader's actual reads (the two differ: a reader does not
     read a uniform sample of the catalog).
  2. WHERE IT STOPPED — the classifier resolves in four stages. For each miss, which stages had no
     input at all versus had input and produced no hit. "No title" and "a title the lexicon does
     not cover" are different problems with different fixes.
  3. WHO — the publishers and URL shapes that dominate the misses. A single publisher whose feed
     omits categories is a different story from a long tail.
  4. WHAT WOULD RESCUE THEM — the counterfactual. Reads persist only url + title (`ScoredRead`
     keeps no description), while the CATALOG keeps description and body for the same URL. So:
     how many misses would classify if the classifier were re-run with the text we already store?
     That number is the difference between a data-plumbing fix and a lexicon gap.

Read-only: opens the store, reads rows, writes nothing.

    dc exec -T api python examples/audit_topic_coverage.py --engine-user 1
    dc exec -T api python examples/audit_topic_coverage.py --catalog 5000 --samples 25
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ingest                     # noqa: E402
import store as store_mod         # noqa: E402


def _stage_report(url: str, source_category: str, title: str, description: str) -> str:
    """Which stage of `classify_topic` would have fired, or the first one that had nothing to work
    with. Mirrors the classifier's own order — it does not re-implement the decision, it re-runs the
    same helpers and names the step."""
    for label in ingest._category_labels(source_category or ""):
        if ingest._CATEGORY_ALIASES.get(label):
            return "1-source-category-alias"
    if ingest._lexicon_topic(source_category or ""):
        return "2-source-category-lexicon"
    path = urlsplit(url).path.lower() if url else ""
    section = ingest._topic_from_path(path)
    if section and section not in ingest._GEO_TOPICS:
        return "3-url-section"
    if ingest._lexicon_topic(title or ""):
        return "4-title-lexicon"
    if ingest._lexicon_topic(description or ""):
        return "5-description-lexicon"
    if section:
        return "6-geographic-section"
    # Nothing hit. Say WHY there was nothing to hit — the actionable half.
    missing = []
    if not (source_category or "").strip():
        missing.append("no-source-category")
    if not path.strip("/"):
        missing.append("no-url-path")
    if not (title or "").strip():
        missing.append("no-title")
    if not (description or "").strip():
        missing.append("no-description")
    return "MISS(" + ",".join(missing) + ")" if missing else "MISS(all-inputs-present)"


def _first_segment(url: str) -> str:
    parts = [p for p in urlsplit(url or "").path.split("/") if p]
    return parts[0] if parts else "(root)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--engine-user", type=int, default=0,
                    help="census this reader's own reads as well as the catalog")
    ap.add_argument("--catalog", type=int, default=20000,
                    help="how many catalog articles to scan (most recent first)")
    ap.add_argument("--samples", type=int, default=12,
                    help="example titles to print per section")
    args = ap.parse_args(argv)

    st = store_mod.Store(None)

    # -- 1. the catalog ---------------------------------------------------------------------- #
    rows = st.list_feed_articles(limit=args.catalog)
    total = len(rows)
    misses = [r for r in rows if not str(((r.get("scored") or {}).get("category")) or "").strip()]
    print(f"-- 1. catalog ({total} articles scanned) --")
    print(f"  uncategorized: {len(misses)} ({len(misses) / max(1, total):.1%})")

    # -- 2. where the classifier stopped ----------------------------------------------------- #
    # Re-run on the text the CATALOG holds, which is the most signal that ever existed for these.
    stages = Counter()
    for r in misses:
        stages[_stage_report(r.get("url") or r.get("canonicalUrl") or "", "",
                             r.get("title") or "", r.get("description") or "")] += 1
    print(f"\n-- 2. where it stopped (catalog misses) --")
    for stage, n in stages.most_common():
        print(f"  {n:>6}  {stage}")
    print("  A stage name here means the classifier WOULD hit it now — the article was stored")
    print("  before that text existed, or with a thinner payload. MISS(...) names what was absent.")

    # -- 3. who ------------------------------------------------------------------------------ #
    print(f"\n-- 3. publishers with the most uncategorized (of {len(misses)}) --")
    pubs = Counter((r.get("publisher") or "(none)") for r in misses)
    all_pubs = Counter((r.get("publisher") or "(none)") for r in rows)
    for pub, n in pubs.most_common(12):
        share = n / max(1, all_pubs[pub])
        print(f"  {n:>5} / {all_pubs[pub]:<5} ({share:5.1%} of its articles)  {pub}")
    print("\n-- 3b. URL first segment --")
    for seg, n in Counter(_first_segment(r.get("url") or "") for r in misses).most_common(10):
        print(f"  {n:>5}  /{seg}/")

    # -- 4. the counterfactual --------------------------------------------------------------- #
    # What the CURRENT classifier makes of the text we already store. A rescue here is a plumbing
    # fix (re-score what we have); a miss is a genuine lexicon/taxonomy gap.
    rescued = Counter()
    still = 0
    for r in misses:
        got = ingest.classify_topic(url=r.get("url") or "", source_category="",
                                    title=r.get("title") or "",
                                    description=f"{r.get('description') or ''} "
                                                f"{(r.get('body') or '')[:400]}".strip())
        if got:
            rescued[got] += 1
        else:
            still += 1
    n_res = sum(rescued.values())
    print(f"\n-- 4. re-running the classifier on the stored text --")
    print(f"  would now classify : {n_res} of {len(misses)} ({n_res / max(1, len(misses)):.1%})")
    for topic, n in rescued.most_common():
        print(f"      {n:>5}  {topic}")
    print(f"  still uncategorized: {still}")
    print("  Rescued = the signal is already stored and only the SCORE is stale (a re-score fixes")
    print("  it). Still = the classifier genuinely cannot place this text: a lexicon/taxonomy gap.")

    # -- 5. samples -------------------------------------------------------------------------- #
    print(f"\n-- 5. examples still uncategorized --")
    shown = 0
    for r in misses:
        if shown >= args.samples:
            break
        if ingest.classify_topic(url=r.get("url") or "", source_category="",
                                 title=r.get("title") or "",
                                 description=r.get("description") or ""):
            continue
        shown += 1
        print(f"  [{r.get('publisher') or '?'}] {(r.get('title') or '(no title)')[:96]}")
        print(f"      {(r.get('url') or '')[:110]}")

    # -- 6. one reader ----------------------------------------------------------------------- #
    if args.engine_user:
        reads = st.list_reads(args.engine_user) or []
        r_miss = [r for r in reads
                  if not str(((r.get("scored") or {}).get("category")) or "").strip()]
        print(f"\n-- 6. engine user {args.engine_user}: {len(reads)} reads --")
        print(f"  uncategorized: {len(r_miss)} ({len(r_miss) / max(1, len(reads)):.1%})")
        # A read stores no description; the catalog may hold one for the same URL. That gap is
        # itself a finding — the reader's own report is scored from the thinner copy.
        recoverable = 0
        for r in r_miss:
            art = st.get_feed_article(r.get("canonicalUrl") or "")
            if not art:
                continue
            if ingest.classify_topic(url=art.get("url") or "", source_category="",
                                     title=art.get("title") or "",
                                     description=art.get("description") or ""):
                recoverable += 1
        print(f"  of those, the CATALOG has enough text to classify: {recoverable}")
        print(f"  (a read persists url + title only — ScoredRead keeps no description — so a read")
        print(f"   can be uncategorized while the same article in the catalog is not)")
        for r in r_miss[:args.samples]:
            sc = r.get("scored") or {}
            print(f"  [{sc.get('outlet') or '?'}] {(sc.get('title') or '(no title)')[:96]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
