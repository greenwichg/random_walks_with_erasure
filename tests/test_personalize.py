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
        assert set(r) >= {"article", "reason", "strategy", "healthImpact", "helpsMetric", "crossCutting"}

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
