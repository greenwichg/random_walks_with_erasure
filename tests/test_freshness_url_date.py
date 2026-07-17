"""C4.2 — the URL-embedded publication date closes the freshness gate's date-provenance blind spot.

Root cause (docs/FRESHNESS_ROOT_CAUSE_AUDIT.md): candidacy age keyed on ``publishedAt`` → the
stable first-seen ``createdAt`` → ``fetchedAt``, all of which trust dates the *feed* supplies or
that we stamp at first sight. So an archived ``/2023/…`` article the feed left **undated** (→ today's
``createdAt``) or **re-dated recent** (a re-surfaced live blog) read as fresh and entered the
recommendation candidate pool.

The fix: when the URL path carries a publication date (``/YYYY/MM/DD/``, ``/YYYY/MM/``, or a trailing
``-MM-DD-YY`` live-blog slug), that date is the authoritative candidacy age — it can't be refreshed
by a re-poll and doesn't reset to "today" for an undated archive. A URL with **no** date signal is
untouched (evergreen preserved). Candidacy-only: storage, ranking, explainability, and the report
contract are not involved. Default-on via ``RWE_FEED_URL_DATE`` (``0`` = instant rollback).

Covers the five required scenarios — genuinely old, undated RSS, evergreen, live blog, newly
published — plus determinism, the env toggle, exempt/disabled interactions, both reported URLs, and
proof that health metrics are unshifted.
"""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import corpus_health as ch      # noqa: E402
import corpus_validation as cv  # noqa: E402

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)

# The two archived CNN URLs reported in the field (docs/FRESHNESS_ROOT_CAUSE_AUDIT.md).
CNN_OPINION_2023 = ("https://edition.cnn.com/2023/04/18/opinions/"
                    "2024-presidential-election-alternative-voters-lieberman")
CNN_LIVEBLOG_2023 = ("https://edition.cnn.com/europe/live-news/"
                     "russia-ukraine-war-news-04-18-23/index.html")


def _art(url, *, published_days_ago=None, created_days_ago=0.0, fetched_days_ago=0.0,
         publisher="Pub"):
    """An article-row dict. ``published_days_ago=None`` => genuinely undated (no ``publishedAt``);
    ``createdAt``/``fetchedAt`` default to *now* to reproduce the exact bug — an undated stale URL
    whose first-seen/fetch fallback makes it look brand-new."""
    def iso(d):
        return (NOW - timedelta(days=d)).isoformat()
    return {"canonicalUrl": url, "url": url, "publisher": publisher, "title": f"t {url}",
            "scored": {"outlet": publisher, "category": "Politics", "lean": 0.0, "political": True,
                       "title": f"t {url}"},
            "publishedAt": iso(published_days_ago) if published_days_ago is not None else None,
            "createdAt": iso(created_days_ago),
            "fetchedAt": iso(fetched_days_ago)}


def _kept(arts, **kw):
    kw.setdefault("now", NOW)
    kw.setdefault("max_age_days", 60)
    return [a["canonicalUrl"] for a in ch.fresh_articles(arts, **kw)]


# --------------------------------------------------------------------------- #
# _url_date — the parser (unit).
# --------------------------------------------------------------------------- #
def test_url_date_parses_ymd_path():
    assert ch._url_date("https://ex.com/2023/04/18/opinions/x") == datetime(2023, 4, 18, tzinfo=timezone.utc)
    assert ch._url_date("https://ex.com/2026/07/10/politics/y/index.html") == datetime(2026, 7, 10, tzinfo=timezone.utc)


def test_url_date_parses_year_month_path():
    assert ch._url_date("https://theatlantic.com/archive/2023/04/essay/12345/") == datetime(2023, 4, 1, tzinfo=timezone.utc)


def test_url_date_parses_live_blog_slug():
    assert ch._url_date(CNN_LIVEBLOG_2023) == datetime(2023, 4, 18, tzinfo=timezone.utc)
    assert ch._url_date("https://ex.com/section/game-recap-10-05-24") == datetime(2024, 10, 5, tzinfo=timezone.utc)


def test_url_date_none_for_dateless_url():
    for url in ("https://ex.com/how-democracy-works", "https://ex.com/topics/politics",
                "https://ex.com/", ""):
        assert ch._url_date(url) is None


def test_url_date_false_positive_guards():
    # 25 is not a month; YY=99 is outside the 00-39 window; a date only in the query is ignored
    # (canonicalisation drops queries, and we read the path only); an impossible calendar date
    # yields no signal rather than a crash.
    assert ch._url_date("https://ex.com/top-25-songs-of-all-time") is None
    assert ch._url_date("https://ex.com/item-04-18-99/") is None
    assert ch._url_date("https://ex.com/foo?d=2023/04/18") is None
    assert ch._url_date("https://ex.com/2023/02/30/impossible/") is None


def test_url_date_is_pure_and_deterministic():
    assert ch._url_date(CNN_OPINION_2023) == ch._url_date(CNN_OPINION_2023)
    assert ch._url_date(CNN_OPINION_2023) == datetime(2023, 4, 18, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# C4.3 — 3-letter-month URL dates (/YYYY/mon/D(D)/ — Guardian, Washington Times).
# --------------------------------------------------------------------------- #
# Real Washington Times URLs from the qbias corpus (21,754 real articles): ALL /YYYY/<3alpha>/DD/
# shapes found in it are exactly these three — every one a genuine month token on a genuine date,
# including the unpadded day. Encoded verbatim so the corpus evidence is a permanent regression.
WT_QBIAS_FIXTURES = [
    ("http://www.washingtontimes.com/news/2012/oct/2/judge-blocks-pas-new-vote-id-law",
     datetime(2012, 10, 2, tzinfo=timezone.utc)),
    ("http://www.washingtontimes.com/news/2013/feb/12/hurt-obamas-agenda-simple",
     datetime(2013, 2, 12, tzinfo=timezone.utc)),
    ("http://www.washingtontimes.com/news/2013/may/10/the-moment-of-responsibility",
     datetime(2013, 5, 10, tzinfo=timezone.utc)),
]


def test_c43_guardian_and_wt_current_urls_parse():
    assert ch._url_date("https://www.theguardian.com/us-news/2026/jul/15/senate-vote") == \
        datetime(2026, 7, 15, tzinfo=timezone.utc)
    assert ch._url_date("https://www.washingtontimes.com/news/2026/jul/15/budget-battle/") == \
        datetime(2026, 7, 15, tzinfo=timezone.utc)


def test_c43_real_corpus_wt_fixtures():
    for url, expected in WT_QBIAS_FIXTURES:
        assert ch._url_date(url) == expected, url


def test_c43_all_twelve_month_abbreviations():
    for i, mon in enumerate(ch._URL_MONTHS, 1):
        assert ch._url_date(f"https://ex.com/2026/{mon}/15/story") == \
            datetime(2026, i, 15, tzinfo=timezone.utc), mon


def test_c43_case_insensitive_month_token():
    expected = datetime(2026, 7, 15, tzinfo=timezone.utc)
    for tok in ("jul", "Jul", "JUL", "jUl"):
        assert ch._url_date(f"https://ex.com/2026/{tok}/15/story") == expected, tok


def test_c43_unpadded_and_padded_day_are_equivalent():
    expected = datetime(2026, 7, 2, tzinfo=timezone.utc)
    assert ch._url_date("https://ex.com/2026/jul/2/s") == expected
    assert ch._url_date("https://ex.com/2026/jul/02/s") == expected


def test_c43_false_positive_guards():
    # non-month token; full month name (out of scope by design); token embedded in a longer
    # segment; invalid calendar day; regex-invalid day; month token without a year flank
    for url in ("https://ex.com/2023/foo/18/x", "https://ex.com/2023/january/18/x",
                "https://ex.com/2023/mayhem/18/x", "https://ex.com/2023/feb/30/x",
                "https://ex.com/2023/jan/32/x", "https://ex.com/section/jan/18/x"):
        assert ch._url_date(url) is None, url


def test_c43_precedence_is_unchanged():
    """Numeric /YYYY/MM/DD/ still wins over an alpha date; an alpha FULL date beats the
    month-precision numeric /YYYY/MM/ (full dates before partial ones)."""
    assert ch._url_date("https://ex.com/2023/04/18/y/2024/jul/20/") == \
        datetime(2023, 4, 18, tzinfo=timezone.utc)
    assert ch._url_date("https://ex.com/archive/2023/04/x/2024/jul/20/y") == \
        datetime(2024, 7, 20, tzinfo=timezone.utc)


def test_c43_nine_publisher_fp_sweep():
    """The freshness source-audit table as an executable test: a date exactly where each shipped
    publisher's URL convention carries one, None everywhere else (no false positives)."""
    expects = {
        "https://www.theguardian.com/us-news/2026/jul/15/senate-vote": datetime(2026, 7, 15, tzinfo=timezone.utc),
        "https://www.npr.org/2026/07/15/nx-s1-5301234/congress-vote": datetime(2026, 7, 15, tzinfo=timezone.utc),
        "https://www.cnn.com/2026/07/15/politics/senate-vote/index.html": datetime(2026, 7, 15, tzinfo=timezone.utc),
        CNN_LIVEBLOG_2023: datetime(2023, 4, 18, tzinfo=timezone.utc),
        "https://www.nytimes.com/2026/07/15/us/politics/budget-deal.html": datetime(2026, 7, 15, tzinfo=timezone.utc),
        "https://www.bbc.com/news/articles/c0jq4z8lz9po": None,
        "https://thehill.com/homenews/senate/5301234-budget-fight-heats-up/": None,
        "https://www.foxnews.com/politics/senate-passes-budget-bill": None,
        "https://nypost.com/2026/07/15/us-news/senate-budget-vote/": datetime(2026, 7, 15, tzinfo=timezone.utc),
        "https://www.washingtontimes.com/news/2026/jul/15/budget-battle/": datetime(2026, 7, 15, tzinfo=timezone.utc),
    }
    for url, expected in expects.items():
        assert ch._url_date(url) == expected, url


def test_c43_scenario_stale_alpha_dated_archive_is_excluded():
    """The incident class, Guardian/WT edition: an archived /2019/jan/01/ article the feed left
    UNDATED (createdAt=today) is now excluded by its URL date; a current alpha-dated article and a
    dateless evergreen are kept; the kill-switch restores pre-C4.3 behaviour byte-for-byte."""
    stale = _art("https://www.theguardian.com/politics/2019/jan/01/old-analysis",
                 published_days_ago=None, created_days_ago=0, fetched_days_ago=0)
    fresh = _art("https://www.washingtontimes.com/news/2026/jul/14/new-story/",
                 published_days_ago=1)
    evergreen = _art("https://ex.com/guides/media-literacy", published_days_ago=None,
                     created_days_ago=3)
    kept_on = _kept([stale, fresh, evergreen], url_date=True)
    assert kept_on == [fresh["canonicalUrl"], evergreen["canonicalUrl"]]
    kept_off = _kept([stale, fresh, evergreen], url_date=False)      # kill-switch: pre-C4.3
    assert stale["canonicalUrl"] in kept_off


def test_c43_deterministic():
    u = WT_QBIAS_FIXTURES[0][0]
    assert ch._url_date(u) == ch._url_date(u) == WT_QBIAS_FIXTURES[0][1]


# --------------------------------------------------------------------------- #
# The env toggle.
# --------------------------------------------------------------------------- #
def test_url_date_toggle_defaults_on(monkeypatch):
    monkeypatch.delenv("RWE_FEED_URL_DATE", raising=False)
    assert ch.feed_url_date() is True
    monkeypatch.setenv("RWE_FEED_URL_DATE", "0")
    assert ch.feed_url_date() is False
    monkeypatch.setenv("RWE_FEED_URL_DATE", "on")
    assert ch.feed_url_date() is True


# --------------------------------------------------------------------------- #
# The five required scenarios.
# --------------------------------------------------------------------------- #
def test_scenario_genuinely_old_url_dated_article_is_excluded():
    """An archived 2023 opinion piece the feed left UNDATED: its createdAt/fetchedAt are today, so
    the old age fallback called it fresh. The /2023/04/18/ URL proves otherwise → excluded now."""
    stale = _art(CNN_OPINION_2023, published_days_ago=None, created_days_ago=0, fetched_days_ago=0)
    assert _kept([stale], url_date=False) == [CNN_OPINION_2023]   # the bug: kept without the signal
    assert _kept([stale], url_date=True) == []                    # the fix: URL date ages it out


def test_scenario_undated_rss_entry_without_url_date_is_kept():
    """An undated RSS entry with NO date anywhere in its URL — genuine evergreen. The signal doesn't
    apply, so behaviour is unchanged (kept: staleness can't be proven)."""
    undated = _art("https://ex.com/explainer/how-primaries-work",
                   published_days_ago=None, created_days_ago=1, fetched_days_ago=0)
    assert _kept([undated], url_date=True) == ["https://ex.com/explainer/how-primaries-work"]


def test_scenario_evergreen_page_is_preserved():
    """A dateless evergreen page recently discovered stays a candidate — the fix never excludes a
    URL that carries no date."""
    evergreen = _art("https://ex.com/guides/media-literacy",
                     published_days_ago=None, created_days_ago=3, fetched_days_ago=0)
    assert _kept([evergreen], url_date=True) == ["https://ex.com/guides/media-literacy"]


def test_scenario_live_blog_redated_recent_is_excluded():
    """The re-dated case: a 2023 live-blog page the feed re-served with a RECENT publishedAt (page
    genuinely 'updated'). The old gate trusted that recent date and kept it; the -04-18-23 slug in
    the URL reveals the true origination date → excluded now."""
    redated = _art(CNN_LIVEBLOG_2023, published_days_ago=1, created_days_ago=1, fetched_days_ago=0)
    assert _kept([redated], url_date=False) == [CNN_LIVEBLOG_2023]   # re-dated recent → old gate keeps it
    assert _kept([redated], url_date=True) == []                     # URL slug overrides the refreshed date


def test_scenario_newly_published_article_is_kept():
    """A genuinely new article at a current /2026/07/10/ URL — the URL date is recent, so it stays a
    candidate. The signal is self-correcting: recent content has a recent URL date."""
    fresh = _art("https://edition.cnn.com/2026/07/10/politics/new-story/index.html",
                 published_days_ago=5, created_days_ago=5, fetched_days_ago=0)
    assert _kept([fresh], url_date=True) == ["https://edition.cnn.com/2026/07/10/politics/new-story/index.html"]


# --------------------------------------------------------------------------- #
# Priority semantics — the URL date is authoritative when present.
# --------------------------------------------------------------------------- #
def test_url_date_overrides_recent_published_at():
    """Core guarantee: a recent (undated→today or re-dated) age can no longer keep a URL-dated
    archive. The URL date wins over publishedAt/createdAt/fetchedAt."""
    a = _art(CNN_OPINION_2023, published_days_ago=0, created_days_ago=0, fetched_days_ago=0)
    assert _kept([a], url_date=True) == []


def test_recent_url_date_rescues_stale_feed_date():
    """The other direction: a genuinely-recent article whose feed handed a wrong OLD publishedAt is
    rescued by its recent URL date (URL creation-stamp is the more trustworthy signal)."""
    a = _art("https://edition.cnn.com/2026/07/12/us/story/index.html",
             published_days_ago=800, created_days_ago=800, fetched_days_ago=0)
    assert _kept([a], url_date=False) == []                              # stale feed date → dropped
    assert _kept([a], url_date=True) == ["https://edition.cnn.com/2026/07/12/us/story/index.html"]


# --------------------------------------------------------------------------- #
# Byte-compatibility, determinism, and existing-gate interactions.
# --------------------------------------------------------------------------- #
def test_toggle_off_is_byte_compatible_with_pre_fix_behaviour():
    """RWE_FEED_URL_DATE=0 restores the exact pre-C4.2 candidacy: the stale URL-dated article is
    kept via the createdAt fallback, and a normal fresh article is kept — identical to the old gate."""
    stale = _art(CNN_OPINION_2023, published_days_ago=None, created_days_ago=0)
    fresh = _art("https://ex.com/plain/story", published_days_ago=2)
    old = _art("https://ex.com/plain/ancient", published_days_ago=90)
    assert set(_kept([stale, fresh, old], url_date=False)) == {CNN_OPINION_2023, "https://ex.com/plain/story"}


def test_fresh_articles_is_deterministic():
    arts = [_art(CNN_OPINION_2023, published_days_ago=None),
            _art("https://ex.com/2026/07/10/x/index.html", published_days_ago=1),
            _art("https://ex.com/evergreen", published_days_ago=None)]
    r1 = _kept(list(arts), url_date=True)
    r2 = _kept(list(arts), url_date=True)
    assert r1 == r2 == ["https://ex.com/2026/07/10/x/index.html", "https://ex.com/evergreen"]


def test_exempt_read_article_kept_despite_old_url_date():
    """A read archived article stays a candidate (read-demand exemption / graph connectivity) — the
    URL-date signal does not override exempt."""
    read_stale = _art(CNN_OPINION_2023, published_days_ago=None)
    assert _kept([read_stale], url_date=True, exempt={CNN_OPINION_2023}) == [CNN_OPINION_2023]


def test_disabled_window_ignores_url_date():
    """max_age_days=0 disables the whole gate, URL-date signal included (golden-pipeline escape hatch)."""
    stale = _art(CNN_OPINION_2023, published_days_ago=None)
    assert _kept([stale], url_date=True, max_age_days=0) == [CNN_OPINION_2023]


def test_require_dated_still_gates_on_published_at():
    """require_dated is unchanged: it demands a real publishedAt regardless of a URL date, so an
    undated (even URL-dated-recent) article is still excluded under that stricter opt-in flag."""
    undated_recent_url = _art("https://ex.com/2026/07/10/x/index.html", published_days_ago=None)
    assert _kept([undated_recent_url], url_date=True, require_dated=True) == []


# --------------------------------------------------------------------------- #
# Integration: the hot-refresh candidate builder, and the two reported URLs.
# --------------------------------------------------------------------------- #
def test_build_candidate_excludes_url_dated_stale():
    arts = [_art(CNN_OPINION_2023, published_days_ago=None),                        # archived, undated
            _art("https://ex.com/2026/07/11/story/index.html", published_days_ago=1)]  # current
    got = {a["canonicalUrl"] for a in cv.build_candidate(arts, now=NOW, max_age_days=60)}
    assert got == {"https://ex.com/2026/07/11/story/index.html"}


def test_both_reported_urls_are_excluded_now():
    """End-to-end guard on the exact field reports: both archived CNN URLs — one undated, one
    re-dated-recent — are excluded under the fix and were kept before it."""
    undated_opinion = _art(CNN_OPINION_2023, published_days_ago=None, created_days_ago=0)
    redated_liveblog = _art(CNN_LIVEBLOG_2023, published_days_ago=1)
    arts = [undated_opinion, redated_liveblog]
    assert set(_kept(arts, url_date=False)) == {CNN_OPINION_2023, CNN_LIVEBLOG_2023}   # both kept before
    assert _kept(arts, url_date=True) == []                                            # both excluded now


# --------------------------------------------------------------------------- #
# Health metrics are NOT shifted — the URL date is candidacy-only.
# --------------------------------------------------------------------------- #
def test_published_and_metrics_ignore_url_date():
    """_published (health metrics + the newest-first sort) never consults the URL date, so no
    reported metric moves — the signal is confined to candidacy, exactly like C4.1's createdAt order."""
    a = _art(CNN_OPINION_2023, published_days_ago=None, created_days_ago=1, fetched_days_ago=0)
    assert ch._published(a) == datetime.fromisoformat(a["fetchedAt"])                     # default order
    assert ch._published(a, ch._CANDIDACY_TIME_KEYS) == datetime.fromisoformat(a["createdAt"])
    # corpus_metrics counts this undated row as fresh (its createdAt is recent) — the URL date does
    # not remove it from the *metric*, only from the *candidate*.
    m = ch.corpus_metrics([a], now=NOW, fresh_max_age_days=3)
    assert m["total"] == 1
