"""Tests for ``text_utils.clean_html`` — the ONE canonical HTML→text normalizer — and the FeedEntry
normalized-contract invariant it backs (bug fix: HTML markup appearing in news content).

Proves: every required sanitization case; idempotency; that FeedEntry normalizes
``title``/``description``/``body`` at construction (so RSS, NewsAPI, GDELT, and any future adapter
inherit it with no duplicated logic); and that the text is already clean *at rest* (FeedArticle) and
everywhere downstream (Discover/Recommendations Article, Story summary) — without any of those layers
sanitizing separately. Runs fully offline (in-memory SQLite; no network).
"""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import text_utils            # noqa: E402
import rss_ingest as ri      # noqa: E402
import store as store_mod    # noqa: E402
import discover              # noqa: E402
import story_service         # noqa: E402


@pytest.fixture
def store():
    return store_mod.Store("sqlite://")


# --------------------------------------------------------------------------- #
# clean_html — the required validation matrix
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw, expected", [
    # <p>
    ("<p>Hello world</p>", "Hello world"),
    # <br> (both spellings)
    ("Line one<br>Line two", "Line one\nLine two"),
    ("Line one<br/>Line two", "Line one\nLine two"),
    # <a> keeps its text, drops the href
    ('<a href="http://x.com/story">Read more</a>', "Read more"),
    # <ul>/<li>
    ("<ul><li>First</li><li>Second</li></ul>", "First\nSecond"),
    # <img> ignored (no alt text injected)
    ('Photo <img src="x.jpg" alt="cat"/> here', "Photo here"),
    # HTML entities
    ("Tom &amp; Jerry", "Tom & Jerry"),
    ("It&#39;s here", "It's here"),
    ("a&nbsp;b", "a b"),
    ("5 &gt; 3 &amp;&amp; true", "5 > 3 && true"),
    # entity-escaped markup with no literal '<'
    ("&lt;p&gt;Hello&lt;/p&gt;", "Hello"),
    # real tags wrapping entity-escaped tags (JSON sources can mix these)
    ("<p>x &lt;b&gt;y&lt;/b&gt; &amp; z</p>", "x y & z"),
    # scripts / styles removed wholesale
    ("<p>Keep</p><script>alert('x')</script>", "Keep"),
    ("<style>.a{color:red}</style>Text", "Text"),
    # empty / None
    ("", ""),
    (None, ""),
    # plain text unchanged
    ("The Senate confirmed the nominee.", "The Senate confirmed the nominee."),
    # a bare ampersand that is not an entity is preserved
    ("AT&T earnings", "AT&T earnings"),
    # the exact example from the bug report
    ('<p>Senate pick announced his withdrawal.</p>\n<ul>\n<li>First</li>\n<li>Second</li>\n</ul>\n'
     '<a href="...">Read more</a>',
     "Senate pick announced his withdrawal.\n\nFirst\nSecond\n\nRead more"),
])
def test_clean_html_cases(raw, expected):
    assert text_utils.clean_html(raw) == expected


def test_malformed_html_is_best_effort_not_error():
    out = text_utils.clean_html("<p>unclosed <b>bold <i>x</p> tail <div>y")
    assert "<" not in out and ">" not in out
    assert "bold" in out and "tail" in out and "y" in out


def test_literal_less_than_is_preserved():
    # a real '<' that is not a tag (e.g. decoded from &lt;) must survive
    assert text_utils.clean_html("5 &lt; 3 is false") == "5 < 3 is false"


def test_never_emits_html():
    dirty = ('<div onclick="x"><p>A &amp; B</p><img src=q>'
             '<script>bad()</script><a href="u">L</a></div>')
    out = text_utils.clean_html(dirty)
    assert "<" not in out and ">" not in out
    assert "bad()" not in out                       # script *content* dropped, not just the tag
    assert out == "A & B\n\nL"


@pytest.mark.parametrize("raw", [
    "<p>Hi &amp; bye</p>", "Tom &amp; Jerry", "plain text", "5 &lt; 3", "line<br>break",
    "<ul><li>A</li><li>B</li></ul>",
    '<p>Senate pick announced his withdrawal.</p><ul><li>First</li><li>Second</li></ul>',
])
def test_idempotent(raw):
    once = text_utils.clean_html(raw)
    assert text_utils.clean_html(once) == once


# --------------------------------------------------------------------------- #
# FeedEntry is the canonical normalized contract (all adapters + future ones)
# --------------------------------------------------------------------------- #
def test_feedentry_normalizes_title_description_body():
    e = ri.FeedEntry(
        url="http://x.com/a",
        title="Breaking: Tom &amp; Jerry",
        description="<p>Senate pick announced his withdrawal.</p>"
                    "<ul><li>First</li><li>Second</li></ul><a href='x'>Read more</a>",
        body="<p>Full <b>body</b> &amp; more.</p><script>x()</script>")
    assert e.title == "Breaking: Tom & Jerry"
    assert e.description == "Senate pick announced his withdrawal.\n\nFirst\nSecond\n\nRead more"
    assert e.body == "Full body & more."
    for field in (e.title, e.description, e.body):
        assert "<" not in field and ">" not in field


def test_feedentry_image_only_body_becomes_none():
    e = ri.FeedEntry(url="http://x.com/a", title="t", description="d", body="<img src='x.jpg'>")
    assert e.body is None


def test_feedentry_plain_text_unchanged():
    e = ri.FeedEntry(url="http://x.com/a", title="Senate vote", description="A clear summary.")
    assert e.title == "Senate vote" and e.description == "A clear summary."


def test_feedentry_missing_body_stays_none():
    e = ri.FeedEntry(url="http://x.com/a", title="t", description="d")
    assert e.body is None


# --------------------------------------------------------------------------- #
# Clean at rest + everywhere downstream — proves nobody re-sanitizes
# --------------------------------------------------------------------------- #
def _ingest_html(store, url, publisher, scorer):
    e = ri.FeedEntry(
        url=url, title="Senate passes the funding bill",
        description=f"<p>{publisher} reports &amp; analysis.</p>"
                    "<ul><li>Point one</li><li>Point two</li></ul>",
        body="<p>Body text.</p>", published_at="2026-07-08T10:00:00Z",
        source_type="rss", source_provider=publisher)
    ri.ingest_entries([e], publisher, "rss://x", scorer, store, source_type="rss")


def test_clean_at_rest_and_downstream(store):
    sc = ri.make_scorer()
    _ingest_html(store, "https://cnn.com/senate-bill", "CNN", sc)
    _ingest_html(store, "https://npr.org/senate-bill", "NPR", sc)

    # (1) Clean AT REST — the FeedArticle row (what Search serializes verbatim via _feed_row).
    row = store.get_feed_article("https://cnn.com/senate-bill")
    assert "<" not in row["description"] and "&lt;" not in row["description"]
    assert "CNN reports & analysis" in row["description"]
    assert "Point one\nPoint two" in row["description"]
    assert row["body"] == "Body text."

    # (2) Discover / Recommendations Article shape — clean, with NO sanitization inside discover.
    art = discover.feed_article_to_article(row)
    assert "<" not in art["description"] and "&lt;" not in art["description"]

    # (3) Stories — the summary (representative article's description) is clean.
    stories = story_service.cluster_from_store(store)
    assert stories, "the two same-headline publishers should cluster into one story"
    assert "<" not in stories[0]["summary"] and "&lt;" not in stories[0]["summary"]
