"""RQ2/RQ3 evaluation driver on an ingested MIND ``.npz`` (see docs/PAPER_PLAN.md).

Takes a file produced by ``examples/ingest_mind.py``, runs the baselines
(ItemKNN / BPRMF / P3 / RP3-beta) and RWE-D / RWE-B, and prints + saves the
accuracy, long-tail-diversity (RQ2) and ideological-diversity (RQ3) tables.

End-to-end::

    # 1. ingest MIND with click-behaviour positions (no outlet labels needed)
    python examples/ingest_mind.py --mind-dir data/MINDsmall_train \
        --political-only --ideology --min-user-clicks 5 --min-item-clicks 5 \
        --out mind_ideo.npz
    # 2. evaluate (full baselines + RWE-D/RWE-B -> RQ2 & RQ3 tables)
    python examples/eval_mind.py --npz mind_ideo.npz --out-csv results.csv
    # 2b. robust: average over seeds + Wilcoxon significance vs a reference
    #     (use >= 7 seeds so the signed-rank test can reach p < 0.05)
    python examples/eval_mind.py --npz mind_ideo.npz --seeds 7 --no-bprmf --out-csv results.csv
    # 3. (optional) bounded-bridging sweep over RWE-B's 'not too far' bound
    python examples/eval_mind.py --npz mind_ideo.npz --out-csv sweep.csv \
        --sweep-max-distance 3,2,1.5,1,0.5

With ``--seeds N`` the train/test split (and BPRMF init) is re-drawn N times;
tables show ``mean ± std`` and a Wilcoxon signed-rank ``p`` per metric vs
``--sig-ref`` (saved as ``*_std.csv`` / ``*_pvalues.csv``).

The ``.npz`` must carry item ideological positions (from ``--ideology`` or an
outlet-lean join); unknown-position items and click-less users are dropped so
RWE-B and the ideological metrics are well-defined. BPRMF is slow pure-Python
SGD, so use ``--no-bprmf`` for multi-seed runs.
"""

import argparse

import numpy as np
import pandas as pd

from rwe import (FeedbackGraph, P3, RP3Beta, RWED, RWEB, ItemKNN, BPRMF,
                 data, experiment)
from rwe.mind import MINDData


def _recommenders(g, theta, item_pos, args, seed):
    recs = {
        "ItemKNN": ItemKNN(g, k_neighbors=args.itemknn_k),
        "P3": P3(g),
        "RP3-beta": RP3Beta(g, beta=args.rp3_beta),
        "RWE-D": RWED(g, beta=args.rwed_beta, v=args.rwed_v),
        "RWE-B": RWEB(g, theta, item_pos, epsilon=args.epsilon,
                      max_distance=args.rweb_max_distance),
    }
    if not args.no_bprmf:
        recs["BPRMF"] = BPRMF(g, seed=seed)
    return recs


def _sweep_recommenders(g, theta, item_pos, args, seed):
    dists = sorted((float(x) for x in args.sweep_max_distance.split(",")), reverse=True)
    dist_grid = [None] + dists                       # unbounded first, then tightening
    eps_grid = ([float(x) for x in args.sweep_epsilon.split(",")]
                if args.sweep_epsilon else [args.epsilon])
    recs = {"P3 (ref)": P3(g)}
    for eps in eps_grid:
        for dd in dist_grid:
            tag = "inf" if dd is None else f"{dd:g}"
            label = f"RWE-B d={tag}" + (f" e={eps:g}" if len(eps_grid) > 1 else "")
            recs[label] = RWEB(g, theta, item_pos, epsilon=eps, max_distance=dd)
    return recs


def _eval_across_seeds(build_fn, dataset, item_pos, theta, args):
    """Run the eval for seeds 0..N-1; return ``(mean, std, arr, methods, metrics)``."""
    tables = []
    for sd in range(args.seeds):
        train, test_pos = data.train_test_split(dataset, test_frac=args.test_frac, seed=sd)
        g = FeedbackGraph(train)
        tables.append(experiment.compare(
            build_fn(g, sd), g, test_pos, top_k=args.top_k, diversity_k=args.diversity_k,
            item_positions=item_pos, user_positions=theta, n_users_total=g.m))
    methods, metrics = list(tables[0].index), list(tables[0].columns)
    arr = np.stack([t.loc[methods, metrics].to_numpy(dtype=float) for t in tables])
    mean = pd.DataFrame(np.nanmean(arr, axis=0), index=methods, columns=metrics)
    std = pd.DataFrame(np.nanstd(arr, axis=0), index=methods, columns=metrics)
    return mean, std, arr, methods, metrics


def _show(mean, std, cols, multiseed):
    cols = [c for c in cols if c in mean.columns]
    if not multiseed:
        return mean[cols].round(3).to_string()
    return (mean[cols].round(3).astype(str) + " ± "
            + std[cols].round(3).astype(str)).to_string()


def _wilcoxon_vs_ref(arr, methods, metrics, ref, cols):
    """Wilcoxon signed-rank p per metric for each method vs ``ref`` across seeds."""
    from scipy.stats import wilcoxon

    cols = [c for c in cols if c in metrics]
    if ref not in methods:
        return None
    ri = methods.index(ref)
    rows = {}
    for m in methods:
        if m == ref:
            continue
        mi, ps = methods.index(m), {}
        for c in cols:
            a, b = arr[:, mi, metrics.index(c)], arr[:, ri, metrics.index(c)]
            ok = np.isfinite(a) & np.isfinite(b)
            a, b = a[ok], b[ok]
            try:
                ps[c] = float("nan") if a.size < 3 or np.allclose(a, b) \
                    else float(wilcoxon(a, b).pvalue)
            except Exception:
                ps[c] = float("nan")
        rows[m] = ps
    return pd.DataFrame(rows).T[cols]


def _print_pvalues(pv, ref, seeds):
    print(f"Wilcoxon signed-rank p vs {ref} ({seeds} seeds; * = p<0.05):")
    disp = pv.copy()
    for c in disp.columns:
        disp[c] = [("*" if (x == x and x < 0.05) else " ")
                   + (f"{x:.3f}" if x == x else " -- ") for x in pv[c]]
    print(disp.to_string(), "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--npz", required=True, help="ingested MIND .npz (from ingest_mind.py)")
    ap.add_argument("--out-csv", default="eval_mind.csv")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--diversity-k", type=int, default=20)
    ap.add_argument("--test-frac", type=float, default=0.3)
    ap.add_argument("--seeds", type=int, default=1,
                    help="re-draw the split this many times -> mean±std + Wilcoxon")
    ap.add_argument("--sig-ref", default="P3", help="reference method for the Wilcoxon test")
    ap.add_argument("--epsilon", type=float, default=0.9, help="RWE-B non-bridge erasure")
    ap.add_argument("--rweb-max-distance", type=float, default=None,
                    help="RWE-B 'not too far' bound (None = unbounded)")
    ap.add_argument("--rwed-beta", type=float, default=0.5)
    ap.add_argument("--rwed-v", type=float, default=0.7)
    ap.add_argument("--rp3-beta", type=float, default=0.5)
    ap.add_argument("--itemknn-k", type=int, default=200)
    ap.add_argument("--no-bprmf", action="store_true", help="skip the (slow) BPRMF baseline")
    ap.add_argument("--sweep-max-distance", default=None,
                    help="comma-separated RWE-B bounds to sweep, e.g. '3,2,1.5,1,0.5'")
    ap.add_argument("--sweep-epsilon", default=None,
                    help="comma-separated RWE-B epsilons to cross with the sweep")
    args = ap.parse_args()

    d = MINDData.load(args.npz)
    dataset, theta, item_pos = d.recommender_inputs()
    if dataset.n_items < d.n_items:
        print(f"[note] kept {dataset.n_items}/{d.n_items} items with a known position")
    print(f"users={dataset.n_users}  items={dataset.n_items}  clicks={dataset.matrix.nnz}"
          f"  position range=[{item_pos.min():.2f}, {item_pos.max():.2f}]"
          f"  seeds={args.seeds}\n")
    multiseed = args.seeds > 1
    tag = f"  ({args.seeds} seeds, mean ± std)" if multiseed else ""

    if args.sweep_max_distance:
        mean, std, _, _, _ = _eval_across_seeds(
            lambda g, sd: _sweep_recommenders(g, theta, item_pos, args, sd),
            dataset, item_pos, theta, args)
        k = args.top_k
        cols = [f"hit_rate@{k}", "auc", f"rec_range@{k}", f"shift@{k}", "uw_shift", "uw_recs"]
        print("RWE-B bounded-bridging sweep" + tag
              + "  (uw_recs low = recs land nearer the centre)")
        print(_show(mean, std, cols, multiseed), "\n")
        mean.to_csv(args.out_csv)
        if multiseed:
            std.to_csv(args.out_csv.replace(".csv", "_std.csv"))
        print(f"wrote sweep table → {args.out_csv}")
        return

    mean, std, arr, methods, metrics = _eval_across_seeds(
        lambda g, sd: _recommenders(g, theta, item_pos, args, sd),
        dataset, item_pos, theta, args)
    k, dk = args.top_k, args.diversity_k
    rq2 = ["auc", f"hit_rate@{k}", f"ndcg@{k}", "mean_rank", f"gini_div@{dk}",
           f"coverage@{dk}", f"avg_deg@{dk}", f"surprisal@{dk}", f"personalization@{dk}"]
    rq3 = [f"rec_range@{k}", f"shift@{k}", "uw_recs", "uw_shift", "uw_range"]

    print("RQ2 — accuracy + long-tail diversity" + tag)
    print(_show(mean, std, rq2, multiseed), "\n")
    print("RQ3 — ideological diversity (RecRange / directed shift; UW-weighted)" + tag)
    print(_show(mean, std, rq3, multiseed), "\n")

    mean.to_csv(args.out_csv)
    print(f"wrote table → {args.out_csv}")
    if multiseed:
        std.to_csv(args.out_csv.replace(".csv", "_std.csv"))
        pv = _wilcoxon_vs_ref(arr, methods, metrics, args.sig_ref, rq2 + rq3)
        if pv is not None:
            print()
            _print_pvalues(pv, args.sig_ref, args.seeds)
            pv.to_csv(args.out_csv.replace(".csv", "_pvalues.csv"))
    print("Expected pattern (paper Results II–IV): RWE-D lifts long-tail diversity "
          "(higher gini/coverage/surprisal, lower avg_deg) at ~equal accuracy; RWE-B "
          "raises the (UW) shift, bridging users across the centre.")


if __name__ == "__main__":
    main()
