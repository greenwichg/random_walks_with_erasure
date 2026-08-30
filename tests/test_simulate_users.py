"""Tests for examples/simulate_users.py (agent-based synthetic-user simulator).

Uses the fully-synthetic catalog (no Qbias needed). Covers trait ranges, determinism,
the MINDData round-trip into the pipeline, and the key model-validity check: agents with
higher openness actually click more cross-cutting content."""

import collections
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
    # Commit R2: uncategorized stays "" — no synthesized "general" topic
    assert su._first_tag("") == "" and su._first_tag(None) == ""
    assert su._first_tag("[]") == ""


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
    e1, imp1 = su.simulate(cat, pop, cfg)
    e2, imp2 = su.simulate(cat, pop, cfg)
    assert len(e1) > 0 and e1 == e2 and imp1 == imp2                    # deterministic given seed
    assert {u for u, _, _, _ in e1} <= set(range(80))
    assert {a for _, _, _, a in e1} <= {"ignore", "save", "share"}
    assert all(d > 0 for _, _, d, _ in e1)                             # positive dwell
    # every clicked event appears as a `-1` in some slate impression
    clicked_in_imps = {(u, it) for u, slate in imp1 for it, c in slate if c}
    assert {(u, it) for u, it, _, _ in e1} <= clicked_in_imps


def test_enrichments_present_and_normalised():
    cat = su.synthetic_catalog(n_items=100, seed=5)
    assert cat.register.shape == (100,) and (cat.register >= 0).all() and (cat.register <= 1).all()
    assert cat.emotion.shape == (100, len(su.EMOTION_LABELS))
    assert np.allclose(cat.emotion.sum(axis=1), 1.0)                   # per-article shares


def test_openness_increases_cross_cutting():
    cfg = su.SimConfig(n_users=400, max_items=250, seed=3, sessions_lambda=12.0)
    cat = su.synthetic_catalog(n_items=250, seed=3)
    pop = su.sample_population(cat, cfg)
    events, _ = su.simulate(cat, pop, cfg)
    rows, _ = su.population_metrics(events, cat, pop, cfg)
    rate = np.array([r["cross_cutting_rate"] for r in rows])
    med = np.median(pop.openness)
    assert rate[pop.openness >= med].mean() > rate[pop.openness < med].mean()   # model validity


def test_build_dataset_roundtrip_and_enrichment_files(tmp_path):
    from rwe.mind import MINDData
    cfg = su.SimConfig(n_users=120, max_items=200, seed=4)
    cat, pop, events, impressions, mind, mrows, prows = su.run(cfg)
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
    # enrichment writers produce files health_report._load_item_csv can join
    from importlib import import_module  # noqa: F401
    su.write_behaviors_tsv(str(tmp_path / "beh.tsv"), impressions, cat)
    su.write_enrichment_csvs(str(tmp_path / "reg.csv"), str(tmp_path / "emo.csv"), cat)
    reg = open(tmp_path / "reg.csv").readline().strip()
    emo = open(tmp_path / "emo.csv").readline().strip()
    assert reg == "news_id,reporting" and emo == "news_id," + ",".join(su.EMOTION_LABELS)
    beh_first = open(tmp_path / "beh.tsv").readline().split("\t")
    assert beh_first[1].startswith("sim_u") and "-" in beh_first[4]     # MIND impressions format


# --------------------------------------------------------------------------- #
# Recency-weighted corpus subsample (RWE_REC_RECENCY_HALFLIFE_DAYS).
# --------------------------------------------------------------------------- #
def _age_corpus(tmp_path, days=30, per_day=60):
    """A dated catalogue shaped like the live one: many publishers, a month of history."""
    import csv as _c
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    path = tmp_path / "aged.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = _c.writer(f)
        w.writerow(["title", "source", "bias_rating", "tags", "url", "political", "country",
                    "published_at"])
        for d in range(days):
            for k in range(per_day):
                w.writerow([f"headline {d}-{k}", f"Outlet{k % 12}",
                            ("left", "center", "right")[k % 3], "Politics",
                            f"https://a{d}-{k}.example/x", "1", "US",
                            (now - timedelta(days=d, hours=k % 24)).isoformat()])
    return str(path), now


def _ages_of(catalog, path, now):
    import csv as _c
    from datetime import datetime
    rows = list(_c.DictReader(open(path, encoding="utf-8")))
    out = []
    for ident in catalog.ids:
        when = datetime.fromisoformat(rows[int(str(ident)[1:])]["published_at"])
        out.append((now - when).total_seconds() / 86400.0)
    return np.array(out)


def test_recency_weighting_is_off_by_default(tmp_path, monkeypatch):
    """The shipped default must be the uniform draw, byte for byte — this changes nothing until
    an operator turns it on."""
    monkeypatch.delenv("RWE_REC_RECENCY_HALFLIFE_DAYS", raising=False)
    assert su.recency_halflife_days() == 0.0
    path, _ = _age_corpus(tmp_path)
    a = su.catalog_from_qbias(path, max_items=400, seed=0)
    monkeypatch.setenv("RWE_REC_RECENCY_HALFLIFE_DAYS", "0")
    b = su.catalog_from_qbias(path, max_items=400, seed=0)
    assert list(a.ids) == list(b.ids), "0 must be identical to unset, not merely similar"


def test_a_half_life_makes_the_pool_measurably_newer(tmp_path, monkeypatch):
    path, now = _age_corpus(tmp_path)
    monkeypatch.delenv("RWE_REC_RECENCY_HALFLIFE_DAYS", raising=False)
    uniform = _ages_of(su.catalog_from_qbias(path, max_items=400, seed=0), path, now)
    monkeypatch.setenv("RWE_REC_RECENCY_HALFLIFE_DAYS", "3")
    weighted = _ages_of(su.catalog_from_qbias(path, max_items=400, seed=0), path, now)

    # The claim is the whole distribution moving, not one lucky draw: a uniform sample of a 30-day
    # window sits near 15 days, and a 3-day half-life must pull the median well under it.
    assert np.median(uniform) > 12.0, "fixture: the uniform draw should span the window"
    assert np.median(weighted) < np.median(uniform) / 2
    assert (weighted < 7).mean() > (uniform < 7).mean() * 2


def test_the_weighting_does_not_starve_publishers_or_a_side(tmp_path, monkeypatch):
    """The guardrail on the knob. Recency weights ARTICLES, not publishers — a prolific outlet's
    old articles are discounted exactly as much as a rare outlet's — so representation must hold
    even at an aggressive half-life. Measured, because the intuition runs the other way."""
    import csv as _c
    path, _ = _age_corpus(tmp_path)
    rows = list(_c.DictReader(open(path, encoding="utf-8")))
    monkeypatch.setenv("RWE_REC_RECENCY_HALFLIFE_DAYS", "1")
    cat = su.catalog_from_qbias(path, max_items=400, seed=0)

    assert len(set(str(o) for o in cat.outlets)) == 12, "every publisher still reaches the pool"
    leans = collections.Counter(rows[int(str(i)[1:])]["bias_rating"] for i in cat.ids)
    assert min(leans.values()) > 0.25 * max(leans.values()), f"a side was starved: {leans}"


def test_an_undated_article_gets_the_median_weight_not_the_best(tmp_path, monkeypatch):
    """A missing timestamp must not buy a slot, and must not cost one either.

    Treating it as newest would let a publisher whose feed omits dates outrank everyone; treating
    it as oldest would silently drop that publisher entirely. Both are worse than saying "unknown"
    and drawing on ordinary terms."""
    w = su._recency_weights(["2026-08-30T00:00:00+00:00", None,
                             "2026-07-01T00:00:00+00:00"], 3.0)
    assert w is not None
    assert w[0] > w[1] > w[2], f"undated must sit between the dated extremes: {w}"
    # No date anywhere -> no ordering to invent, so the caller falls back to the uniform draw.
    assert su._recency_weights([None, "", "not-a-date"], 3.0) is None


def test_a_corpus_without_the_column_still_builds(tmp_path, monkeypatch):
    """The static qbias dataset has no published_at. A half-life set against it must be inert
    rather than an error — the column is appended, and appended columns are optional."""
    import csv as _c
    path = tmp_path / "undated.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = _c.writer(f)
        w.writerow(["title", "source", "bias_rating", "tags", "url", "political", "country"])
        for k in range(200):
            w.writerow([f"h{k}", f"Outlet{k % 8}", ("left", "center", "right")[k % 3],
                        "Politics", f"https://u{k}.example/x", "1", "US"])
    monkeypatch.setenv("RWE_REC_RECENCY_HALFLIFE_DAYS", "3")
    cat = su.catalog_from_qbias(str(path), max_items=50, seed=0)
    assert cat.n == 50


def test_a_junk_half_life_is_off_rather_than_a_crash(monkeypatch):
    for bad in ("", "abc", "-5", "0", "nan", "inf"):
        monkeypatch.setenv("RWE_REC_RECENCY_HALFLIFE_DAYS", bad)
        assert su.recency_halflife_days() == 0.0, bad
