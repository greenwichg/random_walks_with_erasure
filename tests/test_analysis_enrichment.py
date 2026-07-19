"""A3.1 — unit tests for the article-analysis reader enrichment (`analysis_enrichment`).

Covers the pure assembler (`enrich`), the stable verdict, evidence-licensing of the chosen
explanation (`evidence_resolver.validate`), determinism, the catalog-`political` regression, and the
`enrich_for_reader` measured-gate. The heavy end-to-end (a real measured reader through the
endpoint) lives in `test_analyze_api.py`; here the reader context is the resolver's documented shape
(shared with the golden builder), so these tests and the fixtures move together.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "analysis"))

import analysis_enrichment as ae             # noqa: E402
import article_analyzer as aa                # noqa: E402
import evidence_resolver as er               # noqa: E402
import build_analysis_fixtures as fx         # noqa: E402


@pytest.fixture()
def store():
    er._INDEX_CACHE.update(key=None, index=None)   # avoid the count-keyed story-index memo leaking
    st = fx.store_mod.Store("sqlite://")
    fx.seed(st)
    return st


@pytest.fixture()
def index(store):
    return er.story_index(store)


def _rec(analysis, explanation):
    return {"article": ae._rec_article(analysis), "crossCutting": explanation.get("type") == "bridge"}


# --------------------------------------------------------------------------- #
# The two golden scenarios — explanation + verdict + evidence-licensing.
# --------------------------------------------------------------------------- #
def test_enrich_bridge_cross_cutting(store, index):
    ctx = fx._authed_contexts()["authed_bridge"]     # right reader, never read The Guardian
    analysis = aa.analyze(store, fx.GUARDIAN_URL)     # the left cluster member
    out = ae.enrich(analysis, ctx, index=index, blind_spot_topics=fx._BLIND_SPOTS)

    assert out["explanation"]["type"] == "bridge"
    assert out["recommendation"]["wouldBroaden"] is True
    assert "cross_cutting" in out["recommendation"]["reasons"]
    assert "new_publisher" in out["recommendation"]["reasons"]
    assert out["recommendation"]["blindSpotTopic"] is None
    # the shown sentence is licensed by real evidence (no over-claim)
    assert er.validate(out["explanation"], _rec(analysis, out["explanation"]), ctx, index) == []


def test_enrich_familiar_not_broadening(store, index):
    ctx = fx._authed_contexts()["authed_familiar"]    # left reader who reads The Guardian
    analysis = aa.analyze(store, fx.GUARDIAN_URL)
    out = ae.enrich(analysis, ctx, index=index, blind_spot_topics=fx._BLIND_SPOTS)

    assert out["explanation"]["type"] == "topic_continuity"
    assert out["recommendation"]["wouldBroaden"] is False
    assert out["recommendation"]["reasons"] == ["familiar_topic"]
    assert er.validate(out["explanation"], _rec(analysis, out["explanation"]), ctx, index) == []


# --------------------------------------------------------------------------- #
# Verdict signals + the catalog-`political` regression.
# --------------------------------------------------------------------------- #
def test_rec_article_reads_political_and_lean_from_scoring(store):
    """`feed_article_to_article` omits `political`; the synthetic rec must take it from `scoring`,
    or nothing is ever cross-cutting (the bug that first made the bridge golden resolve wrong)."""
    art = ae._rec_article(aa.analyze(store, fx.GUARDIAN_URL))
    assert art["political"] is True and art["lean"] == -1.0


def test_verdict_blind_spot_topic(store, index):
    ctx = fx._authed_contexts()["authed_familiar"]
    analysis = aa.analyze(store, fx.GUARDIAN_URL)     # topic "Politics"
    out = ae.enrich(analysis, ctx, index=index, blind_spot_topics=("Politics",))
    assert "blind_spot" in out["recommendation"]["reasons"]
    assert out["recommendation"]["blindSpotTopic"] == "Politics"
    assert out["recommendation"]["wouldBroaden"] is True     # blind_spot is a broadening reason


def test_reasons_are_a_stable_closed_vocabulary(store, index):
    for name, ctx in fx._authed_contexts().items():
        out = ae.enrich(aa.analyze(store, fx.GUARDIAN_URL), ctx, index=index)
        assert set(out["recommendation"]["reasons"]) <= set(ae._REASON_ORDER)


# --------------------------------------------------------------------------- #
# Totality, determinism, gating.
# --------------------------------------------------------------------------- #
def test_invalid_and_urlless_yield_null_sections(store, index):
    assert ae.enrich(aa.analyze(store, "not a url"), fx._authed_contexts()["authed_bridge"],
                     index=index) == {"explanation": None, "recommendation": None}
    assert ae.enrich({"status": "analyzed", "article": None, "scoring": None, "input": {}},
                     {}, index=index) == {"explanation": None, "recommendation": None}


def test_enrich_is_deterministic(store, index):
    ctx = fx._authed_contexts()["authed_bridge"]
    a = ae.enrich(aa.analyze(store, fx.GUARDIAN_URL), ctx, index=index, blind_spot_topics=fx._BLIND_SPOTS)
    b = ae.enrich(aa.analyze(store, fx.GUARDIAN_URL), ctx, index=index, blind_spot_topics=fx._BLIND_SPOTS)
    import json
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


class _StubPersonalizer:
    def __init__(self, measured, ctx):
        self._measured, self._ctx = measured, ctx

    def has_measured(self, uid):
        return self._measured

    def explanation_context(self, uid):
        return self._ctx


def test_enrich_for_reader_gates_on_measured(store):
    ctx = fx._authed_contexts()["authed_bridge"]
    analysis = aa.analyze(store, fx.GUARDIAN_URL)
    null = {"explanation": None, "recommendation": None}

    assert ae.enrich_for_reader(_StubPersonalizer(True, ctx), store, None, analysis) == null   # anon
    assert ae.enrich_for_reader(None, store, 1, analysis) == null                              # no personalizer
    assert ae.enrich_for_reader(_StubPersonalizer(False, ctx), store, 1, analysis) == null     # not measured
    measured = ae.enrich_for_reader(_StubPersonalizer(True, ctx), store, 1, analysis)          # measured
    assert measured["explanation"]["type"] == "bridge" and measured["recommendation"]["wouldBroaden"] is True


def test_blind_spot_topics_read_from_stored_report(store):
    uid = store.upsert_user_by_identity("dev", "a3-blind").id
    store.save_report(uid, {"mode": "measured", "overall": 60,
                            "blindSpots": [{"topic": "Economy", "gap": 0.4, "note": "n"},
                                           {"topic": "Science", "gap": 0.3, "note": "n"}]})
    assert ae._blind_spot_topics(store, uid) == ("Economy", "Science")
    assert ae._blind_spot_topics(store, 999_999) == ()     # no report -> empty, never a guess
