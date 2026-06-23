"""Ingest a MIND release into an RWE-ready ``.npz`` (click matrix + ideology).

Download MIND (start with *MINDsmall*) from https://msnews.github.io/ and unzip so
that ``<mind-dir>`` contains ``news.tsv`` and ``behaviors.tsv``.

Examples
--------
Click matrix + political mask only (works from MIND alone)::

    python examples/ingest_mind.py --mind-dir data/MINDsmall_train --out mind.npz

Add ideological positions via an outlet-lean join (MIND lacks the publisher, so
supply a news-id -> outlet map; lean defaults to the bundled illustrative table)::

    python examples/ingest_mind.py --mind-dir data/MINDsmall_train \
        --source-map data/news_source.tsv --lean-csv allsides.csv \
        --political-only --min-user-clicks 5 --min-item-clicks 5 --out mind_pol.npz

Then downstream::

    from rwe import FeedbackGraph, RWEB
    from rwe.mind import MINDData
    d = MINDData.load("mind_pol.npz")
    g = FeedbackGraph(d.dataset.matrix)
    recs = RWEB(g, d.user_positions_from_clicks(fill=0.0), d.item_positions,
                epsilon=0.9).recommend(range(d.n_users), top_k=10)
"""

import argparse
import json

from rwe.mind import load_mind, load_lean_table


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mind-dir", required=True,
                    help="directory containing news.tsv and behaviors.tsv")
    ap.add_argument("--out", default="mind.npz", help="output .npz path")
    ap.add_argument("--source-map", default=None,
                    help="news_id->outlet map (dict file) for the outlet-lean join")
    ap.add_argument("--lean-csv", default=None,
                    help="outlet,lean table (int -2..2 or L..R label); "
                         "defaults to the bundled illustrative table")
    ap.add_argument("--no-impressions", action="store_true",
                    help="use history only (ignore positive impression clicks)")
    ap.add_argument("--min-user-clicks", type=int, default=1)
    ap.add_argument("--min-item-clicks", type=int, default=1)
    ap.add_argument("--political-only", action="store_true",
                    help="restrict the saved data to political items with a known lean")
    args = ap.parse_args()

    lean = load_lean_table(args.lean_csv) if args.lean_csv else None
    d = load_mind(args.mind_dir, source_map=args.source_map, lean=lean,
                  include_impressions=not args.no_impressions,
                  min_user_clicks=args.min_user_clicks,
                  min_item_clicks=args.min_item_clicks)

    print("Ingested MIND:")
    print(json.dumps(d.summary(), indent=2))
    if d.summary()["items_with_lean"] == 0:
        print("\n[note] No item leans resolved. MIND URLs are MSN URLs with no "
              "publisher; pass --source-map (news_id->outlet) to enable the join, "
              "or fit rwe.IdeologyModel on the click graph instead.")

    if args.political_only:
        d = d.political_subset(require_lean=True)
        print("\nAfter political_subset(require_lean=True):")
        print(json.dumps(d.summary(), indent=2))

    d.save(args.out)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
