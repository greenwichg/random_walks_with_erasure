"""Merge support BREADTH — the cluster-level corroboration rule (``clustering.min_support``).

The failure it exists for is not a vocabulary defect and cannot be reached by one: a comparative
round-up article is *genuinely* similar to two unrelated events, so every edge in the weld is
legitimate and no stop-list should remove any of them. What is wrong is the SHAPE of the merge —
all of its supporting cross-pairs run through a single article. These pin that distinction:

  * the off state is byte-identical to today's linkage, on every path;
  * a bridge cannot annex a cluster, in either merge order;
  * a story still FORMS from one pair and still GROWS by genuinely-matching articles;
  * the rule can only refuse merges, never invent them — so no correct cluster changes shape;
  * the primary build and the repair re-cluster link on the SAME rule.
"""
import pathlib
import random
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import clustering as cl      # noqa: E402
import story_service as ss   # noqa: E402

T0 = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)


def _items(*specs):
    return [{"t": t, "when": T0 + timedelta(days=d)} for t, d in specs]


def _groups(items, **kw):
    g = cl.cluster(items, tokens=lambda x: cl.title_tokens(x["t"]), time=lambda x: x["when"], **kw)
    return sorted(sorted(grp) for grp in g)


# --------------------------------------------------------------------------- #
# The production exhibit, at its real token overlaps.
#
# Probed on the live catalog 2026-08-25:
#   odyssey <-> spider (direct) : shared []                                    j=0.000
#   odyssey <-> bridge          : shared [becomes film grossing highest odyssey] j=0.312
#   bridge  <-> spider          : shared [box fourth man office spider tops]    j=0.286
# The bridge is a real round-up covering both films. Nothing here is boilerplate.
# --------------------------------------------------------------------------- #
_ODYSSEY = "The Odyssey becomes Nolan highest grossing film ever"
_BRIDGE = ("Spider-Man tops box office in fourth weekend The Odyssey becomes Nolan "
           "highest grossing film")
_SPIDER = "Spider-Man Brand New Day tops box office fourth weekend"
_SPIDER2 = "Spider-Man Brand New Day holds box office top spot fourth weekend"
_SPIDER3 = "Box office Spider-Man Brand New Day leads fourth weekend charts"


def _weld_items():
    """The Spider-Man side is a genuine multi-article story; Odyssey is one foreign article."""
    return _items((_SPIDER, 0), (_SPIDER2, 0), (_SPIDER3, 0), (_BRIDGE, 0), (_ODYSSEY, 0))


def test_the_bridge_welds_two_events_under_todays_rules():
    """The precondition, stated as a test so the fix has something to be measured against.

    Note which guards are already on here: this is the FULL production linkage rule, quorum
    included. The weld survives it."""
    welded = _groups(_weld_items(), link_quorum=0.2)
    assert welded == [[0, 1, 2, 3, 4]], "one cluster containing both films"


def test_support_breadth_severs_the_bridge():
    """The Odyssey article reaches the Spider-Man story through the round-up alone, so the
    merge's support breadth on that side is 1. The Spider-Man story keeps the round-up — it
    genuinely is about that film's box office too — and sheds only the foreign article."""
    split = _groups(_weld_items(), link_quorum=0.2, min_support=2)
    assert [0, 1, 2, 3] in split, "the genuine story survives intact, round-up included"
    assert [4] in split, "the foreign article is no longer welded in"


def test_neither_merge_ORDER_lets_the_weld_through():
    """Merges are consumed best-first, so the bridge may reach either side first. The rule has to
    refuse both ways round or it is only a reordering. Presenting the articles in the reverse
    order changes which merges are attempted first; the outcome must not move."""
    forward = _groups(_weld_items(), link_quorum=0.2, min_support=2)
    rev = list(reversed(_weld_items()))
    back = _groups(rev, link_quorum=0.2, min_support=2)
    # index 0 in the reversed list is the Odyssey article
    assert [0] in back, "still detached when the bridge meets Spider-Man first"
    assert sorted(len(g) for g in forward) == sorted(len(g) for g in back)


# --------------------------------------------------------------------------- #
# What must NOT change.
# --------------------------------------------------------------------------- #
def test_off_is_byte_identical_on_the_single_linkage_path():
    """min_support 1 is the default and must not perturb the fast path, which never sorts or
    tracks membership."""
    rnd = random.Random(17)
    vocab = [f"word{i}" for i in range(40)]
    items = [{"t": " ".join(rnd.sample(vocab, 6)), "when": T0} for _ in range(120)]
    assert _groups(items) == _groups(items, min_support=1)
    assert cl.DEFAULT_MIN_SUPPORT == 1


def test_off_is_byte_identical_on_the_quorum_path():
    """And on the bookkeeping path, where it shares a scan with the quorum — the risk is that
    folding breadth into that loop perturbs the quorum's own early exits."""
    rnd = random.Random(29)
    vocab = [f"tok{i}" for i in range(30)]
    items = [{"t": " ".join(rnd.sample(vocab, 5)), "when": T0} for _ in range(90)]
    for q in (0.2, 0.5, 1.0):
        assert _groups(items, link_quorum=q) == _groups(items, link_quorum=q, min_support=1), q


def test_a_story_still_forms_from_a_single_pair():
    """The requirement is capped at each side's own size, so two singletons need one participant
    each — the pair that already passed the similarity gate. Without that cap the rule would
    delete the catalog's median two-article story."""
    pair = _items(("harbour bridge closed after crash", 0), ("crash closes harbour bridge", 0))
    assert _groups(pair, min_support=2) == [[0, 1]]
    assert _groups(pair, min_support=5) == [[0, 1]], "even an absurd requirement cannot block it"


def test_a_genuine_story_still_grows_one_article_at_a_time():
    """Growth is the pass the rule constrains, so it has to be shown NOT blocking growth that is
    corroborated: a fourth article resembling the whole story joins it."""
    items = _items((_SPIDER, 0), (_SPIDER2, 0), (_SPIDER3, 0),
                   ("Spider-Man Brand New Day box office fourth weekend record", 0))
    assert _groups(items, link_quorum=0.2, min_support=2) == [[0, 1, 2, 3]]


def test_the_rule_can_only_refuse_merges_never_create_them():
    """A structural property worth pinning rather than trusting: every cluster under the rule is
    a SUBSET of some cluster without it. That is what makes the change safe to measure — the
    reachable outcomes are a split or no change, never a reshuffle."""
    rnd = random.Random(5)
    vocab = [f"w{i}" for i in range(24)]
    items = [{"t": " ".join(rnd.sample(vocab, 5)), "when": T0} for _ in range(70)]
    base = _groups(items, link_quorum=0.2)
    tight = _groups(items, link_quorum=0.2, min_support=2)
    for g in tight:
        assert any(set(g) <= set(b) for b in base), f"{g} is not contained in any prior cluster"


def test_breadth_is_deterministic():
    """Order-dependent by nature (merges are consumed best-first), so runs must still agree or
    story ids churn on every rebuild."""
    rnd = random.Random(7)
    vocab = [f"t{i}" for i in range(30)]
    items = [{"t": " ".join(rnd.sample(vocab, 5)), "when": T0} for _ in range(80)]
    first = _groups(items, link_quorum=0.2, min_support=2)
    assert first == _groups(items, link_quorum=0.2, min_support=2)


def test_breadth_alone_engages_without_a_quorum():
    """The two rules are independent. With the quorum off entirely, breadth must still take the
    bookkeeping path and still sever the bridge — otherwise it would silently no-op for any
    deployment that has not adopted the quorum."""
    split = _groups(_weld_items(), link_quorum=0.0, min_support=2)
    assert [4] in split, "the foreign article is detached on breadth alone"


# --------------------------------------------------------------------------- #
# The env knob and the story-level wiring.
# --------------------------------------------------------------------------- #
def test_min_support_env_resolves_and_falls_back(monkeypatch):
    monkeypatch.delenv("RWE_CLUSTER_MIN_SUPPORT", raising=False)
    assert ss.min_support() == 1, "unset = off"
    monkeypatch.setenv("RWE_CLUSTER_MIN_SUPPORT", "2")
    assert ss.min_support() == 2
    monkeypatch.setenv("RWE_CLUSTER_MIN_SUPPORT", "banana")
    assert ss.min_support() == 1, "junk falls back rather than silently reshaping the catalog"
    monkeypatch.setenv("RWE_CLUSTER_MIN_SUPPORT", "0")
    assert ss.min_support() == 1, "below the floor is junk too"


def _row(url, headline, at=T0, publisher="P1"):
    return {"canonicalUrl": url, "url": url, "title": headline, "description": "",
            "publishedAt": at.isoformat(), "publisher": publisher, "scored": {}}


def _weld_rows():
    """Two publishers per side, so both candidate stories clear min_publishers=2."""
    return [
        _row("https://x.com/s1", _SPIDER, publisher="P1"),
        _row("https://x.com/s2", _SPIDER2, publisher="P2"),
        _row("https://x.com/s3", _SPIDER3, publisher="P3"),
        _row("https://x.com/bridge", _BRIDGE, publisher="P4"),
        _row("https://x.com/odyssey", _ODYSSEY, publisher="P5"),
    ]


def _headlines(story):
    return {(c.get("headline") or "") for c in story["coverage"]}


def test_build_stories_threads_support_and_separates_the_events(monkeypatch):
    monkeypatch.delenv("RWE_CLUSTER_MIN_SUPPORT", raising=False)
    rows = _weld_rows()
    welded = ss.build_stories(rows, quorum=0.2)
    assert any(len(s["coverage"]) == 5 for s in welded), "precondition: one story holds both films"

    split = ss.build_stories(rows, quorum=0.2, support=2)
    assert all(not (any("Odyssey" in h and "Spider" not in h for h in _headlines(s))
                    and any("Brand New Day" in h for h in _headlines(s)))
               for s in split), "no surviving story holds the lone Odyssey article and Spider-Man"
    assert split == ss.build_stories(rows, quorum=0.2, support=2), "deterministic"


def test_build_stories_reads_the_env_when_no_argument_is_passed(monkeypatch):
    rows = _weld_rows()
    monkeypatch.delenv("RWE_CLUSTER_MIN_SUPPORT", raising=False)
    off = ss.build_stories(rows, quorum=0.2)
    monkeypatch.setenv("RWE_CLUSTER_MIN_SUPPORT", "2")
    on = ss.build_stories(rows, quorum=0.2)
    assert on != off, "the knob reaches the build without an explicit argument"
    monkeypatch.setenv("RWE_CLUSTER_MIN_SUPPORT", "1")
    assert ss.build_stories(rows, quorum=0.2) == off, "and 1 restores production exactly"


def test_repair_links_on_the_same_rule_as_the_build():
    """The article_tokens discipline: the repair re-clusters a condemned cluster, and if it linked
    on a different corroboration rule than the primary build it would re-split on the
    disagreement rather than on a defect."""
    members = [dict(m) for m in
               [{"headline": _SPIDER, "url": "u1", "publisher": "P1",
                 "publishedAt": T0.isoformat()},
                {"headline": _SPIDER2, "url": "u2", "publisher": "P2",
                 "publishedAt": T0.isoformat()},
                {"headline": _SPIDER3, "url": "u3", "publisher": "P3",
                 "publishedAt": T0.isoformat()},
                {"headline": _BRIDGE, "url": "u4", "publisher": "P4",
                 "publishedAt": T0.isoformat()},
                {"headline": _ODYSSEY, "url": "u5", "publisher": "P5",
                 "publishedAt": T0.isoformat()}]]
    pieces = ss._repair(members, quorum=0.5, support=2, sim=cl.DEFAULT_SIM,
                        window_days=cl.DEFAULT_WINDOW_DAYS, min_shared=cl.MIN_SHARED_TOKENS,
                        min_tokens=cl.MIN_TITLE_TOKENS, idf=False,
                        min_articles=2, min_publishers=2)
    if pieces is not None:                      # a repair may decline; it must never re-weld
        for p in pieces:
            heads = {m["headline"] for m in p}
            assert not (_ODYSSEY in heads and _SPIDER in heads), \
                "the repair re-cluster must not restore the weld it was given"
