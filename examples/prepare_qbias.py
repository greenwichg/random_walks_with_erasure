"""prepare_qbias.py — canonicalize the raw Qbias dataset for the Qbias reference profile.

Qbias labels outlets in its own vocabulary — ``"Fox News (Online News)"``, ``"New York Times
(News)"``, ``"USA TODAY"`` — which do **not** match the canonical outlet names the ingestion
scorer emits for a reader's URLs (``"Fox News"``, ``"New York Times"``, ``"USA Today"``). Left
unaligned, a reader's Fox News reads would never line up with the population's Fox News articles,
and onboarding would show the messy raw labels.

This script rewrites Qbias's outlet column through the **same** :mod:`outlet_registry` the scorer
uses, so the reference corpus and real reads share one outlet vocabulary. It is a PRODUCT-LAYER
preprocessing step that touches no research module: the cleaned CSV keeps Qbias's exact schema,
so ``simulate_users.catalog_from_qbias`` consumes it unchanged — only the outlet-column *values*
change (canonical where the registry knows the outlet, left as-is otherwise, so no article is
dropped).

    # download the raw dataset first (see docs/QBIAS_MIGRATION.md), then:
    python examples/prepare_qbias.py --in allsides_balanced_news_headlines-texts.csv \
                                     --out data/qbias_clean.csv
    python examples/prepare_qbias.py --in raw.csv --report        # analyse only, write nothing
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # sibling examples
from outlet_registry import OutletRegistry, default_registry
from enrich import BaselineEnricher, LABELS

# Qbias's outlet + headline columns, by the same candidate names simulate_users.catalog_from_qbias
# / validate_qbias look for — so we touch the exact columns the corpus builder will read.
_OUTLET_COLS = ("source", "outlet", "news_outlet", "publisher", "source_name", "media")
_HEADLINE_COLS = ("heading", "headline", "title")

csv.field_size_limit(10_000_000)   # Qbias rows carry full article text


def _pick_column(fieldnames, candidates) -> Optional[str]:
    lower = {(c or "").lower(): c for c in (fieldnames or [])}
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    return None


def pick_outlet_column(fieldnames) -> Optional[str]:
    """The outlet column name present in ``fieldnames`` (case-insensitive), or ``None``."""
    return _pick_column(fieldnames, _OUTLET_COLS)


@dataclass
class CanonReport:
    """Canonicalisation statistics — also what ``--report`` prints and the verification uses."""
    articles_total: int = 0
    articles_matched: int = 0
    canonical_outlets: List[str] = field(default_factory=list)   # distinct canonical names produced
    sources_matched: List[str] = field(default_factory=list)     # distinct raw sources that resolved
    unmatched: "Counter[str]" = field(default_factory=Counter)   # raw source -> article count
    enriched_articles: int = 0                                    # headlines baseline-enriched
    enrich_seconds: float = 0.0
    register_path: Optional[str] = None
    emotion_path: Optional[str] = None

    @property
    def pct_articles_canonicalized(self) -> float:
        return 100.0 * self.articles_matched / self.articles_total if self.articles_total else 0.0

    def summary(self, top_unmatched: int = 15) -> str:
        lines = [
            "Qbias canonicalization report",
            "=" * 30,
            f"canonical outlets produced : {len(self.canonical_outlets)}",
            f"Qbias sources matched      : {len(self.sources_matched)}",
            f"articles canonicalized     : {self.articles_matched}/{self.articles_total} "
            f"({self.pct_articles_canonicalized:.1f}%)",
            f"unmatched sources          : {len(self.unmatched)}",
        ]
        if self.enriched_articles:
            lines.append(f"headlines enriched         : {self.enriched_articles} "
                         f"(register+emotion, baseline) in {self.enrich_seconds:.1f}s")
        if self.unmatched:
            lines.append(f"\ntop unmatched (kept as-is, by article count):")
            for name, n in self.unmatched.most_common(top_unmatched):
                lines.append(f"  {n:6d}  {name}")
        return "\n".join(lines)


def canonicalize_rows(rows, outlet_col: str,
                      registry: Optional[OutletRegistry] = None) -> Tuple[List[dict], CanonReport]:
    """Rewrite ``outlet_col`` in each row to its canonical outlet name (registry), leaving rows
    whose outlet the registry doesn't know untouched. Returns the rewritten rows + a report."""
    reg = registry or default_registry()
    rep = CanonReport()
    canon_set, matched_sources = set(), set()
    out_rows: List[dict] = []
    for row in rows:
        raw = (row.get(outlet_col) or "").strip()
        canonical = reg.canonical(raw) if raw else None
        rep.articles_total += 1
        if canonical is not None:
            rep.articles_matched += 1
            canon_set.add(canonical)
            matched_sources.add(raw)
            new = dict(row)
            new[outlet_col] = canonical
            out_rows.append(new)
        else:
            if raw:
                rep.unmatched[raw] += 1
            out_rows.append(dict(row))
    rep.canonical_outlets = sorted(canon_set)
    rep.sources_matched = sorted(matched_sources)
    return out_rows, rep


def write_enrichment(rows, headline_col: str, register_path: str, emotion_path: str) -> Tuple[int, float]:
    """Baseline-enrich each article's headline — the SAME ``BaselineEnricher`` ingested reads use
    — and write register + emotion sidecars in the exact format ``health_report._load_item_csv``
    reads, keyed by ``Q{i}`` (``i`` = row order, the id ``catalog_from_qbias`` assigns). So the
    population and real reads carry identical register/emotion semantics. Rows without a headline
    are omitted (left n/a, as the engine handles missing data). Returns (count, seconds)."""
    be = BaselineEnricher()
    t0 = time.time()
    n = 0
    with open(register_path, "w", encoding="utf-8") as fr, \
            open(emotion_path, "w", encoding="utf-8") as fe:
        fr.write("news_id,reporting\n")
        fe.write("news_id," + ",".join(LABELS) + "\n")
        for i, row in enumerate(rows):
            text = (row.get(headline_col) or "").strip()
            if not text:
                continue
            emo = be.emotion(text)
            fr.write(f"Q{i},{be.register(text):.4f}\n")
            fe.write(f"Q{i}," + ",".join(f"{emo[l]:.4f}" for l in LABELS) + "\n")
            n += 1
    return n, time.time() - t0


def prepare(in_path: str, out_path: Optional[str] = None, registry: Optional[OutletRegistry] = None,
            enrich: bool = True, register_out: Optional[str] = None,
            emotion_out: Optional[str] = None) -> CanonReport:
    """Read the raw Qbias CSV, canonicalise its outlet column, and (when ``out_path`` is given)
    write the cleaned CSV plus, unless ``enrich`` is False, baseline register/emotion sidecars.
    The cleaned CSV keeps Qbias's schema; the sidecars align to it by row order. Writing is
    skipped when ``out_path`` is ``None`` (report-only mode)."""
    with open(in_path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        outlet_col = pick_outlet_column(fieldnames)
        if not outlet_col:
            raise SystemExit(f"no outlet column in {in_path} (looked for {_OUTLET_COLS})")
        rows, report = canonicalize_rows(reader, outlet_col, registry)

    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        if enrich:
            headline_col = _pick_column(fieldnames, _HEADLINE_COLS)
            if not headline_col:
                raise SystemExit(f"no headline column for enrichment (looked for {_HEADLINE_COLS})")
            base = os.path.splitext(out_path)[0]
            report.register_path = register_out or base + ".register.csv"
            report.emotion_path = emotion_out or base + ".emotion.csv"
            report.enriched_articles, report.enrich_seconds = write_enrichment(
                rows, headline_col, report.register_path, report.emotion_path)
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="in_path", required=True, help="raw Qbias CSV")
    ap.add_argument("--out", dest="out_path", default=None,
                    help="cleaned CSV to write (omit / use --report to only analyse)")
    ap.add_argument("--report", action="store_true", help="analyse + print stats, write nothing")
    ap.add_argument("--registry", default=None, help="outlet registry CSV (defaults to bundled)")
    ap.add_argument("--no-enrich", action="store_true",
                    help="skip the baseline register/emotion sidecars (outlet canonicalization only)")
    ap.add_argument("--register-out", default=None, help="register sidecar path (default: <out>.register.csv)")
    ap.add_argument("--emotion-out", default=None, help="emotion sidecar path (default: <out>.emotion.csv)")
    args = ap.parse_args()

    registry = OutletRegistry.load(args.registry) if args.registry else default_registry()
    out_path = None if args.report else args.out_path
    report = prepare(args.in_path, out_path, registry, enrich=not args.no_enrich,
                     register_out=args.register_out, emotion_out=args.emotion_out)
    print(report.summary())
    if out_path:
        print(f"\nwrote cleaned dataset -> {out_path}")
        if report.register_path:
            print(f"wrote enrichment sidecars -> {report.register_path}, {report.emotion_path}")


if __name__ == "__main__":
    main()
