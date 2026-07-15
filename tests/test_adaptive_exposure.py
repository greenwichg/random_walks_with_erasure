"""W2 — adaptive exposure from measured cross-cutting reception (shrinkage), with the three
architectural invariants pinned as tests:

  1. RATE-not-count   — exposure is driven by openedCross/shownCross, never the raw opened count,
                        so a larger bridge budget (W1) cannot inflate it.
  2. ADAPTIVE-only    — only AdaptiveRWEB's exposure moves; RWE-B / RWE-D are byte-identical.
  3. EPSILON FLOOR    — exposure maps to epsilon in [0.5, 0.95]; a resistant reader still gets the
                        floor dose (epsilon_low = 0.5), so W2 can never zero out cross-cutting.

Plus: new/no-signal readers are byte-identical to the neutral 0.5 prior (REPORT CONTRACT v1 / demo
safety), the Open-Mindedness gate governs activation, and the agreed worked examples hold."""
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import adaptive_satisfaction as asf   # noqa: E402
import api_server as engine           # noqa: E402
import personalize                    # noqa: E402
import store as store_mod             # noqa: E402
from rwe.satisfaction import AdaptiveRWEB  # noqa: E402


# --------------------------------------------------------------------------- #
# The pure shrinkage math (kappa = 10, prior = 0.5)
# --------------------------------------------------------------------------- #
def test_shrunk_exposure_math_and_worked_examples():
    f = asf.shrunk_exposure
    assert f(None, None) == 0.5 and f(0.9, 0) == 0.5          # no signal / n=0 -> prior exactly
    assert f(0.5, 7, 10) == 0.5 and f(0.5, 100, 10) == 0.5    # neutral reception stays neutral, any n
    assert f(0.8, 20, 10) == pytest.approx(0.700)             # agreed worked examples
    assert f(0.8, 5, 10) == pytest.approx(0.600)
    assert f(0.2, 100, 10) == pytest.approx(25 / 110)
    assert f(1.0, 3, 10) == pytest.approx(8 / 13)             # activation boundary, damped (not 1.0)
    # monotone in rate and in n; clipped to [0,1]
    assert f(0.2, 20, 10) < f(0.5, 20, 10) < f(0.8, 20, 10)
    assert f(0.8, 5, 10) < f(0.8, 20, 10) < f(0.8, 100, 10)   # more evidence -> closer to rate
    assert 0.0 <= f(9.9, 5, 10) <= 1.0 and 0.0 <= f(-9.9, 5, 10) <= 1.0


# --------------------------------------------------------------------------- #
# Invariant 1 — RATE, not the raw opened count
# --------------------------------------------------------------------------- #
def test_invariant_rate_not_count_pure():
    # 100 shown / 20 opened (rate 0.2) gets LOWER exposure than 5 shown / 4 opened (rate 0.8),
    # despite 20 > 4 opens: the driver is the ratio, not the count.
    lo = asf.shrunk_exposure(20 / 100, 100, 10)
    hi = asf.shrunk_exposure(4 / 5, 5, 10)
    assert lo < 0.5 < hi and lo < hi


def _record(st, uid, shown, opened):
    st.record_recommendations_shown(uid, [(f"x{uid}_{i}", True) for i in range(shown)])
    for i in range(opened):
        st.record_recommendation_open(uid, f"x{uid}_{i}", cross_cutting=True)


def test_invariant_rate_not_count_through_the_store(tmp_path):
    st = store_mod.Store(f"sqlite:///{tmp_path/'w2.db'}")
    be = engine.Backend(engine.DatasetProfile.synthetic(n_users=60, max_items=200, seed=0))
    pers = personalize.Personalizer(be, st, persist=False)
    u_lo = st.upsert_user_by_identity("dev", "lo").id       # 100 shown, 20 opened -> rate 0.2
    u_hi = st.upsert_user_by_identity("dev", "hi").id       # 5 shown, 4 opened   -> rate 0.8
    _record(st, u_lo, 100, 20)
    _record(st, u_hi, 5, 4)
    e_lo, e_hi = pers._reader_exposure(u_lo), pers._reader_exposure(u_hi)
    assert e_lo == pytest.approx(25 / 110, abs=1e-6) and e_lo < 0.5      # low rate -> below prior
    assert e_hi == pytest.approx(0.600) and e_hi > 0.5                   # high rate -> above prior
    assert e_lo < e_hi                                                   # more opens != more exposure


def test_openmindedness_gate_governs_activation(tmp_path):
    st = store_mod.Store(f"sqlite:///{tmp_path/'w2g.db'}")
    be = engine.Backend(engine.DatasetProfile.synthetic(n_users=60, max_items=200, seed=0))
    pers = personalize.Personalizer(be, st, persist=False)
    u_new = st.upsert_user_by_identity("dev", "new").id     # 2 shown < min_shown(3)   -> None
    u_none = st.upsert_user_by_identity("dev", "none").id   # 5 shown, 0 opened        -> None
    u_ok = st.upsert_user_by_identity("dev", "ok").id       # 5 shown, 3 opened        -> active
    _record(st, u_new, 2, 2)
    _record(st, u_none, 5, 0)
    _record(st, u_ok, 5, 3)
    assert pers._reader_exposure(u_new) is None             # below min_shown -> neutral default
    assert pers._reader_exposure(u_none) is None            # no opens (below min_opened) -> neutral
    assert pers._reader_exposure(u_ok) == pytest.approx(asf.shrunk_exposure(3 / 5, 5, 10))


# --------------------------------------------------------------------------- #
# Invariant 2 — only AdaptiveRWEB moves; RWE-B / RWE-D untouched
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def be():
    return engine.Backend(engine.DatasetProfile.synthetic(n_users=200, max_items=500, seed=0))


def test_invariant_adaptive_slice_only(be):
    base = be._build_recommenders(be.mind)
    uid0 = str(base.rec_dataset.user_ids[0])
    tuned = be._build_recommenders(be.mind, reader_exposure=(uid0, 0.9))
    # RWE-B epsilon and RWE-D beta are byte-identical — W2 never touches them
    assert float(tuned.models["rwe-b"].epsilon) == float(base.models["rwe-b"].epsilon) == 0.9
    assert tuned.models["rwe-d"].beta == base.models["rwe-d"].beta == 0.5
    # only the AdaptiveRWEB exposure differs, and only at the reader's row
    assert tuned.models["adaptive"].exposure[0] == 0.9 and base.models["adaptive"].exposure[0] == 0.5
    assert np.array_equal(tuned.models["adaptive"].exposure[1:], base.models["adaptive"].exposure[1:])


# --------------------------------------------------------------------------- #
# Invariant 3 — epsilon floor (0.5) preserved
# --------------------------------------------------------------------------- #
def test_invariant_epsilon_floor(be):
    rec = be._build_recommenders(be.mind)
    m = rec.fg.m
    exp = np.linspace(0.0, 1.0, m)                       # spans the whole exposure range
    model = AdaptiveRWEB(rec.fg, rec.theta, rec.item_pos, exp)
    assert model.epsilon_low == 0.5 and model.epsilon_high == 0.95
    assert model.epsilon.min() == pytest.approx(0.5)    # exposure 0 -> epsilon floor 0.5 (never lower)
    assert model.epsilon.max() == pytest.approx(0.95)
    assert np.all(model.epsilon >= 0.5) and np.all(model.epsilon <= 0.95)


# --------------------------------------------------------------------------- #
# New / no-signal readers are byte-identical to the neutral 0.5 prior
# --------------------------------------------------------------------------- #
def test_new_user_is_byte_identical_to_neutral_default(be):
    base = be._build_recommenders(be.mind)
    none = be._build_recommenders(be.mind, reader_exposure=None)
    assert np.array_equal(base.exposure, none.exposure)             # None override -> unchanged
    assert np.all(base.exposure == 0.5)                             # every user at the neutral prior
    # an unmatched user id is a no-op (defensive) -> still all 0.5
    unmatched = be._build_recommenders(be.mind, reader_exposure=("__no_such_user__", 0.9))
    assert np.all(unmatched.exposure == 0.5)
