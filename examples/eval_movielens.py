"""Long-tail (RQ2) replication of RWE on **MovieLens-1M** — a 2nd public dataset.

MovieLens has no ideological axis, so only the accuracy + long-tail-diversity half
(RQ2) transfers; this is the cross-dataset check that RWE-D's long-tail win is not
MIND-specific (it answers the "single dataset" limitation in docs/RESULTS.md).

Get the data (not redistributed here): download ``ml-1m.zip`` from
<https://files.grouplens.org/datasets/movielens/ml-1m.zip>, unzip, and point
``--ratings`` at ``ratings.dat``.

    python examples/eval_movielens.py --ratings ml-1m/ratings.dat --seeds 5
"""

import argparse

import numpy as np
import pandas as pd

from rwe import FeedbackGraph, P3, RP3Beta, RWED, ItemKNN, data, experiment


def _recommenders(g, args) -> dict:
    return {"ItemKNN": ItemKNN(g, k_neighbors=args.itemknn_k),
            "P3": P3(g),
            "RP3-beta": RP3Beta(g, beta=args.rp3_beta),
            "RWE-D": RWED(g, beta=args.rwed_beta, v=args.rwed_v)}


def run(dataset, args) -> tuple:
    """Multi-seed RQ2 comparison; returns ``(mean, std)`` DataFrames."""
    k, dk = args.top_k, args.diversity_k
    want = ["auc", f"hit_rate@{k}", f"ndcg@{k}", "mean_rank", f"gini_div@{dk}",
            f"coverage@{dk}", f"avg_deg@{dk}", f"surprisal@{dk}", f"personalization@{dk}"]
    tables = []
    for sd in range(args.seeds):
        train, test_pos = data.train_test_split(dataset, test_frac=args.test_frac, seed=sd)
        g = FeedbackGraph(train)
        tables.append(experiment.compare(
            _recommenders(g, args), g, test_pos, top_k=k, diversity_k=dk,
            item_positions=None, n_users_total=g.m))
        print(f"  seed {sd} done")
    methods = list(tables[0].index)
    cols = [c for c in want if c in tables[0].columns]
    arr = np.stack([t.loc[methods, cols].to_numpy(dtype=float) for t in tables])
    mean = pd.DataFrame(np.nanmean(arr, axis=0), index=methods, columns=cols)
    std = pd.DataFrame(np.nanstd(arr, axis=0), index=methods, columns=cols)
    return mean, std


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ratings", required=True, help="MovieLens-1M ratings.dat")
    ap.add_argument("--out-csv", default="movielens_rq2.csv")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--diversity-k", type=int, default=20)
    ap.add_argument("--test-frac", type=float, default=0.3)
    ap.add_argument("--rwed-beta", type=float, default=0.5)
    ap.add_argument("--rwed-v", type=float, default=0.7)
    ap.add_argument("--rp3-beta", type=float, default=0.5)
    ap.add_argument("--itemknn-k", type=int, default=200)
    args = ap.parse_args()

    ds = data.load_movielens_1m(args.ratings)
    print(f"MovieLens-1M: users={ds.n_users}  items={ds.n_items}  "
          f"ratings={ds.matrix.nnz}  seeds={args.seeds}\n")
    mean, std = run(ds, args)
    multiseed = args.seeds > 1
    show = ((mean.round(3).astype(str) + " ± " + std.round(3).astype(str))
            if multiseed else mean.round(3))
    tag = f"  ({args.seeds} seeds, mean ± std)" if multiseed else ""
    print("RQ2 — accuracy + long-tail diversity on MovieLens-1M" + tag)
    print(show.to_string())
    mean.to_csv(args.out_csv)
    print(f"\nwrote {args.out_csv}")
    print("Expected (cf. MIND): RWE-D lifts long-tail diversity (higher gini / "
          "coverage / surprisal, lower avg_deg) at ~accuracy parity with RP3-beta.")


if __name__ == "__main__":
    main()
