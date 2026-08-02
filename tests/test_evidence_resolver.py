"""Commit 21a.3 — Evidence Resolver: ONE sentence per recommendation, licensed by computed
evidence, checkable by ``type`` (never by parsing prose).

Covers the approved validation cases: (1) same story / different publisher → the story-first
sentence; (2) different story never says "same story"; (3) same publisher suppresses it;
(4) no prior read falls through to another truthful explanation; (5) multiple story reads cite
the most recent one — plus the wording variants, strict never-combine, the claim-free fallback,
``validate()`` failure modes, a real-clustering index round-trip, and the endpoint regression:
every served explanation must validate clean against its own evidence (the 21d pipeline hook).
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import api_server as engine  # noqa: E402
import evidence_resolver as er  # noqa: E402

FOX = "https://foxnews.example.com/story/fusion-1"
CNN = "https://cnn.example.com/story/fusion-2"
FOX2 = "https://foxnews.example.com/story/fusion-3"
GUARDIAN = "https://theguardian.example.com/story/fusion-4"

# All four cover the SAME event (one story); membership is what P1 consumes.
_COVERAGE = [
    {"url": FOX, "publisher": "Fox News", "publishedAt": "2026-07-09T08:00:00+00:00"},
    {"url": CNN, "publisher": "CNN", "publishedAt": "2026-07-10T09:00:00+00:00"},
    {"url": FOX2, "publisher": "Fox News", "publishedAt": "2026-07-09T12:00:00+00:00"},
    {"url": GUARDIAN, "publisher": "The Guardian", "publishedAt": "2026-07-09T10:00:00+00:00"},
]
INDEX = {er._canon(m["url"]): {"storyId": "s1", "coverage": _COVERAGE} for m in _COVERAGE}

SENTENCE_SIGNATURES = ("covered the same story", "latest update", "following this story",
                       "You've been reading about", "broadens your source diversity",
                       "another political perspective", "less frequently recommended",
                       "Broadens your")


def _fam(bands):
    def fam(publisher):
        reads, share = bands.get(publisher, (10, 0.2))
        return {"reads": reads, "share": share, "band": engine._familiarity_band(reads, share)}
    return fam


def _ctx(reads=(), bands=None, tops=()):
    return {"reads": [{"url": er._canon(u), "publisher": p, "publishedAt": None}
                      for u, p in reads],
            "familiarity": _fam(bands or {}), "top_topics": list(tops)}


def _rec(url, publisher, topic="Politics", cross=False, strategy="rwe-b",
         published="2026-07-09T09:00:00+00:00"):
    return {"article": {"url": url, "id": url, "publisher": publisher, "topic": topic,
                        "lean": 1.0 if cross else 0.0, "publishedAt": published},
            "crossCutting": cross, "strategy": strategy}


def _one_signature(message):
    return sum(1 for s in SENTENCE_SIGNATURES if s in message)


# ---------------------------------------------------------------- the five approved cases
def test_case1_same_story_different_publisher():
    ctx = _ctx(reads=[(FOX, "Fox News")])
    out = er.resolve(_rec(CNN, "CNN", published="2026-07-09T09:00:00+00:00"), ctx, INDEX)
    assert out["type"] == "story_match" and out["priority"] == 1
    assert out["message"] == ("You already read this story from Fox News. "
                              "Here's how CNN covered the same story.")
    assert er.validate(out, _rec(CNN, "CNN"), ctx, INDEX) == []


def test_case2_different_story_never_says_same_story():
    ctx = _ctx(reads=[(FOX, "Fox News")], bands={"Reuters": (0, 0.0)})
    out = er.resolve(_rec("https://reuters.example.com/other-event", "Reuters"), ctx, INDEX)
    assert out["type"] != "story_match"
    assert "same story" not in out["message"]


def test_case3_same_publisher_suppresses_story_match():
    ctx = _ctx(reads=[(FOX, "Fox News")], bands={"Fox News": (3, 0.5)})
    out = er.resolve(_rec(FOX2, "Fox News", cross=True), ctx, INDEX)
    assert out["type"] != "story_match"          # falls through (here: bridge)
    assert out["type"] == "bridge"


def test_case4_no_prior_read_falls_back():
    ctx = _ctx(reads=[], bands={"CNN": (0, 0.0)})
    out = er.resolve(_rec(CNN, "CNN"), ctx, INDEX)
    assert out["type"] == "new_publisher"
    assert out["message"].startswith("You've never read CNN")


def test_case5_most_recent_story_read_is_cited():
    # reads oldest-first: CNN then Guardian — the sentence must cite the Guardian read;
    # >= 2 story reads makes this the "following" variant by design.
    ctx = _ctx(reads=[(CNN, "CNN"), (GUARDIAN, "The Guardian")])
    out = er.resolve(_rec(FOX, "Fox News"), ctx, INDEX)
    assert out["type"] == "story_match" and out["variant"] == "following"
    assert out["evidence"]["readPublisher"] == "The Guardian"
    assert er.validate(out, _rec(FOX, "Fox News"), ctx, INDEX) == []


# ---------------------------------------------------------------- wording variants
def test_variant_follow_up_when_rec_is_newer():
    ctx = _ctx(reads=[(FOX, "Fox News")])
    out = er.resolve(_rec(CNN, "CNN", published="2026-07-10T09:00:00+00:00"), ctx, INDEX)
    assert out["variant"] == "follow_up"
    assert out["message"] == ("You already read the earlier coverage from Fox News. "
                              "Here's CNN's latest update.")
    assert er.validate(out, _rec(CNN, "CNN"), ctx, INDEX) == []


def test_variant_following_when_reader_read_two_members():
    ctx = _ctx(reads=[(FOX, "Fox News"), (GUARDIAN, "The Guardian")])
    out = er.resolve(_rec(CNN, "CNN"), ctx, INDEX)
    assert out["variant"] == "following"
    assert out["message"] == "You've been following this story. Here's CNN's coverage."


def test_bridge_and_new_publisher_outrank_topic_continuity():
    """21a.4 priority: a cross-cutting or unfamiliar-publisher reason beats a mere shared topic."""
    ctx = _ctx(bands={"CNN": (9, 0.3)}, tops=["Politics"])
    # cross-cutting + top-topic → bridge (P2), not topic_continuity (P4)
    assert er.resolve(_rec("https://cnn.example.com/x", "CNN", cross=True), ctx, {})["type"] == "bridge"
    # new/rarely-read publisher + top-topic → new_publisher (P3), not topic_continuity
    ctx_new = _ctx(bands={"CNN": (0, 0.0)}, tops=["Politics"])
    assert er.resolve(_rec("https://cnn.example.com/x", "CNN"), ctx_new, {})["type"] == "new_publisher"
    # only a plain, familiar-publisher topic match falls through to topic_continuity
    plain = er.resolve(_rec("https://cnn.example.com/x", "CNN", cross=False), ctx, {})
    assert plain["type"] == "topic_continuity" and "another outlet" in plain["message"]


def test_story_match_overrides_topic_continuity():
    """A provable same-story read wins even when the article's topic is a top topic (Case 4)."""
    ctx = _ctx(reads=[(FOX, "Fox News")], tops=["Politics"])
    out = er.resolve(_rec(CNN, "CNN"), ctx, INDEX)
    assert out["type"] == "story_match"


# ---------------------------------------------------------------- discipline
def test_priorities_never_combine():
    """A rec satisfying FOUR gates at once yields exactly the P1 sentence and nothing else."""
    ctx = _ctx(reads=[(FOX, "Fox News")], bands={"CNN": (0, 0.0)}, tops=["Politics"])
    out = er.resolve(_rec(CNN, "CNN", cross=True, strategy="rwe-d",
                          published="2026-07-09T09:00:00+00:00"), ctx, INDEX)
    assert out["type"] == "story_match"
    assert _one_signature(out["message"]) == 1


def test_p6_claim_free_fallback():
    ctx = _ctx(bands={"CNN": (9, 0.3)}, tops=["Business"])
    out = er.resolve(_rec("https://cnn.example.com/x", "CNN", cross=False, strategy="rwe-b"), ctx, {})
    assert out["type"] == "coverage_breadth" and out["priority"] == 6
    for claim in ("never read", "rarely read", "perspective", "same story"):
        assert claim not in out["message"]


def test_every_type_yields_exactly_one_signature():
    cases = [
        (_rec(CNN, "CNN"), _ctx(reads=[(FOX, "Fox News")]), INDEX),
        (_rec("https://cnn.example.com/x", "CNN"), _ctx(bands={"CNN": (9, 0.3)}, tops=["Politics"]), {}),
        (_rec("https://cnn.example.com/x", "CNN"), _ctx(bands={"CNN": (0, 0.0)}), {}),
        (_rec("https://cnn.example.com/x", "CNN", cross=True), _ctx(bands={"CNN": (9, 0.3)}), {}),
        (_rec("https://cnn.example.com/x", "CNN", strategy="rwe-d"), _ctx(bands={"CNN": (9, 0.3)}), {}),
        (_rec("https://cnn.example.com/x", "CNN"), _ctx(bands={"CNN": (9, 0.3)}), {}),
    ]
    seen = set()
    for rec, ctx, idx in cases:
        out = er.resolve(rec, ctx, idx)
        assert _one_signature(out["message"]) == 1, out
        assert er.validate(out, rec, ctx, idx) == []
        seen.add(out["type"])
    assert seen == set(er.TYPES)


# ---------------------------------------------------------------- validate() failure modes
def test_validate_catches_tampering_and_over_claims():
    ctx = _ctx(reads=[(FOX, "Fox News")])
    rec = _rec(CNN, "CNN", published="2026-07-09T09:00:00+00:00")
    good = er.resolve(rec, ctx, INDEX)

    tampered = dict(good, evidence=dict(good["evidence"], storyId="s999"))
    assert any("story ids differ" in f for f in er.validate(tampered, rec, ctx, INDEX))

    same_pub = dict(good, evidence=dict(good["evidence"], readPublisher="CNN"))
    assert any("publishers do not differ" in f for f in er.validate(same_pub, rec, ctx, INDEX))

    wrong_variant = dict(good, variant="follow_up")   # sentence says "same story"
    assert any("sentence not allowed" in f for f in er.validate(wrong_variant, rec, ctx, INDEX))

    fake_bridge = {"type": "bridge", "priority": 2, "message": "x", "evidence": {}}
    assert er.validate(fake_bridge, _rec(CNN, "CNN", cross=False), ctx, INDEX)

    fake_tail = {"type": "long_tail", "priority": 5, "message": "x", "evidence": {}}
    assert er.validate(fake_tail, _rec(CNN, "CNN", strategy="rwe-b"), ctx, INDEX)

    fake_topic = {"type": "topic_continuity", "priority": 4, "message": "x", "evidence": {}}
    assert er.validate(fake_topic, _rec(CNN, "CNN", topic="Energy"), _ctx(tops=["Politics"]), INDEX)


# ---------------------------------------------------------------- Commit 23: structured parts
def test_parts_story_variants_and_validate():
    ctx = _ctx(reads=[(FOX, "Fox News")])
    same = _rec(CNN, "CNN", published="2026-07-09T09:00:00+00:00")
    out = er.resolve(same, ctx, INDEX)
    assert out["readerFact"] == {"key": "read_story_from", "params": {"publisher": "Fox News"}}
    assert out["contribution"] == {"key": "covered_same_story", "params": {"publisher": "CNN"}}
    assert er.validate(out, same, ctx, INDEX) == []

    update = _rec(CNN, "CNN", published="2026-07-10T09:00:00+00:00")
    out2 = er.resolve(update, ctx, INDEX)
    assert out2["contribution"] == {"key": "story_update", "params": {"publisher": "CNN"}}
    assert er.validate(out2, update, ctx, INDEX) == []

    ctx3 = _ctx(reads=[(FOX, "Fox News"), (GUARDIAN, "The Guardian")])
    out3 = er.resolve(_rec(CNN, "CNN"), ctx3, INDEX)
    assert out3["readerFact"] == {"key": "following_story", "params": {"n": 2}}
    assert out3["contribution"] == {"key": "story_coverage", "params": {"publisher": "CNN"}}
    assert er.validate(out3, _rec(CNN, "CNN"), ctx3, INDEX) == []


def test_parts_new_publisher_bands():
    never = er.resolve(_rec("https://cnn.example.com/x", "CNN"), _ctx(bands={"CNN": (0, 0.0)}), {})
    assert never["readerFact"] == {"key": "never_read_publisher", "params": {"publisher": "CNN"}}
    assert never["contribution"]["key"] == "add_new_publisher"

    ctx = _ctx(bands={"CNN": (2, 0.02)})   # share < 5 % → the "rarely" band
    rarely = er.resolve(_rec("https://cnn.example.com/x", "CNN"), ctx, {})
    assert rarely["readerFact"] == {"key": "rarely_read_publisher",
                                    "params": {"publisher": "CNN", "n": 2}}
    assert er.validate(rarely, _rec("https://cnn.example.com/x", "CNN"), ctx, {}) == []


def test_parts_topic_long_tail_and_claim_free():
    ctx = _ctx(bands={"CNN": (9, 0.3)}, tops=["Politics"])
    topic = er.resolve(_rec("https://cnn.example.com/x", "CNN", cross=False), ctx, {})
    assert topic["readerFact"] == {"key": "top_topic", "params": {"topic": "Politics"}}
    assert topic["contribution"]["key"] == "more_topic_coverage"
    assert er.validate(topic, _rec("https://cnn.example.com/x", "CNN", cross=False), ctx, {}) == []

    # long_tail: the documented exception — contribution only, NEVER a reader fact
    tail_ctx = _ctx(bands={"CNN": (9, 0.3)})
    tail = er.resolve(_rec("https://cnn.example.com/x", "CNN", strategy="rwe-d"), tail_ctx, {})
    assert "readerFact" not in tail
    assert tail["contribution"] == {"key": "rare_in_feeds", "params": {}}
    assert er.validate(tail, _rec("https://cnn.example.com/x", "CNN", strategy="rwe-d"), tail_ctx, {}) == []

    # coverage_breadth stays part-free
    cb = er.resolve(_rec("https://cnn.example.com/x", "CNN"), _ctx(bands={"CNN": (9, 0.3)}), {})
    assert cb["type"] == "coverage_breadth"
    assert "readerFact" not in cb and "contribution" not in cb


def test_bridge_semantic_profile_banding():
    rec = _rec("https://cnn.example.com/x", "CNN", cross=True)
    # no reader_mean_lean in ctx → balanced (the no-claim default) → article-centric, no parts
    ctx = _ctx(bands={"CNN": (9, 0.3)})
    out = er.resolve(rec, ctx, {})
    assert out["type"] == "bridge"
    assert out["evidence"]["readerPoliticalProfile"] == "balanced"
    assert "readerFact" not in out and "contribution" not in out
    assert er.validate(out, rec, ctx, {}) == []

    # near-center mean stays balanced; presentation never sees the raw number
    ctx_near = dict(ctx, reader_mean_lean=0.3)
    near = er.resolve(rec, ctx_near, {})
    assert near["evidence"]["readerPoliticalProfile"] == "balanced"
    assert "readerFact" not in near

    # meaningfully off-center → semantic profile + reader-first parts, validate clean
    for mean, profile, key in ((0.8, "leans_right", "political_lean_right"),
                               (-0.8, "leans_left", "political_lean_left")):
        ctx_lean = dict(ctx, reader_mean_lean=mean)
        lean = er.resolve(rec, ctx_lean, {})
        assert lean["evidence"]["readerPoliticalProfile"] == profile
        assert lean["readerFact"] == {"key": key, "params": {}}
        assert lean["contribution"] == {"key": "other_side_perspective", "params": {}}
        assert er.validate(lean, rec, ctx_lean, {}) == []


def test_validate_catches_part_tampering():
    ctx = _ctx(reads=[(FOX, "Fox News")])
    rec = _rec(CNN, "CNN", published="2026-07-09T09:00:00+00:00")
    good = er.resolve(rec, ctx, INDEX)

    wrong_pub = dict(good, contribution={"key": "covered_same_story", "params": {"publisher": "Reuters"}})
    assert any("contribution cites a publisher" in f for f in er.validate(wrong_pub, rec, ctx, INDEX))

    wrong_fact = dict(good, readerFact={"key": "read_story_from", "params": {"publisher": "CNN"}})
    assert any("readerFact cites a publisher" in f for f in er.validate(wrong_fact, rec, ctx, INDEX))

    # a balanced bridge must never carry reader-first parts
    brec = _rec("https://cnn.example.com/x", "CNN", cross=True)
    bctx = _ctx(bands={"CNN": (9, 0.3)})
    bridge = er.resolve(brec, bctx, {})
    forged = dict(bridge, readerFact={"key": "political_lean_right", "params": {}},
                  contribution={"key": "other_side_perspective", "params": {}})
    assert any("balanced bridge" in f for f in er.validate(forged, brec, bctx, {}))

    # evidence profile must match the recomputed profile when the ctx carries the mean
    lean_ctx = dict(bctx, reader_mean_lean=0.8)
    lean = er.resolve(brec, lean_ctx, {})
    tampered = dict(lean, evidence=dict(lean["evidence"], readerPoliticalProfile="leans_left"))
    assert any("recomputed profile" in f for f in er.validate(tampered, brec, lean_ctx, {}))

    # long_tail may never claim a reader fact
    tail = er.resolve(_rec("https://cnn.example.com/x", "CNN", strategy="rwe-d"), bctx, {})
    tail_forged = dict(tail, readerFact={"key": "never_read_publisher", "params": {"publisher": "CNN"}})
    assert any("documented exception" in f for f in er.validate(tail_forged,
               _rec("https://cnn.example.com/x", "CNN", strategy="rwe-d"), bctx, {}))

    # rarely count must match the evidence
    rctx = _ctx(bands={"CNN": (2, 0.02)})
    rare = er.resolve(_rec("https://cnn.example.com/x", "CNN"), rctx, {})
    off_by_one = dict(rare, readerFact={"key": "rarely_read_publisher",
                                        "params": {"publisher": "CNN", "n": 7}})
    assert any("count does not match" in f for f in er.validate(off_by_one,
               _rec("https://cnn.example.com/x", "CNN"), rctx, {}))


# ---------------------------------------------------------------- real clustering round-trip
def test_story_index_from_real_clusters(tmp_path):
    """Case 1 across the REAL Story Service: two publishers, same title tokens → one story;
    the resolver's index joins by canonical URL and the P1 sentence validates."""
    import store as store_mod
    st = store_mod.Store(f"sqlite:///{tmp_path}/resolver.db")
    for url, pub in ((FOX, "Fox News"), (CNN, "CNN")):
        st.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title="Fusion milestone reached in landmark laboratory test",
            description="d", body=None, published_at="2026-07-09T09:00:00+00:00",
            source_feed="seed", source_type="rss",
            scored={"article_id": url, "outlet": pub, "category": "Science", "lean": 0.0,
                    "political": False, "title": "Fusion milestone reached"})
    er._INDEX_CACHE.update(key=None, index=None)      # isolate from other tests
    import story_service
    story_service.warm_cache(st)      # production steady state: the poller warms; requests never build
    idx = er.story_index(st)
    assert er._canon(FOX) in idx and er._canon(CNN) in idx
    assert idx[er._canon(FOX)]["storyId"] == idx[er._canon(CNN)]["storyId"]

    ctx = _ctx(reads=[(FOX, "Fox News")])
    out = er.resolve(_rec(CNN, "CNN", published="2026-07-09T09:00:00+00:00"), ctx, idx)
    assert out["type"] == "story_match"
    assert er.validate(out, _rec(CNN, "CNN"), ctx, idx) == []
    er._INDEX_CACHE.update(key=None, index=None)


# ---------------------------------------------------------------- endpoint regression (RVP hook)
def test_every_served_explanation_validates(tmp_path, monkeypatch):
    """The regression the milestone exists for: every explanation the API serves must validate
    clean against the reader's real context — and the explain payload must carry the SAME
    sentence the card shows."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    import importlib.util
    import store as store_mod

    db = f"sqlite:///{tmp_path}/resolver_api.db"
    st = store_mod.Store(db)
    outlets = [("The Guardian", -1.5), ("NPR", -1.0), ("Associated Press", 0.0), ("Fox News", 1.6)]
    for pub, lean in outlets:
        dom = pub.lower().replace(" ", "")
        for k in range(3):
            u = f"https://{dom}.example.com/story/{k}"
            st.upsert_feed_article(
                canonical_url=u, url=u, publisher=pub, source_publisher=pub,
                title=f"{pub} covers the vote and the economy, item {k}", description="d",
                body=None, published_at="2026-07-01T00:00:00+00:00", source_feed="seed",
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

    spec = importlib.util.spec_from_file_location("api_fastapi_evres",
                                                  ROOT / "examples" / "api_fastapi.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["api_fastapi_evres"] = m
    spec.loader.exec_module(m)

    er._INDEX_CACHE.update(key=None, index=None)
    with TestClient(m.app) as client:
        uid = client.post("/api/internal/users",
                          json={"provider": "google", "providerAccountId": "resolver-a"}).json()["userId"]
        h = {"X-IH-User-Id": str(uid)}
        arts = client.get("/api/discover?limit=50").json()["articles"]
        client.post("/api/me/reads",
                    json={"reads": [{"url": x["url"], "title": x["headline"]} for x in arts[:5]]},
                    headers=h)

        served = client.get("/api/recommendations", headers=h).json()
        assert served
        active = m._active()
        ctx = active.personalizer.explanation_context(uid)
        idx = er.story_index(m.state.store)
        for r in served:
            exp = r.get("explanation")
            assert exp and exp["type"] in er.TYPES and 1 <= exp["priority"] <= 6
            assert r["reason"] == exp["message"]                      # the mirror contract
            assert "RWE-B" not in r["reason"] and "RWE-D" not in r["reason"]
            assert er.validate(exp, r, ctx, idx) == [], (exp, r["article"]["id"])
            # Commit 23: structured parts must survive the typed FastAPI model (additive fields)
            for part in ("readerFact", "contribution"):
                if exp.get(part) is not None:
                    assert isinstance(exp[part].get("key"), str) and exp[part]["key"]

        # the explain payload carries the SAME resolved sentence per article
        exp_payload = client.get("/api/internal/recommendations/explain", headers=h).json()
        by_id = {e["articleId"]: e for e in exp_payload["recommendations"]}
        for r in served:
            assert by_id[r["article"]["id"]]["explanation"]["message"] == r["reason"]
    er._INDEX_CACHE.update(key=None, index=None)
