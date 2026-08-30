"""Commit 21a — Recommendation Explainability: the observer must tell the truth.

Two guarantees are pinned here:

* **Parity** — the explain pass must describe EXACTLY the feed the serving path returns (same
  models, same plan, same order); an explanation that can disagree with the card it explains is
  worse than no explanation.
* **Evidence** — every surfaced field must be derivable from the recommender: ranks consistent
  with scores, match bands from ranks, cross-cutting equal to the serving flag, familiarity from
  the reader's measured outlet shares, and reason templates that claim only what was computed.
"""
import datetime as _dt
import pathlib
import sys
import urllib.parse

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import api_server  # noqa: E402
import rec_explain  # noqa: E402

STRATEGIES = ("rwe-b", "rwe-d", "adaptive")


def _seeded_at() -> str:
    """A day old, computed rather than pinned.

    Seeded articles have to clear the recommendation-candidate freshness window
    (``RWE_FEED_MAX_AGE_DAYS``, default 60 days) or ``feed_source`` exports an empty corpus and the
    engine is handed nothing to recommend. The literal that used to sit here — 2026-07-01 — aged out
    of that window on 2026-08-30 and took this test with it. A fixed date inside a rolling window is
    a fuse, not a fixture.
    """
    return (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)).isoformat()


@pytest.fixture(scope="module")
def backend():
    """A small synthetic backend (real pipeline, generated clicks) built once."""
    profile = api_server.DatasetProfile.synthetic(n_users=200, max_items=500, seed=0)
    return api_server.Backend(profile)


@pytest.fixture(scope="module")
def user(backend):
    return backend.demo_user


def _row_and_seen(backend, u):
    """The reader's row in the rec graph + their seen item set (read-only attrs)."""
    uid = np.asarray(backend.base_corpus.mind.dataset.user_ids)[u]
    row = int(np.flatnonzero(np.asarray(backend.rec.rec_dataset.user_ids) == uid)[0])
    return row, {int(i) for i in backend.rec.fg.seen_items(row)}


# --------------------------------------------------------------------------- parity
@pytest.mark.parametrize("strategy", [None, "rwe-b", "rwe-d", "adaptive"])
def test_explain_matches_the_served_feed(backend, user, strategy):
    served = backend.recommendations(user, strategy)
    exp = backend.explain_recommendations(user, strategy)
    assert [r["articleId"] for r in exp["recommendations"]] == [r["article"]["id"] for r in served]
    assert [r["chosenBy"] for r in exp["recommendations"]] == [r["strategy"] for r in served]
    assert exp["trace"]["served"] == len(served)


@pytest.mark.parametrize("params", [{"epsilon": 0.97}, {"beta": 0.3},
                                    {"epsilon": 0.70, "beta": 0.8}])
def test_explain_matches_the_served_feed_under_slider_params(backend, user, params):
    served = backend.recommendations(user, None, params)
    exp = backend.explain_recommendations(user, None, params)
    assert [r["articleId"] for r in exp["recommendations"]] == [r["article"]["id"] for r in served]
    used_b = exp["trace"]["strategies"]["rwe-b"]["paramsUsed"]
    assert used_b["source"] == "sliders"
    if "epsilon" in params:
        assert used_b["epsilon"] == pytest.approx(params["epsilon"])
    if "beta" in params:
        assert exp["trace"]["strategies"]["rwe-d"]["paramsUsed"]["beta"] == pytest.approx(params["beta"])


def test_explain_matches_the_served_feed_under_interest_weights(backend, user):
    """Interest Intensity parity: the observer replicates the SAME per-topic nudge the serving
    path applies (Backend._interest_rerank, shared), so the explained feed is the served feed
    under interest weights too — including combined with a model slider."""
    cats = sorted({str(c).strip().lower()
                   for c in np.asarray(backend.base_corpus.mind.categories) if str(c).strip()})
    weights = {cats[0]: 10, cats[-1]: 1}
    for params in ({"interests": weights}, {"beta": 0.3, "interests": weights}):
        served = backend.recommendations(user, None, params)
        exp = backend.explain_recommendations(user, None, params)
        assert [r["articleId"] for r in exp["recommendations"]] == \
            [r["article"]["id"] for r in served]
        assert [r["chosenBy"] for r in exp["recommendations"]] == \
            [r["strategy"] for r in served]


# --------------------------------------------------------------------------- evidence
def test_cross_cutting_parity_and_derivation(backend, user):
    served = {r["article"]["id"]: r for r in backend.recommendations(user)}
    exp = backend.explain_recommendations(user)
    for r in exp["recommendations"]:
        cc = r["crossCutting"]
        assert cc["value"] == served[r["articleId"]]["crossCutting"]
        assert cc["value"] == api_server._cross_of(np.sign(cc["userMeanLean"]), cc["articleLean"],
                                                   cc["articlePolitical"])


def test_rank_band_and_score_invariants(backend, user):
    exp = backend.explain_recommendations(user)
    slices = {"rwe-b": 6, "rwe-d": 4, "adaptive": 4}
    per_strategy_last_rank = {}
    for r in exp["recommendations"]:
        chosen = r["chosenBy"]
        own = r["byStrategy"][chosen]
        assert r["rank"] == own["rank"] >= 1
        # inSlice = occupies an admitted slot. With slice admission (Commit R1: rwe-b admits
        # political items only) a slot-holder's RAW rank may exceed the slice size, so the old
        # rank <= slice bound no longer holds — membership is the invariant.
        assert own["inSlice"]
        assert 0.0 < r["scorePercentile"] <= 100.0
        n_cand = exp["trace"]["strategies"][chosen]["candidates"]
        assert r["match"] == rec_explain.match_band(own["rank"] - 1, n_cand)
        # Within one strategy, serving order is score order — per preference tier. rwe-b serves
        # cross-cutting items first (Commit R1.5), so its order is: all cross (rank-monotonic),
        # then all non-cross (rank-monotonic). Other strategies stay strictly rank-monotonic.
        tier = (chosen, bool(r["crossCutting"]["value"]) if chosen == "rwe-b" else None)
        prev = per_strategy_last_rank.get(tier)
        if prev is not None:
            assert own["rank"] > prev
        per_strategy_last_rank[tier] = own["rank"]
        if chosen == "rwe-b" and r["crossCutting"]["value"]:
            assert per_strategy_last_rank.get(("rwe-b", False)) is None, \
                "a cross-cutting rwe-b card must never follow a same-side one"
        for s in STRATEGIES:
            e = r["byStrategy"][s]
            assert (e["rank"] is None) or e["rank"] >= 1
            assert isinstance(e["score"], float)


def test_familiarity_bands_and_reason_truthfulness(backend, user):
    assert api_server._familiarity_band(0, 0.0) == "never"
    assert api_server._familiarity_band(1, 0.01) == "rarely"
    assert api_server._familiarity_band(10, 0.2) == "familiar"

    fam = api_server._familiarity_of(backend.base_corpus.pop, user)
    for r in backend.recommendations(user):
        band = fam(r["article"]["publisher"])["band"]
        reason = r["reason"]
        if "you rarely read" in reason:
            assert band == "rarely", (reason, band)
        if "you've never read" in reason:
            assert band == "never", (reason, band)
        if band == "familiar":
            assert "rarely read" not in reason and "never read" not in reason, (reason, band)


def test_adaptive_copy_states_the_neutral_truth(backend, user):
    recs = [r for r in backend.recommendations(user, "adaptive")]
    assert recs
    for r in recs:
        assert "open-mindedness signal" in r["reason"]
        assert "how open you've been" not in r["reason"]      # the pre-21a over-claim


def test_viewpoint_shift_is_report_identical_and_directional(backend, user):
    """The 'Estimated effect' must be the report's own computation: current == rep['viewpoint'],
    and appending a right article must move the right share up (and never invent a projection
    for non-political articles)."""
    import health_report as hr
    exp = backend.explain_recommendations(user)
    rep = hr.user_report(backend.base_corpus.pop, backend.base_corpus.mind, user)
    cur_rep = rep["viewpoint"]
    saw_projection = False
    for r in exp["recommendations"]:
        vs = r["viewpointShift"]
        if vs is None:
            continue
        saw_projection = True
        assert vs["estimated"] is True and "political reads" in vs["basis"]
        assert vs["current"]["left"] == pytest.approx(round(100 * cur_rep[0], 1))
        assert vs["current"]["right"] == pytest.approx(round(100 * cur_rep[2], 1))
        total = vs["after"]["left"] + vs["after"]["center"] + vs["after"]["right"]
        assert total == pytest.approx(100.0, abs=0.3)
        if r["lean"] > 0.5:          # a right article must not move the right share DOWN
            assert vs["after"]["right"] >= vs["current"]["right"]
        if r["lean"] < -0.5:
            assert vs["after"]["left"] >= vs["current"]["left"]
    assert saw_projection, "no political recommendation produced a projection"


def test_topic_share_and_lean_gap_evidence(backend, user):
    import health_report as hr
    exp = backend.explain_recommendations(user)
    mean_lean = exp["trace"]["reader"]["meanLean"]
    for r in exp["recommendations"]:
        assert r["leanGap"] == pytest.approx(abs(r["lean"] - mean_lean), abs=0.011)
        ts = r["topicShare"]
        if ts is not None:
            assert 0.0 <= ts["share"] <= 1.0


def test_two_hop_and_degree_evidence_bounds(backend, user):
    exp = backend.explain_recommendations(user)
    for r in exp["recommendations"]:
        c = r["connectivity"]
        assert 0 <= c["readsWithinTwoHops"] <= c["graphReads"]
        assert 0.0 < r["longTail"]["degreePercentile"] <= 100.0
        assert r["longTail"]["itemDegree"] >= 0
        assert r["outletFamiliarity"]["band"] in ("never", "rarely", "familiar")


def test_trace_shape_and_notes(backend, user):
    exp = backend.explain_recommendations(user)
    t = exp["trace"]
    assert t["graph"]["users"] > 0 and t["graph"]["items"] > 0 and t["graph"]["edges"] > 0
    assert {p["strategy"] for p in t["plan"]} == set(STRATEGIES)
    assert t["strategies"]["rwe-b"]["seenExcluded"] == len(_row_and_seen(backend, user)[1])
    assert any("Saved status does not affect recommendations" in n for n in exp["notes"])
    assert any("neutral exposure" in n for n in exp["notes"])


# --------------------------------------------------------------------------- exclusions
def test_exclusion_verdicts(backend, user):
    row, seen = _row_and_seen(backend, user)
    rec = backend.rec
    exp = backend.explain_recommendations(user)
    feed_ids = {r["articleId"] for r in exp["recommendations"]}

    # 1) an article the reader read → seen_excluded
    seen_id = str(rec.rec_ids[next(iter(seen))])
    v = backend.explain_recommendations(user, article=seen_id)["exclusion"]
    assert v["verdict"] == "seen_excluded"

    # 2) a served article → recommended (with the choosing strategy)
    served_id = next(iter(feed_ids))
    v = backend.explain_recommendations(user, article=served_id)["exclusion"]
    assert v["verdict"] == "recommended" and v["detail"].split()[-1] in STRATEGIES

    # 3) an unseen, unserved graph article → below_cutoff, with the numbers
    other = next(str(rec.rec_ids[j]) for j in range(len(rec.rec_ids))
                 if j not in seen and str(rec.rec_ids[j]) not in feed_ids)
    v = backend.explain_recommendations(user, article=other)["exclusion"]
    assert v["verdict"] == "below_cutoff"
    for s in STRATEGIES:
        e = v["byStrategy"][s]
        # not served ⇒ outside every strategy's served slice (rank is 1-based; None = unranked)
        assert e["cutoff"] >= 1
        assert e["rank"] is None or e["rank"] > e["cutoff"]
        assert isinstance(e["score"], float)

    # 4) an unknown article → not_in_catalog
    v = backend.explain_recommendations(user, article="https://nowhere.example/story")["exclusion"]
    assert v["verdict"] == "not_in_catalog"


# --------------------------------------------------------------------------- measured + endpoint
def test_measured_reader_explain_endpoint(tmp_path, monkeypatch):
    """The full engine path: a measured reader's explain output over HTTP must match their served
    feed, carry the read-join evidence, and answer an exclusion query by raw URL."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    import importlib.util
    import store as store_mod

    db = f"sqlite:///{tmp_path}/explain.db"
    st = store_mod.Store(db)
    outlets = [("The Guardian", -1.5), ("NPR", -1.0), ("Associated Press", 0.0), ("Fox News", 1.6)]
    for pub, lean in outlets:
        dom = pub.lower().replace(" ", "")
        for k in range(3):
            u = f"https://{dom}.example.com/story/{k}"
            st.upsert_feed_article(
                canonical_url=u, url=u, publisher=pub, source_publisher=pub,
                title=f"{pub} covers the vote and the economy, item {k}", description="d",
                body=None, published_at=_seeded_at(), source_feed="seed",
                source_type="rss",
                scored={"article_id": u, "outlet": pub, "category": "Politics", "lean": lean,
                        "political": True, "title": f"{pub} story {k}"})

    monkeypatch.setenv("RWE_DB_URL", db)
    monkeypatch.setenv("RWE_RECS_SOURCE", "feed")
    monkeypatch.setenv("RWE_FEED_MIN_ARTICLES", "5")
    monkeypatch.setenv("RWE_CORPUS_MIN_ARTICLES", "5")
    monkeypatch.setenv("RWE_N_USERS", "80")
    monkeypatch.setenv("RWE_MAX_ITEMS", "200")
    monkeypatch.delenv("RWE_INTERNAL_SECRET", raising=False)

    spec = importlib.util.spec_from_file_location("api_fastapi_explain",
                                                  ROOT / "examples" / "api_fastapi.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["api_fastapi_explain"] = m
    spec.loader.exec_module(m)

    with TestClient(m.app) as client:
        uid = client.post("/api/internal/users",
                          json={"provider": "google", "providerAccountId": "explain-a"}).json()["userId"]
        h = {"X-IH-User-Id": str(uid)}
        arts = client.get("/api/discover?limit=50").json()["articles"]
        client.post("/api/me/reads",
                    json={"reads": [{"url": x["url"], "title": x["headline"]} for x in arts[:5]]},
                    headers=h)

        served = client.get("/api/recommendations", headers=h).json()
        assert served and all("healthImpact" not in r for r in served)

        exp = client.get("/api/internal/recommendations/explain", headers=h).json()
        assert [r["articleId"] for r in exp["recommendations"]] == \
               [r["article"]["id"] for r in served]
        assert exp["trace"]["reader"]["reads"] == {"total": 5, "joined": 5}
        # 21a.2 debugging identity: the exact recommendation instance is nameable
        assert exp["explainId"].startswith("rec_") and f"u{uid}_g" in exp["explainId"]
        assert isinstance(exp["corpusGeneration"], int)
        assert exp["modelVersion"] == {"readingVersion": 5, "receptionVersion":
                                       exp["modelVersion"]["receptionVersion"]}

        q = urllib.parse.quote(arts[0]["url"], safe="")
        v = client.get(f"/api/internal/recommendations/explain?article={q}", headers=h).json()
        assert v["exclusion"]["verdict"] == "seen_excluded"
