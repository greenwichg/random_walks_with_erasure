"""Agent-based newsfeed browsing simulation (step by step).

Computes an opposing-viewpoint satisfaction score per user on a polarized web
graph, following the model in :mod:`rwe.agent_sim`:

  STEP 1  build a web graph and derive per-page ideology from community detection
  STEP 2  simulate browsing sessions (own-side -> trigger -> tracking -> score)
  STEP 3  Monte Carlo: score distributions for independent & cluster agents
  STEP 4  sweep alpha to validate the score behaves sensibly
  STEP 5  feed the score back into a next-session exposure policy

Run with::

    python examples/demo_agent_sim.py
"""

import numpy as np

from rwe.agent_sim import (
    make_synthetic_web_graph, detect_communities, assign_community_ideology,
    NewsfeedSimulator, run_independent_agents, run_cluster_agents, alpha_sweep,
    exposure_policy, next_session_opposite_fraction)


def section(title):
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


def main():
    # -- STEP 1 ----------------------------------------------------------
    section("STEP 1 -- Web graph & per-page ideology from communities")
    G, latent = make_synthetic_web_graph(
        block_ideologies=(-1.5, 0.0, 1.5), block_size=30,
        p_in=0.25, p_out=0.01, seed=0)
    node_comm = detect_communities(G, method="louvain", seed=0)
    node_ideology, community_ideology = assign_community_ideology(latent, node_comm)
    print(f"web graph: {G.number_of_nodes()} pages, {G.number_of_edges()} hyperlinks")
    print(f"communities detected (Louvain): {len(community_ideology)}")
    print("community ideology centroids: "
          + ", ".join(f"{c}:{v:+.2f}" for c, v in sorted(community_ideology.items())))

    # -- STEP 2 ----------------------------------------------------------
    section("STEP 2 -- One example session (state machine)")
    sim = NewsfeedSimulator(G, node_ideology, node_comm, epsilon=0.5, alpha=0.0, seed=0)
    start = sim.own_side_nodes(-1.5)[0]
    score = sim.simulate_session(u_i=-1.5, start=start, max_steps=200)
    print(f"left-leaning agent (u=-1.5) starting on its own side -> "
          f"satisfaction score = {score}")

    # -- STEP 3 ----------------------------------------------------------
    section("STEP 3 -- Monte Carlo score distributions")
    print("Independent agents (distinct nearby left-leaning users), alpha=0:")
    ind = run_independent_agents(sim, positions=[-1.8, -1.5, -1.2],
                                 n_trials=400, seed=0)
    for r in ind.values():
        s = r.summary()
        print(f"  {r.label}: mean={s['mean']:6.2f}  std={s['std']:6.2f}  "
              f"median={s['median']:5.1f}  trigger_rate={s['trigger_rate']:.2f}")

    print("\nCluster-level agents (one per community centroid), alpha=0:")
    clu = run_cluster_agents(sim, community_ideology, n_trials=400, seed=0)
    for r in sorted(clu.values(), key=lambda x: x.ideology):
        s = r.summary()
        print(f"  {r.label} (ideology {r.ideology:+.2f}): mean={s['mean']:6.2f}  "
              f"trigger_rate={s['trigger_rate']:.2f}")

    # -- STEP 4 ----------------------------------------------------------
    section("STEP 4 -- Alpha sweep (validation)")
    df = alpha_sweep(G, node_ideology, node_comm,
                     alphas=[-1.0, -0.5, 0.0, 0.5, 1.0, 2.0],
                     positions=[-1.5, 1.5], n_trials=400, max_steps=200, seed=0)
    print(df.round(2).to_string())
    left = df["u=-1.5"].values
    print("\nalpha < 0 (rabbit hole) -> long dwell in opposing content;")
    print("alpha > 0 (confirmation bias) -> agent rarely leaves its own side.")
    print(f"monotonically non-increasing in alpha: "
          f"{all(left[i] >= left[i+1] for i in range(len(left)-1))}")

    # -- STEP 5 ----------------------------------------------------------
    section("STEP 5 -- Feed the score back into an exposure policy")
    print("score -> exposure level -> fraction of opposing pages seeded next session:")
    for r in ind.values():
        e = exposure_policy(r.scores, k=20.0)
        frac = next_session_opposite_fraction(e)
        print(f"  {r.label}: mean score={r.mean:6.2f} -> exposure={e:.2f} "
              f"-> next-session opposite fraction={frac:.2f}")


if __name__ == "__main__":
    main()
