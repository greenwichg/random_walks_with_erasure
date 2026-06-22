"""End-to-end demo of Random Walks with Erasure on synthetic data.

Runs the full pipeline of Paudel & Bernstein (WWW'21) on generated data with a
*known* ideological ground truth:

1. **Ideology detection (Section 6).**  Recover user / elite / content positions
   from endorsement graphs and show that the joint model (elite + content)
   beats the elite-only model -- the paper's *Result I*.

2. **Recommendation diversification (Sections 4-5, 7).**  On a homophilous
   feedback graph, detect ideological positions, then compare baselines (P3,
   RP3-beta, item-kNN) against RWE-D (long-tail) and RWE-B (political bridging)
   on accuracy + diversity -- the paper's *Results II & III*.

Run with::

    python examples/demo_synthetic.py
"""

import numpy as np

from rwe import (FeedbackGraph, P3, RP3Beta, RWED, RWEB, ItemKNN,
                 IdeologyModel, data, metrics, experiment)


def abs_corr(a, b):
    return abs(np.corrcoef(a, b)[0, 1])


def part1_ideology_detection():
    print("=" * 70)
    print("PART 1 -- Ideology detection (Section 6)")
    print("=" * 70)
    d = data.synthetic_ideology(n_users=300, n_elites=50, n_content=70, seed=0)

    elite_only = IdeologyModel(n_iter=400, seed=0).fit(d["R"])
    joint = IdeologyModel(n_iter=400, seed=0).fit(d["R"], d["S"])

    print("Correlation of recovered elite positions with ground truth:")
    print(f"  elite-endorsement only : {abs_corr(elite_only.phi, d['phi']):.3f}")
    print(f"  joint (elite + content): {abs_corr(joint.phi, d['phi']):.3f}")
    print("  -> joint learning recovers ideology better (paper's Result I)\n")


def part2_recommendation():
    print("=" * 70)
    print("PART 2 -- Recommendation diversification (Sections 4-5, 7)")
    print("=" * 70)
    d = data.synthetic_political(n_users=400, n_items=120, seed=1)
    dataset = data.Dataset(matrix=d["matrix"],
                           user_ids=np.arange(d["matrix"].shape[0]),
                           item_ids=np.arange(d["matrix"].shape[1]))
    train, test_pos = data.train_test_split(dataset, test_frac=0.3, seed=1)
    g = FeedbackGraph(train)

    # Detect ideological positions from the (training) feedback graph itself,
    # then use them for the bridging strategy -- the realistic flow where labels
    # are unavailable.
    res = IdeologyModel(n_iter=400, seed=0).fit(train)
    theta_hat, item_pos_hat = res.theta, res.phi
    print("Detected item positions vs planted truth: "
          f"|corr| = {abs_corr(item_pos_hat, d['item_positions']):.3f}\n")

    recommenders = {
        "P3":        P3(g),
        "RP3-beta":  RP3Beta(g, beta=0.5),
        "item-kNN":  ItemKNN(g, k_neighbors=50),
        "RWE-D":     RWED(g, beta=0.5, v=0.7),
        "RWE-B":     RWEB(g, theta_hat, item_pos_hat, epsilon=0.7),
    }
    table = experiment.compare(recommenders, g, test_pos,
                               top_k=10, diversity_k=20,
                               item_positions=item_pos_hat)
    cols = ["auc", "hit_rate@10", "precision@10", "mean_rank",
            "gini_div@20", "avg_deg@20", "rec_range@10"]
    print(table[cols].round(3).to_string())
    print("\n  RWE-B yields the widest ideological spread (rec_range@10);")
    print("  RWE-D promotes long-tail items (low avg_deg, higher gini_div).\n")

    # Kolmogorov-Smirnov: are RWE-B's recommended ideologies distributed
    # differently from the most accurate baseline? (paper's Result III)
    users = np.array([u for u in range(g.m) if len(test_pos[u]) > 0])
    rweb = recommenders["RWE-B"].recommend(users, top_k=10)
    p3 = recommenders["P3"].recommend(users, top_k=10)
    stat, pval = metrics.ks_statistic(rweb, p3, item_pos_hat)
    print(f"KS test (RWE-B vs P3 ideology distribution): "
          f"statistic={stat:.3f}, p-value={pval:.2e}")


if __name__ == "__main__":
    part1_ideology_detection()
    part2_recommendation()
