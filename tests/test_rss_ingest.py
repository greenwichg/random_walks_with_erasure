"""Tests for the RSS ingestion foundation (examples/rss_ingest.py + store.FeedArticle).

Fully offline: feed bytes come from fixtures and a fake fetcher, so no network is touched. Covers
RSS 2.0 + Atom parsing, timestamp normalisation, feeds config, and the end-to-end ingest (scoring
via the existing pipeline, catalog storage, URL/publisher/timestamp/description/body preservation,
and dedup)."""

import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import ingest        # noqa: E402
import store         # noqa: E402
import rss_ingest as rss  # noqa: E402


RSS2 = """<?xml version="1.0"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Fox News Politics</title>
    <item>
      <title>Senate advances the funding bill</title>
      <link>https://www.foxnews.com/politics/senate-funding-bill</link>
      <description>Lawmakers moved to advance the measure.</description>
      <content:encoded><![CDATA[<p>Full body text of the article.</p>]]></content:encoded>
      <pubDate>Wed, 02 Oct 2024 08:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Opinion: rethinking the economy</title>
      <link>https://www.foxnews.com/opinion/economy-take</link>
      <description>An opinion piece.</description>
      <dc:date>2024-10-03T09:30:00Z</dc:date>
    </item>
  </channel>
</rss>""".encode()

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>NYT Politics</title>
  <entry>
    <title>Analysis: what to know about the ruling</title>
    <link rel="alternate" href="https://www.nytimes.com/2024/us/politics/court-ruling"/>
    <summary>What to know about the court ruling.</summary>
    <content>Full analysis content.</content>
    <published>2024-10-02T12:00:00Z</published>
  </entry>
</feed>""".encode()


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def test_parse_rss2():
    title, entries = rss.parse_feed(RSS2)
    assert title == "Fox News Politics" and len(entries) == 2
    a = entries[0]
    assert a.url == "https://www.foxnews.com/politics/senate-funding-bill"
    assert a.title == "Senate advances the funding bill"
    assert a.description == "Lawmakers moved to advance the measure."
    assert a.body and "Full body text" in a.body                 # content:encoded captured
    assert a.published_at and a.published_at.startswith("2024-10-02")
    assert entries[1].published_at.startswith("2024-10-03")       # dc:date parsed


def test_parse_atom():
    title, entries = rss.parse_feed(ATOM)
    assert title == "NYT Politics" and len(entries) == 1
    a = entries[0]
    assert a.url == "https://www.nytimes.com/2024/us/politics/court-ruling"   # alternate link href
    assert a.title.startswith("Analysis")
    assert a.description == "What to know about the court ruling."
    assert a.published_at.startswith("2024-10-02")


def test_parse_rejects_invalid_xml():
    with pytest.raises(ValueError):
        rss.parse_feed(b"not xml at all <<<")


def test_parse_skips_entry_without_link():
    feed = b"""<rss version="2.0"><channel><title>T</title>
      <item><title>no link</title></item>
      <item><title>has link</title><link>https://x.com/a</link></item>
    </channel></rss>"""
    _, entries = rss.parse_feed(feed)
    assert [e.url for e in entries] == ["https://x.com/a"]        # linkless entry dropped


# --------------------------------------------------------------------------- #
# Channel selection (maintenance fix: replaced `_first(root, "channel") or root`
# with an explicit `is None` check — Element truthiness is deprecated and reflects
# child count, not existence). These pin BOTH selection branches unchanged.
# --------------------------------------------------------------------------- #
def test_parse_selects_channel_when_present():
    """A document with a <channel> reads its title + items FROM that channel — not from a
    stray root-level title (proves the channel Element is selected, not root)."""
    feed = b"""<rss version="2.0">
      <title>ROOT TITLE (must be ignored)</title>
      <channel>
        <title>Channel Title</title>
        <item><title>Inside channel</title><link>https://ex.example/a</link></item>
      </channel>
    </rss>"""
    title, entries = rss.parse_feed(feed)
    assert title == "Channel Title"
    assert [e.url for e in entries] == ["https://ex.example/a"]


def test_parse_falls_back_to_root_without_channel():
    """A non-Atom document with NO <channel> (RSS 1.0 <rdf:RDF>, items at the root) falls back
    to the root so its items are still parsed."""
    feed = b"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                        xmlns="http://purl.org/rss/1.0/">
      <item><title>Root item A</title><link>https://ex.example/a</link></item>
      <item><title>Root item B</title><link>https://ex.example/b</link></item>
    </rdf:RDF>"""
    title, entries = rss.parse_feed(feed)
    assert title == ""                                            # no channel/title -> empty
    assert [e.url for e in entries] == ["https://ex.example/a", "https://ex.example/b"]


def test_parse_emits_no_deprecation_warning():
    """The whole point of the fix: parsing must no longer depend on deprecated Element
    truthiness, for both the channel-present and channel-absent shapes."""
    import warnings
    rdf = b"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
      <item><title>t</title><link>https://ex.example/a</link></item></rdf:RDF>"""
    for feed in (RSS2, rdf):
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)   # any DeprecationWarning -> failure
            rss.parse_feed(feed)


def test_to_iso():
    assert rss._to_iso("Wed, 02 Oct 2024 08:00:00 GMT").startswith("2024-10-02")   # RFC 822
    assert rss._to_iso("2024-10-02T12:00:00Z").startswith("2024-10-02")            # RFC 3339
    assert rss._to_iso("garbage") is None
    assert rss._to_iso("") is None


def test_load_feeds(tmp_path):
    f = tmp_path / "feeds.txt"
    f.write_text("# comment\n\nFox News|https://a.com/rss\nhttps://b.com/rss\n")
    feeds = rss.load_feeds(str(f))
    assert feeds == [("Fox News", "https://a.com/rss"), (None, "https://b.com/rss")]
    assert rss.load_feeds("https://x.com/1, https://y.com/2") == [
        (None, "https://x.com/1"), (None, "https://y.com/2")]


# --------------------------------------------------------------------------- #
# Ingest (scoring + catalog storage + dedup)
# --------------------------------------------------------------------------- #
def _fetch(url):
    return {"feed://fox": RSS2, "feed://nyt": ATOM}[url]


def test_ingest_preserves_fields_and_scores():
    st = store.Store("sqlite://")
    feeds = [("Fox News", "feed://fox"), (None, "feed://nyt")]
    agg = rss.ingest_all(feeds, rss.make_scorer(), st, fetch=_fetch)
    assert agg["new"] == 3 and agg["duplicates"] == 0 and agg["failed"] == 0
    assert st.count_feed_articles() == 3

    fox = st.get_feed_article(
        ingest.canonical_url("https://www.foxnews.com/politics/senate-funding-bill"))
    assert fox is not None
    assert fox["url"].startswith("https://www.foxnews.com")       # canonical publisher URL preserved
    assert fox["publisher"]                                       # resolved outlet (via registry)
    assert fox["publishedAt"].startswith("2024-10-02")           # publication timestamp preserved
    assert fox["description"] == "Lawmakers moved to advance the measure."
    assert fox["body"] and "Full body text" in fox["body"]       # body preserved
    assert fox["sourcePublisher"] == "Fox News"                  # feed-declared publisher kept
    assert fox["sourceFeed"] == "feed://fox"
    assert isinstance(fox["scored"], dict) and "lean" in fox["scored"]   # scored via the same model


def test_ingest_is_idempotent_dedup():
    st = store.Store("sqlite://")
    feeds = [("Fox News", "feed://fox"), (None, "feed://nyt")]
    rss.ingest_all(feeds, rss.make_scorer(), st, fetch=_fetch)
    agg2 = rss.ingest_all(feeds, rss.make_scorer(), st, fetch=_fetch)   # re-poll
    assert agg2["new"] == 0 and agg2["duplicates"] == 3
    assert st.count_feed_articles() == 3                          # no growth


def test_ingest_counts_unknown_outlets_without_dropping_them():
    """W4 observability: ingest counts articles whose outlet the registry doesn't know (NaN lean),
    with a per-outlet breakdown — additive only; scoring, storage, and dedup are unchanged."""
    st = store.Store("sqlite://")
    entries = [rss.FeedEntry(url="https://www.foxnews.com/p/a", title="known",
                             published_at="2026-07-01T00:00:00+00:00"),
               rss.FeedEntry(url="https://blog-unknown.example/x", title="unknown one",
                             published_at="2026-07-01T00:00:00+00:00"),
               rss.FeedEntry(url="https://blog-unknown.example/y", title="unknown two",
                             published_at="2026-07-01T00:00:00+00:00")]
    stats = rss.ingest_entries(entries, "Mixed", "feed://mixed", rss.make_scorer(), st)
    assert stats["unknown_outlet"] == 2                           # the two unknown-outlet articles
    assert sum(stats["unknown_outlets"].values()) == 2           # per-outlet breakdown accounts for them
    assert st.count_feed_articles() == 3                         # nothing dropped


def test_run_summary_reports_unknown_outlets():
    """The CLI run summary surfaces the unknown-outlet count and, when nonzero, points at the tool."""
    agg = {"feeds": 2, "ok": 2, "failed": 0, "entries": 10, "new": 8, "duplicates": 0,
           "skipped": 0, "unknown_outlet": 3, "errors": []}
    out = rss._format_run_summary(agg, before=0, after=8, seconds=1.0)
    assert "unknown outlets" in out and "3" in out
    assert "excluded from recommendations" in out and "outlet_coverage.py" in out


def test_run_summary_is_human_readable_and_preserves_every_metric():
    """Presentation-only guard for the CLI summary: it must keep ALL metrics
    (feeds/ok/failed/new/duplicates/skipped), show previous->current catalog size with growth
    and elapsed time, and carry the repeat-poll reassurance note — without recomputing anything
    (growth == after - before; the counts pass through verbatim)."""
    agg = {"feeds": 9, "ok": 8, "failed": 1, "entries": 300,
           "new": 3, "duplicates": 250, "skipped": 2, "errors": []}
    out = rss._format_run_summary(agg, before=248, after=251, seconds=5.0)
    # feed processing + elapsed
    assert "9 feed(s)" in out and "5.0s" in out and "8 ok, 1 failed" in out
    # every count is present and labelled
    assert "new articles" in out and "existing (duplicate)" in out and "skipped" in out
    for value in ("3", "250", "2"):
        assert value in out
    # previous -> current (+growth), growth derived from the counts, not invented
    assert "248 -> 251" in out and "(+3)" in out
    assert (251 - 248) == agg["new"]                     # growth reconciles with the new-count
    # the reassurance note about repeated polling
    assert "expected on repeat RSS polls" in out
    # a multi-line summary, not the former single line
    assert out.count("\n") >= 4


def test_one_failing_feed_does_not_abort_the_rest():
    st = store.Store("sqlite://")

    def fetch(url):
        if url == "feed://bad":
            raise OSError("connection refused")
        return RSS2

    agg = rss.ingest_all([(None, "feed://bad"), ("Fox", "feed://ok")],
                         rss.make_scorer(), st, fetch=fetch)
    assert agg["failed"] == 1 and agg["ok"] == 1
    assert len(agg["errors"]) == 1 and agg["errors"][0]["feed"] == "feed://bad"
    assert st.count_feed_articles() == 2                          # the good feed still ingested


def test_recommender_corpus_untouched():
    """Sanity: the ingestion foundation adds a table but nothing that the recommender/report reads —
    the catalog is isolated. (The FeedArticle table exists; no read/report table is affected.)"""
    st = store.Store("sqlite://")
    assert st.count_feed_articles() == 0
    rss.ingest_all([("Fox", "feed://fox")], rss.make_scorer(), st, fetch=_fetch)
    assert st.count_feed_articles() == 2
    assert st.count_reads(1) == 0                                 # ingestion created no user reads
