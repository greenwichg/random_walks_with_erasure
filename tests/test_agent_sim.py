import numpy as np
import networkx as nx

from rwe.agent_sim import (
    NewsfeedSimulator, make_synthetic_web_graph, detect_communities,
    assign_community_ideology, run_independent_agents, run_cluster_agents,
    alpha_sweep, exposure_policy, next_session_opposite_fraction, is_opposite,
    AgentResult)


# --- trigger condition ----------------------------------------------------
def test_is_opposite_sign_and_deadband():
    assert is_opposite(-1.5, 1.5, epsilon=0.5)        # opposite side, beyond band
    assert not is_opposite(-1.5, -1.5, epsilon=0.5)   # same side
    assert not is_opposite(-1.5, 0.3, epsilon=0.5)    # opposite side but in deadband
    assert not is_opposite(1.0, 0.5, epsilon=0.5)     # |w| == epsilon, not > epsilon


# --- state machine on a deterministic (directed path) graph ---------------
def _path_sim(ideo, comm, **kwargs):
    G = nx.DiGraph()
    G.add_edges_from([(i, i + 1) for i in range(len(ideo) - 1)])
    node_ideo = {i: ideo[i] for i in range(len(ideo))}
    node_comm = {i: comm[i] for i in range(len(comm))}
    return NewsfeedSimulator(G, node_ideo, node_comm, **kwargs)


def test_score_counts_run_in_first_opposing_community():
    sim = _path_sim([-1.5, -1.5, 1.5, 1.5, 1.5, -1.5],
                    [0, 0, 1, 1, 1, 0], epsilon=0.5)
    # enters community 1 at node 2, stays for nodes 2,3,4 (=3), leaves at node 5.
    assert sim.simulate_session(u_i=-1.5, start=0, max_steps=10) == 3


def test_score_zero_when_never_triggers():
    sim = _path_sim([-1.5, -1.5, -1.5], [0, 0, 0], epsilon=0.5)
    assert sim.simulate_session(u_i=-1.5, start=0, max_steps=10) == 0


def test_score_counts_from_start_if_already_opposite():
    sim = _path_sim([1.5, 1.5, -1.5], [1, 1, 0], epsilon=0.5)
    # start already in opposing community 1: counts nodes 0,1 (=2), leaves at node 2.
    assert sim.simulate_session(u_i=-1.5, start=0, max_steps=10) == 2


def test_deadband_page_does_not_trigger():
    # The middle page is opposite-sign but inside the deadband -> no trigger.
    sim = _path_sim([-1.5, 0.2, -1.5], [0, 1, 0], epsilon=0.5)
    assert sim.simulate_session(u_i=-1.5, start=0, max_steps=10) == 0


# --- session log: which pages were accessed -------------------------------
def test_session_log_records_trajectory_and_states():
    sim = _path_sim([-1.5, -1.5, 1.5, 1.5, 1.5, -1.5],
                    [0, 0, 1, 1, 1, 0], epsilon=0.5)
    log = sim.simulate_session(u_i=-1.5, start=0, max_steps=10, return_log=True)
    assert log.pages == [0, 1, 2, 3, 4, 5]            # the visited trajectory
    assert log.states == ["own", "own", "trigger", "tracking", "tracking", "exited"]
    assert log.counted == [False, False, True, True, True, False]
    assert log.score == 3
    assert log.trigger_index == 2
    # return_log=False still gives the same integer score.
    assert sim.simulate_session(u_i=-1.5, start=0, max_steps=10) == log.score
    assert len(log.to_frame()) == 6


def test_score_trajectory_matches_simulation_and_handles_real_lists():
    sim = _path_sim([-1.5, -1.5, 1.5, 1.5, 1.5, -1.5],
                    [0, 0, 1, 1, 1, 0], epsilon=0.5)
    # Scoring an externally-supplied trajectory (e.g. real click logs).
    assert sim.score_trajectory(-1.5, [0, 1, 2, 3, 4, 5]) == 3
    log = sim.score_trajectory(-1.5, [2, 3, 4], return_log=True)
    assert log.score == 3 and log.trigger_index == 0   # starts already opposing


# --- transition policy ----------------------------------------------------
def _star_sim(alpha):
    # node 0 links to node 1 (close to u_i=-1.5) and node 2 (far), equal weights.
    G = nx.Graph()
    G.add_edge(0, 1, weight=1.0)
    G.add_edge(0, 2, weight=1.0)
    return NewsfeedSimulator(G, {0: -1.5, 1: -1.5, 2: 1.5}, {0: 0, 1: 0, 2: 1},
                             alpha=alpha, seed=0)


def _empirical_choice(sim, n=4000):
    rng = np.random.default_rng(0)
    picks = [sim._next_node(sim._index[0], -1.5, rng) for _ in range(n)]
    return np.mean(np.array(picks) == sim._index[1])  # fraction choosing the close node


def test_confirmation_bias_prefers_close_node():
    assert _empirical_choice(_star_sim(alpha=1.0)) > 0.7


def test_rabbit_hole_prefers_far_node():
    assert _empirical_choice(_star_sim(alpha=-1.0)) < 0.3


def test_alpha_zero_is_topology_only():
    # equal edge weights and alpha=0 -> ~50/50.
    assert abs(_empirical_choice(_star_sim(alpha=0.0)) - 0.5) < 0.05


def test_alpha_zero_respects_edge_weights():
    G = nx.Graph()
    G.add_edge(0, 1, weight=1.0)
    G.add_edge(0, 2, weight=3.0)
    sim = NewsfeedSimulator(G, {0: -1.5, 1: -1.5, 2: 1.5}, {0: 0, 1: 0, 2: 1},
                            alpha=0.0, seed=0)
    frac_close = _empirical_choice(sim)
    assert abs(frac_close - 0.25) < 0.05   # P(node1) ∝ 1 / (1+3)


# --- community detection & ideology assignment ----------------------------
def test_community_detection_recovers_blocks():
    G, latent = make_synthetic_web_graph(block_ideologies=(-1.5, 0.0, 1.5),
                                         block_size=25, seed=0)
    nc = detect_communities(G, method="louvain", seed=0)
    node_ideo, comm_ideo = assign_community_ideology(latent, nc)
    assert len(comm_ideo) == 3
    centroids = sorted(comm_ideo.values())
    assert np.allclose(centroids, [-1.5, 0.0, 1.5], atol=0.4)
    # node ideology equals its community centroid.
    for n in G.nodes():
        assert node_ideo[n] == comm_ideo[nc[n]]


# --- Monte Carlo, agent modes, sweep, exposure ----------------------------
def _setup():
    G, latent = make_synthetic_web_graph(seed=0)
    nc = detect_communities(G, method="louvain", seed=0)
    node_ideo, comm_ideo = assign_community_ideology(latent, nc)
    return G, nc, node_ideo, comm_ideo


def test_monte_carlo_shape_and_bounds():
    G, nc, node_ideo, _ = _setup()
    sim = NewsfeedSimulator(G, node_ideo, nc, alpha=0.0, seed=0)
    scores = sim.monte_carlo(-1.5, n_trials=50, max_steps=80, seed=1)
    assert scores.shape == (50,)
    assert scores.min() >= 0 and scores.max() <= 80


def test_independent_and_cluster_agents():
    G, nc, node_ideo, comm_ideo = _setup()
    sim = NewsfeedSimulator(G, node_ideo, nc, alpha=0.0, seed=0)
    ind = run_independent_agents(sim, [-1.5, 1.5], n_trials=50, max_steps=80, seed=0)
    assert set(ind) == {-1.5, 1.5}
    assert isinstance(ind[-1.5], AgentResult) and ind[-1.5].ideology == -1.5
    clu = run_cluster_agents(sim, comm_ideo, n_trials=50, max_steps=80, seed=0)
    assert len(clu) == len(comm_ideo)


def test_alpha_sweep_monotonic_decrease():
    G, nc, node_ideo, _ = _setup()
    df = alpha_sweep(G, node_ideo, nc, alphas=[-0.5, 0.0, 0.5, 1.0, 2.0],
                     positions=[-1.5], n_trials=150, max_steps=100, seed=0)
    col = df["u=-1.5"].values
    assert all(col[i] >= col[i + 1] for i in range(len(col) - 1))


def test_exposure_policy_monotonic_and_bounded():
    assert exposure_policy(0) == 0.0
    assert 0.0 <= exposure_policy(5) <= 1.0
    assert exposure_policy(20) > exposure_policy(2)


def test_next_session_fraction_bounded():
    assert next_session_opposite_fraction(0.0) == 0.05
    assert abs(next_session_opposite_fraction(1.0) - 0.6) < 1e-9
    assert 0.05 <= next_session_opposite_fraction(0.5) <= 0.6
