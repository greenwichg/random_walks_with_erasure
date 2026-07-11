"""Commit 3 — the canonical deterministic topic classifier (``ingest.classify_topic``).

Proves: obviously political headlines (politicians, elections, legislation, government agencies,
diplomacy, courts, geopolitical events) are classified Politics and can never surface as
"General"; publisher ``<category>`` tags are the highest-confidence input, normalized into the
closed taxonomy (junk labels like "News"/"Top Stories"/"General" contribute nothing); topical URL
sections stay decisive while *geographic* sections (/us-news/, /world/) never hide a political
headline; the scorer and the RSS parser feed this ONE classifier; and the one-shot migration
reclassifies stored articles immediately and idempotently.
"""

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import ingest                    # noqa: E402
import migrate_topics            # noqa: E402
import rss_ingest                # noqa: E402
import store as store_mod        # noqa: E402
from ingest import TAXONOMY, classify_topic   # noqa: E402

GUARDIAN_HEADLINE = "'A slap in the face': small farmers say Trump is turning his back on them"

# The exact validation set the product owner asked for: politicians, elections, legislation,
# government agencies, diplomacy, public policy, courts, geopolitical events.
POLITICAL_HEADLINES = [
    GUARDIAN_HEADLINE,                                                    # Trump
    "Biden signs sweeping executive order on AI safety",                  # Biden (before "AI")
    "Congress passes stopgap funding bill to avert shutdown",             # Congress
    "Parliament votes to suspend member over expenses scandal",           # Parliament
    "White House defends new tariff plan",                                # White House
    "Supreme Court to hear landmark redistricting case",                  # Supreme Court
    "Election officials warn of voter roll purges ahead of midterms",     # elections
    "Senate committee subpoenas tech executives",                         # legislature (before tech)
    "Governor declares state of emergency amid protests",                 # state government
    "Federal judge blocks deportations under wartime law",                # courts as public power
    "Lawmakers spar over redistricting maps",                             # legislation
    "Prime minister survives no-confidence vote",                         # heads of government
    "EU imposes new sanctions on Russian oil exports",                    # diplomacy/geopolitics
    "Kremlin dismisses ceasefire proposal",                               # geopolitical events
]


# --------------------------------------------------------------------------- #
# The non-negotiable regression: political headlines are Politics, never "General".
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("headline", POLITICAL_HEADLINES)
def test_obviously_political_headlines_are_politics(headline):
    assert classify_topic(title=headline) == "Politics"


@pytest.mark.parametrize("headline", POLITICAL_HEADLINES)
def test_political_headline_survives_junk_category_and_geo_url(headline):
    """The failure mode from the audit: a generic feed label + a geographic section used to
    leave the article uncategorized (rendered "General"). Neither may hide the headline now."""
    got = classify_topic(url="https://ex.com/us-news/2026/jul/x",
                         source_category="News; Top stories", title=headline)
    assert got == "Politics"
    assert got != "General"


def test_guardian_screenshot_regression():
    """The exact card that was misfiled: Guardian /us-news/ URL, no usable feed category, a
    plainly political headline. End-to-end through the scorer: Politics, political=True."""
    r = ingest.Scorer().score(ingest.RawRead(
        url="https://www.theguardian.com/us-news/2026/jul/02/small-farmers-trump",
        title=GUARDIAN_HEADLINE, outlet="The Guardian"))
    assert r.category == "Politics"
    assert r.political is True
    assert r.outlet == "The Guardian"


# --------------------------------------------------------------------------- #
# Resolution order: source category > topical URL section > lexicon > geographic section.
# --------------------------------------------------------------------------- #
def test_source_category_is_highest_confidence():
    # a publisher tag beats even a topical URL section
    assert classify_topic(url="https://ex.com/sport/x", source_category="US politics") == "Politics"
    # normalization into the canonical taxonomy (Climate keeps its name)
    assert classify_topic(source_category="Environment") == "Climate"
    assert classify_topic(source_category="Climate crisis") == "Climate"
    assert classify_topic(source_category="Sport") == "Sports"
    assert classify_topic(source_category="Comment is free") == "Opinion"
    assert classify_topic(source_category="Books") == "Culture"
    assert classify_topic(source_category="US news") == "U.S."
    assert classify_topic(source_category="World news") == "World"
    # tags that aren't section names still resolve through the subject lexicon
    assert classify_topic(source_category="Trump administration; Farming") == "Politics"
    assert classify_topic(source_category="White House") == "Politics"


def test_junk_labels_contribute_nothing():
    for junk in ("News", "General", "Top Stories", "Latest", "Featured", "Home"):
        assert classify_topic(url="https://ex.com/technology/x", source_category=junk) == "Technology"
        assert classify_topic(source_category=junk) == ""      # junk alone is no signal


def test_topical_url_section_is_decisive():
    # the publisher filed it under Business; a political name in the headline doesn't override
    assert classify_topic(url="https://ex.com/business/x",
                          title="Trump attacks the Fed over rates") == "Business"
    assert classify_topic(url="https://ex.com/opinion/x",
                          title="Congress must act on housing") == "Opinion"


def test_geographic_sections_defer_to_the_headline():
    assert classify_topic(url="https://ex.com/us-news/x", title=GUARDIAN_HEADLINE) == "Politics"
    assert classify_topic(url="https://ex.com/world/x",
                          title="EU imposes new sanctions on Russian oil exports") == "Politics"
    # ... but still classify a story with no specific subject
    assert classify_topic(url="https://ex.com/world/x",
                          title="Trains resume on the scenic mountain line") == "World"
    assert classify_topic(url="https://ex.com/us-news/x",
                          title="Storm damage closes three interstate bridges") == "U.S."
    # a path carrying both a geographic and a topical segment is topical
    assert classify_topic(url="https://ex.com/us/politics/x") == "Politics"


def test_description_is_consulted_when_the_title_is_bland():
    assert classify_topic(title="Weekly briefing",
                          description="The Senate returns to debate the budget resolution.") == "Politics"


def test_uncategorized_stays_blank_and_taxonomy_is_closed():
    # no signal -> "" (the UI hides the segment); never "General", never a raw label
    assert classify_topic(title="Local bake sale raises funds for library") == ""
    assert classify_topic(source_category="Recipes") == ""
    # every value the classifier can produce is a taxonomy member (or "")
    values = set(ingest._CATEGORY_ALIASES.values()) | set(ingest._SECTIONS.values())
    values |= {topic for topic, _ in ingest._TOPIC_LEXICON}
    assert values <= set(TAXONOMY)
    assert "General" not in TAXONOMY


def test_non_political_subjects_classify_and_stay_non_political():
    cases = {
        "Wildfires force thousands to evacuate as heatwave breaks records": "Climate",
        "New covid variant drives hospital admissions": "Health",
        "FIFA announces host cities for the World Cup": "Sports",
        "Quarterly earnings beat Wall Street estimates": "Business",
        "OpenAI releases new machine learning model": "Technology",
        "NASA spacecraft returns asteroid samples": "Science",
    }
    for title, want in cases.items():
        assert classify_topic(title=title) == want, title
        r = ingest.Scorer().score(ingest.RawRead(url="https://ex.com/x", title=title))
        assert r.political is False, title


def test_lexicon_classified_politics_sets_the_political_flag():
    """classify_topic and looks_political can never disagree: a Politics/Opinion classification
    flips the article-level political flag the cross-cutting gate consumes."""
    r = ingest.Scorer().score(ingest.RawRead(url="https://ex.com/x",
                                             title="Kremlin dismisses ceasefire proposal"))
    assert r.category == "Politics" and r.political is True


def test_deterministic():
    for _ in range(3):
        assert classify_topic(url="https://ex.com/us-news/x", source_category="News",
                              title=GUARDIAN_HEADLINE) == "Politics"


# --------------------------------------------------------------------------- #
# RSS/Atom <category> extraction feeds the classifier.
# --------------------------------------------------------------------------- #
_RSS_WITH_CATEGORIES = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title>
<item>
  <title>Small farmers say Trump is turning his back on them</title>
  <link>https://ex.com/us-news/farmers</link>
  <description>Farm groups react.</description>
  <category>Trump administration</category>
  <category>US politics</category>
  <category>Farming</category>
  <pubDate>Wed, 01 Jul 2026 08:00:00 GMT</pubDate>
</item>
</channel></rss>"""

_ATOM_WITH_CATEGORIES = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Feed</title>
<entry>
  <title>Markets rally after rate decision</title>
  <link rel="alternate" href="https://ex.com/item/1"/>
  <category term="Business news"/>
  <updated>2026-07-01T08:00:00Z</updated>
</entry>
</feed>"""


def test_rss_category_tags_are_parsed_and_classified():
    _, entries = rss_ingest.parse_feed(_RSS_WITH_CATEGORIES)
    assert len(entries) == 1
    e = entries[0]
    assert e.category == "Trump administration; US politics; Farming"
    assert classify_topic(url=e.url, source_category=e.category, title=e.title) == "Politics"


def test_atom_category_term_is_parsed():
    _, entries = rss_ingest.parse_feed(_ATOM_WITH_CATEGORIES)
    assert len(entries) == 1
    assert entries[0].category == "Business news"
    assert classify_topic(source_category=entries[0].category) == "Business"


def test_rss_ingest_stores_the_canonical_topic(tmp_path):
    """End-to-end: feed bytes -> scored catalog row carrying the canonical topic, so Discover /
    Search / Stories / Recommendations all read Politics for the Guardian-style item."""
    st = store_mod.Store(f"sqlite:///{tmp_path / 'c.db'}")
    _, entries = rss_ingest.parse_feed(_RSS_WITH_CATEGORIES)
    rss_ingest.ingest_entries(entries, "The Guardian", "https://feed", rss_ingest.make_scorer(), st)
    row = st.list_feed_articles(limit=1)[0]
    assert row["scored"]["category"] == "Politics"
    assert row["scored"]["political"] is True


# --------------------------------------------------------------------------- #
# One-shot migration: stored articles are reclassified immediately, idempotently.
# --------------------------------------------------------------------------- #
def test_migration_reclassifies_stored_articles(tmp_path):
    st = store_mod.Store(f"sqlite:///{tmp_path / 'm.db'}")
    # a pre-Commit-3 uncategorized political article (the screenshot case), cached + read
    url = "https://theguardian.com/us-news/2026/jul/02/small-farmers-trump"
    stale = {"article_id": url, "outlet": "The Guardian", "category": "",
             "title": GUARDIAN_HEADLINE, "lean": -1.0, "political": False, "read_at": None}
    st.save_scored_article(url, stale)
    uid = st.upsert_user_by_identity("dev", "migration-test").id
    st.add_read(uid, url, stale, "2026-07-02T10:00:00+00:00")
    # a junk-labelled catalog row whose description names the senate
    st.upsert_feed_article(
        canonical_url="https://ex.com/item/2", url="https://ex.com/item/2", publisher="Ex",
        source_publisher="Ex", title="Weekly briefing",
        description="The Senate returns to debate the budget resolution.", body=None,
        published_at="2026-07-01T00:00:00+00:00", source_feed="f",
        scored={"article_id": "https://ex.com/item/2", "outlet": "Ex", "category": "News",
                "title": "Weekly briefing", "lean": 0.0, "political": False, "read_at": None})
    # an already-correct row that must NOT churn
    ok_url = "https://ex.com/sport/final"
    ok = {"article_id": ok_url, "outlet": "Ex", "category": "Sports",
          "title": "Cup final goes to extra time", "lean": 0.0, "political": False,
          "read_at": None}
    st.save_scored_article(ok_url, ok)

    stats = migrate_topics.migrate(st)
    assert stats["scored_articles"]["changed"] == 1          # the Guardian row, not the Sports row
    assert stats["reads"]["changed"] == 1
    assert stats["feed_articles"]["changed"] == 1
    assert stats["scored_articles"]["before"].get("", 0) == 1
    assert stats["scored_articles"]["after"].get("", 0) == 0  # uncategorized eliminated

    assert st.get_scored_article(url)["category"] == "Politics"
    assert st.get_scored_article(url)["political"] is True
    assert st.get_reads(uid)[0]["category"] == "Politics"
    assert st.get_feed_article("https://ex.com/item/2")["scored"]["category"] == "Politics"
    assert st.get_scored_article(ok_url)["category"] == "Sports"

    # idempotent: a second run rewrites nothing
    again = migrate_topics.migrate(st)
    assert all(t["changed"] == 0 for t in again.values())


def test_migration_never_downgrades_political(tmp_path):
    st = store_mod.Store(f"sqlite:///{tmp_path / 'p.db'}")
    url = "https://ex.com/item/3"
    st.save_scored_article(url, {"article_id": url, "outlet": "Ex", "category": "Sports",
                                 "title": "Cup final", "lean": 0.0, "political": True,
                                 "read_at": None})
    migrate_topics.migrate(st)
    row = st.get_scored_article(url)
    assert row["political"] is True                          # ratchet only goes upward
    assert row["category"] == "Sports"


def test_migration_dry_run_writes_nothing(tmp_path):
    st = store_mod.Store(f"sqlite:///{tmp_path / 'd.db'}")
    url = "https://ex.com/us-news/x"
    stale = {"article_id": url, "outlet": "Ex", "category": "", "title": GUARDIAN_HEADLINE,
             "lean": 0.0, "political": False, "read_at": None}
    st.save_scored_article(url, stale)
    stats = migrate_topics.migrate(st, dry_run=True)
    assert stats["scored_articles"]["changed"] == 1
    assert st.get_scored_article(url)["category"] == ""      # untouched
