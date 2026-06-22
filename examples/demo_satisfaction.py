"""Satisfaction-driven adaptive exposure to opposing viewpoints.

Demonstrates the feedback loop added on top of RWE-B:

1. Project the feedback graph onto a webpage (item-item) graph and detect
   communities -- dense clusters of pages sharing a viewpoint.
2. Simulate each user's browsing as a random walk and measure their
   *satisfaction score*: how many pages they traverse inside the first opposing
   community they enter, before leaving it.
3. Turn that score into a per-user exposure level and feed it to AdaptiveRWEB,
   so users who tolerate opposing content get more of it surfaced.

Run with::

    python examples/demo_satisfaction.py
"""

import numpy as np

from rwe import (FeedbackGraph, RWEB, AdaptiveRWEB, WebGraph,
                 SatisfactionModel, IdeologyModel, data)


def opposite_fraction(recs, user_ids, user_pos, item_pos, center):
    """Mean fraction of recommended items on the opposite side of the user."""
    fr = []
    for r, u in zip(recs, user_ids):
        items = r[r >= 0]
        if items.size:
            opp = np.sign(item_pos[items] - center) == -np.sign(user_pos[u] - center)
            fr.append(opp.mean())
    return float(np.mean(fr))


def main():
    # --- data + ideology -------------------------------------------------
    d = data.synthetic_political(n_users=400, n_items=120, seed=1)
    g = FeedbackGraph(d["matrix"])
    res = IdeologyModel(n_iter=400, seed=0).fit(d["matrix"])
    user_pos, item_pos = res.theta, res.phi
    center = float(np.median(user_pos))
    users = np.arange(g.m)

    # --- web graph + communities ----------------------------------------
    print("=" * 70)
    print("STEP 1 -- Webpage graph & community detection")
    print("=" * 70)
    web = WebGraph(g, item_pos)
    labels = web.detect_communities(knn=5, seed=0)
    n_comm = labels.max() + 1
    corr = np.corrcoef(item_pos, web.viewpoints[labels])[0, 1]
    print(f"detected {n_comm} communities on the webpage graph")
    print(f"community viewpoints (mean ideology): "
          f"{np.round(np.sort(web.viewpoints), 2)}")
    print(f"|corr| between a page's ideology and its community's viewpoint: "
          f"{abs(corr):.3f}\n")

    # --- satisfaction scores --------------------------------------------
    print("=" * 70)
    print("STEP 2 -- Satisfaction scores from simulated browsing")
    print("=" * 70)
    sat = SatisfactionModel(web, user_pos, n_walks=25, walk_length=40, seed=0)
    scores = sat.score(users)
    exposure = sat.exposure(users)
    print(f"satisfaction score: mean={scores.mean():.2f}  "
          f"min={scores.min():.0f}  max={scores.max():.0f}")
    print(f"exposure level    : mean={exposure.mean():.2f}  "
          f"(0 = stays siloed, 1 = dwells longest in opposing community)\n")

    # --- adaptive recommendation ----------------------------------------
    print("=" * 70)
    print("STEP 3 -- Adaptive (per-user) vs fixed (one-size-fits-all) bridging")
    print("=" * 70)
    fixed = RWEB(g, user_pos, item_pos, epsilon=0.9).recommend(users, top_k=10)
    adaptive = AdaptiveRWEB(g, user_pos, item_pos,
                            exposure=exposure).recommend(users, top_k=10)

    # Split users by their demonstrated tolerance (satisfaction-driven exposure).
    med = np.median(exposure)
    low, high = exposure <= med, exposure > med

    def of(recs, mask):
        return opposite_fraction(recs[mask], users[mask], user_pos, item_pos, center)

    print("opposite-content fraction      low-tolerance   high-tolerance   all users")
    print(f"  fixed RWE-B (eps=0.9)        {of(fixed, low):>11.3f}    {of(fixed, high):>11.3f}   {opposite_fraction(fixed, users, user_pos, item_pos, center):>9.3f}")
    print(f"  AdaptiveRWEB (satisfaction)  {of(adaptive, low):>11.3f}    {of(adaptive, high):>11.3f}   {opposite_fraction(adaptive, users, user_pos, item_pos, center):>9.3f}")
    print("\n  Fixed bridging flips *every* user -- including low-tolerance users -- to")
    print("  almost entirely opposing content.  AdaptiveRWEB calibrates the dose to")
    print("  each user's measured satisfaction: low-tolerance users are protected,")
    print("  high-tolerance users are bridged toward opposing viewpoints.")


if __name__ == "__main__":
    main()
