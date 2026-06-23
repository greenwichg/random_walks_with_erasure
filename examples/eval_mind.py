"""RQ2/RQ3 evaluation driver on an ingested MIND ``.npz`` (see docs/PAPER_PLAN.md).

Takes a file produced by ``examples/ingest_mind.py``, runs the baselines
(ItemKNN / BPRMF / P3 / RP3-beta) and RWE-D / RWE-B, and prints + saves the
accuracy, long-tail-diversity (RQ2) and ideological-diversity (RQ3) tables — so
the moment you point it at MINDsmall, the paper's results tables fall out.

End-to-end::

    # 1. ingest MIND with click-behaviour positions (no outlet labels needed)
    python examples/ingest_mind.py --mind-dir data/MINDsmall_train \
        --political-only --ideology --min-user-clicks 5 --min-item-clicks 5 \
        --out mind_ideo.npz
    # 2. evaluate (full baselines + RWE-D/RWE-B -> RQ2 & RQ3 tables)
    python examples/eval_mind.py --npz mind_ideo.npz --out-csv results.csv
    # 3. (optional) bounded-bridging sweep: vary RWE-B's 'not too far' bound
    python examples/eval_mind.py --npz mind_ideo.npz --out-csv sweep.csv \
        --sweep-max-distance 3,2,1.5,1,0.5

The ``.npz`` must carry item ideological positions (from ``--ideology`` or an
outlet-lean join); items with an unknown position and then click-less users are
dropped so RWE-B and the ideological metrics are well-defined.

Note: ItemKNN builds a dense item-item similarity and BPRMF is pure-Python SGD,
so start with a filtered MINDsmall political subset (``--min-*-clicks``) and use
``--no-bprmf`` for a quick first pass.
"""

import argparse

from rwe import (FeedbackGraph, P3, RP3Beta, RWED, RWEB, ItemKNN, BPRMF,
                 data, experiment)
from rwe.mind import MINDData


def _run_sweep(g, theta, item_pos, test_pos, args):
    """RWE-B bounded-bridging sweep over ``max_distance`` (and optionally epsilon).

    Tests the bounded-bridging hypothesis (rwe/opinion_dynamics.py): tightening
    the 'not too far' bound should keep the bridging shift high (``uw_shift``)
    while pulling recommendations back toward the centre (``uw_recs`` down),
    rather than blasting users to the opposite extreme (the ``d=inf`` row).
    """
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

    table = experiment.compare(recs, g, test_pos, top_k=args.top_k,
                               diversity_k=args.diversity_k, item_positions=item_pos,
                               user_positions=theta, n_users_total=g.m)
    k = args.top_k
    cols = [c for c in (f"hit_rate@{k}", "auc", f"rec_range@{k}", f"shift@{k}",
                        "uw_shift", "uw_recs") if c in table.columns]
    print("RWE-B bounded-bridging sweep  (uw_shift high = still bridging; "
          "uw_recs low = recs land nearer the centre)")
    print(table[cols].round(3).to_string(), "\n")
    table.to_csv(args.out_csv)
    print(f"wrote full sweep table → {args.out_csv}")
    print("\nHypothesis (opinion_dynamics.py): tightening d keeps uw_shift up while "
          "pulling uw_recs down — bounded bridging lands users nearer the centre "
          "rather than the opposite extreme (the d=inf row).")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--npz", required=True, help="ingested MIND .npz (from ingest_mind.py)")
    ap.add_argument("--out-csv", default="eval_mind.csv")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--diversity-k", type=int, default=20)
    ap.add_argument("--test-frac", type=float, default=0.3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--epsilon", type=float, default=0.9, help="RWE-B non-bridge erasure")
    ap.add_argument("--rweb-max-distance", type=float, default=None,
                    help="RWE-B 'not too far' bound (None = unbounded)")
    ap.add_argument("--rwed-beta", type=float, default=0.5)
    ap.add_argument("--rwed-v", type=float, default=0.7)
    ap.add_argument("--rp3-beta", type=float, default=0.5)
    ap.add_argument("--itemknn-k", type=int, default=200)
    ap.add_argument("--no-bprmf", action="store_true", help="skip the (slow) BPRMF baseline")
    ap.add_argument("--sweep-max-distance", default=None,
                    help="comma-separated RWE-B 'not too far' bounds to sweep, e.g. "
                         "'2,1.5,1,0.5' -> an RWE-B-only bounded-bridging table")
    ap.add_argument("--sweep-epsilon", default=None,
                    help="comma-separated RWE-B epsilons to cross with the sweep "
                         "(default: just --epsilon)")
    args = ap.parse_args()

    d = MINDData.load(args.npz)
    dataset, theta, item_pos = d.recommender_inputs()
    if dataset.n_items < d.n_items:
        print(f"[note] kept {dataset.n_items}/{d.n_items} items with a known position")
    print(f"users={dataset.n_users}  items={dataset.n_items}  "
          f"clicks={dataset.matrix.nnz}  "
          f"position range=[{item_pos.min():.2f}, {item_pos.max():.2f}]\n")

    train, test_pos = data.train_test_split(dataset, test_frac=args.test_frac, seed=args.seed)
    g = FeedbackGraph(train)

    if args.sweep_max_distance:
        _run_sweep(g, theta, item_pos, test_pos, args)
        return

    recs = {
        "ItemKNN": ItemKNN(g, k_neighbors=args.itemknn_k),
        "P3": P3(g),
        "RP3-beta": RP3Beta(g, beta=args.rp3_beta),
        "RWE-D": RWED(g, beta=args.rwed_beta, v=args.rwed_v),
        "RWE-B": RWEB(g, theta, item_pos, epsilon=args.epsilon,
                      max_distance=args.rweb_max_distance),
    }
    if not args.no_bprmf:
        recs["BPRMF"] = BPRMF(g, seed=args.seed)

    table = experiment.compare(recs, g, test_pos, top_k=args.top_k,
                               diversity_k=args.diversity_k, item_positions=item_pos,
                               user_positions=theta, n_users_total=g.m)

    k, dk = args.top_k, args.diversity_k
    rq2 = ["auc", f"hit_rate@{k}", f"ndcg@{k}", "mean_rank", f"gini_div@{dk}",
           f"coverage@{dk}", f"avg_deg@{dk}", f"surprisal@{dk}", f"personalization@{dk}"]
    rq3 = [f"rec_range@{k}", f"shift@{k}", "uw_recs", "uw_shift", "uw_range"]
    rq2 = [c for c in rq2 if c in table.columns]
    rq3 = [c for c in rq3 if c in table.columns]

    print("RQ2 — accuracy + long-tail diversity")
    print(table[rq2].round(3).to_string(), "\n")
    print("RQ3 — ideological diversity (RecRange / directed shift; UW-weighted)")
    print(table[rq3].round(3).to_string(), "\n")

    table.to_csv(args.out_csv)
    print(f"wrote full table → {args.out_csv}")
    print("\nExpected pattern (paper Results II–IV): RWE-D lifts long-tail diversity "
          "(higher gini/coverage/surprisal, lower avg_deg) at ~equal accuracy; RWE-B "
          "widens rec_range and the (UW) shift, bridging users across the centre.")


if __name__ == "__main__":
    main()
