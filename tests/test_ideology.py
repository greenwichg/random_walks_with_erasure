import numpy as np

from rwe.data import synthetic_ideology
from rwe.ideology import IdeologyModel


def _abs_corr(a, b):
    return abs(np.corrcoef(a, b)[0, 1])


def test_elite_only_recovers_positions():
    d = synthetic_ideology(n_users=250, n_elites=40, n_content=50, seed=0)
    model = IdeologyModel(n_iter=400, lr=0.05, lam=0.05, seed=0)
    res = model.fit(d["R"])
    # Recovered user/elite positions correlate strongly with planted truth
    # (sign is arbitrary, so compare absolute correlation).
    assert _abs_corr(res.theta, d["theta"]) > 0.8
    assert _abs_corr(res.phi, d["phi"]) > 0.8


def test_joint_model_recovers_all_positions():
    d = synthetic_ideology(n_users=250, n_elites=40, n_content=50, seed=1)
    model = IdeologyModel(n_iter=400, lr=0.05, lam=0.05, mu=1.0, seed=0)
    res = model.fit(d["R"], d["S"])
    assert res.psi is not None
    assert _abs_corr(res.theta, d["theta"]) > 0.8
    assert _abs_corr(res.phi, d["phi"]) > 0.8
    assert _abs_corr(res.psi, d["psi"]) > 0.8


def test_positions_are_standardized():
    d = synthetic_ideology(seed=2)
    res = IdeologyModel(n_iter=200).fit(d["R"])
    assert abs(res.theta.mean()) < 1e-6
    assert abs(res.theta.std() - 1.0) < 1e-6


def test_anchor_fixes_sign():
    d = synthetic_ideology(seed=3)
    # Pick a user planted on the far left; anchor must keep them negative.
    left_user = int(np.argmin(d["theta"]))
    res = IdeologyModel(n_iter=300, seed=0).fit(d["R"], anchor=left_user)
    assert res.theta[left_user] <= 0.0


def test_objective_increases():
    d = synthetic_ideology(seed=4)
    res = IdeologyModel(n_iter=200, seed=0).fit(d["R"])
    # Log-posterior should be higher at the end than at the start.
    assert res.history[-1] > res.history[0]


def test_restarts_keep_highest_likelihood_fit():
    d = synthetic_ideology(n_users=200, n_elites=30, n_content=40, seed=1)
    m = IdeologyModel(n_iter=120, seed=0)
    one = m.fit(d["R"])                        # restarts=1 -> seed 0 only
    many = m.fit(d["R"], restarts=6)           # seeds 0..5, keep best final objective
    # best-of-6 includes seed 0, so its final (unsupervised) objective can only be >=
    assert many.history[-1] >= one.history[-1] - 1e-6
    # and the winner is still a valid, standardized fit (selection by likelihood only)
    assert many.theta.shape == one.theta.shape
    assert abs(many.theta.mean()) < 1e-6 and abs(many.theta.std() - 1.0) < 1e-6
