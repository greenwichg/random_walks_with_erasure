"""Story Continuation's server-side resolver — story_continuation.resolve (design §3/§4).

Proves each of the seven server-decidable gates independently (a gate that never fires is a gate
nobody can trust), that ranking is a deterministic TOTAL order under member reordering, that the
openness slider selects nearest / novelty-first / furthest at each plateau including its exact
boundaries, and that the payload carries the keys the strip reads.

Gates 8 (dismissed) and 9 (chain cap) are browser storage facts and are covered by the web tests.

The gate tests drive a hand-built story index so each gate can be isolated to one field; the last
tests drive the REAL ``evidence_resolver.story_index`` over a real clustered store, which is what
proves the index actually carries ``clusterTrust`` / ``publisherCount`` / ``title``.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import evidence_resolver as er        # noqa: E402
import personalize                    # noqa: E402
import story_continuation as sc       # noqa: E402
import story_service                  # noqa: E402
import store as store_mod             # noqa: E402

ANCHOR = "https://cnn.example.com/story/harbor-ruling"
NEAR = "https://wsj.example.com/story/harbor-ruling"        # right, distance 1.2 from anchor
FAR = "https://breitbart.example.com/story/harbor-ruling"   # right, distance 1.8 from anchor


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _member(url, publisher, lean, *, headline="Harbor bridge oversight ruling lands", hours_ago=2.0,
            bucket=None):
    return {"publisher": publisher, "headline": headline, "lean": lean,
            "leanBucket": bucket, "register": "reporting", "emotion": None,
            "url": url, "publishedAt": _iso(hours_ago)}


def _story(members, *, trust="ok", story_id="s-harbor", title="Harbor bridge oversight ruling"):
    return {"storyId": story_id, "coverage": members, "clusterTrust": trust,
            "publisherCount": len({m["publisher"] for m in members}), "title": title}


def _index(story):
    return {er._canon(m["url"]): story for m in story["coverage"] if m.get("url")}


@pytest.fixture()
def st(tmp_path):
    return store_mod.Store(f"sqlite:///{tmp_path / 'cont.db'}")


@pytest.fixture()
def uid(st):
    return st.upsert_user_by_identity("dev", "continuation-reader").id


def _read(st, uid, url, publisher, lean=0.0):
    st.add_read(uid, er._canon(url),
                {"article_id": er._canon(url), "outlet": publisher, "publisher": publisher,
                 "category": "Politics", "lean": lean, "political": True, "title": "t"})


def _default_story(**kw):
    return _story([_member(ANCHOR, "CNN", -0.6), _member(NEAR, "The Wall Street Journal", 0.6)],
                  **kw)


# ------------------------------------------------------------------ flags and slider mapping
def test_flag_default_off_and_parsing(monkeypatch):
    monkeypatch.delenv("RWE_STORY_CONTINUATION", raising=False)
    assert sc.enabled() is False
    for on in ("1", "true", "YES", "on"):
        monkeypatch.setenv("RWE_STORY_CONTINUATION", on)
        assert sc.enabled() is True
    for off in ("0", "false", "off", "", "maybe"):
        monkeypatch.setenv("RWE_STORY_CONTINUATION", off)
        assert sc.enabled() is False


def test_freshness_window_default_and_override(monkeypatch):
    monkeypatch.delenv("RWE_CONTINUATION_MAX_AGE_H", raising=False)
    assert sc.freshness_hours() == 4.0
    monkeypatch.setenv("RWE_CONTINUATION_MAX_AGE_H", "1.5")
    assert sc.freshness_hours() == 1.5
    for junk in ("", "soon", "0", "-3"):            # junk and non-positive fall back, never crash
        monkeypatch.setenv("RWE_CONTINUATION_MAX_AGE_H", junk)
        assert sc.freshness_hours() == 4.0


@pytest.mark.parametrize("slider,expected", [
    (0, -1), (12, -1), (37, -1),                    # nearest plateau, inclusive upper bound
    (38, 0), (50, 0), (62, 0),                      # no preference — the default plateau
    (63, 1), (88, 1), (100, 1),                     # furthest plateau, inclusive lower bound
])
def test_distance_preference_plateaus(slider, expected):
    assert sc.distance_preference(slider) == expected


def test_distance_preference_junk_is_the_default_plateau():
    for junk in (None, "", "loud", float("nan")):
        assert sc.distance_preference(junk) == 0


# ------------------------------------------------------------------ gates 1-7, one at a time
def test_gate1_url_not_in_any_story(st, uid):
    assert sc.resolve(st, uid, "https://elsewhere.example.com/x", index={}) is None
    assert sc.resolve(st, uid, "", index=_index(_default_story())) is None
    assert sc.resolve(None, uid, ANCHOR) is None


@pytest.mark.parametrize("trust", ["low", "unverified", None, ""])
def test_gate2_only_a_trusted_cluster_offers_a_continuation(st, uid, trust):
    story = _default_story(trust=trust)
    assert sc.resolve(st, uid, ANCHOR, index=_index(story)) is None
    assert sc.resolve(st, uid, ANCHOR, index=_index(_default_story())) is not None


def test_gate3_template_cluster_is_never_offered(st, uid):
    story = _story([
        _member(ANCHOR, "CNN", -0.6, headline="Powerball winning numbers for Saturday"),
        _member(NEAR, "The Wall Street Journal", 0.6,
                headline="Mega Millions jackpot winning numbers"),
    ])
    assert sc.resolve(st, uid, ANCHOR, index=_index(story)) is None


def test_gate4_no_sibling_when_the_anchor_stands_alone(st, uid):
    story = _story([_member(ANCHOR, "CNN", -0.6)])
    assert sc.resolve(st, uid, ANCHOR, index=_index(story)) is None


def test_gate4_an_already_read_sibling_is_not_a_continuation(st, uid):
    story = _default_story()
    assert sc.resolve(st, uid, ANCHOR, index=_index(story)) is not None
    _read(st, uid, NEAR, "The Wall Street Journal")
    assert sc.resolve(st, uid, ANCHOR, index=_index(story)) is None


def test_gate4_same_outlet_under_two_names_is_not_another_outlet(st, uid):
    """publisher_identity collapses the name forms — a syndicated reprint is not a second account."""
    story = _story([_member(ANCHOR, "Sportskeeda", -0.6),
                    _member(NEAR, "Sportskeeda.Com", 0.6)])
    assert sc.resolve(st, uid, ANCHOR, index=_index(story)) is None


def test_gate4_unusable_sibling_url_is_skipped(st, uid):
    story = _story([_member(ANCHOR, "CNN", -0.6),
                    _member("/story/harbor-ruling", "The Wall Street Journal", 0.6)])
    assert sc.resolve(st, uid, ANCHOR, index=_index(story)) is None


@pytest.mark.parametrize("anchor_lean,sibling_lean", [(None, 0.6), (-0.6, None),
                                                      (None, None), ("", 0.6)])
def test_gate5_an_unrated_outlet_licenses_no_opposition(st, uid, anchor_lean, sibling_lean):
    story = _story([_member(ANCHOR, "CNN", anchor_lean),
                    _member(NEAR, "The Wall Street Journal", sibling_lean)])
    assert sc.resolve(st, uid, ANCHOR, index=_index(story)) is None


@pytest.mark.parametrize("anchor_lean,sibling_lean", [
    (-0.6, -1.4),       # same side, different number
    (-0.6, 0.0),        # centre opposes nothing
    (0.0, 0.9),         # a centre anchor is not "a side" either
    (-0.49, 0.6),       # just inside the bucket -> not left
])
def test_gate6_only_a_genuinely_opposite_side_qualifies(st, uid, anchor_lean, sibling_lean):
    story = _story([_member(ANCHOR, "CNN", anchor_lean),
                    _member(NEAR, "The Wall Street Journal", sibling_lean)])
    assert sc.resolve(st, uid, ANCHOR, index=_index(story)) is None


def test_gate7_freshness_window(st, uid):
    story = _default_story()
    _read(st, uid, ANCHOR, "CNN")
    now = datetime.now(timezone.utc)
    assert sc.resolve(st, uid, ANCHOR, index=_index(story),
                      now=now + timedelta(hours=3, minutes=50)) is not None
    assert sc.resolve(st, uid, ANCHOR, index=_index(story),
                      now=now + timedelta(hours=4, minutes=10)) is None


def test_gate7_an_unrecorded_read_is_the_prefetch_race_not_a_stale_read(st, uid):
    """At click time the read POST is still in flight. Requiring a stored read would fail the
    common case; the click IS the read."""
    assert st.list_reads(uid) == []
    assert sc.resolve(st, uid, ANCHOR, index=_index(_default_story())) is not None


# ------------------------------------------------------------------ ranking (§4)
def _three_way_story():
    """One left anchor, two opposing siblings at different distances, plus a same-side decoy."""
    return _story([
        _member(ANCHOR, "CNN", -0.6, hours_ago=3.0),
        _member(NEAR, "The Wall Street Journal", 0.6, hours_ago=2.0),
        _member(FAR, "Breitbart", 1.2, hours_ago=1.0),
        _member("https://msnbc.example.com/story/harbor-ruling", "MSNBC", -1.0, hours_ago=0.5),
    ])


@pytest.mark.parametrize("slider,expected", [(0, NEAR), (20, NEAR), (37, NEAR),
                                             (63, FAR), (100, FAR)])
def test_slider_selects_nearest_or_furthest_opposing_outlet(st, uid, slider, expected):
    got = sc.resolve(st, uid, ANCHOR, index=_index(_three_way_story()), openness=slider)
    assert got["sibling"]["url"] == expected
    assert got["candidateCount"] == 2                      # the same-side decoy never counts


def test_default_plateau_ranks_novelty_before_recency(st, uid):
    """At 38-62 the reader stated no distance preference, so novelty leads. Pinned so novelty has
    to BEAT recency to pass: the familiar outlet is the newer one."""
    story = _story([_member(ANCHOR, "CNN", -0.6, hours_ago=3.0),
                    _member(NEAR, "The Wall Street Journal", 0.6, hours_ago=0.5),   # newest
                    _member(FAR, "Breitbart", 1.2, hours_ago=2.0)])
    assert sc.resolve(st, uid, ANCHOR, index=_index(story),
                      openness=50)["sibling"]["publisher"] == "The Wall Street Journal"
    for k in range(20):                                    # now WSJ is familiar, Breitbart is not
        _read(st, uid, f"https://wsj.example.com/x/{k}", "The Wall Street Journal")
    assert sc.resolve(st, uid, ANCHOR, index=_index(story),
                      openness=50)["sibling"]["publisher"] == "Breitbart"


def test_default_plateau_falls_through_to_recency_when_novelty_ties(st, uid):
    got = sc.resolve(st, uid, ANCHOR, index=_index(_three_way_story()), openness=50)
    assert got["sibling"]["url"] == FAR                     # both unread; FAR is newer


def test_ranking_is_independent_of_member_order(st, uid):
    story = _three_way_story()
    forward = sc.resolve(st, uid, ANCHOR, index=_index(story), openness=50)
    story["coverage"].reverse()
    backward = sc.resolve(st, uid, ANCHOR, index=_index(story), openness=50)
    assert forward == backward


def test_canonical_url_is_the_final_tiebreak(st, uid):
    """Same distance, same novelty, same timestamp — the order must still be total."""
    a = "https://aaa.example.com/story/harbor-ruling"
    z = "https://zzz.example.com/story/harbor-ruling"
    same = _iso(1.0)                    # ONE timestamp string — two _iso() calls differ in micros
    members = [_member(ANCHOR, "CNN", -0.6, hours_ago=3.0),
               {**_member(z, "Zed Post", 0.6), "publishedAt": same},
               {**_member(a, "Aye Times", 0.6), "publishedAt": same}]
    story = _story(members)
    assert sc.resolve(st, uid, ANCHOR, index=_index(story))["sibling"]["url"] == a
    story["coverage"] = [members[0], members[2], members[1]]
    assert sc.resolve(st, uid, ANCHOR, index=_index(story))["sibling"]["url"] == a


def test_repeated_calls_are_byte_identical(st, uid):
    idx = _index(_three_way_story())
    assert sc.resolve(st, uid, ANCHOR, index=idx) == sc.resolve(st, uid, ANCHOR, index=idx)


# ------------------------------------------------------------------ payload shape (§10.2)
def test_payload_carries_exactly_what_the_strip_reads(st, uid):
    got = sc.resolve(st, uid, ANCHOR, index=_index(_three_way_story()), openness=50)
    assert set(got) == {"storyId", "storyTitle", "outlets", "anchor", "sibling", "distance",
                        "candidateCount"}
    assert set(got["anchor"]) == {"url", "publisher", "lean", "leanBucket"}
    assert set(got["sibling"]) == {"url", "publisher", "headline", "lean", "leanBucket",
                                   "publishedAt"}
    assert got["storyId"] == "s-harbor"
    assert got["outlets"] == 4                          # distinct outlets on the story, not members
    assert got["anchor"]["publisher"] == "CNN"
    assert got["distance"] == 1.8                       # |1.2 - (-0.6)|, rounded


# ------------------------------------------------------------------ the shared primitives
def test_the_feed_slot_and_the_continuation_share_one_opposition_test():
    """Two surfaces asserting the same thing about the same outlets must not be able to drift."""
    assert personalize._opposing_leans is er.opposing_leans
    assert er.opposing_leans(-0.6, 0.6) is True
    assert er.opposing_leans(-1.5, -0.8) is False
    assert er.opposing_leans(None, 0.6) is False


def test_real_story_index_carries_the_gate_and_copy_fields(tmp_path, monkeypatch):
    """The gates read clusterTrust off the index; the copy reads publisherCount and title. Proved
    against the REAL index build, not a hand-built one."""
    monkeypatch.setenv("RWE_FEED_MIN_ARTICLES", "2")
    st = store_mod.Store(f"sqlite:///{tmp_path / 'idx.db'}")
    title = "Landmark ruling reshapes the harbor bridge oversight case"
    for url, pub, lean, extra in ((ANCHOR, "CNN", -0.6, ""), (NEAR, "The Wall Street Journal", 0.6,
                                                              " today")):
        st.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title=title + extra, description="d", body=None,
            published_at=_iso(2.0), source_feed="f",
            scored={"article_id": er._canon(url), "outlet": pub, "category": "Politics",
                    "lean": lean, "political": True, "title": title + extra})
    er._INDEX_CACHE.update(key=None, index=None)
    story_service.warm_cache(st)
    idx = er.story_index(st)
    entry = idx.get(er._canon(ANCHOR))
    assert entry is not None, "the seeded pair must cluster into one story"
    assert entry["clusterTrust"] in {"ok", "low", "unverified"}
    assert entry["publisherCount"] == 2
    assert entry["title"]
    assert entry["storyId"] and entry["coverage"]
    er._INDEX_CACHE.update(key=None, index=None)
