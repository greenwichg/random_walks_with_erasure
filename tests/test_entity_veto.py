"""X5c — the entity channel spent in the VETO direction (``story_service.entity_veto``).

The asymmetry this closes: geography could always refuse a merge, entities could only ever
propose one. A text-similar merge in a domain with no event geography — entertainment, business,
sport, where the recorded welds live — therefore had no independent second opinion at all.

The rule is the geo veto's, over the other channel: refuse iff BOTH sides carry a corroborated
entity consensus and those consensuses share no name. Everything else fails open. These pin that
the fail-open states really are open (that is what makes a 24%-coverage signal safe to consult),
that the veto cannot invent a merge, and that both cluster-level decision points — the build-time
gate and the aggregate dup-merge — consult it.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import story_service as ss   # noqa: E402

T0 = datetime(2026, 8, 25, 9, 0, 0, tzinfo=timezone.utc)


def _m(url, headline, desc="", hours=0, publisher="P1"):
    return {"id": url, "url": url, "canonicalUrl": url, "headline": headline,
            "description": desc, "publisher": publisher,
            "publishedAt": (T0 + timedelta(hours=hours)).isoformat()}


def _ents(**by_url):
    """url -> {"person": [...], "org": [...]} — the shape ``store.entities_for_urls`` returns."""
    return {u: {"person": list(names), "org": []} for u, names in by_url.items()}


# --------------------------------------------------------------------------- #
# The closure: consensus, disagreement, and every fail-open state.
# --------------------------------------------------------------------------- #
_A = [_m("a1", "Rivals clash in the final"), _m("a2", "Final ends in a draw")]
_B = [_m("b1", "Rivals clash in the final"), _m("b2", "Final ends in a draw")]


def _merge_ok(arts, entities, on=True, stats=None):
    ev, ok = ss._entity_closures(arts, entities, on, stats)
    assert ev is None, "the rule is cluster-level by construction; it has no pairwise opinion"
    return ok


def test_disjoint_corroborated_consensuses_veto_the_merge():
    arts = _A + _B
    ents = _ents(a1=["ana lopez"], a2=["ana lopez"], b1=["bo mensah"], b2=["bo mensah"])
    stats = {}
    ok = _merge_ok(arts, ents, stats=stats)
    assert ok([0, 1], [2, 3]) is False
    assert stats["entityMergeVetoed"] == 1 and stats["entityMergeGated"] == 1


def test_any_overlap_at_all_lets_the_merge_through():
    arts = _A + _B
    ents = _ents(a1=["ana lopez", "kit fell"], a2=["ana lopez", "kit fell"],
                 b1=["kit fell"], b2=["kit fell"])
    assert _merge_ok(arts, ents)([0, 1], [2, 3]) is True


def test_uncorroborated_testimony_fails_open():
    """A consensus needs a name carried by >= 2 members. One member naming someone is a sample of
    one — the same standard GEO_MIN_CONSENSUS applies — and must not veto anything."""
    arts = _A + _B
    ents = _ents(a1=["ana lopez"], b1=["bo mensah"])          # one witness per side
    stats = {}
    assert _merge_ok(arts, ents, stats=stats)([0, 1], [2, 3]) is True
    assert "entityMergeVetoed" not in stats
    assert stats.get("entityMergeGated") is None, "never even reached the gated branch"


def test_missing_extraction_fails_open():
    """The coverage state that killed entities as an ADMISSION channel. As a veto it is simply
    silent — which is what makes a 24%-coverage signal safe to consult at all."""
    arts = _A + _B
    assert _merge_ok(arts, _ents(a1=["ana lopez"], a2=["ana lopez"]))([0, 1], [2, 3]) is True


def test_noise_names_cannot_carry_a_consensus():
    """Platform chrome, countries and outlet names are ABOUT the page, not the event. If they
    could form a consensus they would manufacture disagreements between unrelated stories."""
    arts = _A + _B
    ents = _ents(a1=["facebook", "united states"], a2=["facebook", "united states"],
                 b1=["reuters"], b2=["reuters"])
    assert _merge_ok(arts, ents)([0, 1], [2, 3]) is True, "no real consensus on either side"


def test_off_and_no_entities_return_no_closure():
    arts = _A + _B
    ents = _ents(a1=["ana lopez"], a2=["ana lopez"], b1=["bo"], b2=["bo"])
    assert ss._entity_closures(arts, ents, False) == (None, None), "off"
    assert ss._entity_closures(arts, None, True) == (None, None), "no mapping fetched"
    assert ss._entity_closures(arts, {}, True) == (None, None), "empty mapping"


def test_env_knob_defaults_off(monkeypatch):
    monkeypatch.delenv("RWE_STORY_ENTITY_VETO", raising=False)
    assert ss.entity_veto() is False
    monkeypatch.setenv("RWE_STORY_ENTITY_VETO", "1")
    assert ss.entity_veto() is True
    monkeypatch.setenv("RWE_STORY_ENTITY_VETO", "0")
    assert ss.entity_veto() is False


def test_entities_are_fetched_when_either_consumer_is_on(monkeypatch):
    """The veto reads the same side table X5b already requires, so the fetch gate has to answer
    to BOTH consumers or the veto silently no-ops in production."""
    class _Store:
        def __init__(self):
            self.asked = 0

        def entities_for_urls(self, urls):
            self.asked += 1
            return {"u": {"person": ["x"], "org": []}}

    rows = [{"canonicalUrl": "u"}]
    monkeypatch.setenv("RWE_STORY_ENTITY_MERGE", "0")
    monkeypatch.delenv("RWE_STORY_ENTITY_VETO", raising=False)
    st = _Store()
    assert ss._entities_for(st, rows) is None and st.asked == 0, "both off: never queried"

    monkeypatch.setenv("RWE_STORY_ENTITY_VETO", "1")
    st = _Store()
    assert ss._entities_for(st, rows) is not None and st.asked == 1, "veto alone pays for it"

    monkeypatch.setenv("RWE_STORY_ENTITY_MERGE", "2")
    monkeypatch.delenv("RWE_STORY_ENTITY_VETO", raising=False)
    st = _Store()
    assert ss._entities_for(st, rows) is not None and st.asked == 1, "X5b alone still does too"


# --------------------------------------------------------------------------- #
# The composer: two cluster-level gates, ANDed.
# --------------------------------------------------------------------------- #
def test_merge_gates_compose_and_none_drops():
    yes, no = (lambda a, b: True), (lambda a, b: False)
    assert ss._and_merge_ok(None, None) is None
    assert ss._and_merge_ok(yes, None)([0], [1]) is True
    assert ss._and_merge_ok(yes, no)([0], [1]) is False, "either channel can refuse"
    assert ss._and_merge_ok(no, yes)([0], [1]) is False


# --------------------------------------------------------------------------- #
# The aggregate stage: the dup-merge consults the same rule.
# --------------------------------------------------------------------------- #
# Two clusters whose PROFILES (headline + dek) are near-identical — the dup-merge's own signal
# says join. They are different events, and only the entity channel knows it.
_SHOOT_A = [_m("s1", "Gunfire reported downtown", "Police responded to reports of gunfire "
                     "downtown on Tuesday evening, officials said."),
            _m("s2", "Shots fired downtown", "Officials said police responded to reports of "
                     "gunfire downtown Tuesday evening.", publisher="P2")]
_SHOOT_B = [_m("t1", "Gunfire reported downtown", "Police responded to reports of gunfire "
                     "downtown on Tuesday evening, officials said.", hours=2),
            _m("t2", "Shots fired downtown", "Officials said police responded to reports of "
                     "gunfire downtown Tuesday evening.", hours=2, publisher="P3")]


def test_dup_merge_joins_the_lookalikes_without_the_veto():
    """Precondition: the aggregate stage's own signal merges these two clusters."""
    out = ss._merge_duplicates([list(_SHOOT_A), list(_SHOOT_B)], min_sim=0.33,
                               max_gap_hours=48.0, max_size=100)
    assert len(out) == 1, "profiles are near-identical; the dup-merge joins them"


def test_dup_merge_respects_an_entity_disagreement():
    ents = _ents(s1=["ana lopez"], s2=["ana lopez"], t1=["bo mensah"], t2=["bo mensah"])
    stats = {}
    out = ss._merge_duplicates([list(_SHOOT_A), list(_SHOOT_B)], min_sim=0.33,
                               max_gap_hours=48.0, max_size=100,
                               ent_veto=True, entities=ents, veto_stats=stats)
    assert len(out) == 2, "different events, and only the entity channel could tell"
    assert stats["dupMergeEntityVetoed"] == 1


def test_dup_merge_veto_is_off_by_default_and_fails_open():
    ents = _ents(s1=["ana lopez"], s2=["ana lopez"], t1=["bo mensah"], t2=["bo mensah"])
    off = ss._merge_duplicates([list(_SHOOT_A), list(_SHOOT_B)], min_sim=0.33,
                               max_gap_hours=48.0, max_size=100, entities=ents)
    assert len(off) == 1, "the knob is what turns it on; entities alone change nothing"
    open_ = ss._merge_duplicates([list(_SHOOT_A), list(_SHOOT_B)], min_sim=0.33,
                                 max_gap_hours=48.0, max_size=100,
                                 ent_veto=True, entities=_ents(s1=["ana"], s2=["ana"]))
    assert len(open_) == 1, "one side unextracted -> fail open, exactly as at build time"


# --------------------------------------------------------------------------- #
# End to end through build_stories — the BUILD-TIME cluster gate.
#
# Two mayoral primaries in two towns. Each race's pair clusters on its own tokens, and the two
# races then weld to each other on the shared {wins, mayoral, primary} — a real lexical case with
# nothing boilerplate in it, and no geography to separate them (both stories are local politics
# with no extracted event country). The entity channel is the only witness that they are two
# elections, which is exactly the domain gap X5c exists to close.
# --------------------------------------------------------------------------- #
def _row(url, headline, hours=0, publisher="P1"):
    return {"canonicalUrl": url, "url": url, "title": headline, "description": "",
            "publishedAt": (T0 + timedelta(hours=hours)).isoformat(),
            "publisher": publisher, "scored": {}}


def _rows():
    return [_row("https://x.com/a1", "Lopez wins Ridgeway mayoral primary"),
            _row("https://x.com/a2", "Ridgeway mayoral primary won by Lopez", publisher="P2"),
            _row("https://x.com/b1", "Mensah wins Calder mayoral primary", publisher="P3"),
            _row("https://x.com/b2", "Calder mayoral primary won by Mensah", publisher="P4")]


def _story_ents():
    return _ents(**{"https://x.com/a1": ["ana lopez"], "https://x.com/a2": ["ana lopez"],
                    "https://x.com/b1": ["bo mensah"], "https://x.com/b2": ["bo mensah"]})


def _sizes(stories):
    return sorted(len(s["coverage"]) for s in stories)


def test_build_stories_is_byte_identical_with_the_veto_off(monkeypatch):
    monkeypatch.delenv("RWE_STORY_ENTITY_VETO", raising=False)
    rows, ents = _rows(), _story_ents()
    base = ss.build_stories(rows)
    assert base == ss.build_stories(rows, entities=ents), \
        "entities present but unconsumed must not move a single story"
    assert base == ss.build_stories(rows, ent_veto=False, entities=ents)


def test_build_stories_separates_the_events_under_the_veto():
    rows, ents = _rows(), _story_ents()
    welded = ss.build_stories(rows, entities=ents)
    assert _sizes(welded) == [4], "precondition: the two races weld on their shared vocabulary"
    split = ss.build_stories(rows, ent_veto=True, entities=ents)
    assert _sizes(split) == [2, 2], "one story per election"
    assert split == ss.build_stories(rows, ent_veto=True, entities=ents), "deterministic"


def test_the_veto_is_silent_when_the_two_sides_agree():
    """The same weld, with the SAME person named on both sides — one candidate, two write-ups of
    one race. Overlap means agreement, and agreement must not be disturbed."""
    rows = _rows()
    agree = _ents(**{u: ["ana lopez"] for u in
                     ("https://x.com/a1", "https://x.com/a2",
                      "https://x.com/b1", "https://x.com/b2")})
    assert _sizes(ss.build_stories(rows, ent_veto=True, entities=agree)) == [4]


def test_the_veto_can_only_refuse_merges_never_create_them():
    """Same structural guarantee as support breadth: every story under the veto is a SUBSET of
    some story without it, so the reachable outcomes are a split or no change."""
    rows, ents = _rows(), _story_ents()
    base = [frozenset(c["url"] for c in s["coverage"])
            for s in ss.build_stories(rows, entities=ents)]
    tight = [frozenset(c["url"] for c in s["coverage"])
             for s in ss.build_stories(rows, ent_veto=True, entities=ents)]
    for g in tight:
        assert any(g <= b for b in base), f"{sorted(g)} is not contained in any prior story"


def test_the_repair_re_cluster_links_on_the_same_entity_rule():
    """The article_tokens discipline over the entity channel: the repair rebuilds its closures
    over its own sublist, so it must receive the mapping or it would re-weld what the primary
    build separated."""
    members = [_m("https://x.com/a1", "Lopez wins Ridgeway mayoral primary"),
               _m("https://x.com/a2", "Ridgeway mayoral primary won by Lopez", publisher="P2"),
               _m("https://x.com/b1", "Mensah wins Calder mayoral primary", publisher="P3"),
               _m("https://x.com/b2", "Calder mayoral primary won by Mensah", publisher="P4")]
    import clustering as cl
    pieces = ss._repair(members, quorum=0.5, sim=cl.DEFAULT_SIM,
                        window_days=cl.DEFAULT_WINDOW_DAYS, min_shared=cl.MIN_SHARED_TOKENS,
                        min_tokens=cl.MIN_TITLE_TOKENS, idf=False,
                        min_articles=2, min_publishers=2,
                        ent_veto=True, entities=_story_ents())
    if pieces is not None:
        for piece in pieces:
            names = {m["headline"] for m in piece}
            assert not (any("Lopez" in h for h in names) and any("Mensah" in h for h in names)), \
                "the repair must not restore a weld the entity channel refused"
