"""Unit tests for examples/personalize.py — the per-user Measured-report layer.

Verifies the personalization layer turns a real user's *stored reads* into a Measured
Information Health Report / recommendations / coach, all computed by the **unchanged** engine
over an augmented corpus, and that the expensive augmented model is cached per
``(user_id, reading_version)``. A small synthetic backend (real pipeline, generated clicks) is
built once; each test uses a fresh in-memory store.

Two read shapes are exercised:
* **catalog reads** — reads whose ``article_id`` matches a base catalog item, so ``augment``
  reuses the column and the reader inherits its enrichment (the realistic production case,
  where a real URL matches a reference article); this populates the political + tone metrics.
* **novel reads** — reads on unseen articles (new columns); still fully scored from their own
  metadata, so topic / source / viewpoint populate even with nothing in the catalog.
"""

import dataclasses
import json
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import api_server as engine     # noqa: E402
import augmented_corpus as ac   # noqa: E402
import store as store_mod       # noqa: E402
import personalize              # noqa: E402

BUCKETS = {"left", "center", "right"}
BANDS = {"Healthy", "Fair", "Needs work", "Unknown"}


@pytest.fixture(scope="module")
def backend():
    """A small synthetic backend built once (same corpus the contract tests use)."""
    return engine.Backend(engine.DatasetProfile.synthetic(n_users=200, max_items=500, seed=0))


@pytest.fixture
def store():
    return store_mod.Store("sqlite:///:memory:")


def _catalog_reads(backend, n=8):
    """``n`` reads that reuse base *political, finite-lean, enriched* catalog columns — the
    realistic case where a real read matches a reference article and inherits its scoring."""
    ids = np.asarray(backend.mind.dataset.item_ids)
    pos = np.asarray(backend.mind.item_positions, dtype=float)
    pol = np.asarray(backend.mind.political, dtype=bool)
    reg = None if backend.register is None else np.asarray(backend.register, dtype=float)
    cols = [c for c in range(len(ids))
            if pol[c] and np.isfinite(pos[c]) and (reg is None or np.isfinite(reg[c]))]
    # span both sides so the viewpoint / echo metrics are defined
    left = [c for c in cols if pos[c] < 0][: n // 2]
    right = [c for c in cols if pos[c] > 0][: n - len(left)]
    chosen = (left + right) or cols[:n]
    return [ac.ScoredRead(article_id=str(ids[c]), political=True) for c in chosen]


def _store_reads(store, uid, reads):
    for r in reads:
        store.add_read(uid, r.article_id, dataclasses.asdict(r), r.read_at)


def _new_user(store, acct):
    return store.upsert_user_by_identity("google", acct).id


# --------------------------------------------------------------------------- #
# threshold gate
# --------------------------------------------------------------------------- #
def test_below_threshold_has_no_measured(backend, store):
    p = personalize.Personalizer(backend, store)
    uid = _new_user(store, "few-1")
    assert p.threshold == engine.ESTIMATE_MIN_READS
    assert p.has_measured(uid) is False
    _store_reads(store, uid, _catalog_reads(backend, n=3))     # below the floor
    assert p.has_measured(uid) is False


def test_threshold_reached_enables_measured(backend, store):
    p = personalize.Personalizer(backend, store)
    uid = _new_user(store, "enough-1")
    _store_reads(store, uid, _catalog_reads(backend, n=6))
    assert p.has_measured(uid) is True


# --------------------------------------------------------------------------- #
# measured report from stored reads
# --------------------------------------------------------------------------- #
def test_measured_report_is_labeled_and_valid(backend, store):
    p = personalize.Personalizer(backend, store, persist=False)
    uid = _new_user(store, "rep-1")
    _store_reads(store, uid, _catalog_reads(backend, n=8))
    rep = p.report(uid)

    # explicitly a measured report, with real coverage over the stored reads
    assert rep["mode"] == "measured"
    assert rep["coverage"]["reads"] >= engine.ESTIMATE_MIN_READS
    assert rep["coverage"]["sufficient"] is True
    assert rep["band"] in BANDS and 0 <= rep["overall"] <= 100
    # no numpy scalars / NaN leak into the payload (frontend parses this)
    assert json.loads(json.dumps(rep)) == rep
    # a reader with enough enriched, two-sided political reads gets the viewpoint metrics
    keys = {m["key"] for m in rep["metrics"]}
    assert {"topicDiversity", "sourceDiversity", "viewpointBalance", "echoChamber"} <= keys
    vp = rep["viewpoint"]
    assert set(vp) == BUCKETS and all(np.isfinite(v) for v in vp.values())


def test_measured_report_carries_metric_measurements(backend, store):
    """ADR-001: the measured path attaches per-metric Measurement envelopes (coverage + provenance)
    onto all four migrated dimensions, computed from the SAME stored reads that built the corpus —
    and never a confidence heuristic. Metrics without a measurement carry none."""
    p = personalize.Personalizer(backend, store, persist=False)
    uid = _new_user(store, "meas-1")
    _store_reads(store, uid, _catalog_reads(backend, n=8))          # 8 political reads
    rep = p.report(uid)
    by_key = {m["key"]: m for m in rep["metrics"]}

    # Every migrated dimension carries a well-formed envelope with an honest denominator and no
    # heuristic confidence. (basis, provenance) pin the per-dimension contract.
    expected = {
        "viewpointBalance": ("political_reads", {"kind": "authoritative", "source": "outlet_registry"}),
        "topicDiversity":   ("all_reads", {"kind": "derived", "source": "topic_classifier"}),
        "reportingRatio":   ("all_reads", {"kind": "derived", "source": "baseline_lexical"}),
        "emotionalBalance": ("all_reads", {"kind": "derived", "source": "baseline_lexical"}),
    }
    for key, (basis, provenance) in expected.items():
        meas = by_key[key]["measurement"]
        assert meas["coverage"]["basis"] == basis
        assert meas["provenance"] == provenance
        assert meas["coverage"]["eligible"] == 8                    # all 8 reads are eligible/political
        assert 0 <= meas["coverage"]["observed"] <= meas["coverage"]["eligible"]
        assert "confidence" not in meas                             # never a heuristic (ADR-001)

    # Register and Emotion share the same enricher provenance (both set in one enrich call).
    assert (by_key["reportingRatio"]["measurement"]["provenance"]
            == by_key["emotionalBalance"]["measurement"]["provenance"])
    # A metric with no measurement is unchanged (no envelope invented for it).
    assert "measurement" not in by_key["sourceDiversity"]
    # Still valid JSON (the frontend parses this).
    assert json.loads(json.dumps(rep)) == rep


def test_measured_report_handles_no_political_reads(backend, store):
    """A reader over the read floor but with no political reads still gets a valid report
    (undefined viewpoint renders as zeros, never NaN)."""
    p = personalize.Personalizer(backend, store, persist=False)
    uid = _new_user(store, "nopol-1")
    reads = [ac.ScoredRead(article_id=f"novel-non-political-{i}", outlet=f"blog{i}.example",
                           category="Lifestyle", political=False) for i in range(6)]
    _store_reads(store, uid, reads)
    rep = p.report(uid)
    assert rep["mode"] == "measured"
    assert json.loads(json.dumps(rep)) == rep                  # valid JSON, no NaN
    assert rep["viewpoint"] == {"left": 0.0, "center": 0.0, "right": 0.0}


# --------------------------------------------------------------------------- #
# recommendations + coach from the augmented corpus
# --------------------------------------------------------------------------- #
def test_measured_recommendations_and_coach(backend, store):
    p = personalize.Personalizer(backend, store, persist=False)
    uid = _new_user(store, "recs-1")
    _store_reads(store, uid, _catalog_reads(backend, n=8))

    recs = p.recommendations(uid)
    assert json.loads(json.dumps(recs)) == recs
    assert {r["strategy"] for r in recs} <= {"rwe-b", "rwe-d", "adaptive"}
    for r in recs:
        assert set(r) >= {"article", "reason", "strategy", "helpsMetric", "crossCutting"}
        assert "healthImpact" not in r      # Commit 21a: placeholder number removed

    greeting = p.coach_greeting(uid)
    assert isinstance(greeting, list) and greeting[0]["role"] == "assistant"
    reply = p.coach_reply(uid, "how one-sided is my reading?")
    assert reply["role"] == "assistant" and reply["content"]
    assert json.loads(json.dumps(reply)) == reply
    for c in reply["citations"]:
        assert 0 <= c["value"] <= 100


# --------------------------------------------------------------------------- #
# per-(user, reading_version) cache
# --------------------------------------------------------------------------- #
def test_model_cached_and_rebuilt_on_new_read(backend, store):
    p = personalize.Personalizer(backend, store, persist=False)
    uid = _new_user(store, "cache-1")
    _store_reads(store, uid, _catalog_reads(backend, n=6))

    m1 = p._model(uid)
    assert m1.reading_version == store.count_reads(uid)
    assert p._model(uid) is m1                                  # same version -> cached instance
    # a new read bumps reading_version -> the model is rebuilt (and the old one dropped)
    _store_reads(store, uid, [ac.ScoredRead(article_id="cache-extra-read", political=False)])
    m2 = p._model(uid)
    assert m2 is not m1 and m2.reading_version == m1.reading_version + 1


def test_persist_writes_measured_snapshot(backend, store):
    p = personalize.Personalizer(backend, store, persist=True)
    uid = _new_user(store, "persist-1")
    _store_reads(store, uid, _catalog_reads(backend, n=6))
    assert store.latest_report(uid) is None
    served = p.report(uid)                                      # building persists the snapshot
    latest = store.latest_report(uid)
    assert latest is not None and latest["mode"] == "measured"
    assert latest["overall"] == served["overall"]


# --------------------------------------------------------------------------- #
# Open-Mindedness feedback loop: cross-cutting recommendation reception -> the 8th metric.
# --------------------------------------------------------------------------- #
def _om(rep):
    # The Open-Mindedness card is always present now; return its score only when it is actually
    # available (measured), so ``_om(...) is None`` still reads as "not yet measured" for the
    # empty-state card the reader sees before any recommendation reception.
    return next((m["score"] for m in rep["metrics"]
                 if m["key"] == "openMindedness" and m.get("available", True)), None)


def _surface_and_open(store, p, uid, n_open):
    """Surface the augmented recs to the user (records the shown denominator) and open the first
    ``n_open`` cross-cutting ones. Returns the cross-cutting rec ids."""
    recs = p.recommendations(uid)
    store.record_recommendations_shown(uid, [(r["article"]["id"], r["crossCutting"]) for r in recs])
    cross = [r["article"]["id"] for r in recs if r["crossCutting"]]
    assert len(cross) >= p._openmind_min_shown, "fixture must surface enough cross-cutting recs"
    for aid in cross[:n_open]:
        store.record_recommendation_open(uid, aid, cross_cutting=True)
    return cross


def test_open_mindedness_populates_after_reception(backend, store):
    """A measured reader's Open-Mindedness card is an empty state (available: False) until they open
    cross-cutting recommendations; opening one flips it to a real score, and opening more only raises
    it. The card is always present — reception changes its availability, not the set of cards shown."""
    p = personalize.Personalizer(backend, store, persist=False)
    uid = _new_user(store, "om-1")
    _store_reads(store, uid, _catalog_reads(backend, n=8))

    rep0 = p.report(uid)
    keys0 = {m["key"] for m in rep0["metrics"]}
    om0 = next(m for m in rep0["metrics"] if m["key"] == "openMindedness")
    assert _om(rep0) is None                                        # 7/8: no reception yet, not measured
    assert om0["available"] is False and om0["reason"] == "insufficient_data"  # empty-state card, not hidden

    cross = _surface_and_open(store, p, uid, n_open=1)
    rep1 = p.report(uid)                                            # rebuilds on reception change
    assert _om(rep1) is not None                                   # 8/8: Open-Mindedness now measured
    assert next(m for m in rep1["metrics"] if m["key"] == "openMindedness")["available"] is True
    assert {m["key"] for m in rep1["metrics"]} == keys0            # same cards; only availability changed
    assert json.loads(json.dumps(rep1)) == rep1                    # still valid JSON, no NaN leak

    for aid in cross[1:]:                                          # open the rest
        store.record_recommendation_open(uid, aid, cross_cutting=True)
    rep2 = p.report(uid)
    assert _om(rep2) >= _om(rep1)                                  # more reception -> not lower


def test_open_mindedness_stays_na_when_only_surfaced(backend, store):
    """Surfacing cross-cutting recs is not enough — Open-Mindedness needs the reader to actually
    open one (an *interaction*), else the card stays an honest empty state (available: False)."""
    p = personalize.Personalizer(backend, store, persist=False)
    uid = _new_user(store, "om-surface-only")
    _store_reads(store, uid, _catalog_reads(backend, n=8))
    recs = p.recommendations(uid)
    store.record_recommendations_shown(uid, [(r["article"]["id"], r["crossCutting"]) for r in recs])
    rep = p.report(uid)
    assert _om(rep) is None                                       # shown but never opened: not measured
    om = next(m for m in rep["metrics"] if m["key"] == "openMindedness")
    assert om["available"] is False                              # present as an empty-state card


def test_model_rebuilds_on_reception_change(backend, store):
    """Opening a recommendation changes the reception version, so the cached model rebuilds even
    though the read count is unchanged (Open-Mindedness must reflect new reception)."""
    p = personalize.Personalizer(backend, store, persist=False)
    uid = _new_user(store, "om-cache")
    _store_reads(store, uid, _catalog_reads(backend, n=6))
    m1 = p._model(uid)
    _surface_and_open(store, p, uid, n_open=1)
    m2 = p._model(uid)
    assert m2 is not m1                                           # reception change rebuilt it
    assert m2.reading_version == m1.reading_version              # reads unchanged
    assert m2.reception_version != m1.reception_version


def test_reception_does_not_perturb_base_or_estimate(backend, store):
    """The feedback loop only adds the reader's own Open-Mindedness — the shared base/demo report
    (built with the population's own selective signal) is untouched by a real user's reception."""
    before = backend.report(backend.demo_user)
    p = personalize.Personalizer(backend, store, persist=False)
    uid = _new_user(store, "om-isolation")
    _store_reads(store, uid, _catalog_reads(backend, n=8))
    _surface_and_open(store, p, uid, n_open=2)
    p.report(uid)
    after = backend.report(backend.demo_user)
    before.pop("updatedAt"); after.pop("updatedAt")
    assert before == after


# --------------------------------------------------------------------------- #
# read reconstruction (the B4 ScoredRead interface, round-tripped through the store)
# --------------------------------------------------------------------------- #
def test_scored_read_reconstruction_roundtrips():
    original = ac.ScoredRead(article_id="https://x.com/a", outlet="x.com", category="Politics",
                             lean=-1.0, political=True, register=0.6,
                             emotion={"fear": 0.1, "outrage": 0.1, "analysis": 0.5,
                                      "positive": 0.2, "neutral": 0.1}, read_at="t1")
    row = dataclasses.asdict(original)
    back = personalize._scored_read_from_row(row)
    # compare by field, not by instance: sibling suites import augmented_corpus via importlib, so
    # the ScoredRead class object can differ while the interface is identical (as in test_ingest).
    assert dataclasses.asdict(back) == row
    # a NaN field (an unscored lean) survives the JSON round-trip the store performs
    nan_row = json.loads(json.dumps(dataclasses.asdict(
        ac.ScoredRead(article_id="y")), allow_nan=True))
    back2 = personalize._scored_read_from_row(nan_row)
    assert back2.article_id == "y" and np.isnan(back2.lean)
    # extra/unknown keys in a stored row are ignored (forward-compatible)
    assert personalize._scored_read_from_row({"article_id": "z", "future_field": 1}).article_id == "z"


def test_base_corpus_report_is_unchanged_by_personalization(backend, store):
    """Building a user's augmented model must not perturb the shared base corpus / demo report."""
    before = backend.report(backend.demo_user)
    p = personalize.Personalizer(backend, store, persist=False)
    uid = _new_user(store, "isolation-1")
    _store_reads(store, uid, _catalog_reads(backend, n=8))
    p.report(uid)
    after = backend.report(backend.demo_user)
    # timestamps aside, the base reader's report is identical (augment never mutates the base)
    before.pop("updatedAt"); after.pop("updatedAt")
    assert before == after


# --------------------------------------------------------------------------- #
# URL → catalog-id join (live-feed mode)
# --------------------------------------------------------------------------- #
def test_url_reads_join_catalog_columns_and_bridge(backend, store):
    """A measured reader whose stored reads are URLs of catalog articles (the live-feed reality:
    corpus ids are Q{i}, reads are canonical URLs) must land on the REAL catalog columns — a
    connected click graph and genuine RWE-B cross-cutting — not on novel island columns (the
    regression: every measured user's walk was trapped on their own reads → 0 bridging)."""
    ids = np.asarray(backend.mind.dataset.item_ids)
    pos = np.asarray(backend.mind.item_positions, dtype=float)
    pol = np.asarray(backend.mind.political, dtype=bool)
    cols = [c for c in range(len(ids)) if pol[c] and np.isfinite(pos[c]) and pos[c] < -0.5][:6]
    assert len(cols) == 6, "synthetic corpus should have >= 6 strongly-left political items"

    url_map = {str(ids[c]): f"https://pub{i}.example/story/{i}" for i, c in enumerate(cols)}
    backend.attach_url_resolver(url_map)          # exactly what live-feed mode does pre-Personalizer
    try:
        p = personalize.Personalizer(backend, store, threshold=5, persist=False)
        uid = _new_user(store, "url-join")
        _store_reads(store, uid, [ac.ScoredRead(article_id=url_map[str(ids[c])], political=True)
                                  for c in cols])

        m = p._model(uid)
        # joined: no novel columns appended (the matrix keeps the catalog width)…
        assert m.corpus.mind.dataset.matrix.shape[1] == backend.mind.dataset.matrix.shape[1]
        # …the reader's row sits on their 6 real catalog columns…
        assert m.corpus.mind.dataset.matrix.tocsr()[m.reader_row].nnz == 6
        # …and the connected walk yields genuine cross-cutting bridging for this one-sided reader.
        recs = p.recommendations(uid)
        assert recs, "measured recommendations expected"
        assert sum(1 for r in recs if r["crossCutting"]) > 0
    finally:
        backend.url_by_id = {}                    # leave the shared module fixture untouched


def test_unknown_urls_still_append_novel_columns(backend, store):
    """Without a URL map (synthetic/MIND corpora, or a read of an article outside the catalog),
    URL-identified reads keep the previous behaviour: appended as novel columns."""
    p = personalize.Personalizer(backend, store, threshold=5, persist=False)
    uid = _new_user(store, "url-novel")
    _store_reads(store, uid, [ac.ScoredRead(article_id=f"https://elsewhere.example/{i}",
                                            outlet=f"O{i}", category="Politics",
                                            lean=-1.0, political=True) for i in range(5)])
    m = p._model(uid)
    assert (m.corpus.mind.dataset.matrix.shape[1]
            == backend.mind.dataset.matrix.shape[1] + 5)
