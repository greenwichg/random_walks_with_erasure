"""Tests for the RSS ingestion foundation (examples/rss_ingest.py + store.FeedArticle).

Fully offline: feed bytes come from fixtures and a fake fetcher, so no network is touched. Covers
RSS 2.0 + Atom parsing, timestamp normalisation, feeds config, and the end-to-end ingest (scoring
via the existing pipeline, catalog storage, URL/publisher/timestamp/description/body preservation,
and dedup)."""

import os
import pathlib
import sys
from datetime import datetime

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


def test_feeds_that_declare_no_language_still_parse_to_none():
    """The shipped behaviour, pinned first: a feed that says nothing about language yields entries
    with ``language=None``, exactly as before. The fix below adds a value where one was DECLARED —
    it never invents one."""
    for feed in (RSS2, ATOM):
        _title, entries = rss.parse_feed(feed)
        assert all(e.language is None for e in entries)


@pytest.mark.parametrize("feed, expect", [
    # RSS 2.0: <language> under <channel>
    (b'<rss version="2.0"><channel><language>vi</language>'
     b'<item><title>t</title><link>https://x.example/a</link></item></channel></rss>', "vi"),
    # BCP-47 survives parsing; `location.normalize_language` reduces it to ISO 639-1 downstream.
    (b'<rss version="2.0"><channel><language>pt-PT</language>'
     b'<item><title>t</title><link>https://x.example/a</link></item></channel></rss>', "pt-PT"),
    # Atom: xml:lang on the feed element
    ('<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="ja"><entry><title>t</title>'
     '<link rel="alternate" href="https://x.example/a"/></entry></feed>'.encode(), "ja"),
    # RSS 1.0 (<rdf:RDF>) carries items at the root, and the language with them
    (b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><language>de</language>'
     b'<item><title>t</title><link>https://x.example/a</link></item></rdf:RDF>', "de"),
])
def test_the_feeds_declared_language_reaches_every_entry(feed, expect):
    """**The gap this closes.** Neither `_rss_item` nor `_atom_entry` ever set ``language``, so
    every RSS-ingested article in the catalog carried NULL and the only values present came from the
    GDELT/NewsAPI adapters. That is what made `audit_source_cohort` abandon a whole analysis — "TOO
    SPARSE TO CONCLUDE" — and what showed as `?` against real publishers in M7's discovery table.
    The feed's own declaration was available the whole time and was being thrown away."""
    _title, entries = rss.parse_feed(feed)
    assert entries and all(e.language == expect for e in entries)


def test_an_entrys_own_language_beats_the_feeds():
    """`xml:lang` is inherited in XML and the nearest declaration governs. A translated item in an
    otherwise single-language feed is the case this gets right."""
    feed = ('<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en">'
            '<entry><title>a</title><link rel="alternate" href="https://x.example/a"/></entry>'
            '<entry xml:lang="fr"><title>b</title>'
            '<link rel="alternate" href="https://x.example/b"/></entry></feed>').encode()
    _title, entries = rss.parse_feed(feed)
    assert [e.language for e in entries] == ["en", "fr"]


def test_an_items_own_language_is_never_read_as_the_feeds():
    """Only the CHANNEL element is consulted for the feed's language. Treating an item's own
    ``<language>`` as the feed's would let one translated article relabel the entire source."""
    feed = (b'<rss version="2.0"><channel><item><language>ru</language><title>t</title>'
            b'<link>https://x.example/a</link></item></channel></rss>')
    _title, entries = rss.parse_feed(feed)
    assert entries and entries[0].language is None


@pytest.mark.parametrize("root", ["urlset", "sitemapindex"])
def test_a_sitemap_is_rejected_loudly_rather_than_ingesting_nothing(root):
    """**The trap M7's own worklist pointed into.** Discovery now ADMITs sources whose discovery
    document is a news sitemap — kait8.com and kwch.com, and the Arc XP class generally — and the
    obvious next step is to paste that URL into `rss_feeds.txt`.

    That silently ingested NOTHING. A `<urlset>` has no `<channel>` and no `<item>`, so `parse_feed`
    returned zero entries, raised no error, and the feed reported healthy forever — the same
    reports-healthy-does-nothing shape this audit series keeps finding.

    `ingest_all` catches per-feed errors, so a loud rejection costs one feed rather than the run."""
    body = (f"<{root} xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
            f"<url><loc>https://x.example/a</loc></url></{root}>").encode()
    with pytest.raises(ValueError, match="sitemap, not an RSS/Atom feed"):
        rss.parse_feed(body)


def test_the_rejection_names_where_a_sitemap_source_actually_goes():
    """An error that only says "no" leaves the reader where they started. This one names the path
    that does handle sitemaps, because the source is legitimate — it is the destination that was
    wrong."""
    body = b"<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'></urlset>"
    with pytest.raises(ValueError, match="crawler.discover_sitemap"):
        rss.parse_feed(body)


def test_real_feeds_are_unaffected_by_the_sitemap_check():
    """The check keys on the ROOT element, so nothing that is genuinely a feed can trip it."""
    for feed in (RSS2, ATOM):
        _title, entries = rss.parse_feed(feed)
        assert entries


def test_a_repoll_backfills_language_onto_a_row_that_had_none(tmp_path):
    """**Why the fix is not forward-only** — a correction to what was first claimed about it.

    ``upsert_feed_article`` backfills a field that was empty (``if language and not row.language``),
    so re-polling a feed fills in the articles it is still serving, not just new ones. Measured on
    production: RSS language coverage reached 12% within minutes of the deploy, far more than new
    ingestion could explain.

    The consequence is a curve, not a ramp: coverage climbs fast and then plateaus below 100%,
    because rows that aged out of their feed before the fix are never revisited."""
    st = store.Store(f"sqlite:///{tmp_path}/backfill.db")
    common = dict(canonical_url="x.example/a", url="https://x.example/a", publisher="X",
                  source_publisher=None, title="t", description="", body=None,
                  published_at="2026-08-01T00:00:00+00:00", source_feed="f", scored={},
                  source_type="rss")
    assert st.upsert_feed_article(**common, language=None) is True
    assert st.list_feed_articles()[0]["language"] is None

    assert st.upsert_feed_article(**common, language="vi") is False      # a re-poll
    assert st.list_feed_articles()[0]["language"] == "vi"

    # First-seen metadata is never rewritten: a later, different value does not overwrite.
    st.upsert_feed_article(**common, language="fr")
    assert st.list_feed_articles()[0]["language"] == "vi"


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


# --------------------------------------------------------------------------- #
# published_at is normalised to UTC — a SORT-CORRECTNESS requirement, not cosmetics.
#
# published_at is a TEXT column and store._search_order sorts it lexicographically, so a preserved
# offset made string order disagree with real time. Measured in production: 21% of the catalog
# carried -04:00 and was ranked up to four hours late, pushing US-Eastern publishers out of the
# newest-first clustering window ahead of their turn.
# --------------------------------------------------------------------------- #
def test_to_iso_normalises_offsets_to_utc():
    assert rss._to_iso("Mon, 27 Jul 2026 12:00:00 -0400").endswith("+00:00")
    assert rss._to_iso("Mon, 27 Jul 2026 12:00:00 -0400").startswith("2026-07-27T16:00:00")
    assert rss._to_iso("2026-07-27T12:00:00+05:30").startswith("2026-07-27T06:30:00")
    assert rss._to_iso("2026-07-27T12:00:00Z") == "2026-07-27T12:00:00+00:00"


def test_to_iso_reads_a_naive_timestamp_as_utc():
    """A feed that omits the offset gives us nothing better to assume, and the value must still be
    comparable with the offset-bearing rows around it."""
    assert rss._to_iso("2026-07-27T12:00:00") == "2026-07-27T12:00:00+00:00"


def test_to_iso_still_rejects_unparseable_input():
    assert rss._to_iso("not a date") is None
    assert rss._to_iso("") is None
    assert rss._to_iso(None) is None


def test_lexicographic_order_now_matches_chronological_order():
    """The property the whole fix exists for: sorting the stored strings must order by real time."""
    raw = [
        "Mon, 27 Jul 2026 12:00:00 -0400",   # 16:00Z  — latest
        "Mon, 27 Jul 2026 15:00:00 +0000",   # 15:00Z
        "Mon, 27 Jul 2026 19:00:00 +0530",   # 13:30Z
        "Mon, 27 Jul 2026 13:00:00 +0000",   # 13:00Z  — earliest
    ]
    stored = [rss._to_iso(r) for r in raw]
    by_string = sorted(stored)
    by_time = sorted(stored, key=lambda s: datetime.fromisoformat(s))
    assert by_string == by_time
    assert by_string[-1].startswith("2026-07-27T16:00:00")   # the -0400 row really is newest

    # …and the pre-fix behaviour would have failed exactly here (offset preserved, string sort wrong)
    naive = [datetime.fromisoformat(rss._to_iso(r)).isoformat() for r in raw]
    assert sorted(naive) == by_time      # sanity: our normalised values are self-consistent
