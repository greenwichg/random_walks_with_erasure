"""entity_span_backfill.py — one-shot entity-SPAN extraction over the clustering window (Stage 0.3).

The steady-state hook (``rss_ingest.ingest_entries`` under ``RWE_INGEST_ENTITY_SPANS=1``) covers
new rows going forward; this fills the rows already in the window, so the counterfactual can be
measured today rather than six days from now. Reads the same window ``build_stories`` clusters
(``story_service._fetch``), runs ``entity_spans.extract`` over each row's headline and dek, and
writes ``span``-kind rows under the extractor's own source — per-source replace, so re-running is
harmless and the provider's GKG rows are never touched.

    python examples/entity_span_backfill.py                 # write, and print coverage before/after
    python examples/entity_span_backfill.py --dry-run       # extract and count, write nothing
    python examples/entity_span_backfill.py --show 12       # print a sample of what was extracted

Prints THE number the Stage 0.3 bar is written against — the share of window articles carrying
any entity row, provider-only versus provider-plus-spans, overall and for English — because
"entity coverage rises from 24% toward 70%+ on English" is the pre-registered claim, and a
coverage number the build cannot see is not coverage.

Production-data neutrality, same contract as ``gdelt_entity_backfill``: this writes ONLY
``article_entities`` rows of kind ``span``. Nothing in the serving path reads them until
``RWE_STORY_ENTITY_SPANS=1``; with that off, every build after this run is byte-identical to
every build before it.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import entity_spans              # noqa: E402
import story_service             # noqa: E402
import store as store_mod        # noqa: E402

PROVIDER = story_service.ENTITY_KINDS_PROVIDER
WITH_SPANS = PROVIDER + (entity_spans.KIND,)


def run(store_, *, dry_run: bool = False, show: int = 0, limit: int = 0,
        rows: "list | None" = None) -> dict:
    """Extract over the window rows and (unless ``dry_run``) persist. Returns the coverage
    facts; ``rows`` may be injected for tests, else the clustering window is fetched."""
    rows = story_service._fetch(store_) if rows is None else rows
    if limit:
        rows = rows[:limit]
    urls = [r.get("canonicalUrl") for r in rows]
    before = store_.count_entity_covered(urls, kinds=PROVIDER)
    written = articles = 0
    langs: Counter = Counter()
    per_lang_spans: Counter = Counter()
    sample: list = []
    names_total = 0
    for r in rows:
        lang = ((r.get("language") or "?").strip().lower()[:2]) or "?"
        langs[lang] += 1
        names = entity_spans.extract(r.get("title") or "", r.get("description") or "",
                                     language=r.get("language"))
        if not names:
            continue
        articles += 1
        names_total += len(names)
        per_lang_spans[lang] += 1
        if len(sample) < show:
            sample.append((r.get("title") or "", names[:6]))
        if not dry_run and r.get("canonicalUrl"):
            written += store_.replace_article_entities(
                r["canonicalUrl"], {entity_spans.KIND: names}, source=entity_spans.SOURCE)
    after = store_.count_entity_covered(urls, kinds=WITH_SPANS) if not dry_run else None
    en = langs.get("en", 0)
    return {
        "window": len(rows), "coveredBefore": before, "coveredAfter": after,
        "articlesWithSpans": articles, "rowsWritten": written, "namesTotal": names_total,
        "english": en, "englishWithSpans": per_lang_spans.get("en", 0),
        "byLanguage": {k: (langs[k], per_lang_spans.get(k, 0))
                       for k in sorted(langs, key=lambda k: -langs[k])[:12]},
        "sample": sample, "dryRun": dry_run,
    }


def render(res: dict) -> str:
    w = max(1, res["window"])
    lines = [f"window articles       : {res['window']:,}",
             f"provider-covered      : {res['coveredBefore']:,} ({res['coveredBefore'] / w:.1%})"
             "   [person/org rows — the 24% the bar starts from]",
             f"articles with spans   : {res['articlesWithSpans']:,} ({res['articlesWithSpans'] / w:.1%}),"
             f" {res['namesTotal']:,} names"
             + ("   [DRY RUN — nothing written]" if res["dryRun"] else
                f", {res['rowsWritten']:,} rows written")]
    if res["coveredAfter"] is not None:
        lines.append(f"covered with spans    : {res['coveredAfter']:,} ({res['coveredAfter'] / w:.1%})"
                     "   [any entity row, provider or span — what the build can now see]")
    en = max(1, res["english"])
    lines.append(f"English               : {res['englishWithSpans']:,} of {res['english']:,} carry spans "
                 f"({res['englishWithSpans'] / en:.1%})   [the bar: toward 70%+]")
    lines.append(f"\n  {'lang':>6} {'articles':>9} {'with spans':>11} {'share':>7}")
    for lang, (n, s) in res["byLanguage"].items():
        lines.append(f"  {lang:>6} {n:>9,} {s:>11,} {s / max(1, n):>7.1%}")
    if res["sample"]:
        lines.append("\nsample extractions (headline -> names)")
        for title, names in res["sample"]:
            lines.append(f"  {title[:70]}")
            lines.append(f"      -> {', '.join(names)}")
    lines.append("\nNothing in the serving path reads span rows until RWE_STORY_ENTITY_SPANS=1; "
                 "measure first:\n  dc run --rm -T api python examples/audit_clustering_change.py "
                 "--entity-spans --pieces 8")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--dry-run", action="store_true", help="extract and count, write nothing")
    ap.add_argument("--show", type=int, default=0, help="print N sample extractions")
    ap.add_argument("--limit", type=int, default=0, help="debug: first N window rows only")
    args = ap.parse_args(argv)
    res = run(store_mod.Store(args.db), dry_run=args.dry_run, show=args.show, limit=args.limit)
    print(render(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
