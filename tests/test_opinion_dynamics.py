import numpy as np

from rwe import opinion_dynamics as od

P = od.OpinionParams()


# --- the update rule ------------------------------------------------------
def test_assimilation_moves_toward_close_content():
    new = od.update(np.array([0.0]), np.array([0.5]), P)   # |d|=0.5 <= accept(1.0)
    assert 0.0 < new[0] <= 0.5                              # moved toward, not past


def test_backfire_moves_away_from_far_content():
    new = od.update(np.array([-1.0]), np.array([2.0]), P)  # |d|=3.0 >= reject(2.0)
    assert new[0] < -1.0                                   # pushed toward own pole


def test_neutral_zone_no_movement():
    new = od.update(np.array([0.0]), np.array([1.5]), P)   # in the non-commitment band
    assert np.isclose(new[0], 0.0)


# --- the headline result --------------------------------------------------
def test_bridging_depolarizes_while_blast_polarizes():
    theta0 = od.initial_population(n_users=300, seed=0)
    start = od.polarization(theta0)
    _, bridge = od.run(theta0, od.rwe_b_bridging, n_rounds=40, seed=0)
    _, blast = od.run(theta0, od.opposite_blast, n_rounds=40, seed=0)
    assert bridge[-1] < start          # bounded bridging REDUCES polarization
    assert blast[-1] > start           # naive opposite-blast INCREASES it
    assert bridge[-1] < blast[-1]      # bridging ends far less polarized


def test_adaptive_also_depolarizes():
    theta0 = od.initial_population(n_users=300, seed=1)
    start = od.polarization(theta0)
    _, adapt = od.run(theta0, od.adaptive_bridging, n_rounds=40, seed=0)
    assert adapt[-1] < start


# --- bookkeeping ----------------------------------------------------------
def test_compare_policies_shape():
    hist = od.compare_policies(od.initial_population(100, seed=1), n_rounds=10, seed=0)
    assert set(hist) == set(od.POLICIES)
    assert all(len(h) == 11 for h in hist.values())


def test_positions_stay_bounded():
    final, _ = od.run(od.initial_population(100, seed=2), od.opposite_blast,
                      n_rounds=50, seed=0)
    assert final.min() >= -P.bound - 1e-9 and final.max() <= P.bound + 1e-9


def test_deterministic():
    theta0 = od.initial_population(100, seed=3)
    a = od.run(theta0, od.rwe_b_bridging, n_rounds=20, seed=0)[1]
    b = od.run(theta0, od.rwe_b_bridging, n_rounds=20, seed=0)[1]
    assert a == b
