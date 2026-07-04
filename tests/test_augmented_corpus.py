"""Unit tests for examples/augmented_corpus.py (Milestone B4).

Verify the abstraction that places a real reader into the reference corpus: the base is never
mutated, the existing readers/items are preserved, and the **unchanged** Information Health
Report + RWE recommender run on the augmented corpus and yield a valid result for the new
reader. No endpoint is exercised — this module is not wired into the product.
"""

import importlib.util
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


api_server = _load("api_server", "examples/api_server.py")   # inserts examples/ on sys.path
ac = _load("augmented_corpus", "examples/augmented_corpus.py")
import health_report as hr   # noqa: E402  (available after api_server put examples/ on the path)
from rwe import RWEB, FeedbackGraph   # noqa: E402


@pytest.fixture(scope="module")
def base():
    backend = api_server.Backend(api_server.DatasetProfile.synthetic(n_users=150, max_items=400, seed=0))
    return backend, ac.bundle_from_backend(backend)


def _reads(existing_ids):
    """Two existing-catalog reads (reuse their columns) + five novel articles; four of the
    novel are political with finite, spread leans so the viewpoint metric is defined."""
    return [
        ac.ScoredRead(article_id=existing_ids[0], outlet="Reuters", category="world", lean=0.0),
        ac.ScoredRead(article_id=existing_ids[1], outlet="AP", category="world", lean=0.0),
        ac.ScoredRead(article_id="novel-L1", outlet="Vox", category="politics", lean=-1.5, political=True,
                      emotion={"fear": 0.2, "outrage": 0.2, "analysis": 0.4, "positive": 0.1, "neutral": 0.1},
                      register=0.5, confidence=0.7),
        ac.ScoredRead(article_id="novel-L2", outlet="CNN", category="politics", lean=-1.0, political=True),
        ac.ScoredRead(article_id="novel-R1", outlet="Fox", category="politics", lean=1.0, political=True),
        ac.ScoredRead(article_id="novel-R2", outlet="National Review", category="economy", lean=1.5, political=True),
        ac.ScoredRead(article_id="novel-T", outlet="Wired", category="tech", lean=-0.3),
    ]


def test_empty_reads_raises(base):
    _, bundle = base
    with pytest.raises(ValueError):
        ac.augment(bundle, [])


def test_augment_appends_reader_without_mutating_base(base):
    backend, bundle = base
    A0 = backend.mind.dataset.matrix.tocsr()
    m0, n0 = A0.shape
    ids = [str(i) for i in np.asarray(backend.mind.dataset.item_ids)[:2]]

    result = ac.augment(bundle, _reads(ids))
    aug = result.bundle.mind

    # base corpus is untouched
    assert backend.mind.dataset.matrix.shape == (m0, n0)
    assert backend.mind.n_items == n0

    # one new reader + five novel article columns
    assert aug.n_users == m0 + 1
    assert aug.n_items == n0 + 5
    assert result.reader_row == m0

    # existing readers/items are preserved exactly (the reference population is intact)
    A1 = aug.dataset.matrix.tocsr()
    assert (A1[:m0, :n0] != A0).nnz == 0
    assert list(aug.categories[:n0]) == list(backend.mind.categories)
    assert list(aug.outlets[:n0]) == list(backend.mind.outlets)

    # the new reader clicked exactly its seven de-duplicated reads
    assert int(A1[m0].sum()) == 7

    # enrichment arrays were extended to the augmented width
    assert result.bundle.register.shape[0] == n0 + 5
    assert result.bundle.confidence.shape[0] == n0 + 5
    for arr in result.bundle.emotion.values():
        assert arr.shape[0] == n0 + 5


def test_existing_algorithms_run_unchanged_on_augmented(base):
    _, bundle = base
    ids = [str(i) for i in np.asarray(bundle.mind.dataset.item_ids)[:2]]
    result = ac.augment(bundle, _reads(ids))
    b = result.bundle

    # Information Health Report — the unmodified compute / user_report
    pop = hr.compute(b.mind, register=b.register, emotion=b.emotion, confidence=b.confidence)
    rep = hr.user_report(pop, b.mind, result.reader_row)
    assert rep["n_clicks"] == 7
    assert 0 <= (rep["overall"] or 0) <= 100
    left, center, right = rep["viewpoint"]
    assert np.isfinite(left) and left > 0 and right > 0          # the reader read both sides

    # Recommendations — the unmodified RWE-B over the augmented graph
    rec_ds, theta, item_pos = b.mind.recommender_inputs()
    row = int(np.flatnonzero(np.asarray(rec_ds.user_ids) == "__real_user__")[0])
    recs = RWEB(FeedbackGraph(rec_ds.matrix), theta, item_pos, epsilon=0.9).recommend(
        np.array([row]), top_k=5)[0]
    assert recs.shape[0] == 5 and (recs >= 0).any()
