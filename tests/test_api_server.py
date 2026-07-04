"""Contract tests for examples/api_server.py — the JSON adapter the web app depends on.

These pin the *shape* the engine serialises (web/types/domain.ts): the frontend, and any
future mobile / third-party client, relies on these keys, types, and value ranges. They
also guard the most common serialisation bug — numpy scalars leaking into the payload and
breaking ``json.dumps`` — by round-tripping every endpoint through JSON.

Fast: a small synthetic corpus is built once for the module (no external data, no network).
"""

import importlib.util
import json
import os
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_api_server():
    spec = importlib.util.spec_from_file_location("api_server", ROOT / "examples" / "api_server.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["api_server"] = mod          # so @dataclass can resolve the module namespace
    spec.loader.exec_module(mod)
    return mod


api_server = _load_api_server()

METRIC_KEYS = {
    "topicDiversity", "sourceDiversity", "reportingRatio", "emotionalBalance",
    "echoChamber", "viewpointBalance", "openMindedness", "confidence",
}
EMOTIONS = {"fear", "outrage", "analysis", "positive", "neutral"}
BUCKETS = {"left", "center", "right"}
REGISTERS = {"reporting", "opinion", "mixed"}
STRATEGIES = {"rwe-b", "rwe-d", "adaptive"}
BANDS = {"Healthy", "Fair", "Needs work", "Unknown"}


@pytest.fixture(scope="module")
def backend():
    """A small synthetic backend (real pipeline, generated clicks) built once."""
    profile = api_server.DatasetProfile.synthetic(n_users=200, max_items=500, seed=0)
    return api_server.Backend(profile)


@pytest.fixture(scope="module")
def user(backend):
    return backend.demo_user


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _assert_json_roundtrips(obj):
    """No numpy scalars / non-serialisable types leak into the payload."""
    reparsed = json.loads(json.dumps(obj))
    assert reparsed == json.loads(json.dumps(reparsed))  # stable


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #
def test_health_contract(backend):
    h = backend.health()
    assert h["ok"] is True
    assert h["domain"] == "news"
    assert isinstance(h["demoUser"], int)
    assert isinstance(h["eligibleReaders"], int) and h["eligibleReaders"] > 0
    assert isinstance(h["narrative"], bool)
    assert isinstance(h["dataset"], dict)
    _assert_json_roundtrips(h)


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def test_report_contract(backend, user):
    r = backend.report(user)
    assert set(r) >= {
        "overall", "overallDelta", "updatedAt", "metrics", "viewpoint",
        "attention", "topics", "sources", "blindSpots", "improvements", "axisConfidence",
    }
    assert 0 <= r["overall"] <= 100
    assert 0.0 <= r["axisConfidence"] <= 1.0
    assert r["band"] in BANDS  # backend owns the health-band thresholds

    keys_seen = set()
    for m in r["metrics"]:
        assert m["key"] in METRIC_KEYS
        assert 0 <= m["score"] <= 100
        assert "delta" in m
        assert m["band"] in BANDS
        keys_seen.add(m["key"])
    assert keys_seen == METRIC_KEYS  # synthetic corpus populates all eight

    vp = r["viewpoint"]
    assert set(vp) == BUCKETS
    assert all(0.0 <= vp[k] <= 1.0001 for k in vp)
    assert abs(sum(vp.values()) - 1.0) < 1e-6

    assert set(r["attention"]) == EMOTIONS
    for t in r["topics"]:
        assert set(t) >= {"topic", "share", "count"} and 0.0 <= t["share"] <= 1.0
    for s in r["sources"]:
        assert set(s) >= {"source", "share", "count", "lean"} and _is_number(s["lean"])
    for b in r["blindSpots"]:
        assert set(b) >= {"topic", "gap", "note"} and 0.0 <= b["gap"] <= 1.0
    for imp in r["improvements"]:
        assert set(imp) >= {"id", "title", "detail", "metric", "impact"}
        assert imp["metric"] in METRIC_KEYS

    _assert_json_roundtrips(r)


# --------------------------------------------------------------------------- #
# onboarding: outlets + Initial Information Health Estimate (Milestone B1)
# --------------------------------------------------------------------------- #
def test_outlets_listing(backend):
    outs = backend.outlets()
    assert isinstance(outs, list) and len(outs) > 0
    o = outs[0]
    assert {"id", "name", "lean", "leanBucket", "articles"} <= set(o)
    assert o["leanBucket"] in BUCKETS and _is_number(o["lean"])
    _assert_json_roundtrips(outs)


def test_estimate_is_labeled_and_grounded(backend):
    names = [o["id"] for o in backend.outlets()[:6]]
    est = backend.estimate(names)
    # explicitly an estimate, with zero-read coverage — never presented as measured
    assert est["mode"] == "estimate"
    assert est["coverage"]["reads"] == 0 and est["coverage"]["sufficient"] is False
    # no fabricated behaviour: axis confidence + Open-Mindedness are n/a from outlets, so omitted
    assert "axisConfidence" not in est
    keys = {m["key"] for m in est["metrics"]}
    assert keys <= (METRIC_KEYS - {"confidence", "openMindedness"})
    for m in est["metrics"]:
        assert 0 <= m["score"] <= 100 and m["band"] in BANDS
    assert 0 <= est["overall"] <= 100
    assert set(est["viewpoint"]) == BUCKETS and abs(sum(est["viewpoint"].values()) - 1.0) < 1e-6
    assert set(est["attention"]) == EMOTIONS
    _assert_json_roundtrips(est)


def test_estimate_requires_known_outlets(backend):
    with pytest.raises(ValueError):
        backend.estimate(["definitely-not-a-real-outlet"])


# --------------------------------------------------------------------------- #
# article + recommendations
# --------------------------------------------------------------------------- #
def _assert_article(a):
    assert set(a) >= {
        "id", "headline", "publisher", "publisherLean", "topic", "lean",
        "leanBucket", "confidence", "emotion", "dominantEmotion", "register",
        "publishedAt", "readingMinutes",
    }
    assert a["leanBucket"] in BUCKETS
    assert a["register"] in REGISTERS
    assert -2.0001 <= a["lean"] <= 2.0001
    assert 0.0 <= a["confidence"] <= 1.0
    assert set(a["emotion"]) == EMOTIONS
    assert abs(sum(a["emotion"].values()) - 1.0) < 1e-6
    assert a["dominantEmotion"] == max(a["emotion"], key=a["emotion"].get)
    assert isinstance(a["readingMinutes"], int)


def test_recommendations_contract(backend, user):
    recs = backend.recommendations(user)
    assert len(recs) > 0
    for r in recs:
        assert set(r) >= {"article", "reason", "strategy", "healthImpact", "helpsMetric", "crossCutting"}
        _assert_article(r["article"])
        assert r["strategy"] in STRATEGIES
        assert r["helpsMetric"] in METRIC_KEYS
        assert isinstance(r["crossCutting"], bool)
        assert isinstance(r["healthImpact"], int) and r["healthImpact"] > 0
    _assert_json_roundtrips(recs)


@pytest.mark.parametrize("strategy", ["rwe-b", "rwe-d", "adaptive"])
def test_recommendation_strategy_is_honoured(backend, user, strategy):
    recs = backend.recommendations(user, strategy)
    assert len(recs) > 0
    assert {r["strategy"] for r in recs} == {strategy}


def test_recommendations_blend_tags_each_source(backend, user):
    strategies = {r["strategy"] for r in backend.recommendations(user)}
    assert strategies <= STRATEGIES and len(strategies) >= 1


def test_recommenders_built_once(backend):
    # heavy recommender objects are constructed at startup and reused across requests
    assert backend._model("rwe-b") is backend._model("rwe-b")
    assert backend._model("adaptive") is backend._model("adaptive")
    assert backend._model("rwe-b") is not backend._model("rwe-d")
    assert backend._model("unknown") is backend._model("rwe-b")   # fallback unchanged


# --------------------------------------------------------------------------- #
# coach
# --------------------------------------------------------------------------- #
def test_coach_greeting_contract(backend, user):
    greeting = backend.coach_greeting(user)
    assert isinstance(greeting, list) and len(greeting) == 1
    msg = greeting[0]
    assert msg["role"] == "assistant"
    assert isinstance(msg["content"], str) and msg["content"]
    for c in msg.get("citations", []):
        assert c["metric"] in METRIC_KEYS and _is_number(c["value"])
    _assert_json_roundtrips(greeting)


def test_coach_reply_is_grounded(backend, user):
    reply = backend.coach_reply(user, "how one-sided is my reading?")
    assert reply["role"] == "assistant"
    assert isinstance(reply["content"], str) and reply["content"]
    # citations reference real metrics with in-range values
    for c in reply["citations"]:
        assert c["metric"] in METRIC_KEYS
        assert 0 <= c["value"] <= 100
    # keyless → deterministic grounded fallback that states the reader's real overall score
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        assert str(backend.report(user)["overall"]) in reply["content"]
    for s in reply.get("suggestions", []):
        _assert_article(s)
    _assert_json_roundtrips(reply)


# --------------------------------------------------------------------------- #
# resolve_user (the ?user= override the HTTP layer relies on)
# --------------------------------------------------------------------------- #
def test_resolve_user_defaults_and_overrides(backend):
    assert backend.resolve_user({}) == backend.demo_user
    assert backend.resolve_user({"user": ["0"]}) == 0
    # out-of-range ids fall back to the demo user, never crash
    assert backend.resolve_user({"user": ["999999"]}) == backend.demo_user


# --------------------------------------------------------------------------- #
# dataset profiles — config-only data switching (MIND / Politosphere / Qbias / …)
# --------------------------------------------------------------------------- #
def _blank_args(**overrides):
    import argparse
    base = dict(profile=None, npz=None, qbias=None, register_csv=None, emotion_csv=None,
                behaviors=None, lean_tau=None, domain=None, n_users=None, max_items=None, seed=None)
    base.update(overrides)
    return argparse.Namespace(**base)


def test_profile_defaults_to_synthetic():
    p = api_server.resolve_profile(_blank_args())
    assert p.name == "synthetic" and p.kind == "synthetic" and p.domain == "news"
    assert p.lean_tau == api_server.hr.LEAN_TAU  # sourced from the engine, not hard-coded


def test_named_profiles_carry_domain_and_kind():
    assert api_server.resolve_profile(_blank_args(profile="politosphere")).domain == "reddit"
    assert api_server.resolve_profile(_blank_args(profile="mind")).kind == "npz"
    assert api_server.resolve_profile(_blank_args(profile="qbias")).kind == "synthetic"


def test_cli_overrides_win_over_profile_and_env(monkeypatch):
    monkeypatch.setenv("RWE_PROFILE", "synthetic")
    monkeypatch.setenv("RWE_NPZ", "/env/mind.npz")
    p = api_server.resolve_profile(_blank_args(profile="mind", npz="/cli/mind.npz", lean_tau=0.75))
    assert p.name == "mind" and p.npz == "/cli/mind.npz" and p.lean_tau == 0.75


def test_env_selects_profile_when_no_cli(monkeypatch):
    monkeypatch.setenv("RWE_PROFILE", "mind")
    monkeypatch.setenv("RWE_NPZ", "/env/mind.npz")
    p = api_server.resolve_profile(_blank_args())
    assert p.name == "mind" and p.npz == "/env/mind.npz"


def test_unknown_profile_is_rejected():
    with pytest.raises(SystemExit):
        api_server.resolve_profile(_blank_args(profile="does-not-exist"))


def test_synthetic_classmethod_names_qbias_when_catalog_given():
    assert api_server.DatasetProfile.synthetic().name == "synthetic"
    assert api_server.DatasetProfile.synthetic(qbias_csv="a.csv").name == "qbias"
