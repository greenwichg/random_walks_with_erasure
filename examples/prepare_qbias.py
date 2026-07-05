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
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # sibling examples
from outlet_registry import OutletRegistry, default_registry

# Qbias's outlet column, by the same candidate names simulate_users.catalog_from_qbias /
# validate_qbias look for — so we canonicalise the exact column the corpus builder will read.
_OUTLET_COLS = ("source", "outlet", "news_outlet", "publisher", "source_name", "media")

csv.field_size_limit(10_000_000)   # Qbias rows carry full article text


def pick_outlet_column(fieldnames) -> Optional[str]:
    """The outlet column name present in ``fieldnames`` (case-insensitive), or ``None``."""
    lower = {(c or "").lower(): c for c in (fieldnames or [])}
    for cand in _OUTLET_COLS:
        if cand in lower:
            return lower[cand]
    return None


@dataclass
class CanonReport:
    """Canonicalisation statistics — also what ``--report`` prints and the verification uses."""
    articles_total: int = 0
    articles_matched: int = 0
    canonical_outlets: List[str] = field(default_factory=list)   # distinct canonical names produced
    sources_matched: List[str] = field(default_factory=list)     # distinct raw sources that resolved
    unmatched: "Counter[str]" = field(default_factory=Counter)   # raw source -> article count

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


def prepare(in_path: str, out_path: Optional[str] = None,
            registry: Optional[OutletRegistry] = None) -> CanonReport:
    """Read the raw Qbias CSV, canonicalise its outlet column, optionally write the cleaned CSV
    (same schema), and return the report. Writing is skipped when ``out_path`` is ``None``."""
    with open(in_path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        outlet_col = pick_outlet_column(fieldnames)
        if not outlet_col:
            raise SystemExit(f"no outlet column in {in_path} (looked for {_OUTLET_COLS})")
        rows, report = canonicalize_rows(reader, outlet_col, registry)

    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="in_path", required=True, help="raw Qbias CSV")
    ap.add_argument("--out", dest="out_path", default=None,
                    help="cleaned CSV to write (omit / use --report to only analyse)")
    ap.add_argument("--report", action="store_true", help="analyse + print stats, write nothing")
    ap.add_argument("--registry", default=None, help="outlet registry CSV (defaults to bundled)")
    args = ap.parse_args()

    registry = OutletRegistry.load(args.registry) if args.registry else default_registry()
    out_path = None if args.report else args.out_path
    report = prepare(args.in_path, out_path, registry)
    print(report.summary())
    if out_path:
        print(f"\nwrote cleaned dataset -> {out_path}")


if __name__ == "__main__":
    main()
