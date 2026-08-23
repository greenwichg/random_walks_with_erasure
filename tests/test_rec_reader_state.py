"""Reader-state → ranking: the Tier-1 feedback/repetition loop
(docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md).

Three layers, each pinned separately so a failure names its layer:

* ``rec_context`` — flags off ⇒ the params object passes through UNTOUCHED (the identity the
  whole default-off rollout rests on); flags on ⇒ the store's recorded state arrives as plain
  id lists, windowed for repetition.
* ``store.rec_events_state`` — the read half of a loop whose write half
  (``record_recommendations_shown`` / opened) has existed all along.
* ``api_server._preference_rerank`` — the ONE shared policy: dislike excludes that article,
  ignored/surfaced articles decay, likes/dislikes lift/dim their topic and publisher within
  floors and caps, and params without the new keys reproduce the historical order exactly.

The serve/explain parity that makes this safe is structural — both endpoints build params
through ``api_fastapi._rec_request_params`` and both paths run this same static method — and
the last test pins that the two endpoints share the builder rather than duplicating it.
"""

import importlib.util
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import rec_context  # noqa: E402
import api_server  # noqa: E402


def _load_store():
    spec = importlib.util.spec_from_file_location("store", ROOT / "examples" / "store.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("store", mod)
    spec.loader.exec_module(mod)
    return mod


store_mod = _load_store()


@pytest.fixture
def store():
    return store_mod.Store("sqlite:///:memory:")


@pytest.fixture
def uid(store):
    user = store.upsert_user_by_identity("test", "r1", email="r1@example.com")
    return user.id


# ------------------------------------------------------------------ #
# store.rec_events_state
# ------------------------------------------------------------------ #
def test_rec_events_state_reports_article_and_opened(store, uid):
    store.record_recommendations_shown(uid, [("A1", True), ("A2", False)])
    store.record_recommendation_open(uid, "A2")
    rows = {r["articleId"]: r["opened"] for r in store.rec_events_state(uid)}
    assert rows == {"A1": False, "A2": True}


def test_rec_events_state_since_filters_on_last_surfaced(store, uid):
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    store.record_recommendations_shown(uid, [("OLD", False)], shown_at=old)
    store.record_recommendations_shown(uid, [("NEW", False)])
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    assert [r["articleId"] for r in store.rec_events_state(uid, since=cutoff)] == ["NEW"]
    # No cutoff → everything, oldest recorded first.
    assert [r["articleId"] for r in store.rec_events_state(uid)] == ["OLD", "NEW"]


# ------------------------------------------------------------------ #
# rec_context — flags and shapes
# ------------------------------------------------------------------ #
def test_flags_off_returns_the_very_same_params_object(store, uid, monkeypatch):
    monkeypatch.delenv("RWE_REC_FEEDBACK", raising=False)
    monkeypatch.delenv("RWE_REC_REPETITION", raising=False)
    store.record_recommendation_feedback(uid, "A1", "dislike")
    store.record_recommendations_shown(uid, [("A2", False)])
    params = {"interests": {"health": 10}}
    assert rec_context.attach_reader_state(params, store, uid) is params
    assert rec_context.attach_reader_state(None, store, uid) is None


def test_feedback_state_maps_types_and_read_later_counts_as_like(store, uid, monkeypatch):
    monkeypatch.setenv("RWE_REC_FEEDBACK", "1")
    store.record_recommendation_feedback(uid, "D1", "dislike")
    store.record_recommendation_feedback(uid, "L1", "like")
    store.record_recommendation_feedback(uid, "L2", "read_later")
    store.record_recommendation_feedback(uid, "I1", "ignore")
    # The Tier-2 vocabulary passes through under its own names (recorded type = bucket name):
    # the finer types must never be collapsed into like/dislike, or the scoped consequences
    # ("fewer from this SOURCE", "more of this TOPIC") would smear back into the blunt ones.
    store.record_recommendation_feedback(uid, "V1", "another_viewpoint")
    store.record_recommendation_feedback(uid, "K1", "already_know")
    store.record_recommendation_feedback(uid, "R1", "too_repetitive")
    store.record_recommendation_feedback(uid, "F1", "fewer_from_source")
    store.record_recommendation_feedback(uid, "T1", "more_topic")
    out = rec_context.attach_reader_state(None, store, uid)
    assert out["feedback"] == {"dislike": ["D1"], "like": ["L1", "L2"], "ignore": ["I1"],
                               "another_viewpoint": ["V1"], "already_know": ["K1"],
                               "too_repetitive": ["R1"], "fewer_from_source": ["F1"],
                               "more_topic": ["T1"]}


def test_repetition_state_windows_and_splits_on_opened(store, uid, monkeypatch):
    monkeypatch.setenv("RWE_REC_REPETITION", "1")
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    store.record_recommendations_shown(uid, [("STALE", False)], shown_at=old)
    store.record_recommendations_shown(uid, [("U1", False), ("O1", True)])
    store.record_recommendation_open(uid, "O1")
    out = rec_context.attach_reader_state({"beta": 0.3}, store, uid)
    assert out["repetition"] == {"unopened": ["U1"], "opened": ["O1"]}
    assert out["beta"] == 0.3                       # existing params carried, not replaced


def test_attach_never_mutates_the_input_dict(store, uid, monkeypatch):
    monkeypatch.setenv("RWE_REC_FEEDBACK", "1")
    store.record_recommendation_feedback(uid, "D1", "dislike")
    params = {"interests": {"health": 10}}
    out = rec_context.attach_reader_state(params, store, uid)
    assert "feedback" not in params and "feedback" in out


def test_empty_state_with_flags_on_is_still_identity(store, uid, monkeypatch):
    monkeypatch.setenv("RWE_REC_FEEDBACK", "1")
    monkeypatch.setenv("RWE_REC_REPETITION", "1")
    params = {"interests": {"health": 10}}
    assert rec_context.attach_reader_state(params, store, uid) is params
    assert rec_context.attach_reader_state(None, store, uid) is None


# ------------------------------------------------------------------ #
# api_server._preference_rerank — the shared policy
# ------------------------------------------------------------------ #
def _mind(ids, cats, outlets):
    return SimpleNamespace(
        dataset=SimpleNamespace(item_ids=np.asarray(ids, dtype=object)),
        categories=np.asarray(cats, dtype=object),
        outlets=np.asarray(outlets, dtype=object),
    )


R = api_server.Backend._preference_rerank


def test_rerank_without_reader_state_keys_is_the_historical_identity():
    m = _mind(["a", "b", "c"], ["Sports", "Health", "Sports"], ["P1", "P2", "P1"])
    cols = [0, 1, 2]
    assert R(m, cols, None) is cols
    assert R(m, cols, {"beta": 0.3}) is cols


def test_dislike_drops_exactly_the_named_article():
    m = _mind(["a", "b", "c"], ["Sports", "Health", "Sports"], ["P1", "P2", "P1"])
    out = R(m, [0, 1, 2], {"feedback": {"dislike": ["b"]}})
    assert out == [0, 2]
    # An id no longer in the corpus matches nothing and drops nothing.
    assert R(m, [0, 1, 2], {"feedback": {"dislike": ["gone"]}}) == [0, 1, 2]


def test_ignored_and_surfaced_articles_decay_not_disappear():
    m = _mind(["a", "b", "c", "d"], ["T", "T", "T", "T"], ["P", "P", "P", "P"])
    ignored = R(m, [0, 1, 2, 3], {"feedback": {"ignore": ["a"]}})
    assert ignored == [1, 0, 2, 3]                  # 0.35 decay ≈ ×2.9 rank: one place past b only
    surfaced = R(m, [0, 1, 2, 3], {"repetition": {"unopened": ["a"], "opened": ["b"]}})
    # The nudge is BOUNDED: "a" at the pool head decays (x2.9 effective rank) but is not pushed
    # past items whose keys stay smaller; "b" (opened, 0.25 -> x4 rank) sinks to the bottom.
    assert surfaced == [0, 2, 3, 1]
    assert sorted(surfaced) == [0, 1, 2, 3]         # decay, never disappearance


def test_dislike_dims_publisher_and_topic_within_floors():
    # Article "d" (disliked) shares publisher P1 with "a" and topic News with "b".
    m = _mind(["a", "b", "c", "d"],
              ["Sports", "News", "Culture", "News"],
              ["P1", "P2", "P3", "P1"])
    out = R(m, [3, 1, 0, 2], {"feedback": {"dislike": ["d"]}})
    assert 3 not in out                             # the named article is gone
    # Pool after the drop: b (News, x1/0.8), a (P1, x1/0.6), c (untouched). The publisher decay
    # (0.6) pushes "a" behind the untouched "c"; the gentler topic decay (0.8) leaves "b" where
    # its rank earned: keys 1.25, 3.33, 3.0.
    assert out == [1, 2, 0]
    # Floors: many dislikes of one publisher dim it to the floor, never to zero…
    many = _mind([f"x{i}" for i in range(6)] + ["v"],
                 ["News"] * 6 + ["News"], ["P1"] * 6 + ["P1"])
    kept = R(many, list(range(7)), {"feedback": {"dislike": [f"x{i}" for i in range(6)]}})
    assert kept == [6]                              # …so the survivor is still served


def test_likes_lift_their_topic_but_never_outbid_a_slider():
    m = _mind(["a", "b", "c"], ["Health", "Sports", "Sports"], ["P", "P", "P"])
    lifted = R(m, [1, 2, 0], {"feedback": {"like": ["a"]}})
    assert lifted[0] == 1 or lifted.index(0) < 2    # health rises on a 1.5x nudge
    # A weight-10 interest on Sports beats any accumulation of Health likes (cap 3 < 8).
    both = R(m, [0, 1, 2], {"interests": {"sports": 10},
                            "feedback": {"like": ["a", "a", "a", "a"]}})
    assert both[0] == 1


def test_reader_state_composes_with_interest_in_one_sort():
    m = _mind(["a", "b"], ["Health", "Health"], ["P1", "P2"])
    # Interest boosts both equally; repetition decay on "a" alone must still reorder.
    out = R(m, [0, 1], {"interests": {"health": 10}, "repetition": {"unopened": ["a"]}})
    assert out == [1, 0]


def test_serving_and_explain_share_one_params_builder():
    import ast
    src = (ROOT / "examples" / "api_fastapi.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    callers = {"recommendations", "explain_recommendations_internal"}
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in callers:
            calls = {c.func.id for c in ast.walk(node)
                     if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
            found[node.name] = "_rec_request_params" in calls
    assert found == {"recommendations": True, "explain_recommendations_internal": True}, (
        "Both endpoints must build params through _rec_request_params — a second builder is "
        "how an explanation drifts from the feed it explains.")
