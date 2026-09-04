"""Tests for examples/story_tags.py + its wiring in story_service — the story-level topics/tags.

WHAT IS PINNED HERE, and what deliberately is not. The RULES are pinned: what makes a name a tag,
what makes one rank above another, what lets one cross from a related story, and what a fragment
of a name is folded onto. The CONSTANTS are not asserted against a fixture's output — both halves
of the score move with the window's size (story frequency decides specificity, and the df ceiling
is a share of the catalog), so a number that looks right on nine stories says nothing about 2,852.
That is the mistake the Similar Stories floor made twice; here the thresholds are passed in or
compared relative to each other instead.
"""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store as store_mod        # noqa: E402
import story_service as ss       # noqa: E402
import story_tags as stg         # noqa: E402

import pytest                     # noqa: E402

NOW = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv("RWE_STORIES_SCAN_DAYS", raising=False)
    ss.clear_cache()
    yield
    ss.clear_cache()


def _add(st, cu, pub, lean, title, desc, *, category="Health", hours=3):
    st.upsert_feed_article(
        canonical_url=cu, url=cu, publisher=pub, source_publisher=pub, title=title,
        description=desc, body=None, published_at=(NOW - timedelta(hours=hours)).isoformat(),
        source_feed="feed://t", country=None,
        scored={"article_id": cu, "outlet": pub, "category": category, "lean": lean, "title": title})


_OUTBREAK = ("The World Health Organization declared the outbreak in the Democratic Republic of "
             "the Congo. Response teams reached Kivu province clinics on Tuesday.")
_VACCINE = ("Vaccination teams began work in Kivu province, the World Health Organization said. "
            "The Ebola caseload in the Democratic Republic of the Congo is still rising.")
_MARKETS = ("The Federal Reserve held rates steady as Wall Street closed higher. The Dow Jones "
            "Industrial Average rose.")


def _catalog(st):
    """Three events: an outbreak, its vaccination response, and an unrelated markets story.

    The first two are a real pair — the same subject family, close enough to be strongly related
    and far enough apart to survive as two stories — which is the only shape that can exercise
    inheritance. Both name the disease and the country, as two stories about one outbreak would;
    only the first names the WHO, which is the tag with somewhere to travel. The third is the
    control: whatever the other two agree on must not reach it.
    """
    for cu, pub, lean, title in [
        ("https://a1.example/x", "NPR", -1.0,
         "World Health Organization declares Ebola outbreak in the Democratic Republic of the Congo"),
        ("https://a2.example/x", "BBC News", 0.0,
         "World Health Organization confirms Ebola outbreak in the Democratic Republic of the Congo"),
        ("https://a3.example/x", "Reuters", 0.0,
         "World Health Organization reports Ebola outbreak in the Democratic Republic of the Congo"),
    ]:
        _add(st, cu, pub, lean, title, _OUTBREAK)
    for cu, pub, lean, title in [
        ("https://b1.example/x", "AP", 0.0,
         "Ebola vaccination teams reach remote clinics across the Democratic Republic of the Congo"),
        ("https://b2.example/x", "Sky News", 0.0,
         "Ebola vaccination teams enter remote clinics across the Democratic Republic of the Congo"),
        ("https://b3.example/x", "The Guardian", -1.2,
         "Ebola vaccination teams open remote clinics across the Democratic Republic of the Congo"),
    ]:
        _add(st, cu, pub, lean, title, _VACCINE, hours=2)
    _add(st, "https://c1.example/m", "WSJ", 0.8, "Federal Reserve holds rates as Wall Street rallies",
         _MARKETS, category="Business", hours=4)
    _add(st, "https://c2.example/m", "Fox News", 1.2,
         "Federal Reserve holds rates steady, Wall Street rallies", _MARKETS,
         category="Business", hours=4)


def _stories(st):
    return ss.list_stories(st, limit=50)["stories"]


def _find(stories, needle):
    return next(s for s in stories if needle.lower() in s["title"].lower())


def _tags(story):
    return {t["name"]: t for t in story.get("tags") or []}


# --------------------------------------------------------------------------- #
# Direct extraction
# --------------------------------------------------------------------------- #
def test_tags_are_the_story_s_own_entities_not_its_category():
    """The headline requirement: a story's tags are what IT is about. The category is present and
    marked as the category, never passed off as evidence about this story."""
    st = store_mod.Store("sqlite://"); _catalog(st)
    outbreak = _find(_stories(st), "World Health Organization declares")
    tags = _tags(outbreak)
    assert {"ebola", "world health organization", "democratic republic of the congo"} <= set(tags)
    assert all(tags[n]["source"] == stg.SOURCE_DIRECT
               for n in ("ebola", "world health organization", "democratic republic of the congo"))
    # The category is there, and it is labelled as the shelf rather than as evidence.
    assert tags["health"]["source"] == stg.SOURCE_TOPIC
    # …and it can never outrank a corroborated entity, whatever the catalog does.
    assert tags["health"]["score"] < min(tags[n]["score"] for n in tags
                                         if tags[n]["source"] == stg.SOURCE_DIRECT)


def test_a_story_s_tags_are_its_own():
    """No bleed between unrelated stories — the failure the whole projection exists to avoid."""
    stories = None
    st = store_mod.Store("sqlite://"); _catalog(st)
    stories = _stories(st)
    markets = _tags(_find(stories, "Federal Reserve"))
    assert {"federal reserve", "wall street"} <= set(markets)
    assert not ({"ebola", "world health organization", "democratic republic of the congo"} & set(markets))


def test_noise_names_never_become_tags():
    """Platforms and outlet names are about the page and the press, not the event. Places are NOT
    noise here, which is the one way tag_noise differs from entity_noise — see its docstring."""
    assert ss.tag_noise("facebook") and ss.tag_noise("instagram")
    assert ss.tag_noise("reuters") and ss.tag_noise("associated press")
    assert not ss.tag_noise("democratic republic of the congo")
    assert not ss.tag_noise("ebola")
    # And the clustering filter still drops the place, because there it is a duplicate geo vote.
    assert ss.entity_noise("democratic republic of the congo")


def test_a_name_one_member_carries_is_not_a_tag():
    """Corroboration, the entity channel's own rule: one outlet's phrasing is a sample of one."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://s1.example/q", "NPR", -1.0,
         "Ferry service resumes between Portsmouth and Caen after the storm",
         "Sailings restarted on Tuesday morning.")
    _add(st, "https://s2.example/q", "BBC News", 0.0,
         "Ferry service resumes between Portsmouth and Caen following the storm",
         "Sailings restarted on Tuesday morning.")
    # Only the second article mentions the operator, so it is uncorroborated.
    _add(st, "https://s3.example/q", "Sky News", 0.0,
         "Ferry service resumes between Portsmouth and Caen after Brittany Ferries checks",
         "Sailings restarted on Tuesday morning.")
    tags = _tags(_stories(st)[0])
    assert "portsmouth and caen" in tags or "portsmouth" in tags   # carried by all three
    assert "brittany ferries" not in tags                          # carried by one


def test_singleton_names_need_the_window_to_write_them_as_names():
    """"Ebola" is a tag and "Markets" is not, and no word list decides which. A common noun turns
    up lower-case somewhere in the window; a name does not."""
    stories = [
        {"id": "s1", "title": "Ebola spreads in the province",
         "summary": "The ebola response continues.", "coverage": []},
        {"id": "s2", "title": "Markets rally on tech earnings",
         "summary": "European markets closed higher.", "coverage": []},
    ]
    names = stg._case_profile(stories)
    assert "markets" not in names          # appears lower-case in s2's summary
    assert "ebola" not in names            # appears lower-case in s1's summary — evidence, not a guess
    # With no lower-case usage anywhere, the same word IS admitted.
    only_capitalised = [{"id": "s3", "title": "Ebola spreads in the province",
                         "summary": "Teams reached the clinics.", "coverage": []}]
    assert "ebola" in stg._case_profile(only_capitalised)


# --------------------------------------------------------------------------- #
# Normalisation + dedup
# --------------------------------------------------------------------------- #
def test_a_country_arrives_as_one_tag_not_three():
    """Three extractors read the same text and disagree about where a name ends: the span reader
    cannot chain "of the" and returns "Democratic Republic", the singleton pass returns "Congo",
    and the phrase reader returns the whole thing. One country, one tag."""
    st = store_mod.Store("sqlite://"); _catalog(st)
    tags = _tags(_find(_stories(st), "World Health Organization declares"))
    assert "democratic republic of the congo" in tags
    assert "congo" not in tags and "democratic republic" not in tags


def test_phrases_chain_connectors_where_the_span_reader_will_not():
    import entity_spans
    head = "Ebola outbreak confirmed in the Democratic Republic of the Congo"
    assert stg.phrases(head) == ["democratic republic of the congo"]
    # The span reader's own answer, unchanged — this is an addition, not an edit to clustering.
    assert "democratic republic" in entity_spans.extract(head)
    assert "democratic republic of the congo" not in entity_spans.extract(head)


def test_canonicalise_folds_fragments_and_keeps_the_larger_vote():
    canon = ["democratic republic of the congo"]
    folded = stg._canonicalise(
        {"congo": 2, "democratic republic": 3, "democratic republic of the congo": 3, "ebola": 4},
        canon)
    assert folded == {"democratic republic of the congo": 3, "ebola": 4}
    # Votes transfer by MAX, not sum: one member saying both is one member.
    assert stg._canonicalise({"congo": 2, "democratic republic of the congo": 2}, canon) == \
        {"democratic republic of the congo": 2}
    # The container needs no votes of its own: the case this exists for is a story whose HEADLINES
    # carry only fragments ("Congo province") while the dek spells the country out.
    assert stg._canonicalise({"congo": 2, "democratic republic": 3}, canon) == \
        {"democratic republic of the congo": 3}


def test_no_story_ever_lists_the_same_tag_twice():
    st = store_mod.Store("sqlite://"); _catalog(st)
    for s in _stories(st):
        names = [t["name"] for t in s.get("tags") or []]
        assert len(names) == len(set(names))


def test_labels_are_readable_and_stable():
    assert stg.label_for("democratic republic of the congo") == "Democratic Republic of the Congo"
    assert stg.label_for("world health organization") == "World Health Organization"
    assert stg.label_for("nhs") == "NHS"                      # cannot be an English word
    assert stg.label_for("ebola") == "Ebola"


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
def test_specific_names_outrank_ubiquitous_ones():
    """The specificity term, isolated from the fixture: same corroboration, different story
    frequency. This is what keeps a name half the catalog carries below a name two stories do."""
    rare = stg._score(votes=3, members=3, df=1, total_stories=100)
    common = stg._score(votes=3, members=3, df=50, total_stories=100)
    assert rare > common > 0


def test_a_background_name_is_dropped_rather_than_ranked_low():
    """Above the df ceiling a name is not a weak tag, it is not a tag: it describes the window
    rather than the story."""
    stories = [{"id": f"s{i}", "title": "x", "summary": "", "topic": "",
                "coverage": [{"headline": "Acme Holdings reports", "url": f"u{i}a"},
                             {"headline": "Acme Holdings responds", "url": f"u{i}b"}]}
               for i in range(60)]
    tags = stg.extract_tags(stories, {}, noise=lambda n: False)
    assert all("acme holdings" not in {t["name"] for t in rows} for rows in tags.values())


def test_tags_are_ranked_best_first_and_the_order_is_total():
    st = store_mod.Store("sqlite://"); _catalog(st)
    for s in _stories(st):
        rows = s.get("tags") or []
        assert [r["score"] for r in rows] == sorted((r["score"] for r in rows), reverse=True)
    # Deterministic: the same build gives the same order.
    ss.clear_cache()
    again = {s["id"]: [t["name"] for t in s.get("tags") or []] for s in _stories(st)}
    ss.clear_cache()
    assert again == {s["id"]: [t["name"] for t in s.get("tags") or []] for s in _stories(st)}


def test_the_tag_list_is_capped():
    st = store_mod.Store("sqlite://"); _catalog(st)
    assert all(len(s.get("tags") or []) <= stg.TAG_CAP for s in _stories(st))


# --------------------------------------------------------------------------- #
# Inheritance
# --------------------------------------------------------------------------- #
def test_a_tag_crosses_to_a_strongly_related_story_and_says_it_did():
    """The propagation requirement. The vaccination story's outlets never name the WHO in a
    headline; the outbreak story's all do, the two are strongly related, and the vaccination
    story's own summary corroborates it — so it crosses, marked."""
    st = store_mod.Store("sqlite://"); _catalog(st)
    stories = _stories(st)
    vaccine = _tags(_find(stories, "vaccination"))
    who = vaccine.get("world health organization")
    assert who is not None and who["source"] == stg.SOURCE_INHERITED
    # It ranks under everything the story said about itself.
    own = [t["score"] for t in (_find(stories, "vaccination").get("tags") or [])
           if t["source"] == stg.SOURCE_DIRECT]
    assert own and who["score"] < min(own)


def test_nothing_crosses_a_weak_relation():
    st = store_mod.Store("sqlite://"); _catalog(st)
    markets = _tags(_find(_stories(st), "Federal Reserve"))
    assert all(t["source"] != stg.SOURCE_INHERITED for t in markets.values())


def test_a_tag_the_target_cannot_corroborate_is_not_copied():
    """The relevance gate, isolated. One strong neighbour asserting one tag is not evidence about
    this story — which is the blind copy the whole design rules out."""
    target = {"id": "t", "title": "Vaccination reaches the clinics", "summary": "Teams arrived.",
              "coverage": [{"headline": "Vaccination reaches the clinics", "url": "u1"}]}
    other = {"id": "o", "title": "x", "summary": "", "coverage": []}
    direct = {
        "t": [{"name": "vaccination", "label": "Vaccination", "source": stg.SOURCE_DIRECT,
               "score": 0.9, "members": 2}],
        "o": [{"name": "helsinki summit", "label": "Helsinki Summit", "source": stg.SOURCE_DIRECT,
               "score": 0.9, "members": 3}],
    }
    out = stg.inherit_tags([target, other], direct, {"t": [("o", 0.9)]})
    assert [t["name"] for t in out["t"]] == ["vaccination"]     # nothing crossed

    # The same tag DOES cross once the target's own text carries the words.
    target2 = dict(target, summary="Teams arrived after the Helsinki Summit pledge.")
    out2 = stg.inherit_tags([target2, other], direct, {"t": [("o", 0.9)]})
    assert {t["name"] for t in out2["t"]} == {"vaccination", "helsinki summit"}
    assert next(t for t in out2["t"] if t["name"] == "helsinki summit")["source"] == stg.SOURCE_INHERITED


def test_several_neighbours_agreeing_is_the_other_way_in():
    """Corroboration one level up: what one neighbour cannot assert alone, two independently can."""
    target = {"id": "t", "title": "Clinics reopen", "summary": "", "coverage": []}
    peers = [{"id": f"p{i}", "title": "x", "summary": "", "coverage": []} for i in (1, 2)]
    tag = {"name": "kivu province", "label": "Kivu Province", "source": stg.SOURCE_DIRECT,
           "score": 0.8, "members": 3}
    direct = {"t": [], "p1": [dict(tag)], "p2": [dict(tag)]}
    one = stg.inherit_tags([target, peers[0]], {"t": [], "p1": [dict(tag)]}, {"t": [("p1", 0.9)]})
    assert one["t"] == []                                       # one neighbour is one source
    two = stg.inherit_tags([target] + peers, direct, {"t": [("p1", 0.9), ("p2", 0.9)]})
    assert [t["name"] for t in two["t"]] == ["kivu province"]


def test_direct_evidence_always_beats_an_inherited_copy():
    target = {"id": "t", "title": "Ebola cases rise", "summary": "", "coverage": []}
    other = {"id": "o", "title": "x", "summary": "", "coverage": []}
    mine = {"name": "ebola", "label": "Ebola", "source": stg.SOURCE_DIRECT, "score": 0.2, "members": 2}
    theirs = {"name": "ebola", "label": "Ebola", "source": stg.SOURCE_DIRECT, "score": 0.9, "members": 9}
    out = stg.inherit_tags([target, other], {"t": [mine], "o": [theirs]}, {"t": [("o", 0.9)]})
    assert len(out["t"]) == 1 and out["t"][0]["source"] == stg.SOURCE_DIRECT


def test_inheritance_does_not_chain():
    """A tag cannot walk the graph: inheritance reads DIRECT tags only, so two hops is not a path."""
    a = {"id": "a", "title": "Helsinki Summit opens", "summary": "", "coverage": []}
    b = {"id": "b", "title": "Helsinki Summit talks continue", "summary": "", "coverage": []}
    c = {"id": "c", "title": "Helsinki Summit reaction", "summary": "", "coverage": []}
    direct = {"a": [{"name": "nordic council", "label": "Nordic Council",
                     "source": stg.SOURCE_DIRECT, "score": 0.9, "members": 3}],
              "b": [], "c": []}
    # a -> b -> c. `c` is related only to `b`, which holds the tag by inheritance, never directly.
    out = stg.inherit_tags([a, b, c], direct, {"b": [("a", 0.9)], "c": [("b", 0.9)]})
    assert out["c"] == []


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #
def test_a_tag_retrieves_the_stories_carrying_it():
    st = store_mod.Store("sqlite://"); _catalog(st)
    got = ss.list_stories(st, tag="ebola", limit=50)
    assert got["total"] == 2
    assert not any("Federal Reserve" in s["title"] for s in got["stories"])
    assert ss.list_stories(st, tag="wall street", limit=50)["total"] == 1
    assert ss.list_stories(st, tag="no such subject", limit=50)["total"] == 0


def test_retrieval_normalises_what_the_caller_sends():
    """A link carries the normalised name, but a caller may paste the label. Both resolve, so a
    tag page cannot 404 on capitalisation."""
    st = store_mod.Store("sqlite://"); _catalog(st)
    assert ss.list_stories(st, tag="Wall Street", limit=50)["total"] == 1
    assert ss.list_stories(st, tag="  WALL   street ", limit=50)["total"] == 1


def test_tag_facets_count_what_selecting_would_return():
    st = store_mod.Store("sqlite://"); _catalog(st)
    facets = {r["tag"]: r for r in ss.list_stories(st, limit=50)["tagFacets"]}
    assert facets["ebola"]["count"] == ss.list_stories(st, tag="ebola", limit=50)["total"]
    assert facets["ebola"]["label"] == "Ebola"
    # Most-carried first, and bounded.
    counts = [r["count"] for r in ss.list_stories(st, limit=50)["tagFacets"]]
    assert counts == sorted(counts, reverse=True)
    assert len(counts) <= ss.TAG_FACET_LIMIT


def test_facets_describe_the_page_before_the_tag_filter_narrows_it():
    """Standard faceting discipline — a picker that counted itself would collapse to the current
    selection and could never offer a way out."""
    st = store_mod.Store("sqlite://"); _catalog(st)
    filtered = ss.list_stories(st, tag="ebola", limit=50)
    tags = {r["tag"] for r in filtered["tagFacets"]}
    assert "wall street" in tags        # still offered, though this view excludes it


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def test_the_story_tag_map_is_written_and_pruned_wholesale():
    st = store_mod.Store("sqlite://"); _catalog(st)
    stories = _stories(st)
    stored = st.story_tags()
    assert stored and set(stored) == {s["id"] for s in stories}
    for s in stories:
        assert [t["name"] for t in stored[s["id"]]] == [t["name"] for t in s["tags"]]
    # StoryMember's contract: a rewrite replaces, never accumulates.
    st.replace_story_tags({"st_only": [{"name": "x", "label": "X", "source": "direct", "score": 0.5}]})
    assert set(st.story_tags()) == {"st_only"}


def test_stories_for_tag_is_the_retrieval_index():
    st = store_mod.Store("sqlite://"); _catalog(st)
    ids = {s["id"] for s in _stories(st) if any(t["name"] == "ebola" for t in s["tags"])}
    assert set(st.stories_for_tag("ebola")) == ids
    assert st.stories_for_tag("nothing at all") == []


def test_tags_survive_a_store_that_cannot_answer():
    """Fails soft in both directions: no entity rows means fewer tags, not an error, and an
    unwritable table means tags are served but not persisted."""
    st = store_mod.Store("sqlite://"); _catalog(st)

    class Broken:
        def __getattr__(self, name):
            return getattr(st, name)

        def entities_for_urls(self, *a, **k):
            raise RuntimeError("side table unavailable")

        def replace_story_tags(self, *a, **k):
            raise RuntimeError("read-only")

    stories = ss.build_stories(ss._fetch(st))
    out = ss.attach_tags(Broken(), stories)
    assert all("tags" in s for s in out)                      # served anyway
    assert any(s["tags"] for s in out)                        # and still non-empty


def test_the_feature_can_be_switched_off(monkeypatch):
    st = store_mod.Store("sqlite://"); _catalog(st)
    monkeypatch.setenv("RWE_STORY_TAGS", "0")
    ss.clear_cache()
    assert all("tags" not in s for s in _stories(st))
    monkeypatch.setenv("RWE_STORY_TAGS", "1")
    ss.clear_cache()
    assert any(s.get("tags") for s in _stories(st))


# --------------------------------------------------------------------------- #
# Name shape — a topic is a NAME, not a headline.
#
# Every string in this section is one the live rail actually offered a reader. The Similar News
# Topics list came back with "Dolly Parton Laid to Rest Privately Days After H…", "Parton's" and
# "Beside" in it, which is what a capitalisation reader does when nothing downstream asks whether
# the thing it found is shaped like a name.
# --------------------------------------------------------------------------- #
def test_a_title_case_headline_yields_no_phrase():
    """The whole of the headline-as-topic defect. A Title Case headline capitalises every content
    word, so capitalisation carries no signal — `entity_spans.extract` has always returned nothing
    for one, and the phrase reader had no such guard."""
    import entity_spans
    for head in ["Dolly Parton Laid to Rest Privately Days After Her Death at 79",
                 "Bad Wolves Guitarist Quits Band Over Dolly Parton Tribute"]:
        assert entity_spans.extract(head) == []          # the clustering reader's answer, unchanged
        assert stg.phrases(head) == []                   # and now this one's


def test_a_sentence_cased_headline_still_gives_up_its_entities():
    """The guard above must not be a mute button: the same reader still reads a normal headline."""
    assert stg.phrases("Dolly Parton Imagination Library expands to three more states") == \
        ["dolly parton imagination library"]
    assert stg.phrases("Amid the Bank of England review, Rachel Reeves spoke") == \
        ["bank of england", "rachel reeves"]


def test_grammar_is_trimmed_off_a_name_not_used_to_reject_it():
    """"Beside the Dolly Parton statue" gave the rail the topic "Beside the Dolly Parton". Rejecting
    that string is easy and wrong — it throws away Dolly Parton with the preposition."""
    assert stg.tidy("beside the dolly parton") == "dolly parton"
    assert stg.tidy("over the iran war") == "iran war"
    assert stg.tidy("the pentagon") == "pentagon"
    assert stg.phrases("Beside the Dolly Parton statue, Nashville mourns") == ["dolly parton"]
    # A run that is ONLY grammar tidies to nothing and cannot become a tag.
    assert stg.tidy("of the") == ""
    assert not stg.well_formed(stg.tidy("of the"))


def test_a_possessive_is_not_an_entity():
    """"Parton's" reached the rail down the singleton path, where a trailing strip cannot reach an
    apostrophe with an "s" after it."""
    assert stg.tidy("parton's") == "parton"
    assert not stg.well_formed("parton's")
    names = {"parton", "nashville"}
    votes = stg.singleton_votes(
        [{"headline": "Fans gather at Parton's Nashville home"},
         {"headline": "Crowds outside Parton's Nashville home"}],
        names, noise=lambda n: False)
    assert "parton's" not in votes and votes.get("parton") == 2


def test_a_function_word_is_never_a_tag():
    """"Beside" was offered as a topic: a preposition capitalised by position. `_LEADS` covers
    sentence openers and `_CONNECTORS` covers words a name runs through; neither lists this one."""
    votes = stg.singleton_votes(
        [{"headline": "Beside the statue, Nashville mourns"},
         {"headline": "Beside the museum, Nashville mourns"}],
        {"beside", "nashville"}, noise=lambda n: False)
    assert "beside" not in votes
    assert votes.get("nashville") == 2


def test_names_are_bounded_in_length():
    """A sentence-cased headline that slips past the Title Case guard still cannot become a topic."""
    assert not stg.well_formed("bad wolves guitarist quits band over dolly parton tribute")
    assert not stg.well_formed("a " * 40)
    # …while the long entities that genuinely exist are kept, which is what the bound is for.
    assert stg.well_formed("democratic republic of the congo")
    assert stg.well_formed("new york city police department")
    assert stg.well_formed("dolly parton imagination library")


def test_the_shape_rule_is_applied_once_for_every_extractor():
    """Three extractors and a stored side table feed the same dict. A rule enforced in three of
    four places holds until the fourth is edited — which is how a headline reached the rail."""
    story = {"id": "s", "title": "Anything", "summary": "", "topic": "",
             "coverage": [{"headline": "Anything", "url": "u1"},
                          {"headline": "Anything", "url": "u2"}]}
    # A side-table row carrying a headline-shaped "entity" must not survive.
    entities = {"u1": {"span": ["bad wolves guitarist quits band over dolly parton tribute"]},
                "u2": {"span": ["bad wolves guitarist quits band over dolly parton tribute"]}}
    tags = stg.extract_tags([story], entities, noise=lambda n: False)
    assert all("bad wolves" not in t["name"] for t in tags["s"])


def test_a_fragment_folds_onto_the_fullest_name_the_story_spells_out():
    """One entity, one tag — however many ways the story's outlets wrote it."""
    canon = ["dolly parton imagination library"]
    # Same support -> one entity, written long and short.
    assert stg._canonicalise({"dolly parton": 2, "dolly parton imagination library": 2}, canon) == \
        {"dolly parton imagination library": 2}
    # KNOWN TRADE, recorded rather than discovered later: a short name contained in a longer one
    # the story mentions in passing is folded into it. The alternative — a vote test on the
    # container — was measured and broke the case the fold exists for (see `_canonicalise`), and
    # "keep the most specific name" is the direction that was asked for.
    assert stg._canonicalise({"dolly parton": 3, "dolly parton imagination library": 1}, canon) == \
        {"dolly parton imagination library": 3}
    story = {"id": "s", "title": "Dolly Parton statue unveiled in Nashville",
             "summary": "The Dolly Parton Imagination Library also announced a grant.", "topic": "",
             "coverage": [
                 {"headline": "Dolly Parton statue unveiled in Nashville", "url": "u1"},
                 {"headline": "Dolly Parton statue draws crowds in Nashville", "url": "u2"},
                 {"headline": "Dolly Parton Imagination Library announces grant", "url": "u3"},
             ]}
    names = {t["name"] for t in stg.extract_tags([story], {}, noise=lambda n: False)["s"]}
    # The charity absorbs the person here, by the trade recorded above; what must NOT happen is
    # that the story loses the subject altogether, or gains a headline-shaped tag.
    assert "dolly parton imagination library" in names
    assert "nashville" in names
    assert all(stg.well_formed(n) for n in names)
