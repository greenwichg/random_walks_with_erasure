"""Stage 0.1 — instance anchors (``clustering.instance_anchors`` + ``story_service.anchor_veto``).

The failure class: ``title_tokens`` drops bare numbers, so "Wordle hints for September 2" and
"…for September 3" are IDENTICAL token sets (Jaccard 1.00 on the template) and no threshold,
quorum or lexicon can separate them — the only differing token was discarded before any rule
looked. The anchor reads that number back as a slot->value fact beside the tokens and spends it
as a veto on the edge, the cluster merge, and both aggregate merge passes.

Two properties are load-bearing and pinned first: the OFF state is byte-identical (every
production measurement rests on it), and the rubric's rule-2 counter-case — one film's day-2 and
day-3 collections are ONE event — is never split, which is why ``day`` is not a slot.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "examples"))

import audit_clustering_change as acc   # noqa: E402
import clustering                       # noqa: E402
import evidence_resolver as er          # noqa: E402
import store as store_mod               # noqa: E402
import story_service as ss              # noqa: E402

T0 = datetime(2026, 9, 1, 9, 0, 0, tzinfo=timezone.utc)


def _row(url, headline, hours=0, publisher="P1", dek="Today's puzzle, explained."):
    """FeedArticle ROW shape — what build_stories ingests."""
    return {"canonicalUrl": url, "url": url, "title": headline, "description": dek,
            "publisher": publisher, "publishedAt": (T0 + timedelta(hours=hours)).isoformat(),
            "scored": {}}


def _art(url, headline, hours=0, publisher="P1"):
    """Article-dict shape — what the closures see inside the build."""
    return {"id": url, "url": url, "canonicalUrl": url, "headline": headline, "description": "",
            "publisher": publisher, "publishedAt": (T0 + timedelta(hours=hours)).isoformat()}


# --------------------------------------------------------------------------- #
# The extractor.
# --------------------------------------------------------------------------- #
A = clustering.instance_anchors


@pytest.mark.parametrize("title, expected", [
    ("Wordle hints and answer for September 2", {"date": {"09-02"}}),
    ("NYT Connections hints for Sept. 3rd", {"date": {"09-03"}}),
    ("Strands answers for 2026-09-02", {"date": {"09-02"}}),
    ("2. September: Bundestag berät den Haushalt", {"date": {"09-02"}}),
    ("Horóscopo del 2 de septiembre", {"date": {"09-02"}}),
    ("Météo du 3 septembre", {"date": {"09-03"}}),
    ("Week 3 NFL picks: every game", {"week": {3}}),
    ("Gameweek 4 preview", {"week": {4}}),
    ("GW4 differentials", {"week": {4}}),
    ("Apple Q3 earnings beat estimates", {"quarter": {3}}),
    ("India vs England 2nd Test highlights", {"test": {2}}),
    ("Champions League second leg: Arsenal vs Real", {"leg": {2}}),
    ("Severance S2E5 recap", {"season": {2}, "episode": {5}}),
    ("Stranger Things season 5 episode 3 review", {"season": {5}, "episode": {3}}),
    ("Game 7 preview: Celtics vs Knicks", {"game": {7}}),
])
def test_anchors_are_read_from_dates_and_series_slots(title, expected):
    assert {k: set(v) for k, v in A(title).items()} == expected


@pytest.mark.parametrize("title", [
    "Batwara box office day 2 collection",       # rule 2: one film's run — `day` is not a slot
    "Batwara day 3 collection",
    "Fury drops Usyk in round 3",                 # updates of one fight
    "Lakers rally in fourth quarter to beat Celtics",
    "Hamilton fastest on lap 30",
    "12 dead after floods",                       # rule 6, second clause: counts are not anchors
    "Election 2026: what to know",                # years are context inside a six-day window
    "Boeing 737 MAX returns to service",
    "H5N1 bird flu spreads to dairy herds",
    "Trump may 2 million voters decide",          # ambiguous month word, lower-case: not a date
    "",
])
def test_the_excluded_forms_carry_no_anchor(title):
    assert A(title) == {}


def test_a_capitalised_ambiguous_month_word_is_a_date():
    assert A("Rally set for May 2 in Austin") == {"date": frozenset({"05-02"})}


def test_conflict_needs_the_same_slot_on_both_sides_with_nothing_in_common():
    a, b, c = A("Wordle hints for Sept 2"), A("Wordle hints for Sept 3"), A("Wordle hints today")
    assert clustering.anchors_conflict(a, b) == "date"
    assert clustering.anchors_conflict(a, c) is None, "a side with no anchor is silence"
    assert clustering.anchors_conflict(a, A("Wordle for Sept 2-3 weekend")) is None, \
        "a shared value on the slot is agreement however many other values sit beside it"
    assert clustering.anchors_conflict(A("Week 3 picks"), A("Game 7 picks")) is None, \
        "different slots never conflict with each other"


def test_consensus_is_corroborated_and_a_singleton_has_none():
    two = [A("Wordle hints for Sept 2"), A("Wordle answer for Sept 2")]
    assert clustering.anchor_consensus(two) == {"date": frozenset({"09-02"})}
    assert clustering.anchor_consensus(two[:1]) == {}, "one headline is a sample of one"
    mixed = two + [A("Wordle hints for Sept 3")]
    assert clustering.anchor_consensus(mixed) == {"date": frozenset({"09-02"})}, \
        "a value carried by one member does not join the consensus"


# --------------------------------------------------------------------------- #
# The closure.
# --------------------------------------------------------------------------- #
def test_off_is_none_none_so_the_fast_path_survives():
    assert ss._anchor_closure([_art("a", "Wordle hints for Sept 2")], False) == (None, None)


def test_the_edge_rule_vetoes_a_slot_conflict_and_counts_it_per_slot():
    arts = [_art("a", "Wordle hints and answer for September 2"),
            _art("b", "Wordle hints and answer for September 3"),
            _art("c", "Wordle hints and answer today")]
    stats = {}
    ev, _ = ss._anchor_closure(arts, True, stats)
    assert ev(0, 1) is False
    assert ev(0, 2) is True and ev(1, 2) is True, "an unanchored side fails open"
    assert stats == {"anchorEdgeVetoed": 1, "anchorEdgeVetoed:date": 1}


def test_the_merge_rule_needs_consensus_on_both_sides():
    arts = [_art("a", "Wordle hints for Sept 2"), _art("b", "Wordle answer for Sept 2"),
            _art("c", "Wordle hints for Sept 3"), _art("d", "Wordle answer for Sept 3"),
            _art("e", "Wordle hints for Sept 3"), _art("f", "Wordle hints today")]
    stats = {}
    _, ok = ss._anchor_closure(arts, True, stats)
    assert ok([0, 1], [2, 3]) is False and stats["anchorMergeVetoed"] == 1
    assert ok([0, 1], [4]) is True, "a singleton has no consensus and fails open"
    assert ok([0, 1], [5]) is True


# --------------------------------------------------------------------------- #
# Through the build: the edge, the aggregate merge, the entity merge, the repair.
# --------------------------------------------------------------------------- #
def _wordle_rows():
    return [_row("https://a.example/1", "Wordle hints and answer for September 2", 0, "P1"),
            _row("https://b.example/2", "Wordle hints and answer for September 2", 1, "P2"),
            _row("https://c.example/3", "Wordle hints and answer for September 3", 24, "P3"),
            _row("https://d.example/4", "Wordle hints and answer for September 3", 25, "P4")]


def test_off_is_byte_identical_and_the_two_days_weld():
    """The baseline the production audit compares against: four identical token sets, one
    story. Every measurement of the candidate rests on this state being unchanged."""
    stories = ss.build_stories(_wordle_rows(), anchor=False, merge=0.0)
    assert len(stories) == 1 and stories[0]["totalCoverage"] == 4


def test_on_separates_the_two_instances_and_the_profile_merge_cannot_rejoin_them():
    """The containment property `support_scope` records: a refusal inside cluster() is undone
    by `_merge_duplicates` unless that pass consults the same rule. The two instances share
    every profile token (identical deks), so the merge candidate exists and must be vetoed."""
    stats = {}
    stories = ss.build_stories(_wordle_rows(), anchor=True, merge=0.33, veto_stats=stats)
    assert sorted(s["totalCoverage"] for s in stories) == [2, 2]
    assert stats["anchorEdgeVetoed"] >= 1
    assert stats.get("dupMergeAnchorVetoed", 0) >= 1, "the aggregate pass tried and was refused"


def test_the_rule_two_counter_case_stays_one_story():
    """The ``batwara-days`` exhibit (docs/EVENT_IDENTITY_RUBRIC.md, rule 2, same_event): day 2
    and day 3 of ONE film's run. `day` is not a slot and counts are not anchors, so the veto has
    nothing to say and the pair stays together with the rule on."""
    rows = [_row("https://a.example/b2", "Batwara box office day 2: film collects 5 crore", 0, "P1"),
            _row("https://b.example/b3", "Batwara box office day 3: film collects 7 crore", 24, "P2")]
    assert len(ss.build_stories(rows, anchor=True)) == 1


def test_the_env_flag_reaches_the_build(monkeypatch):
    monkeypatch.setenv("RWE_CLUSTER_ANCHOR_VETO", "1")
    assert ss.anchor_veto() is True
    assert len(ss.build_stories(_wordle_rows(), merge=0.0)) == 2
    monkeypatch.setenv("RWE_CLUSTER_ANCHOR_VETO", "garbage")
    assert ss.anchor_veto() is False, "junk falls back to off, never to a guess"
    monkeypatch.delenv("RWE_CLUSTER_ANCHOR_VETO")
    assert ss.anchor_veto() is False and len(ss.build_stories(_wordle_rows(), merge=0.0)) == 1


def test_the_entity_merge_pass_consults_anchors_like_geography():
    """X5b joins stories on shared corroborated names; two instances of one series share every
    name (the puzzle's maker) and differ only in the slot. Anchors outrank entities the way the
    geo consensus does."""
    groups = [[_art("a", "Wordle hints for Sept 2", 0), _art("b", "Wordle answer for Sept 2", 1)],
              [_art("c", "Wordle hints for Sept 3", 24), _art("d", "Wordle answer for Sept 3", 25)]]
    ents = {u: {"person": ["josh wardle"], "org": ["wordle bot"]} for u in "abcd"}
    joined = ss._merge_by_entities(groups, entities=ents, min_names=2, max_gap_hours=48,
                                   max_size=130, anchor=False)
    assert len(joined) == 1, "without the rule the entity pass welds the two days"
    stats = {}
    kept = ss._merge_by_entities(groups, entities=ents, min_names=2, max_gap_hours=48,
                                 max_size=130, stats=stats, anchor=True)
    assert len(kept) == 2 and stats["entityMergeAnchorVetoed"] == 1


def test_the_repair_re_cluster_consults_the_same_rule(monkeypatch):
    """A repair that ignored a rule the primary build applied would re-split on the passes'
    disagreement rather than on a defect (the article_tokens discipline). Pinned at the seam:
    `_repair` must build the anchor closure over ITS member list, with the flag it was handed."""
    seen = []
    real = ss._anchor_closure

    def spy(arts, on, stats=None):
        seen.append((len(arts), on))
        return real(arts, on, stats)
    monkeypatch.setattr(ss, "_anchor_closure", spy)
    members = [_art("a", "Wordle hints for Sept 2"), _art("b", "Wordle hints for Sept 3")]
    ss._repair(members, quorum=0.5, sim=0.28, window_days=6.0, min_shared=3, min_tokens=3,
               idf=False, min_articles=1, min_publishers=1, anchor=True)
    assert seen == [(2, True)]


# --------------------------------------------------------------------------- #
# The audit harness: AFTER side only, telemetry printed, the split visible.
# --------------------------------------------------------------------------- #
def test_the_audit_flag_applies_to_the_after_side_and_prints_telemetry(tmp_path, monkeypatch,
                                                                       capsys):
    monkeypatch.setenv("RWE_CLUSTER_LINK_QUORUM", "0")     # deploy env present, no warning
    monkeypatch.delenv("RWE_CLUSTER_ANCHOR_VETO", raising=False)
    st = store_mod.Store(f"sqlite:///{tmp_path / 'anchor.db'}")
    for r in _wordle_rows():
        st.upsert_feed_article(
            canonical_url=er._canon(r["url"]), url=r["url"], publisher=r["publisher"],
            source_publisher=r["publisher"], title=r["title"], description=r["description"],
            body=None, published_at=r["publishedAt"], source_feed="f",
            scored={"article_id": er._canon(r["url"]), "outlet": r["publisher"],
                    "category": "Games", "lean": 0.0, "title": r["title"]})
    monkeypatch.setenv("RWE_STORIES_SCAN_DAYS", "100000")
    rc = acc.main(["--db", f"sqlite:///{tmp_path / 'anchor.db'}", "--anchor-veto"])
    out = capsys.readouterr().out
    assert rc == 0
    before = next(l for l in out.splitlines() if l.startswith("before"))
    after = next(l for l in out.splitlines() if l.startswith("after"))
    assert "anchor-veto" in after and "anchor-veto" not in before, "an AFTER-side change"
    assert "anchor telemetry   : edges vetoed" in out and "date" in out
    assert "clusters split     : 1" in out, "one template weld severed into its two days"
