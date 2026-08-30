"""M4 — Tier B story attachment: coverage joins, the partition never moves.

The roadmap's load-bearing claim ("assignment is linear in new arrivals and cannot alter the
partition") has never had a running implementation, only the shadow harness's offline rule. Now
that `attach_tier_b` serves it, these tests pin the claim from both sides:

* everything a built story already carries is BYTE-IDENTICAL after attachment — the old coverage
  list is a strict prefix, ids/distribution/counts untouched;
* an attached article is coverage and nothing else — never a member (the member table is synced
  before attachment runs), never a vote, never fetched from the shadow lane.

Times are relative to now, never pinned — the date-fuse rule this suite already follows.
"""
import copy
import sys
import pathlib
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "examples"))

import corpus
import story_service
import store as store_mod

NOW = datetime.now(timezone.utc)


def _iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def _story(sid="s-volcano", n=2):
    """A built-story dict in exactly the shape `_build_story` emits (the fields attach reads)."""
    coverage = [
        {"publisher": f"Alpha Wire {i}", "headline": "Volcano erupts near Reykjavik forcing evacuations",
         "lean": 0.0, "leanBucket": "center", "register": None, "emotion": None,
         "url": f"https://alpha{i}.example/volcano", "publishedAt": _iso(3 + i)}
        for i in range(n)
    ]
    return {"id": sid, "title": "Volcano erupts near Reykjavik", "totalCoverage": n,
            "publisherCount": n, "publishers": [c["publisher"] for c in coverage],
            "distribution": {"left": 0.0, "center": 1.0, "right": 0.0},
            "coverage": coverage}


def _tier_b_store(rows):
    """An in-memory catalogue holding exactly `rows` — (publisher, headline, url, hours_ago)."""
    st = store_mod.Store("sqlite://")
    for pub, headline, url, hours in rows:
        st.upsert_feed_article(canonical_url=url, url=url, publisher=pub, source_publisher=pub,
                               title=headline, description="", body=None,
                               published_at=_iso(hours), source_feed="t",
                               scored={"article_id": url, "outlet": pub, "lean": float("nan"),
                                       "category": ""})
    return st


def test_the_flag_ships_dark(monkeypatch):
    monkeypatch.delenv("RWE_STORY_TIER_B_ATTACH", raising=False)
    assert story_service.tier_b_attach_enabled() is False
    monkeypatch.setenv("RWE_STORY_TIER_B_ATTACH", "1")
    assert story_service.tier_b_attach_enabled() is True


def test_a_matching_tier_b_article_attaches_as_marked_coverage_and_nothing_else_moves(monkeypatch):
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb-gazette.example")
    st = _tier_b_store([("tierb-gazette.example",
                         "Volcano erupts near Reykjavik as evacuations widen",
                         "https://tierb-gazette.example/volcano", 1)])
    stories = [_story()]
    before = copy.deepcopy(stories)

    out = story_service.attach_tier_b(st, stories)

    s, b = out[0], before[0]
    # The prefix property IS the partition claim: every pre-existing byte is untouched.
    assert s["coverage"][: len(b["coverage"])] == b["coverage"]
    for key in b:
        if key != "coverage":
            assert s[key] == b[key], f"attachment moved {key!r} — it may only append"
    tail = s["coverage"][len(b["coverage"]):]
    assert len(tail) == 1 and tail[0]["tierB"] is True
    # The SHARED serializer prettifies a host-named publisher exactly as it does for members —
    # divergence here is what reusing `feed_article_to_article` exists to prevent.
    assert tail[0]["publisher"] == "Tierb-Gazette.Example"
    assert tail[0]["lean"] is None, "an unrated outlet attaches with NO lean — L2.2, never Centre"
    assert s["attachedCoverage"] == 1


def test_a_non_matching_article_attaches_nowhere(monkeypatch):
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb-gazette.example")
    st = _tier_b_store([("tierb-gazette.example",
                         "Quarterly earnings beat expectations at regional bank",
                         "https://tierb-gazette.example/earnings", 1)])
    stories = [_story()]
    before = copy.deepcopy(stories)
    assert story_service.attach_tier_b(st, stories) == before


def test_an_alias_twin_of_an_existing_member_is_not_new_coverage(monkeypatch):
    # Same canonical URL as a member: the article is already IN the story under its Tier A
    # identity; attaching it again would double-count the one thing dedup exists to keep single.
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb-gazette.example")
    st = _tier_b_store([("tierb-gazette.example",
                         "Volcano erupts near Reykjavik forcing evacuations",
                         "https://alpha0.example/volcano", 1)])
    stories = [_story()]
    before = copy.deepcopy(stories)
    assert story_service.attach_tier_b(st, stories) == before


def test_a_shadow_host_never_attaches_even_when_listed_tier_b(monkeypatch):
    # Shadow wins over B (`corpus._tier_with` order), and the store is where the surfacing rule
    # lives: `include_shadow=False` folds shadow into the exclusion, ANDed with the include set,
    # so a both-listed host is unreachable from the attachment fetch by construction.
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "dual.example")
    monkeypatch.setenv("RWE_CORPUS_SHADOW", "dual.example")
    st = _tier_b_store([("dual.example",
                         "Volcano erupts near Reykjavik as evacuations widen",
                         "https://dual.example/volcano", 1)])
    rows, _ = st.search_feed_articles(include_publishers=frozenset({"dual.example"}))
    assert rows == [], "the store must hide a shadow host from an include fetch"
    stories = [_story()]
    before = copy.deepcopy(stories)
    assert story_service.attach_tier_b(st, stories) == before


def test_include_publishers_is_a_positive_match_only(monkeypatch):
    monkeypatch.delenv("RWE_CORPUS_SHADOW", raising=False)
    st = _tier_b_store([("tierb-gazette.example", "A", "https://tierb-gazette.example/a", 1),
                        ("Other Outlet", "B", "https://other.example/b", 1)])
    rows, total = st.search_feed_articles(include_publishers=frozenset({"tierb-gazette.example"}))
    assert total == 1 and [r["publisher"] for r in rows] == ["tierb-gazette.example"]


def test_the_full_serving_path_attaches_after_the_member_table_is_synced(monkeypatch):
    """list_stories end to end: flag OFF and flag ON differ by exactly the marked tail, and the
    member table never carries the attached URL — the ordering-based containment, exercised
    through the real seam rather than asserted from the call-site comment."""
    monkeypatch.setenv("RWE_STORIES_CACHE_TTL", "0")     # both calls build inline, no cache twin
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb-gazette.example")
    st = _tier_b_store([
        ("Alpha Wire", "Volcano erupts near Reykjavik forcing evacuations",
         "https://alpha.example/volcano", 3),
        ("Beta Post", "Volcano erupts near Reykjavik forcing evacuations tonight",
         "https://beta.example/volcano", 2),
        ("tierb-gazette.example", "Volcano erupts near Reykjavik as evacuations widen",
         "https://tierb-gazette.example/volcano", 1),
    ])

    monkeypatch.delenv("RWE_STORY_TIER_B_ATTACH", raising=False)
    story_service.clear_cache()
    off = story_service.list_stories(st)["stories"]

    monkeypatch.setenv("RWE_STORY_TIER_B_ATTACH", "1")
    story_service.clear_cache()
    on = story_service.list_stories(st)["stories"]

    assert len(off) == 1 and len(on) == 1, "the Tier A pair must cluster with or without the flag"
    s_off, s_on = off[0], on[0]
    assert s_on["coverage"][: len(s_off["coverage"])] == s_off["coverage"]
    tail = s_on["coverage"][len(s_off["coverage"]):]
    assert [c["publisher"] for c in tail] == ["Tierb-Gazette.Example"]
    assert all(c["tierB"] for c in tail) and s_on["attachedCoverage"] == 1
    for key in s_off:
        if key != "coverage":
            assert s_on[key] == s_off[key], f"the flag moved {key!r} on the serving path"
    # The member table was synced BEFORE attachment: the attached URL is not a member.
    members = st.story_member_ids()
    assert members.get("https://alpha.example/volcano"), "sanity: a real member is mapped"
    assert not members.get("https://tierb-gazette.example/volcano"), \
        "an attached article must never enter the story-member table"
