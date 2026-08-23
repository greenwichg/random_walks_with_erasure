"""The settings ledger's effects view (the Recommendation Feedback redesign).

The load-bearing property is PARITY BY CONSTRUCTION: the page that says "seeing less from X" and
the rerank that dims X both read ``FEEDBACK_DIMENSIONS`` (+ ``rec_context.RAW_TO_BUCKET``), and a
signal contributes a chip only when its article resolves against the same corpus ranking resolves
against. So the pins here are: chips == the ranker's own multiplier keysets from identical
inputs; expired references appear only as honestly-marked article rows; and the raw→bucket
collapse (read_later → like) reaches the display exactly as it reaches ranking.
"""
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import api_server as engine   # noqa: E402
import rec_context            # noqa: E402


@pytest.fixture(scope="module")
def backend():
    return engine.Backend(engine.DatasetProfile.synthetic(n_users=120, max_items=300, seed=0))


def _ids(backend, n=4):
    return list(np.asarray(backend.base_corpus.mind.dataset.item_ids).astype(str)[:n])


def _row(aid, feedback, created="2026-08-23T10:00:00+00:00"):
    return {"articleId": aid, "feedback": feedback, "createdAt": created, "updatedAt": created}


def test_chips_match_the_rankers_own_multipliers(backend):
    """The parity pin: feed the SAME signals to the effects view and to _reader_state_factors;
    the chip names and directions must equal the multiplier keysets and their sides of 1.0."""
    a, b, c, d = _ids(backend)
    rows = [_row(a, "dislike"), _row(b, "more_topic"), _row(c, "read_later"),
            _row(d, "fewer_from_source")]
    out = backend.feedback_effects(rows, article_meta=None)

    fb = {k: [] for k in engine.FEEDBACK_DIMENSIONS}
    for r in rows:
        fb[rec_context.RAW_TO_BUCKET[r["feedback"]]].append(r["articleId"])
    mind = backend.base_corpus.mind
    _drop, _art, topic_mult, pub_mult = engine._reader_state_factors(mind, fb, {})

    assert {g["name"] for g in out["publishers"]} == set(pub_mult), \
        "publisher chips must be exactly the publishers ranking dims"
    norm = lambda name: name.strip().lower().replace(" ", "_")
    assert {norm(g["name"]) for g in out["topics"]} == set(topic_mult), \
        "topic chips must be exactly the topics ranking moves"
    for g in out["topics"]:
        mult = topic_mult[norm(g["name"])]
        assert (g["direction"] == "more") == (mult > 1.0), (g["name"], mult)
    for g in out["publishers"]:
        assert g["direction"] == "less" and pub_mult[g["name"]] < 1.0


def test_read_later_reaches_the_display_as_a_like(backend):
    """The raw→bucket collapse is one map for both consumers: a read_later row must produce the
    same topic chip a like would, and never an article-level entry (its article dim is None)."""
    a = _ids(backend, 1)[0]
    out = backend.feedback_effects([_row(a, "read_later")], article_meta=None)
    assert out["topics"] and out["topics"][0]["direction"] == "more"
    assert out["topics"][0]["signals"][0]["feedback"] == "read_later"   # raw type preserved
    assert out["articles"] == [], "a save is not a dismissal"


def test_expired_references_claim_no_effect(backend):
    """A rotated-out id 'matches nothing' in ranking, so it must claim nothing here either:
    no chips, only an honestly-marked article row."""
    out = backend.feedback_effects([_row("GONE_123", "dislike")], article_meta=None)
    assert out["publishers"] == [] and out["topics"] == []
    (art,) = out["articles"]
    assert art["inCatalog"] is False and art["headline"] is None and art["publisher"] is None
    assert art["feedback"] == "dislike"


def test_articles_humanize_through_the_catalog_lookup(backend, monkeypatch):
    """Where the catalog still knows the article, the dismissed list shows the human facts."""
    a = _ids(backend, 1)[0]
    monkeypatch.setattr(backend, "url_by_id", {a: f"https://example.com/{a}"}, raising=False)
    meta = lambda url: {"title": "Senate reaches budget deal", "publisher": "AP"}
    out = backend.feedback_effects([_row(a, "already_know")], article_meta=meta)
    (art,) = out["articles"]
    assert art["inCatalog"] is True and art["headline"] == "Senate reaches budget deal"
    assert art["publisher"] == "AP" and art["url"] == f"https://example.com/{a}"


def test_grouping_aggregates_and_orders_deterministically(backend):
    """Two dislikes from one outlet = ONE chip with two signals; article rows newest first."""
    mind = backend.base_corpus.mind
    outlets = np.asarray(mind.outlets).astype(str)
    ids = np.asarray(mind.dataset.item_ids).astype(str)
    pub = outlets[0]
    same = [ids[i] for i in np.flatnonzero(outlets == pub)[:2]]
    assert len(same) == 2, "fixture corpus must carry two articles from one outlet"
    rows = [_row(same[0], "dislike", "2026-08-20T10:00:00+00:00"),
            _row(same[1], "fewer_from_source", "2026-08-22T10:00:00+00:00")]
    out = backend.feedback_effects(rows, article_meta=None)
    chip = next(g for g in out["publishers"] if g["name"] == pub)
    assert len(chip["signals"]) == 2, "one chip per publisher, however many signals feed it"
    assert [a["createdAt"] for a in out["articles"]] == sorted(
        (r["createdAt"] for r in rows), reverse=True)
