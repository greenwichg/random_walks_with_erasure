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
        assert m["available"] is True  # synthetic corpus measures every metric — none is an empty state
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


def test_report_is_labeled_measured(backend, user):
    """The measured report carries the same mode/coverage contract as the estimate."""
    r = backend.report(user)
    assert r["mode"] == "measured"
    cov = r["coverage"]
    assert cov["threshold"] == 5 and cov["reads"] >= 5           # demo reader is above the floor
    assert cov["sufficient"] is (cov["reads"] >= cov["threshold"]) is True
    assert "axisConfidence" in r                                 # measured keeps axis confidence


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
    # no fabricated behaviour: axis confidence is n/a from outlets, so the report-level field is omitted
    assert "axisConfidence" not in est
    # every metric card is emitted (none hidden) — confidence + Open-Mindedness need measured reads, so
    # they carry an EXPLICIT empty-state signal rather than being dropped (Metric Empty State).
    keys = {m["key"] for m in est["metrics"]}
    assert keys == METRIC_KEYS
    avail = {m["key"]: m["available"] for m in est["metrics"]}
    assert avail["confidence"] is False and avail["openMindedness"] is False
    for m in est["metrics"]:
        assert 0 <= m["score"] <= 100 and m["band"] in BANDS
        assert isinstance(m["available"], bool)
        if not m["available"]:                                   # unavailable → truthful, non-fabricated
            assert m["reason"] == "insufficient_data" and m["minimumActivity"] == 5
    assert 0 <= est["overall"] <= 100
    assert set(est["viewpoint"]) == BUCKETS and abs(sum(est["viewpoint"].values()) - 1.0) < 1e-6
    assert set(est["attention"]) == EMOTIONS
    _assert_json_roundtrips(est)


# --------------------------------------------------------------------------- #
# RC2.1 — personalized recommendation evidence binding
# --------------------------------------------------------------------------- #
_EVIDENCE_FIELDS = {"trigger", "evidence", "suggestedAction", "expectedBenefit", "evidenceBasis"}


def _expected_improvement_metrics(report):
    """Recompute the CURRENT selection rule independently: the 3 lowest available, non-confidence
    metrics by score, in ascending-score order — so a drift in selection/order is caught."""
    ranked = sorted((m for m in report["metrics"]
                     if m["key"] != "confidence" and m.get("available")),
                    key=lambda m: m["score"])
    return [m["key"] for m in ranked[:3] if api_server._IMPROVEMENTS.get(m["key"])]


def test_improvements_selection_and_impact_unchanged(backend, user):
    """RC2.1 must NOT touch which recommendations appear, their order, or the (static) impact — it only
    binds evidence. Selection is re-derived from the metrics and the impact from the template table."""
    r = backend.report(user)
    assert [imp["metric"] for imp in r["improvements"]] == _expected_improvement_metrics(r)
    for imp in r["improvements"]:
        tpl = api_server._IMPROVEMENTS[imp["metric"]]
        assert imp["title"] == tpl[0] and imp["detail"] == tpl[1] and imp["impact"] == tpl[2]


def test_improvements_carry_bound_evidence(backend, user):
    """Every improvement gains the four evidence parts + a traceability basis, all non-empty."""
    r = backend.report(user)
    assert r["improvements"], "the synthetic reader has weak metrics, so recommendations exist"
    for imp in r["improvements"]:
        assert _EVIDENCE_FIELDS <= set(imp)
        for f in ("trigger", "evidence", "suggestedAction", "expectedBenefit"):
            assert isinstance(imp[f], str) and imp[f].strip()
        assert isinstance(imp["evidenceBasis"], list) and imp["evidenceBasis"]
        for b in imp["evidenceBasis"]:
            assert set(b) == {"field", "label", "value"}
            assert isinstance(b["field"], str) and b["field"]
            assert _is_number(b["value"])
    _assert_json_roundtrips(r)


def test_improvement_evidence_is_traceable_to_report_fields(backend, user):
    """No fabrication: each basis value equals the exact number in the report field it names, so every
    quoted figure is auditable back to the payload the same request returned."""
    r = backend.report(user)
    src_by_name = {s["source"]: s["share"] for s in r["sources"]}
    topic_by_name = {t["topic"]: t["share"] for t in r["topics"]}
    vp, att = r["viewpoint"], r["attention"]
    metric_score = {m["key"]: m["score"] for m in r["metrics"]}
    for imp in r["improvements"]:
        for b in imp["evidenceBasis"]:
            field, label, val = b["field"], b["label"], b["value"]
            if field == "sources":
                assert src_by_name.get(label) == pytest.approx(val)
            elif field == "topics":
                assert topic_by_name.get(label) == pytest.approx(val)
            elif field == "viewpoint":
                assert vp[label] == pytest.approx(val)
            elif field == "attention":
                assert att[label] == pytest.approx(val)
            elif field == "metric.score":
                assert metric_score[imp["metric"]] == pytest.approx(val)
            elif field == "metric.benchmark":
                assert val == pytest.approx(50.0)   # measured metrics benchmark to the population median


def test_improvement_evidence_never_claims_false_comparison(backend, user):
    """Honesty guard: the selection surfaces a reader's LOWEST metrics, which can still sit at/above the
    population median — so an evidence trigger must never say 'below the typical reader' unless the
    score genuinely is below its benchmark."""
    r = backend.report(user)
    score_by_key = {m["key"]: m["score"] for m in r["metrics"]}
    bench_by_key = {m["key"]: m.get("benchmark") for m in r["metrics"]}
    for imp in r["improvements"]:
        bm = bench_by_key.get(imp["metric"])
        if bm is not None and score_by_key[imp["metric"]] >= bm:
            assert "below the typical" not in imp["trigger"]


def test_improvement_evidence_is_deterministic(backend, user):
    """Same corpus + reader → byte-identical improvements (evidence included)."""
    a = backend.report(user)["improvements"]
    b = backend.report(user)["improvements"]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_estimate_improvements_carry_evidence_without_false_concentration(backend):
    """The estimate binds evidence too, but must NOT present its equal-weighted source shares as a real
    reading mix (no "X% of your reading came from …" claim on an estimate)."""
    names = [o["id"] for o in backend.outlets()[:6]]
    est = backend.estimate(names)
    for imp in est["improvements"]:
        assert _EVIDENCE_FIELDS <= set(imp)
        assert imp["trigger"].strip() and imp["suggestedAction"].strip()
        if imp["metric"] == "sourceDiversity":
            assert "of your reading came from" not in imp["trigger"]
    _assert_json_roundtrips(est)


# --------------------------------------------------------------------------- #
# RC2.1.1 — evidence honesty fixes (mode-aware wording, ties, rounding, benefit)
# --------------------------------------------------------------------------- #
# Phrases that assert the reader has ALREADY read something — forbidden in estimate mode (0 reads).
_READING_PHRASES = ("you've read", "your reading", "your political reading", "of your reading")


def _ev(key, *, measured, metric=None, topics=None, sources=None, viewpoint=None,
        attention=None, blind=None):
    """Call the binder directly with crafted, fully-controlled inputs (F2/F4/F5 unit coverage)."""
    return api_server._improvement_evidence(
        key, metric=metric or {"score": 30, "benchmark": 50}, topics=topics or [],
        sources=sources or [], viewpoint=viewpoint or {}, attention=attention or {},
        blind=blind or [], measured=measured)


def test_estimate_wording_never_implies_reading(backend):
    """F1: no estimate-mode trigger or evidence may claim the reader has already read anything."""
    est = backend.estimate([o["id"] for o in backend.outlets()[:6]])
    assert est["coverage"]["reads"] == 0                       # a genuine zero-read estimate
    for imp in est["improvements"]:
        blob = f"{imp['trigger']} {imp['evidence']}".lower()
        for phrase in _READING_PHRASES:
            assert phrase not in blob, f"{imp['metric']} leaked reading-language: {blob!r}"


def test_measured_wording_may_describe_reading_and_never_uses_outlet_framing(backend, user):
    """F1 (converse): the measured path may say 'your reading' and must never borrow the estimate's
    'outlets you picked' framing."""
    r = backend.report(user)
    for imp in r["improvements"]:
        blob = f"{imp['trigger']} {imp['evidence']}".lower()
        assert "outlets you picked" not in blob and "based on the outlets" not in blob
    # the demo reader's diet is concentrated, so at least one card does describe real reading
    assert any(p in f"{imp['trigger']} {imp['evidence']}".lower()
               for imp in r["improvements"] for p in _READING_PHRASES)


def test_reporting_ratio_estimate_wording(backend):
    """F3: reportingRatio evidence must not presume reading in estimate mode."""
    est = api_server._improvement_evidence(
        "reportingRatio", metric={"score": 40, "benchmark": 50}, topics=[], sources=[],
        viewpoint={}, attention={}, blind=[], measured=False)
    trigger, evidence = est[0], est[1]
    assert "outlets you picked" in evidence
    for phrase in _READING_PHRASES:
        assert phrase not in f"{trigger} {evidence}".lower()


def test_viewpoint_left_right_tie_is_neutral():
    """F2: on an exact left==right tie, wording is neutral and the action never names a single side."""
    trigger, evidence, action, benefit, _ = _ev(
        "viewpointBalance", measured=True, viewpoint={"left": 0.4, "center": 0.2, "right": 0.4})
    assert "leans" not in evidence.lower()                     # no false lean claim
    assert "left-leaning" not in action.lower() and "right-leaning" not in action.lower()
    assert "evenly split" in evidence.lower() and "cross-cutting" in action.lower()


def test_trigger_percentage_equals_sum_of_displayed_parts():
    """F4: the trigger total is the sum of the rounded parts shown in the evidence, so they always agree
    on screen (0.474 + 0.474 → 47 + 47 = 94, not round(94.8)=95)."""
    trigger, evidence, *_ = _ev(
        "sourceDiversity", measured=True,
        sources=[{"source": "A", "share": 0.474}, {"source": "B", "share": 0.474}])
    assert trigger.startswith("94%")
    assert "A (47%)" in evidence and "B (47%)" in evidence
    # emotionalBalance sums the same way
    trg, ev, *_ = _ev("emotionalBalance", measured=True,
                      attention={"fear": 0.204, "outrage": 0.204, "analysis": 0.1})
    assert trg.startswith("40%") and "Fear 20%" in ev and "outrage 20%" in ev


def test_benefit_wording_is_non_guaranteeing(backend, user):
    """F5: expectedBenefit must not assert a guaranteed effect; it uses 'Can improve/broaden …'."""
    for report in (backend.report(user),
                   backend.estimate([o["id"] for o in backend.outlets()[:6]])):
        for imp in report["improvements"]:
            b = imp["expectedBenefit"]
            assert b.startswith("Can "), b
            assert not any(b.startswith(v) for v in ("Broadens", "Improves", "Raises",
                                                     "Lifts", "Loosens"))


def test_estimate_evidence_basis_still_traceable(backend):
    """Basis integrity survives the wording changes: every estimate basis value equals the exact number
    in the report field it names (viewpoint/attention/topics/metric)."""
    est = backend.estimate([o["id"] for o in backend.outlets()[:6]])
    vp, att = est["viewpoint"], est["attention"]
    topic_share = {t["topic"]: t["share"] for t in est["topics"]}
    score_by_key = {m["key"]: m["score"] for m in est["metrics"]}
    for imp in est["improvements"]:
        for b in imp["evidenceBasis"]:
            f, label, val = b["field"], b["label"], b["value"]
            if f == "viewpoint":
                assert vp[label] == pytest.approx(val)
            elif f == "attention":
                assert att[label] == pytest.approx(val)
            elif f == "topics":
                assert topic_share.get(label) == pytest.approx(val)
            elif f == "metric.score":
                assert score_by_key[imp["metric"]] == pytest.approx(val)


def test_unavailable_metric_contract():
    """The empty-state card the UI renders when a metric cannot be measured yet. It is an explicit,
    truthful signal — available False + a reason + the activity threshold — never a fabricated score,
    so the frontend never has to guess "unavailable" from score == 0 (Metric Empty State)."""
    m = api_server._unavailable_metric("openMindedness")
    assert m["key"] == "openMindedness"
    assert m["available"] is False
    assert m["reason"] == "insufficient_data"
    assert m["minimumActivity"] == api_server.ESTIMATE_MIN_READS == 5
    assert m["band"] == "Unknown" and m["band"] in BANDS   # no health band without a real score
    assert m["score"] == 0 and m["delta"] == 0             # neutral placeholders the UI does not render
    json.dumps(m)                                          # serialises cleanly (no numpy scalars)


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
    # Lean is nullable (L2.2): a reading-history article from an outlet the registry doesn't know
    # serialises lean/leanBucket as null — never a fabricated centre. The pair is consistent: both
    # null (unknown) or both present (known). Every other path (corpus/rec/report) always emits a
    # known number, so it still takes the strict branch — no relaxation for the known case.
    if a["lean"] is None:
        assert a["leanBucket"] is None
    else:
        assert a["leanBucket"] in BUCKETS
        assert -2.0001 <= a["lean"] <= 2.0001
    assert a["register"] in REGISTERS
    assert 0.0 <= a["confidence"] <= 1.0
    assert set(a["emotion"]) == EMOTIONS
    assert abs(sum(a["emotion"].values()) - 1.0) < 1e-6
    assert a["dominantEmotion"] == max(a["emotion"], key=a["emotion"].get)
    assert isinstance(a["readingMinutes"], int)


def test_recommendations_contract(backend, user):
    recs = backend.recommendations(user)
    assert len(recs) > 0
    for r in recs:
        assert set(r) >= {"article", "reason", "strategy", "helpsMetric", "crossCutting"}
        _assert_article(r["article"])
        assert r["strategy"] in STRATEGIES
        assert r["helpsMetric"] in METRIC_KEYS
        assert isinstance(r["crossCutting"], bool)
        # Commit 21a: the placeholder impact number (a stable hash, not a measurement) is gone —
        # every surfaced signal must be traceable to real recommender evidence.
        assert "healthImpact" not in r
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
# reading history — serialised from stored reads via the SHARED article payload
# --------------------------------------------------------------------------- #
def test_serialize_history_shape_and_degradation(backend):
    """serialize_history renders each stored read as the same Article shape, preserving order and
    degrading a sparse read (no title / no emotion) to safe neutral defaults. A NaN lean from an
    unknown outlet degrades to null (L2.2) — never a fabricated centre."""
    rows = [
        {"id": 2, "canonicalUrl": "https://cnn.com/x", "observedAt": "2026-07-01T10:00:00Z",
         "createdAt": "2026-07-01T10:00:01Z",
         "scored": {"article_id": "https://cnn.com/x", "outlet": "CNN", "category": "Politics",
                    "title": "Senate passes the bill, official says", "lean": -1.0,
                    "register": 0.8, "confidence": 0.7,
                    "emotion": {"fear": 0.1, "outrage": 0.1, "analysis": 0.5,
                                "positive": 0.2, "neutral": 0.1}, "read_at": "2026-07-01T10:00:00Z"}},
        {"id": 1, "canonicalUrl": "https://blog.example/y", "observedAt": None,
         "createdAt": "2026-06-30T09:00:00Z",
         "scored": {"article_id": "https://blog.example/y", "outlet": "Some Blog", "category": "",
                    "title": "", "lean": float("nan"), "register": float("nan"),
                    "confidence": float("nan"), "emotion": None, "read_at": None}},
    ]
    hist = backend.serialize_history(rows)
    assert [h["id"] for h in hist] == ["2", "1"]                      # store order preserved
    for h in hist:
        assert set(h) >= {"id", "article", "readAt", "readingMinutes", "completed"}
        assert h["completed"] is True
        _assert_article(h["article"])                                # same contract as any article
    _assert_json_roundtrips(hist)                                    # valid JSON, no NaN leak
    assert hist[0]["article"]["headline"] == "Senate passes the bill, official says"
    assert hist[0]["article"]["leanBucket"] == "left"
    assert hist[0]["readAt"] == "2026-07-01T10:00:00Z"
    # sparse read from an unknown outlet degrades safely: unknown lean → null, not fabricated centre
    assert hist[1]["article"]["lean"] is None
    assert hist[1]["article"]["leanBucket"] is None
    assert hist[1]["article"]["emotion"]["neutral"] == 1.0
    assert hist[1]["readAt"] == "2026-06-30T09:00:00Z"               # falls back to createdAt


def test_history_article_reuses_catalog_serializer(backend):
    """A stored read whose id equals a catalog item's id yields the same id-derived fields as
    _serialize_article — proving both paths flow through the one _article_payload builder."""
    col = 0
    item_id = str(np.asarray(backend.base_corpus.mind.dataset.item_ids)[col])
    art = backend._serialize_article(backend.base_corpus, col)
    hist = backend.serialize_history([{
        "id": 9, "canonicalUrl": item_id, "observedAt": "2026-07-01T00:00:00Z",
        "scored": {"article_id": item_id, "outlet": "CNN", "category": "Politics", "title": "T",
                   "lean": 0.5, "register": 0.7, "confidence": 0.7,
                   "emotion": {"fear": 0, "outrage": 0, "analysis": 0, "positive": 0, "neutral": 1}}}])
    h_art = hist[0]["article"]
    assert h_art["id"] == art["id"]
    # readingMinutes is the id-derived deterministic field: identical => both use one _article_payload
    # (publishedAt is _iso_recent(now-relative), so it is intentionally not id-stable to compare).
    assert h_art["readingMinutes"] == art["readingMinutes"]


def test_serialize_history_unknown_outlet_lean_is_null(backend):
    """An unknown outlet (lean missing or NaN) serialises lean/leanBucket as JSON null in reading
    history — the L2.2 fix that stops an unknown outlet being fabricated (and later aggregated) as
    a centre read. Both encodings — a missing lean (None) and a NaN — degrade the same way."""
    rows = [
        {"id": 1, "canonicalUrl": "https://unknown.example/a", "observedAt": "2026-07-01T10:00:00Z",
         "scored": {"article_id": "a", "outlet": "Unknown Outlet", "category": "Politics",
                    "title": "Missing-lean read", "lean": None, "register": 0.8,
                    "confidence": 0.7, "emotion": None, "read_at": "2026-07-01T10:00:00Z"}},
        {"id": 2, "canonicalUrl": "https://unknown.example/b", "observedAt": "2026-07-01T11:00:00Z",
         "scored": {"article_id": "b", "outlet": "Another Unknown", "category": "Politics",
                    "title": "NaN-lean read", "lean": float("nan"), "register": 0.8,
                    "confidence": 0.7, "emotion": None, "read_at": "2026-07-01T11:00:00Z"}},
    ]
    hist = backend.serialize_history(rows)
    for h in hist:
        _assert_article(h["article"])                 # full Article contract, now nullable-lean aware
        assert h["article"]["lean"] is None            # unknown → null, never a fabricated 0.0
        assert h["article"]["leanBucket"] is None      # and no bucket claim ("center") is invented
    _assert_json_roundtrips(hist)                      # emits literal JSON null, no NaN leak


def test_serialize_history_mixed_known_and_unknown_lean(backend):
    """Mixed history: a known-lean read keeps its exact lean/bucket; an unknown-lean read is null.
    Known values are untouched by the L2.2 change — only the unknown case is corrected, so a reader
    with any known reads sees those unchanged alongside a truthful null for the unknown ones."""
    rows = [
        {"id": 3, "canonicalUrl": "https://cnn.com/z", "observedAt": "2026-07-02T10:00:00Z",
         "scored": {"article_id": "z", "outlet": "CNN", "category": "Politics", "title": "Known left",
                    "lean": -1.2, "register": 0.8, "confidence": 0.7, "emotion": None,
                    "read_at": "2026-07-02T10:00:00Z"}},
        {"id": 4, "canonicalUrl": "https://unknown.example/w", "observedAt": "2026-07-02T09:00:00Z",
         "scored": {"article_id": "w", "outlet": "Mystery Wire", "category": "Politics",
                    "title": "Unknown outlet", "lean": None, "register": 0.8, "confidence": 0.7,
                    "emotion": None, "read_at": "2026-07-02T09:00:00Z"}},
    ]
    hist = backend.serialize_history(rows)
    known, unknown = hist[0]["article"], hist[1]["article"]
    assert known["lean"] == -1.2 and known["leanBucket"] == "left"     # known lean preserved exactly
    assert unknown["lean"] is None and unknown["leanBucket"] is None   # unknown lean → null
    _assert_json_roundtrips(hist)


def test_article_payload_unknown_lean_flag_scopes_null_to_history(backend):
    """The ``unknown_lean_to_null`` flag is the exact L2.2 seam: reading history (flag on) nulls an
    unknown lean, while the corpus/recommendation/story path (flag off — the default) keeps the
    legacy neutral 0.0/'center'. Same builder, same inputs — only the flag differs, so the canonical
    corpus serialisation is provably unchanged by this fix."""
    kw = dict(item_id="x", headline="H", outlet="Nowhere", topic="Politics",
              lean=float("nan"), register=0.7, emotion={"neutral": 1.0}, confidence=0.7,
              outlet_lean={})
    corpus = backend._article_payload(**kw)                            # flag defaults to False
    history = backend._article_payload(**kw, unknown_lean_to_null=True)
    assert corpus["lean"] == 0.0 and corpus["leanBucket"] == "center"  # legacy corpus path unchanged
    assert history["lean"] is None and history["leanBucket"] is None   # history nulls the unknown


# --------------------------------------------------------------------------- #
# dashboard — composed from the report + reads + snapshots, reusing serialisers
# --------------------------------------------------------------------------- #
def test_build_dashboard_composes_report_reads_snapshots(backend):
    """build_dashboard lifts overall/metrics from the report verbatim, builds the trend + delta from
    snapshots, and aggregates *today's* reads — no report re-serialisation, no new algorithm."""
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    report = {"overall": 72, "overallDelta": 0,
              "metrics": [{"key": "topicDiversity", "score": 60, "delta": 0,
                           "band": "Fair", "benchmark": 50}]}
    reads = [
        {"id": 3, "canonicalUrl": "https://cnn.com/a", "observedAt": f"{today}T10:00:00Z",
         "scored": {"article_id": "https://cnn.com/a", "outlet": "CNN", "category": "Politics",
                    "title": "A", "lean": -1.0, "register": 0.7, "confidence": 0.7,
                    "emotion": {"fear": 0, "outrage": 0, "analysis": 0, "positive": 0, "neutral": 1},
                    "political": True}},
        {"id": 2, "canonicalUrl": "https://x.com/b", "observedAt": f"{today}T09:00:00Z",
         "scored": {"article_id": "https://x.com/b", "outlet": "X", "category": "Sports",
                    "title": "B", "political": False, "emotion": None}},
        {"id": 1, "canonicalUrl": "https://y.com/c", "observedAt": "2020-01-01T00:00:00Z",   # not today
         "scored": {"article_id": "https://y.com/c", "outlet": "Y", "category": "World", "political": True}},
    ]
    snaps = [{"id": 1, "mode": "estimate", "overall": 60, "createdAt": "2026-06-01T00:00:00+00:00"},
             {"id": 2, "mode": "measured", "overall": 68, "createdAt": "2026-06-20T00:00:00+00:00"},
             {"id": 3, "mode": "measured", "overall": 72, "createdAt": "2026-07-01T00:00:00+00:00"}]
    dash = backend.build_dashboard(report, reads, snaps)
    assert dash["overall"] == 72
    assert dash["overallDelta"] == 72 - 68                       # vs the previous snapshot
    assert dash["metrics"] == report["metrics"]                  # reused verbatim, not re-derived
    assert [p["overall"] for p in dash["trend"]] == [60, 68, 72] # snapshot history, oldest-first
    t = dash["today"]
    assert t["articlesRead"] == 2                                # only today's two reads
    assert t["politicalShare"] == 0.5                            # 1 of 2 today is political
    assert set(t["topTopics"]) == {"Politics", "Sports"}
    assert isinstance(t["avgReadingMinutes"], int)
    assert dash["streakDays"] >= 1                               # read today -> streak >= 1
    _assert_json_roundtrips(dash)


def test_build_dashboard_empty_for_no_activity(backend):
    dash = backend.build_dashboard({"overall": 50, "overallDelta": 0, "metrics": []}, [], [])
    assert dash["overall"] == 50 and dash["overallDelta"] == 0
    assert dash["trend"] == [] and dash["streakDays"] == 0
    assert dash["today"] == {"articlesRead": 0, "avgReadingMinutes": 0, "minutesRead": 0,
                             "politicalShare": 0.0, "topTopics": []}


def test_build_dashboard_reading_goal_progress(backend):
    """A reader's stored daily goal adds today-vs-goal progress; without a goal (anonymous/demo)
    the goal keys are absent, so the pre-goal payload is unchanged."""
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    reads = [{"id": 1, "canonicalUrl": "https://a.com/1", "observedAt": f"{today}T10:00:00Z",
              "scored": {"article_id": "https://a.com/1", "outlet": "A", "category": "World",
                         "title": "Aid convoy reaches the region, officials say", "political": False}}]
    report = {"overall": 50, "overallDelta": 0, "metrics": []}

    dash = backend.build_dashboard(report, reads, [], goal_minutes=20)
    t = dash["today"]
    assert t["goalMinutes"] == 20
    assert isinstance(t["minutesRead"], int) and t["minutesRead"] >= 1
    assert t["goalMet"] == (t["minutesRead"] >= 20)

    met = backend.build_dashboard(report, reads, [], goal_minutes=1)["today"]
    assert met["goalMet"] is True                                 # any read meets a 1-minute goal

    none = backend.build_dashboard(report, reads, [])["today"]
    assert "goalMinutes" not in none and "goalMet" not in none    # no goal -> no goal keys


# --------------------------------------------------------------------------- #
# analytics — every series is a deterministic aggregation of stored data
# --------------------------------------------------------------------------- #
def test_build_analytics_from_stored_data(backend):
    snapshots = [
        {"date": "2026-06-01", "overall": 60,
         "metrics": {"topicDiversity": 55, "viewpointBalance": 40, "sourceDiversity": 50},
         "attention": {"fear": 0.2, "outrage": 0.1, "analysis": 0.4, "positive": 0.2, "neutral": 0.1}},
        {"date": "2026-06-10", "overall": 66,
         "metrics": {"topicDiversity": 60, "viewpointBalance": 45, "sourceDiversity": 52},
         "attention": {"fear": 0.15, "outrage": 0.1, "analysis": 0.45, "positive": 0.2, "neutral": 0.1}},
    ]
    reads = [
        {"id": 2, "observedAt": "2026-06-10T10:00:00Z", "scored": {"register": 0.8, "political": True}},
        {"id": 1, "observedAt": "2026-06-10T11:00:00Z", "scored": {"register": 0.6, "political": False}},
        {"id": 3, "observedAt": "2026-06-09T09:00:00Z", "scored": {"register": 0.4}},
        {"id": 4, "observedAt": "2026-06-08T09:00:00Z", "scored": {"register": None}},   # no register
    ]
    rec_events = [
        {"shownAt": "2026-06-10T08:00:00Z", "openedAt": "2026-06-10T09:00:00Z", "crossCutting": True},
        {"shownAt": "2026-06-10T08:00:00Z", "openedAt": None, "crossCutting": False},
        {"shownAt": "2026-06-09T08:00:00Z", "openedAt": None, "crossCutting": True},
    ]
    a = backend.build_analytics(snapshots, reads, rec_events)
    # snapshot-derived score/metric trends (oldest-first)
    assert [p["overall"] for p in a["healthImprovement"]] == [60, 66]
    assert [p["overall"] for p in a["topicDiversity"]] == [55, 60]
    assert [p["overall"] for p in a["politicalDiversity"]] == [40, 45]     # viewpointBalance
    assert [p["overall"] for p in a["publisherDiversity"]] == [50, 52]     # sourceDiversity
    assert set(a["emotion"][0]) == {"date", "fear", "outrage", "analysis", "positive", "neutral"}
    assert a["emotion"][0]["analysis"] == 0.4
    # reads-derived: volume per day + reporting = mean register per day (06-08 excluded, no register)
    rot = {p["date"]: p["overall"] for p in a["readingOverTime"]}
    assert rot == {"2026-06-08": 1, "2026-06-09": 1, "2026-06-10": 2}
    rep = {p["date"]: p for p in a["reporting"]}
    assert rep["2026-06-10"]["reporting"] == 0.7 and rep["2026-06-10"]["opinion"] == 0.3   # mean(0.8,0.6)
    assert "2026-06-08" not in rep
    # rec-event-derived acceptance: opened->accepted on opened day; unopened->ignored on shown day
    acc = {p["date"]: p for p in a["recommendationAcceptance"]}
    assert acc["2026-06-10"] == {"date": "2026-06-10", "accepted": 1, "ignored": 1}
    assert acc["2026-06-09"]["ignored"] == 1 and acc["2026-06-09"]["accepted"] == 0
    _assert_json_roundtrips(a)


def test_build_analytics_empty_is_honest(backend):
    a = backend.build_analytics([], [], [])
    # coverage reflects the real read count (0 here) toward the measured threshold — carried so
    # Analytics shows the same Estimate-vs-Measured context as the dashboard/report.
    assert a == {"coverage": {"reads": 0, "threshold": 5, "sufficient": False},
                 "readingOverTime": [], "topicDiversity": [], "politicalDiversity": [],
                 "publisherDiversity": [], "emotion": [], "reporting": [],
                 "recommendationAcceptance": [], "healthImprovement": []}


def test_recommendation_acceptance_reconciles_with_stored_interactions(backend):
    """Store -> aggregation reconciliation through the REAL writers (not hand-built dicts):
    every surfaced recommendation lands in exactly one state (opened -> accepted on its opened
    day; unopened -> ignored on its LATEST shown day), re-surfacing is idempotent (moves the
    ignored bucket day, never duplicates), a double open counts once, an open that races the
    surfacing still counts once, and the day totals reconcile with the stored rows."""
    sys.path.insert(0, str(ROOT / "examples"))
    import store as store_mod
    st = store_mod.Store("sqlite://")
    uid = st.upsert_user_by_identity("dev", "acceptance-audit").id

    d = lambda day, hour=8: f"2026-06-{day:02d}T{hour:02d}:00:00+00:00"
    # day 1: A, B, C surfaced
    assert st.record_recommendations_shown(
        uid, [("A", False), ("B", True), ("C", False)], shown_at=d(1)) == 3
    # day 2: A and B re-surfaced -> idempotent (0 new rows); their ignored bucket moves to day 2
    assert st.record_recommendations_shown(uid, [("A", False), ("B", False)], shown_at=d(2)) == 0
    # day 3: B opened (ignored -> accepted); D opened before any surfacing was recorded (race)
    assert st.record_recommendation_open(uid, "B", opened_at=d(3)) is True
    assert st.record_recommendation_open(uid, "D", cross_cutting=False, opened_at=d(3)) is True
    # day 4: B opened again -> no-op, stays accepted on day 3
    assert st.record_recommendation_open(uid, "B", opened_at=d(4)) is False

    events = st.list_rec_events(uid)
    acc = {p["date"]: p for p in backend.build_analytics([], [], events)["recommendationAcceptance"]}

    assert acc == {
        "2026-06-01": {"date": "2026-06-01", "accepted": 0, "ignored": 1},   # C
        "2026-06-02": {"date": "2026-06-02", "accepted": 0, "ignored": 1},   # A (moved from day 1)
        "2026-06-03": {"date": "2026-06-03", "accepted": 2, "ignored": 0},   # B + D
    }
    # reconciliation: exactly one state per stored recommendation, nothing dropped or doubled
    opened_rows = [e for e in events if e["openedAt"]]
    unopened_rows = [e for e in events if not e["openedAt"]]
    assert len(events) == 4                                    # A, B, C, D — one row each
    assert sum(p["accepted"] for p in acc.values()) == len(opened_rows) == 2
    assert sum(p["ignored"] for p in acc.values()) == len(unopened_rows) == 2
    assert all(e["shownAt"] for e in events)                   # the writers never leave a bare row
    # cross-cutting reception (Open-Mindedness) reads the SAME rows consistently
    rec = st.recommendation_reception(uid)
    assert rec == {"shownCross": 1, "openedCross": 1, "rate": 1.0}   # B was the one cross rec


def test_recommendation_feedback_records_per_type_idempotently():
    """Explicit recommendation feedback (B1) persists one row per (user, article, feedback): each of
    the four signals on one article is its own row, a repeat is idempotent (moves updated_at, adds no
    row), the list is oldest-first with the wire shape, an unknown type is rejected, and it is
    user-scoped. Recorded only — this store method touches no recommender, reception, or report path."""
    sys.path.insert(0, str(ROOT / "examples"))
    import store as store_mod
    st = store_mod.Store("sqlite://")
    uid = st.upsert_user_by_identity("dev", "feedback-store").id
    other = st.upsert_user_by_identity("dev", "feedback-store-other").id

    for fb in ("like", "dislike", "ignore", "read_later"):                     # each distinct type → a row
        assert st.record_recommendation_feedback(uid, "https://a/x", fb, at="2026-06-01T00:00:00+00:00") is True
    # a repeat of the same (user, article, feedback) is idempotent: no new row, returns False
    assert st.record_recommendation_feedback(uid, "https://a/x", "like", at="2026-06-02T00:00:00+00:00") is False

    rows = st.list_recommendation_feedback(uid)
    assert [r["feedback"] for r in rows] == ["like", "dislike", "ignore", "read_later"]   # oldest-first
    assert set(rows[0]) == {"articleId", "feedback", "createdAt", "updatedAt"}
    assert rows[0]["createdAt"] == "2026-06-01T00:00:00+00:00"                  # created stays put
    assert rows[0]["updatedAt"] == "2026-06-02T00:00:00+00:00"                  # updated moves on repeat

    assert st.list_recommendation_feedback(other) == []                        # per-user isolation
    import pytest as _pt
    with _pt.raises(ValueError):                                               # unknown type rejected
        st.record_recommendation_feedback(uid, "https://a/x", "love")


# --------------------------------------------------------------------------- #
# profile — identity + streaks + score history from persisted data; honest empties
# --------------------------------------------------------------------------- #
def test_build_profile_from_persisted_data(backend):
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).date()
    d = lambda n: (today - dt.timedelta(days=n)).isoformat()
    user = {"email": "ada@example.com", "displayName": "Ada Lovelace",
            "createdAt": "2026-01-01T00:00:00+00:00"}
    reads = [
        {"id": 1, "observedAt": f"{d(0)}T10:00:00Z", "scored": {}},
        {"id": 2, "observedAt": f"{d(0)}T11:00:00Z", "scored": {}},          # same day (today)
        {"id": 3, "observedAt": "2026-02-01T09:00:00Z", "scored": {}},
        {"id": 4, "observedAt": "2026-02-02T09:00:00Z", "scored": {}},
        {"id": 5, "observedAt": "2026-02-03T09:00:00Z", "scored": {}},       # 3 consecutive -> longest 3
    ]
    snapshots = [{"id": 1, "mode": "estimate", "overall": 55, "createdAt": "2026-02-01T00:00:00+00:00"},
                 {"id": 2, "mode": "measured", "overall": 62, "createdAt": "2026-02-10T00:00:00+00:00"}]
    p = backend.build_profile(user, reads, snapshots)
    assert p["name"] == "Ada Lovelace" and p["email"] == "ada@example.com"
    assert p["handle"] == "ada"                                             # email local-part, alnum
    assert p["joinedAt"] == "2026-01-01T00:00:00+00:00"
    assert p["streakDays"] == 1                                             # only today's reads
    assert p["longestStreak"] == 3                                          # the Feb 1-3 run
    assert [s["overall"] for s in p["scoreHistory"]] == [55, 62]           # reuses the snapshot trend
    assert p["achievements"] == [] and p["savedCount"] == 0 and "bookmarkCount" not in p
    assert backend.build_profile(user, reads, snapshots, saved_count=3)["savedCount"] == 3
    _assert_json_roundtrips(p)


def test_build_profile_identity_fallbacks(backend):
    p = backend.build_profile({"email": "", "displayName": "", "createdAt": None}, [], [])
    assert p["name"] == "Reader" and p["handle"] == "reader"               # no email/name -> defaults
    assert p["streakDays"] == 0 and p["longestStreak"] == 0 and p["scoreHistory"] == []
    assert isinstance(p["joinedAt"], str) and p["joinedAt"]                # falls back to now (non-null)
    p2 = backend.build_profile(
        {"email": "sam.q@x.com", "displayName": None, "createdAt": "2026-01-01T00:00:00+00:00"}, [], [])
    assert p2["name"] == "sam.q" and p2["handle"] == "samq"                # name = local part; handle alnum


# --------------------------------------------------------------------------- #
# settings normalisation — defaults, merge, coercion (preferences only)
# --------------------------------------------------------------------------- #
def test_normalize_settings_defaults_merge_and_coerce():
    # no stored preferences -> the full honest defaults
    assert api_server.normalize_settings(None) == api_server.DEFAULT_SETTINGS
    assert api_server.normalize_settings({}) == api_server.DEFAULT_SETTINGS

    stored = {"theme": "dark", "readingGoalMinutes": 45,
              "notifications": {"weeklyDigest": False}, "bogus": 123}          # unknown key
    patch = {"politicalOpenness": 999, "language": "x" * 40,
             "notifications": {"streakReminders": True, "blindSpotAlerts": True}}
    m = api_server.normalize_settings(stored, patch)
    assert m["theme"] == "dark" and m["readingGoalMinutes"] == 45              # from stored
    assert m["politicalOpenness"] == 100                                      # patch, clamped [0,100]
    assert m["language"] == "en"                    # Commit 20: unsupported value falls back to English
    assert m["notifications"]["weeklyDigest"] is False                        # stored (deep-merged)
    assert m["notifications"]["streakReminders"] is True                      # patch
    assert m["notifications"]["blindSpotAlerts"] is True                      # 2nd patch key, same group
    assert m["notifications"]["recommendations"] is True                      # untouched default
    assert "bogus" not in m                                                   # unknown key dropped

    # Commit 20: every supported language passes through; case/whitespace normalized; junk → en.
    for lang in ("en", "es", "fr", "de", "pt"):
        assert api_server.normalize_settings({}, {"language": lang})["language"] == lang
    assert api_server.normalize_settings({}, {"language": " ES "})["language"] == "es"
    assert api_server.normalize_settings({}, {"language": "klingon"})["language"] == "en"
    assert api_server.normalize_settings({}, {})["language"] == "en"           # default
    assert set(m) == set(api_server.DEFAULT_SETTINGS)                         # stable shape

    assert api_server.normalize_settings({"theme": "neon"})["theme"] == "system"   # bad enum -> default
    assert api_server.normalize_settings({"politicalOpenness": "abc"})["politicalOpenness"] == 50  # non-int -> default
    _assert_json_roundtrips(m)


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


# --------------------------------------------------------------------------- #
# Preference sliders → per-request recommender parameters (Commit 16)
# --------------------------------------------------------------------------- #
def test_rec_params_mapper_anchors_and_clamps():
    """50 = None (the untouched default stack); the extremes hit the documented anchors; only a
    *moved* slider contributes a key; out-of-range and missing settings are safe."""
    f = api_server.rec_params_from_settings
    assert f(None) is None                                        # no settings at all
    assert f({}) is None                                          # defaults everywhere
    assert f({"politicalOpenness": 50, "recommendationStrength": 50}) is None
    assert f({"politicalOpenness": 0}) == {"openness": 0}
    assert f({"politicalOpenness": 100}) == {"openness": 100}
    assert f({"recommendationStrength": 0}) == {"beta": 0.30}
    assert f({"recommendationStrength": 100}) == {"beta": 0.80}
    both = f({"politicalOpenness": 100, "recommendationStrength": 0})
    assert both == {"openness": 100, "beta": 0.30}
    # clamped by normalize_settings before mapping
    assert f({"politicalOpenness": 999}) == {"openness": 100}
    assert f({"politicalOpenness": -5}) == {"openness": 0}
    # monotone: more openness never lowers the RWE-B bridge budget (W1)
    budgets = [dict(api_server.blend_plan_for(f({"politicalOpenness": v})))["rwe-b"]
               for v in (0, 25, 50, 75, 100)]
    assert budgets == [4, 5, 6, 7, 8]


def test_blend_plan_for_openness_budget():
    """W1: openness moves the RWE-B bridge budget; slider-50 / absent is byte-identical to the
    historical DEFAULT_BLEND_PLAN; the total slot count and strategy order are preserved."""
    D = api_server.DEFAULT_BLEND_PLAN
    total = sum(k for _, k in D)
    assert api_server.blend_plan_for(None) is D                       # no params → default (identity)
    assert api_server.blend_plan_for({"beta": 0.3}) is D              # no openness key → default
    assert api_server.blend_plan_for({"openness": 50}) == D           # slider 50 → byte-identical
    assert dict(api_server.blend_plan_for({"openness": 0})) == {"rwe-b": 4, "rwe-d": 5, "adaptive": 5}
    assert dict(api_server.blend_plan_for({"openness": 100})) == {"rwe-b": 8, "rwe-d": 3, "adaptive": 3}
    for op in (0, 25, 50, 75, 100):
        p = api_server.blend_plan_for({"openness": op})
        assert sum(k for _, k in p) == total                         # total slots preserved
        assert [n for n, _ in p] == [n for n, _ in D]                # order preserved


def test_openness_reshapes_the_served_feed(backend, user):
    """W1: the openness slider now VISIBLY moves a (sided) reader's served feed via the RWE-B
    bridge-slot budget — the reshape the W1 audit proved epsilon could NOT do (identical=True →
    identical=False). Slider 50 stays byte-identical to the untouched default; deterministic."""
    def ids(op):
        params = api_server.rec_params_from_settings({"politicalOpenness": op}) if op != 50 else None
        return [r["article"]["id"] for r in backend.recommendations(user, None, params)]
    default = [r["article"]["id"] for r in backend.recommendations(user, None, None)]
    assert ids(50) == default                                        # untouched slider → byte-identical
    assert ids(0) != default and ids(100) != default                # openness now MOVES the feed (W1)
    assert ids(0) != ids(100)                                        # the two extremes differ
    assert ids(0) == ids(0)                                          # deterministic


def _rec_ids(backend, u, strategy, params):
    return [r["article"]["id"] for r in backend.recommendations(u, strategy, params)]


def test_sliders_change_the_feed_and_defaults_do_not(backend, user):
    """The impact proof: untouched sliders (params=None) serve the exact pre-slider feed; moved
    sliders change it; adaptive ignores the params (its epsilon is the satisfaction policy)."""
    base_b = _rec_ids(backend, user, "rwe-b", None)
    base_d = _rec_ids(backend, user, "rwe-d", None)
    assert base_b and base_d

    # identity anchors — a rebuild at the default constants is byte-identical to the cached stack
    assert _rec_ids(backend, user, "rwe-b", {"epsilon": 0.9}) == base_b
    assert _rec_ids(backend, user, "rwe-d", {"beta": 0.5}) == base_d

    # impact — openness (epsilon) still reaches the rwe-b MODEL: its walk scores move with the
    # slider. Since Commit R1.5 the rwe-b slice is cross-cutting-first, which is deliberately
    # robust to epsilon when cross supply exceeds the slice (bridge items keep their sim-based
    # erasure, so the cross tier's internal order barely moves) — the sliced feed can therefore be
    # identical across epsilon extremes on a cross-rich corpus. The slider's effect is asserted
    # where it truthfully lives: the model scores (and fallback composition on thin corpora).
    row = int(np.flatnonzero(np.asarray(backend.rec.rec_dataset.user_ids)
                             == np.asarray(backend.mind.dataset.user_ids)[user])[0])
    lo_scores = api_server.Backend._model_for(
        backend.rec, "rwe-b", {"epsilon": 0.70}).scores(np.array([row]))[0]
    hi_scores = api_server.Backend._model_for(
        backend.rec, "rwe-b", {"epsilon": 0.97}).scores(np.array([row]))[0]
    assert not np.allclose(lo_scores, hi_scores)                   # openness moves the rwe-b model
    lo_d = _rec_ids(backend, user, "rwe-d", {"beta": 0.30})
    hi_d = _rec_ids(backend, user, "rwe-d", {"beta": 0.80})
    assert lo_d != base_d and hi_d != base_d and lo_d != hi_d      # strength moves rwe-d both ways

    # the blended default feed responds too (params apply per strategy inside the blend)
    blend = _rec_ids(backend, user, None, None)
    moved = _rec_ids(backend, user, None, {"epsilon": 0.70, "beta": 0.80})
    assert blend != moved

    # adaptive serves the shared policy model regardless of params
    assert _rec_ids(backend, user, "adaptive", {"epsilon": 0.7, "beta": 0.8}) == \
        _rec_ids(backend, user, "adaptive", None)


def test_slider_params_do_not_mutate_the_shared_stack(backend, user):
    """A per-request override must never change the cached models — the same default request
    before and after a params request returns the identical feed."""
    before = _rec_ids(backend, user, "rwe-b", None)
    _rec_ids(backend, user, "rwe-b", {"epsilon": 0.70})            # a moved-slider request in between
    _rec_ids(backend, user, "rwe-d", {"beta": 0.80})
    assert _rec_ids(backend, user, "rwe-b", None) == before
    assert float(backend.rec.models["rwe-b"].epsilon) == 0.9       # cached hyperparams untouched
    assert float(backend.rec.models["rwe-d"].beta) == 0.5
