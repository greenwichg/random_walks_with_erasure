"""Commit C4 — freshness gates candidate selection; publishedAt is never fabricated for real articles.

Proves: the ``RWE_FEED_MAX_AGE_DAYS`` window (default 60, ``0`` disables) keeps stale articles out
of BOTH corpus-composition paths (the startup ``feed_source.export_catalog_csv`` export and the
hot-refresh ``corpus_validation.build_candidate``) while read articles stay exempt (graph
connectivity) and storage is untouched; and that a served article's ``publishedAt`` is the real
publication timestamp when one exists — the deterministic ``_iso_recent`` estimate survives ONLY
for demo/synthetic corpus items that have no verified URL anywhere.
"""

import csv
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import corpus_health as ch      # noqa: E402
import corpus_validation as cv  # noqa: E402
import feed_source              # noqa: E402
import store as store_mod       # noqa: E402

# The anchor every fixture is dated relative to. It must be the REAL now, not a literal.
#
# Half the tests below pass `now=NOW` into the pure filter, where any anchor is self-consistent. The
# other half seed a store and let `export_catalog_csv` / `prepare` filter against the wall clock —
# and there a literal anchor slides: pinned at 2026-07-11, a fixture written as `days_ago=2` was
# genuinely 52 days old by 2026-08-30 and would have crossed the 60-day window ten days later,
# failing tests that assert freshness. No test here asserts a calendar date, only relative ages, so
# the anchor moving with the clock is exactly what the fixtures mean.
NOW = datetime.now(timezone.utc)


def _art(url, days_ago=0.0, publisher="Pub", published=True, fetched_days_ago=None):
    a = {"canonicalUrl": url, "url": url, "publisher": publisher, "title": f"t {url}",
         "scored": {"outlet": publisher, "category": "Politics", "lean": 0.0, "political": True,
                    "title": f"t {url}"},
         "publishedAt": (NOW - timedelta(days=days_ago)).isoformat() if published else None,
         "fetchedAt": (NOW - timedelta(days=(fetched_days_ago if fetched_days_ago is not None
                                             else days_ago))).isoformat()}
    return a


# --------------------------------------------------------------------------- #
# The env window.
# --------------------------------------------------------------------------- #
def test_the_freshness_anchor_tracks_the_clock():
    """Re-pinning NOW to a literal must fail here rather than a month later, somewhere else.

    That is how this arrived: a sibling suite seeded articles at a hard-coded 2026-07-01, which was
    fine for 60 days and then silently emptied the exported corpus. A fixed anchor inside a rolling
    window does not fail when it is written — it fails on a date nobody chose.
    """
    assert abs((datetime.now(timezone.utc) - NOW).total_seconds()) < 3600


def test_window_defaults_to_60_days(monkeypatch):
    monkeypatch.delenv("RWE_FEED_MAX_AGE_DAYS", raising=False)
    assert ch.feed_max_age_days() == 60.0


def test_window_zero_disables_and_junk_falls_back(monkeypatch):
    monkeypatch.setenv("RWE_FEED_MAX_AGE_DAYS", "0")
    assert ch.feed_max_age_days() is None
    monkeypatch.setenv("RWE_FEED_MAX_AGE_DAYS", "-5")
    assert ch.feed_max_age_days() is None
    monkeypatch.setenv("RWE_FEED_MAX_AGE_DAYS", "ninety")
    assert ch.feed_max_age_days() == 60.0            # unparseable -> the default window
    monkeypatch.setenv("RWE_FEED_MAX_AGE_DAYS", "7.5")
    assert ch.feed_max_age_days() == 7.5


# --------------------------------------------------------------------------- #
# The shared filter.
# --------------------------------------------------------------------------- #
def test_fresh_articles_filters_by_publication_age():
    arts = [_art("https://ex.com/new", days_ago=3), _art("https://ex.com/old", days_ago=90)]
    kept = ch.fresh_articles(arts, now=NOW, max_age_days=60)
    assert [a["canonicalUrl"] for a in kept] == ["https://ex.com/new"]


def test_fresh_articles_undated_uses_fetch_time():
    fresh_undated = _art("https://ex.com/u1", published=False, fetched_days_ago=2)
    stale_undated = _art("https://ex.com/u2", published=False, fetched_days_ago=120)
    kept = ch.fresh_articles([fresh_undated, stale_undated], now=NOW, max_age_days=60)
    assert [a["canonicalUrl"] for a in kept] == ["https://ex.com/u1"]


def test_fresh_articles_unparseable_time_is_kept():
    a = _art("https://ex.com/x", days_ago=1)
    a["publishedAt"] = "not-a-date"
    a["fetchedAt"] = "also-junk"
    assert ch.fresh_articles([a], now=NOW, max_age_days=60) == [a]   # staleness can't be proven


def test_fresh_articles_undated_ages_out_from_stable_created_at():
    """C4.1: an undated article's candidacy age is its STABLE first-seen ``createdAt``, not the
    re-poll-refreshed ``fetchedAt`` — so a long-known undated item ages out even while re-polls keep
    ``fetchedAt`` fresh (the 'immortal undated' fix), while a genuinely new undated item stays."""
    old_seen = _art("https://ex.com/wayday", published=False, fetched_days_ago=1)  # re-polled: fresh fetch
    old_seen["createdAt"] = (NOW - timedelta(days=400)).isoformat()                # but first seen 400d ago
    new_seen = _art("https://ex.com/brandnew", published=False, fetched_days_ago=1)
    new_seen["createdAt"] = (NOW - timedelta(days=2)).isoformat()                  # just discovered
    kept = ch.fresh_articles([old_seen, new_seen], now=NOW, max_age_days=60)
    assert [a["canonicalUrl"] for a in kept] == ["https://ex.com/brandnew"]        # old-seen aged out


def test_published_default_order_is_unchanged_for_metrics():
    """The default :func:`_published` order (health metrics + the newest-first sort) is untouched —
    ``publishedAt`` then ``fetchedAt``, never ``createdAt`` — so only candidate freshness opts into the
    ``createdAt``-anchored order and no reported metric shifts."""
    a = {"publishedAt": None, "createdAt": (NOW - timedelta(days=400)).isoformat(),
         "fetchedAt": (NOW - timedelta(days=1)).isoformat()}
    assert ch._published(a) == datetime.fromisoformat(a["fetchedAt"])                          # default
    assert ch._published(a, ch._CANDIDACY_TIME_KEYS) == datetime.fromisoformat(a["createdAt"])  # candidacy


def test_fresh_articles_exempt_and_disabled():
    old = _art("https://ex.com/read-old", days_ago=200)
    assert ch.fresh_articles([old], now=NOW, max_age_days=60,
                             exempt={"https://ex.com/read-old"}) == [old]
    assert ch.fresh_articles([old], now=NOW, max_age_days=0) == [old]   # 0/None disables


# --------------------------------------------------------------------------- #
# Hot-refresh path: corpus_validation.build_candidate.
# --------------------------------------------------------------------------- #
def test_build_candidate_excludes_stale(monkeypatch):
    monkeypatch.delenv("RWE_FEED_MAX_AGE_DAYS", raising=False)      # default 60
    arts = [_art("https://ex.com/a", days_ago=1), _art("https://ex.com/b", days_ago=61),
            _art("https://ex.com/c", days_ago=59)]
    got = {a["canonicalUrl"] for a in cv.build_candidate(arts, now=NOW)}
    assert got == {"https://ex.com/a", "https://ex.com/c"}


def test_build_candidate_gate_runs_before_publisher_cap():
    """The cap must be spent on recommendable (fresh) articles: a stale row may not consume a
    publisher slot and push a fresh article out."""
    arts = [_art("https://ex.com/stale", days_ago=90, publisher="One"),
            _art("https://ex.com/f1", days_ago=1, publisher="One"),
            _art("https://ex.com/f2", days_ago=2, publisher="One")]
    got = [a["canonicalUrl"] for a in cv.build_candidate(arts, max_per_publisher=2, now=NOW,
                                                         max_age_days=60)]
    assert got == ["https://ex.com/f1", "https://ex.com/f2"]


def test_build_candidate_disabled_keeps_everything():
    arts = [_art("https://ex.com/a", days_ago=400), _art("https://ex.com/b", days_ago=1)]
    got = {a["canonicalUrl"] for a in cv.build_candidate(arts, now=NOW, max_age_days=0)}
    assert got == {"https://ex.com/a", "https://ex.com/b"}


# --------------------------------------------------------------------------- #
# Startup path: feed_source.export_catalog_csv (through a real store).
# --------------------------------------------------------------------------- #
def _seed(st, url, days_ago, publisher="Pub", published=True):
    a = _art(url, days_ago=days_ago, publisher=publisher, published=published)
    st.upsert_feed_article(
        canonical_url=url, url=url, publisher=publisher, source_publisher=publisher,
        title=a["title"], description="d", body=None, published_at=a["publishedAt"],
        source_feed="f", scored=a["scored"])


def test_export_catalog_csv_drops_stale_keeps_read(tmp_path, monkeypatch):
    monkeypatch.delenv("RWE_FEED_MAX_AGE_DAYS", raising=False)      # default 60
    st = store_mod.Store(f"sqlite:///{tmp_path / 'f.db'}")
    _seed(st, "https://ex.com/fresh", days_ago=2)
    _seed(st, "https://ex.com/stale", days_ago=120)
    _seed(st, "https://ex.com/stale-read", days_ago=120)
    uid = st.upsert_user_by_identity("dev", "freshness-test").id
    st.add_read(uid, "https://ex.com/stale-read",
                {"article_id": "https://ex.com/stale-read", "outlet": "Pub",
                 "category": "Politics", "lean": 0.0, "political": True})
    out = tmp_path / "corpus.csv"
    feed_source.export_catalog_csv(st, str(out))
    urls = {row["url"] for row in csv.DictReader(open(out, encoding="utf-8"))}
    assert "https://ex.com/fresh" in urls
    assert "https://ex.com/stale" not in urls                      # stale: never a candidate
    assert "https://ex.com/stale-read" in urls                     # read-demand exemption
    assert st.count_feed_articles() == 3                           # storage untouched


def test_prepare_refuses_a_catalog_that_is_all_stale(tmp_path, monkeypatch):
    """A stored count above the threshold is not a corpus — the EXPORTED count is.

    `prepare` used to gate on `count_feed_articles()` only, so a catalog that was large enough but
    entirely outside the freshness window returned a path to a header-only CSV. The engine built a
    zero-item corpus from it and the population sampler died on `rng.choice` — "probabilities do not
    sum to 1" — a corpus too small to simulate arriving as a crash instead of the fallback this
    function exists to provide.
    """
    monkeypatch.delenv("RWE_FEED_MAX_AGE_DAYS", raising=False)      # default 60
    st = store_mod.Store(f"sqlite:///{tmp_path / 'stale.db'}")
    for i in range(8):
        _seed(st, f"https://ex.com/stale-{i}", days_ago=120)
    out = tmp_path / "corpus.csv"

    assert st.count_feed_articles() == 8                # the stored count clears the threshold...
    assert feed_source.export_catalog_csv(st, str(out)) == 0        # ...and nothing is exportable
    assert feed_source.prepare(st, str(out), min_articles=5) is None

    # A fresh article among them and the same call succeeds — the refusal is about candidacy, not
    # about the store being unreadable.
    _seed(st, "https://ex.com/fresh-0", days_ago=1)
    assert feed_source.prepare(st, str(out), min_articles=1) == str(out)


def test_prepare_refuses_when_the_window_leaves_too_few(tmp_path, monkeypatch):
    """The threshold is applied to the exported rows, not merely to "more than zero"."""
    monkeypatch.delenv("RWE_FEED_MAX_AGE_DAYS", raising=False)      # default 60
    st = store_mod.Store(f"sqlite:///{tmp_path / 'thin.db'}")
    for i in range(8):
        _seed(st, f"https://ex.com/old-{i}", days_ago=120)
    for i in range(2):
        _seed(st, f"https://ex.com/new-{i}", days_ago=1)
    out = tmp_path / "corpus.csv"

    assert st.count_feed_articles() == 10               # stored: clears a threshold of 5
    assert feed_source.export_catalog_csv(st, str(out)) == 2        # exportable: does not
    assert feed_source.prepare(st, str(out), min_articles=5) is None
    assert feed_source.prepare(st, str(out), min_articles=2) == str(out)


def test_export_catalog_csv_gate_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("RWE_FEED_MAX_AGE_DAYS", "0")
    st = store_mod.Store(f"sqlite:///{tmp_path / 'g.db'}")
    _seed(st, "https://ex.com/ancient", days_ago=500)
    out = tmp_path / "corpus.csv"
    feed_source.export_catalog_csv(st, str(out))
    urls = {row["url"] for row in csv.DictReader(open(out, encoding="utf-8"))}
    assert "https://ex.com/ancient" in urls


# --------------------------------------------------------------------------- #
# RWE_FEED_REQUIRE_DATED — candidacy requires a real publishedAt (default off).
# --------------------------------------------------------------------------- #
def test_require_dated_flag_default_off(monkeypatch):
    monkeypatch.delenv("RWE_FEED_REQUIRE_DATED", raising=False)
    assert ch.feed_require_dated() is False
    monkeypatch.setenv("RWE_FEED_REQUIRE_DATED", "1")
    assert ch.feed_require_dated() is True
    monkeypatch.setenv("RWE_FEED_REQUIRE_DATED", "off")
    assert ch.feed_require_dated() is False


def test_require_dated_excludes_undated_from_candidacy():
    """The stale-cache defense: re-polls refresh fetchedAt, so an undated item's fallback age
    never grows — with the flag on, candidacy demands a parseable publishedAt (unparseable
    counts as undated; 'staleness can't be proven' no longer keeps it)."""
    undated = _art("https://ex.com/undated", published=False, fetched_days_ago=0)  # looks brand new
    dated = _art("https://ex.com/dated", days_ago=2)
    junk = _art("https://ex.com/junk", days_ago=1)
    junk["publishedAt"], junk["fetchedAt"] = "not-a-date", "also-junk"
    kept = ch.fresh_articles([undated, dated, junk], now=NOW, max_age_days=60, require_dated=True)
    assert [a["canonicalUrl"] for a in kept] == ["https://ex.com/dated"]


def test_require_dated_keeps_exempt_and_respects_disabled_window():
    undated = _art("https://ex.com/undated-read", published=False, fetched_days_ago=0)
    assert ch.fresh_articles([undated], now=NOW, max_age_days=60, require_dated=True,
                             exempt={"https://ex.com/undated-read"}) == [undated]
    # windowing disabled -> the whole gate (this flag included) is off — today's escape hatch,
    # which is also what keeps the golden pipeline (RWE_FEED_MAX_AGE_DAYS=0) untouched
    assert ch.fresh_articles([undated], now=NOW, max_age_days=0, require_dated=True) == [undated]


def test_require_dated_off_is_byte_compatible(monkeypatch):
    monkeypatch.delenv("RWE_FEED_REQUIRE_DATED", raising=False)
    undated = _art("https://ex.com/u1", published=False, fetched_days_ago=2)
    assert ch.fresh_articles([undated], now=NOW, max_age_days=60) == [undated]  # pre-flag policy


def test_build_candidate_require_dated_env(monkeypatch):
    """The hot-refresh path resolves the flag from the env through the shared filter."""
    monkeypatch.setenv("RWE_FEED_REQUIRE_DATED", "1")
    arts = [_art("https://ex.com/dated", days_ago=1),
            _art("https://ex.com/undated", published=False, fetched_days_ago=0)]
    got = {a["canonicalUrl"] for a in cv.build_candidate(arts, now=NOW, max_age_days=60)}
    assert got == {"https://ex.com/dated"}


def test_export_catalog_csv_require_dated_env(tmp_path, monkeypatch):
    """The startup path, end-to-end on a real store: undated rows leave candidacy but stay
    stored, and a read undated article keeps its exemption (graph connectivity)."""
    monkeypatch.delenv("RWE_FEED_MAX_AGE_DAYS", raising=False)      # default 60
    monkeypatch.setenv("RWE_FEED_REQUIRE_DATED", "1")
    st = store_mod.Store(f"sqlite:///{tmp_path / 'rd.db'}")
    _seed(st, "https://ex.com/dated", days_ago=2)
    _seed(st, "https://ex.com/undated", days_ago=0, published=False)
    _seed(st, "https://ex.com/undated-read", days_ago=0, published=False)
    uid = st.upsert_user_by_identity("dev", "require-dated-test").id
    st.add_read(uid, "https://ex.com/undated-read",
                {"article_id": "https://ex.com/undated-read", "outlet": "Pub",
                 "category": "Politics", "lean": 0.0, "political": True})
    out = tmp_path / "corpus.csv"
    feed_source.export_catalog_csv(st, str(out))
    urls = {row["url"] for row in csv.DictReader(open(out, encoding="utf-8"))}
    assert "https://ex.com/dated" in urls
    assert "https://ex.com/undated" not in urls                    # no date -> not a candidate
    assert "https://ex.com/undated-read" in urls                   # read-demand exemption
    assert st.count_feed_articles() == 3                           # storage untouched


# --------------------------------------------------------------------------- #
# publishedAt truthfulness (the serializer + the catalog join).
# --------------------------------------------------------------------------- #
def test_serializer_never_fabricates_for_real_articles():
    import api_server
    be = object.__new__(api_server.Backend)                        # serializer-only surface
    be.url_by_id = {"Q0": "https://ex.com/real"}
    be.lean_tau = 0.5
    emotion = {"fear": 0, "outrage": 0, "analysis": 0, "positive": 0, "neutral": 1}
    real = be._article_payload(item_id="Q0", headline="h", outlet="Pub", topic="Politics",
                               lean=0.0, register=None, emotion=emotion, confidence=None,
                               outlet_lean={})
    assert real["publishedAt"] == ""                               # real article: join or nothing
    read = be._article_payload(item_id="https://ex.com/read", headline="h", outlet="Pub",
                               topic="", lean=0.0, register=None, emotion=emotion,
                               confidence=None, outlet_lean={})
    assert read["publishedAt"] == ""                               # a stored read is real too
    demo = be._article_payload(item_id="N42", headline="h", outlet="Pub", topic="", lean=0.0,
                               register=None, emotion=emotion, confidence=None, outlet_lean={})
    assert demo["publishedAt"] != ""                               # demo/synthetic keeps estimate
    datetime.fromisoformat(demo["publishedAt"])                    # ... and it parses


def test_feed_article_media_carries_real_published_at(tmp_path):
    st = store_mod.Store(f"sqlite:///{tmp_path / 'm.db'}")
    _seed(st, "https://ex.com/dated", days_ago=5)
    st.upsert_feed_article(                                        # no publication date at all
        canonical_url="https://ex.com/undated", url="https://ex.com/undated", publisher="Pub",
        source_publisher="Pub", title="t", description="d", body=None, published_at=None,
        source_feed="f", scored={"outlet": "Pub", "category": "", "lean": 0.0, "political": False})
    m = st.feed_article_media(["https://ex.com/dated", "https://ex.com/undated"])
    assert m["https://ex.com/dated"]["publishedAt"] == (NOW - timedelta(days=5)).isoformat()
    # undated rows fall back to the observed fetch time (real, never fabricated)
    got = m["https://ex.com/undated"]["publishedAt"]
    assert got
    datetime.fromisoformat(got)                                    # parseable ISO timestamp
    assert "image" not in m["https://ex.com/undated"]              # imageless row: timestamp only
