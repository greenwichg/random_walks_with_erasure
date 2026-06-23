import numpy as np

from rwe import guardrails as gr, opinion_dynamics as od

GP = gr.GuardrailParams()
OP = od.OpinionParams()


# --- the engagement/comfort signal ----------------------------------------
def test_comfort_decreases_with_dose():
    assert gr.comfort(np.array([0.0]), OP)[0] == 1.0
    assert gr.comfort(np.array([OP.latitude_reject]), OP)[0] == 0.0
    assert gr.comfort(np.array([0.5]), OP)[0] > gr.comfort(np.array([1.5]), OP)[0]


# --- the controllers ------------------------------------------------------
def test_backfire_monitor_cuts_on_drift_holds_on_assimilation():
    new = gr.BackfireMonitor(GP).step(np.array([2.0, 2.0]), np.array([0.1, -0.1]))
    assert new[0] < 2.0                 # drifting more extreme -> cut
    assert np.isclose(new[1], 2.0)      # assimilating toward centre -> hold


def test_engagement_guardrail_cuts_when_low():
    new = gr.EngagementGuardrail(GP).step(np.array([2.0, 2.0]), np.array([0.2, 0.9]))
    assert new[0] < 2.0                 # low engagement (bouncing out) -> cut
    assert np.isclose(new[1], 2.0)      # comfortably engaged -> hold


def test_engagement_guardrail_cuts_on_drop():
    m = gr.EngagementGuardrail(GP)
    m.step(np.array([1.0]), np.array([0.9]))            # establish previous level
    new = m.step(np.array([1.0]), np.array([0.7]))      # a sharp drop trips the warning
    assert new[0] < 1.0


# --- the headline result: guardrails prevent backfire ---------------------
def test_unguarded_polarizes_guardrails_prevent_it():
    theta0 = od.initial_population(n_users=300, seed=0)
    res = gr.compare_guardrails(theta0, n_rounds=40)
    start = res["no guardrail"]["polarization"][0]
    none_end = res["no guardrail"]["polarization"][-1]
    bf_end = res["backfire monitor (drift)"]["polarization"][-1]
    en_end = res["engagement early-warning"]["polarization"][-1]
    assert none_end > start             # an un-guarded aggressive dose backfires
    assert bf_end < none_end            # the drift guardrail prevents it
    assert en_end < none_end            # the engagement guardrail prevents it


def test_guardrails_actually_cut_the_dose():
    theta0 = od.initial_population(n_users=200, seed=1)
    res = gr.compare_guardrails(theta0, n_rounds=40)
    assert np.isclose(res["no guardrail"]["dose"][-1], GP.dose0)      # unchanged
    assert res["backfire monitor (drift)"]["dose"][-1] < GP.dose0     # cut
    assert res["engagement early-warning"]["dose"][-1] < GP.dose0     # cut


# --- bookkeeping ----------------------------------------------------------
def test_dose_stays_bounded():
    r = gr.simulate_guardrail(od.initial_population(100, seed=2),
                              gr.BackfireMonitor(), "drift", n_rounds=50)
    assert min(r["dose"]) >= GP.dose_min - 1e-9


def test_deterministic():
    theta0 = od.initial_population(100, seed=3)
    a = gr.simulate_guardrail(theta0, gr.EngagementGuardrail(), "engagement", 20)["polarization"]
    b = gr.simulate_guardrail(theta0, gr.EngagementGuardrail(), "engagement", 20)["polarization"]
    assert a == b
