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
    # build_inline mirrors the production analyze route (analysis_enrichment.enrich_for_reader):
    # the zero-write contract path builds read-only on a cold cache instead of kicking a refresh.
    return er.story_index(store, build_inline=True)


def _rec(analysis, explanation):
    return {"article": ae._rec_article(analysis), "crossCutting": explanation.get("type") == "bridge"}


# --------------------------------------------------------------------------- #
# The two golden scenarios — explanation + verdict + evidence-licensing.
# --------------------------------------------------------------------------- #
def test_enrich_bridge_cross_cutting(store, index):
    ctx = fx._authed_contexts()["authed_bridge"]     # right reader, never read The Guardian
    analysis = aa.analyze(store, fx.GUARDIAN_URL)     # the left cluster member
    out = ae.enrich(analysis, ctx, index=index, blind_spot_topics=fx._BLIND_SPOTS, store=store)

    assert out["explanation"]["type"] == "bridge"
    assert out["recommendation"]["wouldBroaden"] is True
    assert "cross_cutting" in out["recommendation"]["reasons"]
    assert "new_publisher" in out["recommendation"]["reasons"]
    assert out["recommendation"]["blindSpotTopic"] is None
    # the shown sentence is licensed by real evidence (no over-claim)
    assert er.validate(out["explanation"], _rec(analysis, out["explanation"]), ctx, index) == []
    # A3.3a: the story-relative pick rides along (details pinned in the dedicated tests below)
    assert out["recommendation"]["nextArticle"]["source"] == "story"


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
    null = {"explanation": None, "recommendation": None, "personal": None}
    assert ae.enrich(aa.analyze(store, "not a url"), fx._authed_contexts()["authed_bridge"],
                     index=index) == null
    assert ae.enrich({"status": "analyzed", "article": None, "scoring": None, "input": {}},
                     {}, index=index) == null


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
    null = {"explanation": None, "recommendation": None, "personal": None}

    assert ae.enrich_for_reader(_StubPersonalizer(True, ctx), store, None, analysis) == null   # anon
    assert ae.enrich_for_reader(None, store, 1, analysis) == null                              # no personalizer
    assert ae.enrich_for_reader(_StubPersonalizer(False, ctx), store, 1, analysis) == null     # not measured
    measured = ae.enrich_for_reader(_StubPersonalizer(True, ctx), store, 1, analysis)          # measured
    assert measured["explanation"]["type"] == "bridge" and measured["recommendation"]["wouldBroaden"] is True
    assert measured["personal"]["publisher"] == {"name": "The Guardian", "band": "never"}      # A4.1


def test_blind_spot_topics_read_from_stored_report(store):
    uid = store.upsert_user_by_identity("dev", "a3-blind").id
    store.save_report(uid, {"mode": "measured", "overall": 60,
                            "blindSpots": [{"topic": "Economy", "gap": 0.4, "note": "n"},
                                           {"topic": "Science", "gap": 0.3, "note": "n"},
                                           {"topic": "Health", "gap": float("nan"), "note": "n"}]})
    # A4.1: topic -> gap, verbatim from the stored report; a junk gap degrades to membership-only.
    assert ae._blind_spot_topics(store, uid) == {"Economy": 0.4, "Science": 0.3, "Health": None}
    assert ae._blind_spot_topics(store, 999_999) == {}     # no report -> empty, never a guess


# --------------------------------------------------------------------------- #
# A3.3a — recommendation.nextArticle: story selection.
# --------------------------------------------------------------------------- #
CTX = None  # set per test via fx._authed_contexts()


def test_story_pick_deterministic_with_canonical_tiebreak(store, index):
    """Guardian analyzed; siblings AP + Reuters (both center, equal publishedAt in the seed):
    different-bucket preference keeps both, dates tie, canonical URL decides -> AP."""
    ctx = fx._authed_contexts()["authed_bridge"]
    out = ae.enrich(aa.analyze(store, fx.GUARDIAN_URL), ctx, index=index, store=store)
    na = out["recommendation"]["nextArticle"]
    assert na["source"] == "story"
    assert na["article"]["publisher"] == "AP"                       # apnews.com < reuters.com
    assert na["article"]["publisher"] != "The Guardian"             # different publisher, always
    assert na["explanation"]["type"] in er.TYPES


def test_story_pick_prefers_cross_viewpoint_then_newest():
    """A 4-member cluster: analyzed LEFT; siblings center-old, center-new, LEFT-newest. The
    cross-viewpoint preference beats recency (center-new wins over left-newest), and within the
    preferred bucket the newest wins."""
    er._INDEX_CACHE.update(key=None, index=None)
    st = fx.store_mod.Store("sqlite://")
    members = [  # (publisher, domain, lean, hour) — shared title tokens so they cluster
        ("The Guardian", "theguardian.com", -1.0, "12"),   # the analyzed article
        ("AP", "apnews.com", 0.0, "09"),                   # center, old
        ("Reuters", "reuters.com", 0.0, "11"),             # center, new  <- expected pick
        ("CNN", "cnn.com", -1.0, "11:30"),                 # left, newest (same bucket -> demoted)
    ]
    for pub, dom, lean, hh in members:
        url = f"https://{dom}/story/cv-case"
        t = f"{hh.replace(':','')}"
        st.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title=f"city council budget vote sparks debate {t}", description="d", body=None,
            published_at=f"2026-07-18T{hh if ':' in hh else hh + ':00'}:00+00:00", source_feed="t",
            scored={"article_id": er._canon(url), "outlet": pub, "category": "Politics",
                    "lean": lean, "political": True, "title": "t"})
    er._INDEX_CACHE.update(key=None, index=None)
    idx = er.story_index(st, build_inline=True)   # the analyze route builds read-only
    analysis = aa.analyze(st, "https://theguardian.com/story/cv-case")
    assert analysis["story"]["matched"] is True                     # the cluster actually formed
    ctx = fx._authed_contexts()["authed_bridge"]
    na = ae.enrich(analysis, ctx, index=idx, store=st)["recommendation"]["nextArticle"]
    assert na["source"] == "story"
    assert na["article"]["publisher"] == "Reuters"                  # center beats left; 11:00 beats 09:00


def test_story_pick_excludes_read_and_same_publisher(store, index):
    """A sibling the reader already read is excluded; if every sibling is read or same-publisher,
    the story path yields nothing (and with no feed fallback, nextArticle is null)."""
    base = fx._authed_contexts()["authed_bridge"]
    read_all = dict(base)
    read_all["reads"] = [{"url": er._canon("https://apnews.com/story/an-cluster-1"),
                          "publisher": "AP", "publishedAt": "2026-07-18T13:00:00+00:00"},
                         {"url": er._canon("https://reuters.com/story/an-cluster-2"),
                          "publisher": "Reuters", "publishedAt": "2026-07-18T13:00:00+00:00"}]
    analysis = aa.analyze(store, fx.GUARDIAN_URL)
    na = ae.enrich(analysis, read_all, index=index, store=store)["recommendation"]["nextArticle"]
    assert na is None                                               # both siblings read -> no pick

    one_read = dict(base)
    one_read["reads"] = [read_all["reads"][0]]                      # AP read -> Reuters remains
    na2 = ae.enrich(analysis, one_read, index=index, store=store)["recommendation"]["nextArticle"]
    assert na2["article"]["publisher"] == "Reuters"


def test_story_path_never_consults_the_feed(store, index):
    """Story path independent of feed ranking: when a sibling exists, the lazy feed callable is
    never invoked."""
    called = {"feed": False}

    def feed_next():
        called["feed"] = True
        return None

    ctx = fx._authed_contexts()["authed_bridge"]
    na = ae.enrich(aa.analyze(store, fx.GUARDIAN_URL), ctx, index=index, store=store,
                   feed_next=feed_next)["recommendation"]["nextArticle"]
    assert na["source"] == "story" and called["feed"] is False


def test_story_pick_explanation_is_licensed_and_never_unearned_story_match(store, index):
    """The pick's explanation passes evidence_resolver.validate(); with no cluster read in the
    reader's history it must not be story_match (the story relation is provenance, not a claim)."""
    ctx = fx._authed_contexts()["authed_bridge"]                    # no cluster reads
    na = ae.enrich(aa.analyze(store, fx.GUARDIAN_URL), ctx, index=index, store=store)[
        "recommendation"]["nextArticle"]
    exp = na["explanation"]
    assert exp["type"] != "story_match"
    row = store.get_feed_article(er._canon(str(na["article"]["id"])))
    sc = row.get("scored") or {}
    rec = ae._resolver_rec({"url": na["article"]["url"], "id": na["article"]["id"],
                            "publisher": na["article"]["publisher"], "topic": na["article"]["topic"],
                            "lean": sc.get("lean"), "political": sc.get("political"),
                            "publishedAt": na["article"]["publishedAt"]}, ctx)
    assert er.validate(exp, rec, ctx, index) == []


# --------------------------------------------------------------------------- #
# A3.3a — feed fallback.
# --------------------------------------------------------------------------- #
class _FeedPersonalizer:
    """A stub whose recommendations are a fixed, ordered list — the 'engine' for fallback tests."""
    def __init__(self, recs):
        self._recs, self.calls = recs, 0

    def has_measured(self, uid):
        return True

    def explanation_context(self, uid):
        return fx._authed_contexts()["authed_bridge"]

    def recommendations(self, uid, strategy=None, params=None):
        self.calls += 1
        return self._recs


def _top(url, publisher, lean=0.0):
    return {"article": {"id": url, "url": url, "publisher": publisher, "topic": "Politics",
                        "lean": lean, "leanBucket": "center", "publishedAt": "2026-07-18T08:00:00+00:00"},
            "crossCutting": False, "strategy": "rwe-b", "reason": "r"}


def test_feed_fallback_reflects_feed_ranking(store, index):
    """No cluster (scored-url-only miss) -> the pick IS recommendations[0]; reordering the feed
    changes the pick accordingly."""
    ctx = fx._authed_contexts()["authed_bridge"]
    a, b = _top("https://apnews.com/top-a", "Associated Press"), _top("https://reuters.com/top-b", "Reuters")

    n1 = ae._feed_next(_FeedPersonalizer([a, b]), store, 1, ctx, index)
    assert n1["source"] == "feed" and n1["article"] == a["article"]
    n2 = ae._feed_next(_FeedPersonalizer([b, a]), store, 1, ctx, index)
    assert n2["article"] == b["article"]                            # ranking is the selection
    assert n1["explanation"]["type"] in er.TYPES                    # resolved with the shared resolver
    assert ae._feed_next(_FeedPersonalizer([]), store, 1, ctx, index) is None


def test_enrich_for_reader_feed_fallback_wiring(store):
    """End-to-end through enrich_for_reader: a clusterless analysis falls back to the stub feed's
    top; a clustered analysis never calls the stub."""
    er._INDEX_CACHE.update(key=None, index=None)
    top = _top("https://apnews.com/top-a", "Associated Press")
    p = _FeedPersonalizer([top])
    uid = store.upsert_user_by_identity("dev", "a33-feed").id

    miss = aa.analyze(store, "https://apnews.com/article/not-in-any-cluster",
                      metadata={"title": "zebra chess quartet marathon"})
    out = ae.enrich_for_reader(p, store, uid, miss)
    assert out["recommendation"]["nextArticle"]["source"] == "feed"
    assert out["recommendation"]["nextArticle"]["article"] == top["article"]
    assert p.calls == 1

    clustered = aa.analyze(store, fx.GUARDIAN_URL)
    out2 = ae.enrich_for_reader(p, store, uid, clustered)
    assert out2["recommendation"]["nextArticle"]["source"] == "story"
    assert p.calls == 1                                             # the feed was not consulted again


def test_no_candidate_yields_null_next_article(store):
    """Clusterless analysis + no feed available -> the verdict still carries the key, value null."""
    er._INDEX_CACHE.update(key=None, index=None)
    idx = er.story_index(store)
    ctx = fx._authed_contexts()["authed_familiar"]
    miss = aa.analyze(store, "https://apnews.com/article/lonely", metadata={"title": "a lonely piece"})
    out = ae.enrich(miss, ctx, index=idx, store=store, feed_next=lambda: None)
    assert "nextArticle" in out["recommendation"]
    assert out["recommendation"]["nextArticle"] is None


# --------------------------------------------------------------------------- #
# A4.1 — the `personal` standing section (facts only; every block independently null).
# --------------------------------------------------------------------------- #
def test_personal_closed_shape_and_bridge_standing(store, index):
    ctx = fx._authed_contexts()["authed_bridge"]
    p = ae.enrich(aa.analyze(store, fx.GUARDIAN_URL), ctx, index=index,
                  blind_spot_topics=fx._BLIND_SPOTS)["personal"]
    assert set(p) == {"alreadyRead", "publisher", "topic", "viewpoint", "story"}
    assert p["alreadyRead"] is None                       # never read the analyzed article
    assert p["publisher"] == {"name": "The Guardian", "band": "never"}
    assert p["topic"] == {"topic": "Politics", "share": 0.85, "blindSpot": None}
    assert p["viewpoint"] == {"articleBucket": "left",
                              "readerShares": {"left": 0.1, "center": 0.05, "right": 0.85},
                              "addsMissing": False}
    assert p["story"] is None                             # no cluster member read -> no story claim


def test_personal_already_read_on_catalog_and_url_only(store, index):
    """`alreadyRead` matches on canonical identity, so it works for a catalog hit AND a
    scored-url-only miss; the analyzed article itself never counts as story coverage."""
    miss_url = "https://apnews.com/live/a4-miss"
    ctx = fx._reader_ctx(
        mean_lean=0.0,
        reads=[(fx.GUARDIAN_URL, "The Guardian", "2026-07-18T14:00:00+00:00"),
               (miss_url, "AP", None)],
        fam_shares={"The Guardian": {"reads": 1, "share": 0.03}, "AP": {"reads": 1, "share": 0.03}},
        top_topics=["Politics"], topic_shares={"Politics": 0.5},
        lean_shares={"left": 0.4, "center": 0.3, "right": 0.3})

    hit = ae.enrich(aa.analyze(store, fx.GUARDIAN_URL), ctx, index=index)["personal"]
    assert hit["alreadyRead"] == {"at": "2026-07-18T14:00:00+00:00"}
    assert hit["story"] is None                           # the self-read is not story coverage

    miss = ae.enrich(aa.analyze(store, miss_url, metadata={"title": "budget liveblog"}),
                     ctx, index=index)["personal"]
    assert miss["alreadyRead"] == {"at": None}            # read known; timestamp honestly unknown


def test_personal_story_standing_counts_read_members(store, index):
    """Read cluster members (excluding the analyzed article) -> count + buckets covered + the
    bucket this article would add; the addsMissing claim needs an exactly-zero measured share."""
    ctx = fx._authed_contexts()["authed_following"]       # read the AP (center) member; left 0.0
    p = ae.enrich(aa.analyze(store, fx.GUARDIAN_URL), ctx, index=index)["personal"]
    assert p["story"] == {"readCount": 1, "bucketsRead": ["center"], "addsBucket": "left"}
    assert p["viewpoint"]["addsMissing"] is True

    both = dict(ctx)
    both["reads"] = ctx["reads"] + [{"url": er._canon("https://reuters.com/story/an-cluster-2"),
                                     "publisher": "Reuters",
                                     "publishedAt": "2026-07-18T15:00:00+00:00"}]
    p2 = ae.enrich(aa.analyze(store, fx.GUARDIAN_URL), both, index=index)["personal"]
    assert p2["story"] == {"readCount": 2, "bucketsRead": ["center"], "addsBucket": "left"}


def test_personal_band_and_blind_spot_agree_with_verdict(store, index):
    """Single-pass consistency: ONE band and ONE blind-spot mapping feed both sections, so the
    verdict and the standing can never disagree; a legacy sequence input keeps membership with an
    honestly-unknown gap."""
    ctx = fx._authed_contexts()["authed_bridge"]
    out = ae.enrich(aa.analyze(store, fx.GUARDIAN_URL), ctx, index=index,
                    blind_spot_topics={"Politics": 0.6})
    assert "new_publisher" in out["recommendation"]["reasons"]
    assert out["personal"]["publisher"]["band"] == "never"
    assert out["recommendation"]["blindSpotTopic"] == "Politics"
    assert out["personal"]["topic"]["blindSpot"] == {"gap": 0.6}

    seq = ae.enrich(aa.analyze(store, fx.GUARDIAN_URL), ctx, index=index,
                    blind_spot_topics=("Politics",))
    assert seq["recommendation"]["blindSpotTopic"] == "Politics"
    assert seq["personal"]["topic"]["blindSpot"] == {"gap": None}


def test_personal_rarely_band_matches_rarely_read_reason(store, index):
    ctx = dict(fx._authed_contexts()["authed_bridge"])
    ctx["familiarity"] = fx._familiarity({"The Guardian": {"reads": 1, "share": 0.02},
                                          "Fox News": {"reads": 20, "share": 0.8}})
    out = ae.enrich(aa.analyze(store, fx.GUARDIAN_URL), ctx, index=index)
    assert "rarely_read_publisher" in out["recommendation"]["reasons"]
    assert out["personal"]["publisher"] == {"name": "The Guardian", "band": "rarely"}


def test_personal_blocks_null_without_licensing_data(store, index):
    """Honesty gates: no lean bucket -> no viewpoint block at all; missing lean_shares ->
    readerShares null and addsMissing NEVER claimed (a zero-share claim needs real shares)."""
    ctx = dict(fx._authed_contexts()["authed_bridge"])
    del ctx["lean_shares"]                                # below the report's political minimum
    unknown = aa.analyze(store, "https://unknown-blog.example/post",
                         metadata={"title": "a take on the budget", "category": "Politics"})
    assert ae.enrich(unknown, ctx, index=index)["personal"]["viewpoint"] is None

    guardian = ae.enrich(aa.analyze(store, fx.GUARDIAN_URL), ctx, index=index)["personal"]
    assert guardian["viewpoint"]["readerShares"] is None
    assert guardian["viewpoint"]["addsMissing"] is False
