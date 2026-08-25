"""The banded semantic event-identity mechanism (event_identity + story_service + store).

The load-bearing properties, each pinned: the build NEVER calls a network and is byte-identical
to production whenever the judge is off or a pair is unjudged (fail-open); only a confident
``different_event`` verdict removes an edge, and only inside the ambiguity band; band pairs
without verdicts are emitted once, snapshotted, and drained out-of-band; transport failures fail
CLOSED to retryable api-error rows that never influence clustering; and a decisive verdict whose
quoted spans are not substrings of the texts is demoted, never trusted.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "examples"))

import clustering        # noqa: E402
import event_identity    # noqa: E402
import store as store_mod  # noqa: E402
import story_service     # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures: the production weld, as a buildable corpus.
# --------------------------------------------------------------------------- #
def _art(url, headline, dek="", at="2026-08-21T09:00:00+00:00", publisher="P1"):
    """Article-dict shape — what the closures see inside the build."""
    return {"id": url, "url": url, "canonicalUrl": url, "headline": headline,
            "description": dek, "publishedAt": at, "publisher": publisher}


def _row(url, headline, dek="", at="2026-08-21T09:00:00+00:00", publisher="P1"):
    """FeedArticle ROW shape — what build_stories ingests (feed_article_to_article's input)."""
    return {"canonicalUrl": url, "url": url, "title": headline, "description": dek,
            "publishedAt": at, "publisher": publisher, "scored": {}}


def _recall_arts():
    """Two genuine eye-drop articles + the boilerplate bridge + the fruit-bar article: the bridge
    welds fruit into the eye-drop story lexically (in band), unless a verdict says otherwise."""
    return [
        _art("https://x.com/eye1", "Nearly 40,000 bottles of eye drops recalled over possible "
                                   "contamination nationwide", publisher="P1"),
        _art("https://x.com/eye2", "Eye drops recalled nationwide over possible contamination, "
                                   "FDA warns", publisher="P2"),
        _art("https://x.com/fruit", "Frozen fruit bars recalled nationwide over possible glass "
                                    "contamination", publisher="P3"),
    ]


def _recall_rows():
    return [_row(a["url"], a["headline"], a["description"], a["publishedAt"], a["publisher"])
            for a in _recall_arts()]


def _pk(a, b):
    return event_identity.pair_key(a, b)


# --------------------------------------------------------------------------- #
# pair_key + prompt mechanics.
# --------------------------------------------------------------------------- #
def test_pair_key_is_order_independent_and_rubric_versioned():
    assert _pk("u1", "u2") == _pk("u2", "u1")
    assert _pk("u1", "u2") != _pk("u1", "u3")
    assert _pk("u1", "u2").startswith(event_identity.RUBRIC_VERSION + ":")


def test_quote_verification_is_mechanical():
    art = {"headline": "Frozen fruit bars recalled", "description": "Glass shards were found.",
           "publishedAt": "2026-08-19"}
    assert event_identity.quote_ok("fruit bars RECALLED", art)
    assert event_identity.quote_ok("glass  shards", art), "whitespace-normalized"
    assert not event_identity.quote_ok("eye drops", art), "words the article does not contain"
    assert not event_identity.quote_ok("", art)


# --------------------------------------------------------------------------- #
# The closure: band membership, veto direction, fail-open, band emission.
# --------------------------------------------------------------------------- #
def test_closure_vetoes_only_confident_different_inside_the_band():
    arts = _recall_arts()
    veto_key = _pk(arts[1]["url"], arts[2]["url"])
    stats, band = {}, {}
    ok = story_service._event_identity_closure(
        arts, 0, False, {veto_key: "different_event"}, 0.5, stats, band)
    assert not ok(1, 2), "a confident different_event removes the in-band edge"
    assert stats["eventEdgeVetoed"] == 1
    assert ok(0, 1), "the genuine pair is untouched"


def test_closure_fails_open_and_emits_unjudged_band_pairs():
    arts = _recall_arts()
    band = {}
    ok = story_service._event_identity_closure(arts, 0, False, {}, 0.5, {}, band)
    assert ok(1, 2), "no verdict -> edge behaves exactly as production"
    key = _pk(arts[1]["url"], arts[2]["url"])
    assert key in band and band[key]["title_b"].startswith("Frozen fruit bars")
    assert band[key]["title_a"].startswith("Eye drops"), "snapshots carry what the build saw"


def test_closure_never_asks_above_the_band():
    a = _art("https://x.com/a", "Berlin pride event canceled after vehicle incident")
    b = _art("https://x.com/b", "Vehicle incident: Berlin pride event canceled")
    band = {}
    # even a (wrong) different_event verdict is IGNORED above the band: tokens decide there
    key = _pk(a["url"], b["url"])
    ok = story_service._event_identity_closure([a, b], 0, False, {key: "different_event"},
                                               0.5, {}, band)
    assert ok(0, 1), "high-overlap edges are decided lexically, the judge is never consulted"
    assert not band, "and nothing is queued for them"


def test_uncertain_changes_nothing():
    arts = _recall_arts()
    key = _pk(arts[1]["url"], arts[2]["url"])
    ok = story_service._event_identity_closure(arts, 0, False, {key: "uncertain"}, 0.5, {}, {})
    assert ok(1, 2)


# --------------------------------------------------------------------------- #
# The build: fail-open byte-identity; a verdict splits the weld; determinism.
# --------------------------------------------------------------------------- #
def test_build_without_verdicts_is_byte_identical_to_production():
    rows = _recall_rows()
    base = story_service.build_stories(rows)
    off = story_service.build_stories(rows, event_verdicts=None)
    empty = story_service.build_stories(rows, event_verdicts={}, band_out={})
    assert base == off == empty, "judge off / no verdicts -> exactly today's clustering"


def test_a_different_event_verdict_separates_the_weld_and_is_deterministic():
    rows = _recall_rows()
    welded = story_service.build_stories(rows)
    assert any(len(s["coverage"]) == 3 for s in welded), \
        "precondition: the bridge welds all three lexically"
    verdicts = {_pk(rows[1]["url"], rows[2]["url"]): "different_event",
                _pk(rows[0]["url"], rows[2]["url"]): "different_event"}
    split = story_service.build_stories(rows, event_verdicts=verdicts)
    sizes = sorted(len(s["coverage"]) for s in split)
    assert sizes == [2], "the eye-drop pair stands; the fruit-bar article is no longer welded"
    assert split == story_service.build_stories(rows, event_verdicts=verdicts), "deterministic"


def test_band_emission_flows_out_of_the_build():
    rows = _recall_rows()
    band = {}
    story_service.build_stories(rows, event_verdicts={}, band_out=band)
    assert any(v["title_b"].startswith("Frozen fruit bars")
               or v["title_a"].startswith("Frozen fruit bars") for v in band.values())


# --------------------------------------------------------------------------- #
# Store roundtrip + the worker.
# --------------------------------------------------------------------------- #
@pytest.fixture()
def st(tmp_path):
    return store_mod.Store(f"sqlite:///{tmp_path}/t.db")


def _queue_one(st):
    key = _pk("https://x.com/eye2", "https://x.com/fruit")
    st.enqueue_event_pairs([{
        "pair_key": key, "url_a": "https://x.com/eye2", "url_b": "https://x.com/fruit",
        "title_a": "Eye drops recalled nationwide over possible contamination, FDA warns",
        "dek_a": "", "published_a": "2026-08-21",
        "title_b": "Frozen fruit bars recalled nationwide over possible glass contamination",
        "dek_b": "", "published_b": "2026-08-19"}])
    return key


def test_store_queue_roundtrip(st):
    key = _queue_one(st)
    assert st.enqueue_event_pairs([{"pair_key": key, "url_a": "x", "url_b": "y"}]) == 0, \
        "first snapshot wins; re-asking is a no-op"
    (pending,) = st.pending_event_pairs()
    assert pending["pair_key"] == key
    assert st.event_verdicts() == {}, "pending rows never influence a build"
    assert st.record_event_verdict(key, "different_event", source="model", model="fake")
    assert st.event_verdicts() == {key: "different_event"}
    assert st.pending_event_pairs() == [], "judged rows leave the queue"
    assert not st.record_event_verdict("v1:unknown", "same_event", source="model"), \
        "a verdict without a question is not recorded"


class _FakeAdapter:
    name = "fake"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.seen = []

    def verdict(self, a, b):
        self.seen.append((a["headline"], b["headline"]))
        return self.outcomes.pop(0)


def test_worker_drains_and_persists(st):
    key = _queue_one(st)
    fake = _FakeAdapter([{"verdict": "different_event", "quote_a": "x", "quote_b": "y"}])
    done = event_identity.judge_pending(st, fake, budget=10)
    assert done["judged"] == 1 and done["different"] == 1
    assert st.event_verdicts() == {key: "different_event"}
    assert fake.seen[0][0].startswith("Eye drops"), "the worker judged the queued snapshots"


def test_worker_api_errors_fail_closed_and_stay_retryable(st):
    key = _queue_one(st)
    fake = _FakeAdapter([{"verdict": "uncertain", "_api_error": "HTTP 529"}])
    done = event_identity.judge_pending(st, fake, budget=10)
    assert done["api_error"] == 1 and st.event_verdicts() == {}, \
        "transport trouble never becomes a verdict a build can see"
    assert st.pending_event_pairs(retry_after_hours=0.0), "and the pair is retried after cooldown"
    assert st.pending_event_pairs(retry_after_hours=1.0) == [], "but not before it"
    assert key not in st.event_verdicts()


def test_judge_defaults_are_off_and_key_gated(monkeypatch):
    monkeypatch.delenv("RWE_EVENT_JUDGE", raising=False)
    assert event_identity.judge_on() is False
    monkeypatch.setenv("RWE_EVENT_JUDGE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    judge = event_identity.EventJudge(store_=None, log=None)
    assert judge.start() is False, "flag without key must not start a worker"


def test_event_inputs_resolve_off_to_none(monkeypatch, st):
    monkeypatch.delenv("RWE_EVENT_JUDGE", raising=False)
    assert story_service._event_inputs(st) == (None, None)
    monkeypatch.setenv("RWE_EVENT_JUDGE", "1")
    ev, band = story_service._event_inputs(st)
    assert ev == {} and band == {}, "on: verdict dict + empty band accumulator"
