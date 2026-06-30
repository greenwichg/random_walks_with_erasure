"""Build the ``news_id -> outlet`` source-map that ``ingest_mind.py --source-map``
needs, from any catalog that carries publishers.

MIND ships MSN aggregator URLs with **no publisher field**, so its outlet-lean join is
blocked (``docs/RESULTS.md`` Limitation: outlet-lean). The join works the moment you point
it at a catalog that carries **multiple named publishers**, but those are genuinely scarce:
the public *click* datasets are either single-publisher (EB-NeRD = Ekstra Bladet only,
Adressa = Adresseavisen only -> no lean variation) or hide the provider (MIND's MSN URLs).
The realistic unblock for MIND is a **resolved MSN-provider table** (MIND article ->
original publisher; see ``docs/TODO.md``). This tool converts *any* catalog that does carry
named publishers (TSV/CSV/parquet) into the 2-column ``news_id<TAB>outlet`` map the ingest
consumes -- the machinery is ready; only the multi-publisher catalog is missing::

    python examples/build_source_map.py --catalog articles.parquet \\
        --id-col article_id --source-col publisher --out source_map.tsv

    python examples/ingest_mind.py --mind-dir MINDsmall_train \\
        --source-map source_map.tsv --lean-csv examples/data/outlet_lean.csv \\
        --political-only --out mind_outlet.npz

The bundled ``examples/data/outlet_lean.csv`` (AllSides-style, editable) is the default
outlet->lean table; swap in an AllSides/MBFC export to update it.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def read_catalog(path, id_col: str, source_col: str):
    """Yield ``(news_id, outlet)`` from a TSV/CSV/parquet catalog."""
    p = Path(path)
    if p.suffix == ".parquet":
        import pandas as pd                       # optional dep, only for parquet
        df = pd.read_parquet(p, columns=[id_col, source_col])
        return [(str(a), str(b)) for a, b in zip(df[id_col], df[source_col])]
    with open(p, newline="", encoding="utf-8") as f:
        head = f.readline()
        f.seek(0)
        delim = "\t" if (p.suffix == ".tsv" or head.count("\t") >= head.count(",")) else ","
        rd = csv.DictReader(f, delimiter=delim)
        if id_col not in (rd.fieldnames or []) or source_col not in (rd.fieldnames or []):
            raise SystemExit(
                f"columns {id_col!r}/{source_col!r} not found; header is {rd.fieldnames}")
        return [(row[id_col].strip(), (row.get(source_col) or "").strip()) for row in rd]


def write_source_map(rows, out: str) -> int:
    """Write ``news_id<TAB>outlet`` rows (skipping missing id/outlet); return count."""
    n = 0
    with open(out, "w", encoding="utf-8") as f:
        for nid, outlet in rows:
            if nid and outlet and outlet.lower() not in ("none", "nan", ""):
                f.write(f"{nid}\t{outlet}\n")
                n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", required=True,
                    help="news catalog with a publisher column (.tsv/.csv/.parquet)")
    ap.add_argument("--id-col", default="news_id", help="article-id column name")
    ap.add_argument("--source-col", default="publisher",
                    help="publisher/outlet column name (must vary across rows -- a "
                         "single-publisher catalog gives no lean signal)")
    ap.add_argument("--out", default="source_map.tsv")
    args = ap.parse_args()

    rows = read_catalog(args.catalog, args.id_col, args.source_col)
    n = write_source_map(rows, args.out)
    print(f"wrote {args.out}: {n} news_id->outlet rows "
          f"({len(rows) - n} skipped for missing id/publisher)")
    print(f"next: python examples/ingest_mind.py --source-map {args.out} "
          "--lean-csv examples/data/outlet_lean.csv --political-only")


if __name__ == "__main__":
    main()
