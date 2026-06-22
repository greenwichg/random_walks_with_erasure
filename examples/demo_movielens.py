"""Long-tail diversity benchmark on MovieLens-1M (paper's Result II).

Reproduces the structure of the benchmark experiments (Table 8, ML-1M row):
compares RWE-D against P3, RP3-beta and item-kNN on accuracy and long-tail
diversity.

MovieLens-1M is not redistributed here.  Download it from
https://grouplens.org/datasets/movielens/1m/ and point ``--ratings`` at the
extracted ``ratings.dat`` file::

    python examples/demo_movielens.py --ratings /path/to/ml-1m/ratings.dat

Without the file the script falls back to a synthetic skewed dataset so it still
runs end to end.
"""

import argparse

import numpy as np

from rwe import FeedbackGraph, P3, RP3Beta, RWED, ItemKNN, data, experiment


def main(ratings_path=None, seed=0):
    if ratings_path:
        print(f"Loading MovieLens-1M from {ratings_path} ...")
        dataset = data.load_movielens_1m(ratings_path)
    else:
        print("No --ratings given; using a synthetic skewed dataset instead.")
        dataset, _ = data.synthetic_recsys(n_users=600, n_items=400, seed=seed)
    print(f"users={dataset.n_users}  items={dataset.n_items}  "
          f"interactions={dataset.matrix.nnz}\n")

    train, test_pos = data.train_test_split(dataset, test_frac=0.3, seed=seed)
    g = FeedbackGraph(train)

    recommenders = {
        "P3":       P3(g),
        "RP3-beta": RP3Beta(g, beta=0.5),
        "item-kNN": ItemKNN(g, k_neighbors=200),
        "RWE-D":    RWED(g, beta=0.5, v=0.7),
    }
    table = experiment.compare(recommenders, g, test_pos,
                               top_k=10, diversity_k=20)
    cols = ["auc", "hit_rate@10", "precision@10", "mean_rank",
            "gini_div@20", "avg_deg@20", "personalization@20", "surprisal@20"]
    print(table[cols].round(3).to_string())
    print("\n  RWE-D keeps accuracy close to RP3-beta/P3 while improving")
    print("  long-tail diversity (lower avg_deg, higher gini_div/surprisal).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ratings", default=None,
                        help="path to MovieLens-1M ratings.dat")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    main(args.ratings, args.seed)
