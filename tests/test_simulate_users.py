"""Tests for examples/simulate_users.py (agent-based synthetic-user simulator).

Uses the fully-synthetic catalog (no Qbias needed). Covers trait ranges, determinism,
the MINDData round-trip into the pipeline, and the key model-validity check: agents with
higher openness actually click more cross-cutting content."""

import importlib.util
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "simulate_users", ROOT / "examples" / "simulate_users.py")
su = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(su)


def test_synthetic_catalog_aligned():
    cat = su.synthetic_catalog(n_items=200, n_outlets=10, n_topics=5, seed=0)
    assert cat.n == 200
    assert all(len(a) == 200 for a in (cat.positions, cat.outlets, cat.topics,
                                       cat.quality, cat.titles, cat.ids))
    assert cat.topic_idx.max() < len(cat.topic_names)
    assert cat.outlet_idx.max() < len(cat.outlet_names)
    assert (cat.quality >= 0).all() and (cat.quality <= 1).all()
    assert (np.abs(cat.positions) <= 2).all()


def test_first_tag_parses_qbias_stringified_list():
    assert su._first_tag("['White House', 'Politics']") == "White House"   # multi-tag list
    assert su._first_tag("['Politics']") == "Politics"                     # single-tag list
    assert su._first_tag("Economy") == "Economy"                          # plain string
    assert su._first_tag("") == "general" and su._first_tag(None) == "general"


def test_catalog_from_qbias_clean_topics(tmp_path):
    import csv
    p = tmp_path / "qb.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["heading", "text", "bias_rating", "source", "tags"])
        w.writerow(["H1", "b", "left", "CNN", "['White House', 'Politics']"])
        w.writerow(["H2", "b", "right", "Fox News", "['Economy']"])
    cat = su.catalog_from_qbias(str(p))
    assert set(cat.topics) == {"White House", "Economy"}                   # first tag, cleaned
    assert not any(("[" in t) or ("'" in t) for t in cat.topics)          # no list-repr leakage


def test_sample_population_ranges_and_shapes():
    cfg = su.SimConfig(n_users=150, max_items=200, seed=1)
    cat = su.synthetic_catalog(n_items=200, seed=1)
    pop = su.sample_population(cat, cfg)
    assert pop.theta.shape == (150,) and (np.abs(pop.theta) <= 2).all()
    for a in (pop.openness, pop.curiosity, pop.quality_pref, pop.activity):
        assert (a >= 0).all() and (a <= 1).all()
    assert pop.topic_interest.shape == (150, len(cat.topic_names))
    assert np.allclose(pop.topic_interest.sum(axis=1), 1.0)             # dirichlet rows
    assert pop.outlet_trust.shape == (150, len(cat.outlet_names))
    assert (pop.outlet_trust >= 0).all() and (pop.outlet_trust <= 1).all()


def test_simulate_deterministic_and_wellformed():
    cfg = su.SimConfig(n_users=80, max_items=150, seed=2)
    cat = su.synthetic_catalog(n_items=150, seed=2)
    pop = su.sample_population(cat, cfg)
    e1 = su.simulate(cat, pop, cfg)
    e2 = su.simulate(cat, pop, cfg)
    assert len(e1) > 0 and e1 == e2                                     # deterministic given seed
    assert {u for u, _, _, _ in e1} <= set(range(80))
    assert {a for _, _, _, a in e1} <= {"ignore", "save", "share"}
    assert all(d > 0 for _, _, d, _ in e1)                             # positive dwell


def test_openness_increases_cross_cutting():
    cfg = su.SimConfig(n_users=400, max_items=250, seed=3, sessions_lambda=12.0)
    cat = su.synthetic_catalog(n_items=250, seed=3)
    pop = su.sample_population(cat, cfg)
    rows, _ = su.population_metrics(su.simulate(cat, pop, cfg), cat, pop, cfg)
    rate = np.array([r["cross_cutting_rate"] for r in rows])
    med = np.median(pop.openness)
    assert rate[pop.openness >= med].mean() > rate[pop.openness < med].mean()   # model validity


def test_build_dataset_roundtrip_and_pipeline(tmp_path):
    from rwe.mind import MINDData
    cfg = su.SimConfig(n_users=120, max_items=200, seed=4)
    cat, pop, events, mind, mrows, prows = su.run(cfg)
    assert mind.n_users == 120 and mind.n_items == 200
    assert set(np.unique(mind.dataset.matrix.data)) <= {1.0}            # binary clicks
    assert np.allclose(mind.item_positions, cat.positions)             # GOLD lean
    assert np.allclose(mind.user_positions, pop.theta)                 # TRUE viewpoints
    npz = tmp_path / "sim.npz"
    mind.save(str(npz))
    d2 = MINDData.load(str(npz))
    dataset, theta, item_pos = d2.recommender_inputs()                 # drops into the pipeline
    assert theta.shape[0] == dataset.matrix.shape[0] >= 1
    assert "cross_cutting_rate" in mrows[0] and "viewpoint" in mrows[0]
    assert prows and all("cross_welcomed_frac" in r for r in prows)     # closed-loop columns
