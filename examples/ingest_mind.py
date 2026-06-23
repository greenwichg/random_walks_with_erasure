"""Ingest a MIND release into an RWE-ready ``.npz`` (click matrix + ideology).

Download MIND (start with *MINDsmall*) from https://msnews.github.io/ and unzip so
that ``<mind-dir>`` contains ``news.tsv`` and ``behaviors.tsv``.

Examples
--------
Click matrix + political mask only (works from MIND alone)::

    python examples/ingest_mind.py --mind-dir data/MINDsmall_train --out mind.npz

Option 1 -- outlet-lean join (MIND lacks the publisher, so supply a news-id ->
outlet map; lean defaults to the bundled illustrative table)::

    python examples/ingest_mind.py --mind-dir data/MINDsmall_train \
        --source-map data/news_source.tsv --lean-csv allsides.csv \
        --political-only --min-user-clicks 5 --min-item-clicks 5 --out mind_pol.npz

Option 2 -- position purely from click behaviour, no outlet map needed
(``--ideology`` fits rwe.IdeologyModel and stores BOTH user and item positions)::

    python examples/ingest_mind.py --mind-dir data/MINDsmall_train \
        --political-only --ideology --min-user-clicks 5 --min-item-clicks 5 \
        --out mind_ideo.npz

Then downstream::

    from rwe import FeedbackGraph, RWEB
    from rwe.mind import MINDData
    d = MINDData.load("mind_ideo.npz")
    g = FeedbackGraph(d.dataset.matrix)
    theta = d.user_positions if d.user_positions is not None \
        else d.user_positions_from_clicks(fill=0.0)        # ideology vs lean-mean proxy
    recs = RWEB(g, theta, d.item_positions, epsilon=0.9).recommend(range(d.n_users), top_k=10)
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
    ap.add_argument("--positions-csv", default=None,
                    help="news_id,position file that sets item_positions directly "
                         "(e.g. from examples/classify_lean.py); overrides outlet lean")
    ap.add_argument("--no-impressions", action="store_true",
                    help="use history only (ignore positive impression clicks)")
    ap.add_argument("--min-user-clicks", type=int, default=1)
    ap.add_argument("--min-item-clicks", type=int, default=1)
    ap.add_argument("--political-only", action="store_true",
                    help="restrict the saved data to political items "
                         "(with a known lean, unless --ideology is set)")
    ap.add_argument("--ideology", action="store_true",
                    help="estimate user+item positions from click behaviour via "
                         "IdeologyModel -- no outlet source map needed")
    ap.add_argument("--ideology-iters", type=int, default=300,
                    help="gradient-ascent sweeps for --ideology (default 300)")
    ap.add_argument("--sample-users", type=int, default=None,
                    help="randomly keep this many users (caps the dense --ideology fit)")
    ap.add_argument("--max-cells", type=float, default=5e7,
                    help="max users*items allowed for the dense --ideology fit (default 5e7)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    lean = load_lean_table(args.lean_csv) if args.lean_csv else None
    d = load_mind(args.mind_dir, source_map=args.source_map, lean=lean,
                  positions_map=args.positions_csv,
                  include_impressions=not args.no_impressions,
                  min_user_clicks=args.min_user_clicks,
                  min_item_clicks=args.min_item_clicks)
    if args.sample_users:
        d = d.sample_users(args.sample_users, seed=args.seed)

    print("Ingested MIND:")
    print(json.dumps(d.summary(), indent=2))
    if d.summary()["items_with_lean"] == 0 and not args.ideology:
        print("\n[note] No item leans resolved. MIND URLs are MSN URLs with no "
              "publisher; pass --source-map (news_id->outlet) to enable the join, "
              "or pass --ideology to position from click behaviour instead.")

    if args.political_only:
        d = d.political_subset(require_lean=not args.ideology)
        print(f"\nAfter political_subset(require_lean={not args.ideology}):")
        print(json.dumps(d.summary(), indent=2))

    if args.ideology:
        fit = d.fit_ideology(n_iter=args.ideology_iters, seed=args.seed,
                             max_cells=args.max_cells)
        d = d.with_ideology(fit)
        print("\nFit ideology from click behaviour (standardised latent scale): "
              f"items={d.n_items}, users={d.n_users}, lean_corr={fit.lean_corr}")

    d.save(args.out)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
