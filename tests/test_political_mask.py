"""W3A — the political mask now delegates to the canonical `classify_topic` taxonomy instead of
a raw substring test. These pin the documented false positives / false negatives / opinion cases
from docs/W3A_POLITICAL_MASK_DESIGN.md so the mask can never silently regress to the old
`"election" in "selection"` behaviour. No model, no network — deterministic."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import ingest   # noqa: E402


# --------------------------------------------------------------------------- #
# False positives — the old substring test flagged these political; the topic
# classifier (word-boundary lexicon) does not.
# --------------------------------------------------------------------------- #
def test_selection_is_not_political():
    # "election" is a substring of "selection" — the exact bug the old mask had.
    assert ingest.looks_political(category="selection") is False
    assert ingest.looks_political(title="Natural selection in finches") is False
    assert ingest.looks_political(category="Sports", title="Team selection announced for the final") is False
    assert ingest.looks_political(category="Business", title="A guide to portfolio selection") is False


def test_non_political_opinion_is_not_political():
    # Opinion is its own topic, not a synonym for Politics: sports/entertainment/business
    # columns are no longer swept in just because they are opinion.
    assert ingest.looks_political(category="opinion", title="Why the Lakers should trade their star") is False
    assert ingest.looks_political(category="opinion", title="The best films of the year") is False


# --------------------------------------------------------------------------- #
# False negatives — the old substring test missed these; the classifier catches
# them via _CATEGORY_ALIASES / the Politics lexicon.
# --------------------------------------------------------------------------- #
def test_congress_and_institutions_are_political():
    assert ingest.looks_political(category="congress") is True
    assert ingest.looks_political(category="white house") is True
    assert ingest.looks_political(category="supreme court") is True
    # title-only political (no source category), filed under a generic section the old mask ignored
    assert ingest.looks_political(title="Congress passes spending bill") is True
    assert ingest.looks_political(title="Senate votes on immigration reform") is True


def test_explicit_geographic_category_is_honored_precision_first():
    # classify_topic is category-first: an explicit "U.S. news" tag maps to "U.S." and wins over a
    # political headline (trust the source's own category — precision). A known, accepted limitation;
    # the Opinion clause is the ONLY title-override, by design (no new heuristics in W3A).
    assert ingest.looks_political(category="U.S. news", title="Senate votes on immigration reform") is False


def test_political_opinion_is_still_political():
    # a political op-ed stays political — the Opinion clause runs the Politics lexicon on the title
    assert ingest.looks_political(category="opinion", title="Congress must act on the border") is True
    assert ingest.looks_political(category="opinion", title="Why the Supreme Court got it wrong") is True


# --------------------------------------------------------------------------- #
# Plain political / plain non-political still classify correctly (no regression).
# --------------------------------------------------------------------------- #
def test_plainly_political_and_non_political():
    assert ingest.looks_political(category="Politics") is True
    assert ingest.looks_political(url="https://x.com/2024/us/politics/story") is True
    assert ingest.looks_political(category="Sports") is False
    assert ingest.looks_political(category="Technology", title="New smartphone released") is False
    assert ingest.looks_political(url="", category="", title="") is False


# --------------------------------------------------------------------------- #
# Single source of truth + scorer parity + determinism.
# --------------------------------------------------------------------------- #
def test_helper_is_the_single_source_of_truth():
    # looks_political is exactly _political_from_topic(classify_topic(...), title)
    for url, cat, title in [("https://x/us/politics/a", "", "A"),
                            ("", "selection", "Team selection"),
                            ("", "opinion", "Congress must act"),
                            ("", "Sports", "Team selection")]:
        topic = ingest.classify_topic(url=url, source_category=cat, title=title)
        assert ingest.looks_political(url=url, category=cat, title=title) is \
            ingest._political_from_topic(topic, title)


def test_scorer_reuses_topic_for_political_flag():
    s = ingest.Scorer()
    # /politics/ URL -> topic Politics -> political (existing behaviour preserved)
    r = s.score(ingest.RawRead(url="https://www.nytimes.com/2024/01/05/us/politics/story.html", title="A story"))
    assert r.political is True and r.category == "Politics"
    # a plainly non-political science piece stays non-political
    r2 = s.score(ingest.RawRead(url="https://example.com/x", category="Science",
                                title="Astronomers spot a new asteroid"))
    assert r2.political is False
    # an explicit source flag always wins (never overridden by the topic)
    r3 = s.score(ingest.RawRead(url="https://example.com/y", category="Sports",
                                title="Team selection", political=True))
    assert r3.political is True


def test_deterministic():
    calls = [ingest.looks_political(category="congress", title="Senate vote") for _ in range(8)]
    assert all(c is True for c in calls)
    calls2 = [ingest.looks_political(category="selection") for _ in range(8)]
    assert all(c is False for c in calls2)
