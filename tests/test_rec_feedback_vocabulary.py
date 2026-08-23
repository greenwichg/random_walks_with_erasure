"""The Tier-2 feedback vocabulary end-to-end (docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md, 13.6):
five finer-grained signals — another_viewpoint / already_know / too_repetitive /
fewer_from_source / more_topic — through storage, the wire, removal (the undo), and the ranking
mapping.

The mapping contract is SCOPED REUSE of the Tier-1 anchors, no new magnitudes: each type touches
exactly the dimensions the reader named ("fewer from this source" dims the publisher without
smearing the topic; "more of this topic" lifts the topic without privileging an outlet), all
four negative types drop the named article (the dislike rationale: re-serving a card the reader
acted on would be malicious compliance), and removal restores the exact no-feedback feed because
absence is the intended state, not a tombstone.
"""
import pathlib
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import api_server as engine   # noqa: E402
import store as store_mod     # noqa: E402

VOCAB = ("another_viewpoint", "already_know", "too_repetitive",
         "fewer_from_source", "more_topic")


# ------------------------------------------------------------------ #
# storage — recording, the canonical tuple, and removal
# ------------------------------------------------------------------ #
@pytest.fixture()
def store(tmp_path):
    return store_mod.Store(f"sqlite:///{tmp_path / 'vocab.db'}")


@pytest.fixture()
def uid(store):
    return store.upsert_user_by_identity("dev", "vocab-reader").id


def test_the_canonical_tuple_carries_the_vocabulary():
    assert VOCAB == store_mod.RECOMMENDATION_FEEDBACK_TYPES[4:]
    # the declared column width fits the longest type — the String(16) → String(24) widening
    longest = max(store_mod.RECOMMENDATION_FEEDBACK_TYPES, key=len)
    assert len(longest) <= 24


def test_vocabulary_types_record_and_list(store, uid):
    for t in VOCAB:
        assert store.record_recommendation_feedback(uid, f"https://a/{t}", t) is True
        assert store.record_recommendation_feedback(uid, f"https://a/{t}", t) is False  # idempotent
    rows = store.list_recommendation_feedback(uid)
    assert sorted(r["feedback"] for r in rows) == sorted(VOCAB)
    with pytest.raises(ValueError):
        store.record_recommendation_feedback(uid, "https://a/x", "meh")


def test_removal_is_scoped_and_counts_honestly(store, uid):
    store.record_recommendation_feedback(uid, "https://a/1", "fewer_from_source")
    store.record_recommendation_feedback(uid, "https://a/1", "more_topic")
    store.record_recommendation_feedback(uid, "https://a/2", "dislike")
    other = store.upsert_user_by_identity("dev", "vocab-other").id
    store.record_recommendation_feedback(other, "https://a/1", "fewer_from_source")
    # one type
    assert store.remove_recommendation_feedback(uid, "https://a/1", "more_topic") == 1
    assert sorted(r["feedback"] for r in store.list_recommendation_feedback(uid)) == \
        ["dislike", "fewer_from_source"]
    # every type on one article
    assert store.remove_recommendation_feedback(uid, "https://a/1") == 1
    # nothing recorded → 0, never an error
    assert store.remove_recommendation_feedback(uid, "https://a/1") == 0
    # strictly user-scoped: the other reader's identical row survives
    assert [r["feedback"] for r in store.list_recommendation_feedback(other)] == \
        ["fewer_from_source"]


# ------------------------------------------------------------------ #
# the ranking mapping — _reader_state_factors, scoped reuse of the anchors
# ------------------------------------------------------------------ #
def _mind(ids, cats, outs):
    return SimpleNamespace(dataset=SimpleNamespace(item_ids=np.asarray(ids, dtype=object)),
                           categories=np.asarray(cats, dtype=object),
                           outlets=np.asarray(outs, dtype=object))


def test_fewer_from_source_dims_the_publisher_and_only_the_publisher():
    m = _mind(["A", "B"], ["Health", "Tech"], ["Acme Post", "Other"])
    drop, art, topics, pubs = engine._reader_state_factors(
        m, {"fewer_from_source": ["A"]}, {})
    assert drop == {0}, "the named article is dropped like a dislike"
    assert pubs == {"Acme Post": engine._DISLIKE_PUBLISHER_DECAY}
    assert topics == {}, "the topic is untouched — the reader named the SOURCE"
    assert art == {}


def test_more_topic_lifts_the_topic_and_never_drops():
    m = _mind(["A"], ["Health"], ["Acme Post"])
    drop, art, topics, pubs = engine._reader_state_factors(m, {"more_topic": ["A"]}, {})
    assert drop == set() and art == {} and pubs == {}
    assert topics == {"health": engine._LIKE_TOPIC_BOOST}


def test_too_repetitive_drops_and_decays_the_topic_with_the_dislike_anchor():
    m = _mind(["A"], ["Health"], ["Acme Post"])
    drop, _art, topics, pubs = engine._reader_state_factors(m, {"too_repetitive": ["A"]}, {})
    assert drop == {0}
    assert topics == {"health": engine._DISLIKE_TOPIC_DECAY}
    assert pubs == {}, "repetition says nothing about the outlet"


def test_another_viewpoint_and_already_know_drop_the_article_only():
    m = _mind(["A", "B"], ["Health", "Tech"], ["P1", "P2"])
    for t in ("another_viewpoint", "already_know"):
        drop, art, topics, pubs = engine._reader_state_factors(m, {t: ["A"]}, {})
        assert drop == {0} and art == {} and topics == {} and pubs == {}, t


def test_vocabulary_composes_with_the_existing_clamps():
    # many more_topic signals on one topic stay at the like cap, exactly as likes do
    m = _mind(["A", "B", "C"], ["Health"] * 3, ["P"] * 3)
    _d, _a, topics, _p = engine._reader_state_factors(m, {"more_topic": ["A", "B", "C"]}, {})
    assert topics == {"health": engine._LIKE_TOPIC_CAP}
    # rotated-out ids match nothing and produce no effect at all
    _d, _a, topics, pubs = engine._reader_state_factors(
        m, {"fewer_from_source": ["GONE"], "more_topic": ["GONE2"]}, {})
    assert topics == {} and pubs == {}


# ------------------------------------------------------------------ #
# the wire — POST accepts the vocabulary, DELETE is the undo
# ------------------------------------------------------------------ #
@pytest.fixture(scope="module")
def client():
    import api_fastapi
    from fastapi.testclient import TestClient
    with TestClient(api_fastapi.app) as c:   # lifespan builds the backend + store
        yield c


FB = "/api/me/recommendations/feedback"
_RUN = __import__("uuid").uuid4().hex[:8]   # rows persist in the file DB across runs


def _user(client, acct):
    uid = client.post("/api/internal/users",
                      json={"provider": "google",
                            "providerAccountId": f"{acct}-{_RUN}"}).json()["userId"]
    return uid, {"X-IH-User-Id": str(uid)}


def test_wire_accepts_the_vocabulary_and_delete_undoes(client):
    uid, hdr = _user(client, "vocab-wire")
    for t in VOCAB:
        r = client.post(FB, json={"articleId": "https://a/v", "feedback": t}, headers=hdr)
        assert r.status_code == 200 and r.json()["changed"] is True, t
    assert len(client.get(FB, headers=hdr).json()) == len(VOCAB)
    # delete one type
    r = client.request("DELETE", FB, json={"articleId": "https://a/v",
                                           "feedback": "more_topic"}, headers=hdr)
    assert r.status_code == 200 and r.json() == {"ok": True, "removed": 1}
    # delete the rest of the article's signals at once
    r = client.request("DELETE", FB, json={"articleId": "https://a/v"}, headers=hdr)
    assert r.status_code == 200 and r.json()["removed"] == len(VOCAB) - 1
    assert client.get(FB, headers=hdr).json() == []
    # deleting what was never recorded is a 0, not an error
    assert client.request("DELETE", FB, json={"articleId": "https://a/v"},
                          headers=hdr).json()["removed"] == 0


def test_delete_requires_auth_and_rejects_unknown_types(client):
    assert client.request("DELETE", FB, json={"articleId": "x"}).status_code == 401
    _uid, hdr = _user(client, "vocab-bad")
    r = client.request("DELETE", FB, json={"articleId": "x", "feedback": "meh"}, headers=hdr)
    assert r.status_code == 422
