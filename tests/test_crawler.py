"""Tests for the publisher crawl framework (crawler.py) — offline, fixture-driven.

Every network seam is injected, so this suite exercises the whole framework (robots gate, rate
limiting, the discovery ladder, domain/pattern filtering, dedup, the adapter's terminus at
``ingest_entries``) without contacting a publisher. That is not only a test-speed decision: a test
suite that reaches the open internet fails for reasons that have nothing to do with the code.

The behaviours pinned here are the ones where a crawler quietly does the wrong thing — crawling
when it was refused, attributing an impersonator's URL to a trusted publisher, ingesting a tag
page as an article, or re-fetching what the catalog already holds.
"""

import json
import pathlib
import re
import sys
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import crawler                # noqa: E402
import ingest                 # noqa: E402
import rss_ingest             # noqa: E402
import store as store_mod     # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "crawler"


def _fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _fetcher(routes: dict):
    """A fetch seam over a ``{url: body}`` map. An unmapped URL raises, exactly as a 404 would —
    so a test that expects a rung to be tried has to say which URL it serves."""
    def fetch(url, **_kw):
        if url not in routes:
            raise OSError(f"unmapped URL in test: {url}")
        body = routes[url]
        if isinstance(body, Exception):
            raise body
        return body
    return fetch


def _cfg(**over) -> crawler.PublisherCrawlConfig:
    base = dict(publisher="NPR", domains=("npr.org",),
                sources=(crawler.DiscoverySource("sitemap", "https://www.npr.org/s.xml"),),
                article_pattern=r"/\d{4}/\d{2}/\d{2}/", min_interval=0.0)
    base.update(over)
    return crawler.PublisherCrawlConfig(**base)


def _policy(routes: dict) -> crawler.RobotsPolicy:
    return crawler.RobotsPolicy(fetch=_fetcher(routes))


def _no_wait() -> crawler.RateLimiter:
    return crawler.RateLimiter(0.0, sleep=lambda s: None, clock=lambda: 0.0)


ALLOW = {"https://www.npr.org/robots.txt": _fx("robots_allow.txt")}


# --------------------------------------------------------------------------- #
# robots.txt — the gate
# --------------------------------------------------------------------------- #
def test_robots_allows_what_the_publisher_allows_and_reports_their_crawl_delay():
    d = _policy(ALLOW).check("https://www.npr.org/sitemaps/news-sitemap.xml")
    assert d.allowed is True and d.crawl_delay == 4.0


def test_robots_refuses_a_disallowed_path():
    d = _policy(ALLOW).check("https://www.npr.org/search?q=x")
    assert d.allowed is False and "robots" in d.reason


def test_robots_refuses_everything_when_the_publisher_disallows_everything():
    routes = {"https://www.npr.org/robots.txt": _fx("robots_deny.txt")}
    assert _policy(routes).check("https://www.npr.org/2026/08/11/1/x").allowed is False


def test_an_unreachable_robots_txt_is_a_refusal_not_a_permission():
    """The conventional crawler default — no robots.txt means crawl freely — is a reading earned by
    search engines over decades. A commercial product reading newsrooms it has never spoken to does
    not get to make that assumption: "we could not tell whether we were allowed" is not a licence."""
    routes = {"https://www.npr.org/robots.txt": OSError("connection refused")}
    d = _policy(routes).check("https://www.npr.org/2026/08/11/1/x")
    assert d.allowed is False and "unavailable" in d.reason


def test_robots_is_fetched_once_per_host_not_once_per_url():
    """A crawler that re-fetches robots.txt before every request doubles its own traffic against
    the very server the file exists to protect."""
    calls = []

    def fetch(url, **_kw):
        calls.append(url)
        return _fx("robots_allow.txt")

    p = crawler.RobotsPolicy(fetch=fetch)
    for i in range(5):
        p.check(f"https://www.npr.org/2026/08/11/{i}/x")
    assert calls == ["https://www.npr.org/robots.txt"]


def test_a_body_that_is_not_robots_txt_does_not_become_permission():
    """A captive portal or an HTML 404 page parses as "no rules", which RobotFileParser reports as
    allow-everything. Fail-closed has to survive a 200 that is not a policy."""
    routes = {"https://www.npr.org/robots.txt": "<html><body>Not found</body></html>"}
    # Parsed as a policy with no directives -> permissive; the gate must not treat junk as consent.
    d = _policy(routes).check("https://www.npr.org/2026/08/11/1/x")
    assert d.allowed is False, "an HTML page is not a robots policy"


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #
def test_rate_limiter_waits_the_configured_gap_between_two_hits_on_one_host():
    slept, t = [], [0.0]
    rl = crawler.RateLimiter(2.0, sleep=lambda s: slept.append(s), clock=lambda: t[0])
    rl.wait("https://a.example/1")
    rl.wait("https://a.example/2")
    assert slept == [2.0]


def test_rate_limiter_does_not_make_one_publisher_wait_for_another():
    slept, t = [], [0.0]
    rl = crawler.RateLimiter(2.0, sleep=lambda s: slept.append(s), clock=lambda: t[0])
    rl.wait("https://a.example/1")
    rl.wait("https://b.example/1")
    assert slept == [], "different hosts do not share a limit"


def test_a_publishers_own_crawl_delay_can_only_slow_us_down():
    """`Crawl-delay: 4` against a 2s floor must yield 4s. Taking the minimum would let a publisher's
    own stated limit be overridden by our default, which inverts what the directive is for."""
    slept = []
    cfg = _cfg(min_interval=2.0)
    c = crawler.PublisherCrawler(
        cfg, robots=_policy(ALLOW),
        limiter=crawler.RateLimiter(2.0, sleep=lambda s: slept.append(s), clock=lambda: 0.0),
        fetch=_fetcher({"https://www.npr.org/s.xml": _fx("news_sitemap.xml")}))
    r = crawler.CrawlReport(publisher="NPR")
    c._get("https://www.npr.org/s.xml", r)
    c._get("https://www.npr.org/s.xml", r)
    assert slept == [4.0]


# --------------------------------------------------------------------------- #
# Discovery parsers
# --------------------------------------------------------------------------- #
def test_sitemap_yields_urls_with_the_publishers_own_title_and_date():
    entries = crawler.discover_sitemap(_fx("news_sitemap.xml"))
    by_url = {e.url: e for e in entries}
    hit = by_url["https://www.npr.org/2026/08/11/1234567/senate-budget-vote"]
    assert hit.title == "Senate passes budget after bipartisan deal"
    assert hit.published_at.startswith("2026-08-11T09:15:00")


def test_sitemap_falls_back_to_lastmod_when_there_is_no_news_block():
    entries = {e.url: e for e in crawler.discover_sitemap(_fx("news_sitemap.xml"))}
    assert entries["https://www.npr.org/sections/politics/"].published_at.startswith("2026-08-11T12:00")


def test_a_sitemap_index_is_marked_so_the_ladder_can_descend_one_level():
    entries = crawler.discover_sitemap(_fx("sitemap_index.xml"))
    assert [e.source_type for e in entries] == ["sitemap-index"]


def test_malformed_xml_yields_nothing_rather_than_raising():
    """A publisher serving a truncated sitemap should cost us that rung, not the whole cycle."""
    assert crawler.discover_sitemap("<urlset><url><loc>oops") == []


def test_section_page_links_are_absolutised_and_deduped():
    entries = crawler.discover_section(_fx("section.html"), "https://www.npr.org/sections/news/")
    urls = [e.url for e in entries]
    assert "https://www.npr.org/2026/08/11/1234567/senate-budget-vote" in urls
    assert len(urls) == len(set(urls)), "the same href twice is one candidate"


def test_section_page_carries_no_invented_publication_date():
    """A section index does not state when anything was published. Defaulting to "now" would put a
    five-year-old feature at the top of Latest — the same instinct as the null-lean rule."""
    entries = crawler.discover_section(_fx("section.html"), "https://www.npr.org/sections/news/")
    assert all(e.published_at is None for e in entries)


def test_anchor_text_is_normalised_into_a_usable_headline():
    entries = {e.url: e for e in crawler.discover_section(_fx("section.html"),
                                                          "https://www.npr.org/sections/news/")}
    assert entries["https://www.npr.org/2026/08/11/1234567/senate-budget-vote"].title == \
        "Senate passes budget after deal"


def test_rss_discovery_is_the_existing_feed_parser():
    entries = crawler.discover_rss(_fx("feed.xml"))
    assert len(entries) == 1 and entries[0].url.endswith("/senate-budget-vote")


# --------------------------------------------------------------------------- #
# Filtering — domain, pattern, dedup
# --------------------------------------------------------------------------- #
def _crawl(routes, cfg=None, store_=None):
    c = crawler.PublisherCrawler(cfg or _cfg(), robots=_policy({**ALLOW, **{
        "https://evil.example/robots.txt": _fx("robots_allow.txt")}}),
        limiter=_no_wait(), fetch=_fetcher(routes), store_=store_)
    return c.crawl()


def test_an_off_domain_url_in_a_publishers_own_sitemap_is_refused():
    """The fixture sitemap names `evil.example`. A URL is attributed to whoever the config says the
    publisher is, and that attribution carries a lean — so a sitemap that names someone else's host
    must not be able to launder an impersonator into the catalog under NPR's name."""
    entries, report = _crawl({"https://www.npr.org/s.xml": _fx("news_sitemap.xml")})
    assert all("evil.example" not in e.url for e in entries)
    assert report.off_domain == 1


def test_subdomain_matching_is_anchored_on_a_dot_boundary():
    """`endswith("npr.org")` also accepts `notnpr.org` — which is exactly how a crawler ends up
    publishing an impersonator under a trusted outlet's lean."""
    assert crawler._host_allowed("www.npr.org", ["npr.org"]) is True
    assert crawler._host_allowed("text.npr.org", ["npr.org"]) is True
    assert crawler._host_allowed("notnpr.org", ["npr.org"]) is False
    assert crawler._host_allowed("npr.org.evil.example", ["npr.org"]) is False


def test_a_section_index_url_is_rejected_by_the_article_pattern():
    entries, report = _crawl({"https://www.npr.org/s.xml": _fx("news_sitemap.xml")})
    assert all("/sections/" not in e.url for e in entries)
    assert report.pattern_rejected == 1


def test_one_article_linked_three_ways_is_one_candidate():
    """A homepage links the same story with a trailing slash, a tracking param, and bare. Those are
    three different strings and one article, so dedup has to be CANONICAL — and it uses the
    catalog's own ``canonical_url`` rather than a second notion of sameness that could disagree
    with the key the catalog will later dedup on."""
    cfg = _cfg(sources=(crawler.DiscoverySource("section", "https://www.npr.org/sections/news/"),))
    entries, report = _crawl({"https://www.npr.org/sections/news/": _fx("section.html")}, cfg)
    canon = [ingest.canonical_url(e.url) for e in entries]
    assert len(canon) == len(set(canon))
    assert report.duplicate_in_cycle == 2, "two of the three spellings are the same article"
    assert len(entries) == 2


def test_articles_already_in_the_catalog_are_dropped_before_anything_else_happens():
    """A publisher's sitemap is mostly yesterday's articles. Skipping what we already hold is the
    single biggest politeness win available, and it is one batched read, not one per URL."""
    st = store_mod.Store("sqlite://")
    known = "https://www.npr.org/2026/08/11/1234567/senate-budget-vote"
    st.upsert_feed_article(
        canonical_url=ingest.canonical_url(known), url=known, publisher="NPR",
        source_publisher="NPR", title="t", description="", body=None,
        published_at="2026-08-11T09:15:00+00:00", source_feed="f",
        scored={"article_id": ingest.canonical_url(known), "outlet": "NPR", "lean": 0.0})
    entries, report = _crawl({"https://www.npr.org/s.xml": _fx("news_sitemap.xml")}, store_=st)
    assert report.already_in_catalog == 1
    assert all(known not in e.url for e in entries)


def test_every_accepted_entry_is_attributed_to_the_configured_publisher():
    """`publisher_hint` is what resolves the outlet — and therefore the lean — downstream.

    The CONFIG name must win on every discovery document. Today no rung sets a per-entry hint
    (discover_rss discards the channel title — "NPR News" in the fixture below), so both stamp
    forms behave identically and a mutation between them cannot fail; the feed half pins the
    INVARIANT — one host, one label — against any future rung that starts hinting. The production
    label splits (43 variants / 35 hosts, 2026-09-01) were the admission-seed rename loop, pinned
    with its own mutation in test_source_admission."""
    entries, _ = _crawl({"https://www.npr.org/s.xml": _fx("news_sitemap.xml")})
    assert entries and all(e.publisher_hint == "NPR" for e in entries)
    assert all(e.source_type == "crawl" for e in entries)

    cfg = _cfg(sources=(crawler.DiscoverySource("rss", "https://www.npr.org/f.xml"),))
    entries, _ = _crawl({"https://www.npr.org/f.xml": _fx("feed.xml")}, cfg)
    assert entries, "the feed fixture must yield entries for this test to mean anything"
    assert all(e.publisher_hint == "NPR" for e in entries), \
        "the feed said 'NPR News'; one host must not become two publishers"


# --------------------------------------------------------------------------- #
# The ladder
# --------------------------------------------------------------------------- #
def test_the_ladder_stops_at_the_first_rung_that_works():
    """A publisher with a healthy feed is never sitemap-crawled. The lower rungs exist for the days
    the feed is empty, and a ladder that runs every rung every time is just a slower crawler."""
    cfg = _cfg(sources=(crawler.DiscoverySource("rss", "https://www.npr.org/f.xml"),
                        crawler.DiscoverySource("sitemap", "https://www.npr.org/s.xml")))
    entries, report = _crawl({"https://www.npr.org/f.xml": _fx("feed.xml")}, cfg)
    assert report.rung_used == "rss" and report.rungs_tried == ["rss"]
    assert report.fetched == 1 and len(entries) == 1


def test_an_empty_feed_falls_through_to_the_sitemap():
    """This is the entire reason the framework exists: RSS that returns nothing today."""
    empty = ("<?xml version='1.0'?><rss version='2.0'><channel><title>NPR</title>"
             "</channel></rss>")
    cfg = _cfg(sources=(crawler.DiscoverySource("rss", "https://www.npr.org/f.xml"),
                        crawler.DiscoverySource("sitemap", "https://www.npr.org/s.xml")))
    entries, report = _crawl({"https://www.npr.org/f.xml": empty,
                              "https://www.npr.org/s.xml": _fx("news_sitemap.xml")}, cfg)
    assert report.rungs_tried == ["rss", "sitemap"] and report.rung_used == "sitemap"
    assert len(entries) == 2


def test_a_sitemap_index_is_followed_exactly_one_level_down():
    routes = {"https://www.npr.org/s.xml": _fx("sitemap_index.xml"),
              "https://www.npr.org/sitemaps/news-sitemap-1.xml": _fx("news_sitemap.xml")}
    entries, report = _crawl(routes)
    assert report.fetched == 2 and len(entries) == 2


def _index_of(*children) -> str:
    """A sitemap index whose children carry the given `lastmod` values, in document order."""
    items = "".join(
        f"<sitemap><loc>https://www.npr.org/s{i}.xml</loc>"
        + (f"<lastmod>{d}</lastmod>" if d else "") + "</sitemap>"
        for i, d in enumerate(children))
    return ("<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
            + items + "</sitemapindex>")


def test_a_sitemap_index_is_descended_newest_child_first():
    """The defect this fixes: sitemap indexes are conventionally ordered OLDEST-first, so taking
    them in document order spent the entire fetch budget on the deepest archive and never reached
    this week. Daily Maverick and Premium Times both returned 100% `too_old` because of this — a
    bug on our side, not archive-only publishers."""
    routes = {"https://www.npr.org/s.xml": _index_of("2019-01-01", "2020-01-01", "2026-08-19"),
              "https://www.npr.org/s2.xml": _fx("news_sitemap.xml")}   # the NEWEST child
    entries, report = _crawl(routes, _cfg(max_fetches=2))
    assert report.fetched == 2, "index + one child"
    assert len(entries) == 2, "the newest child was the one fetched"


def test_an_undated_index_child_sorts_after_every_dated_one():
    """A child the publisher left undated is not evidence of recency, so it goes last — the same
    reading the age filter takes."""
    routes = {"https://www.npr.org/s.xml": _index_of(None, "2026-08-19"),
              "https://www.npr.org/s1.xml": _fx("news_sitemap.xml")}   # dated child
    entries, report = _crawl(routes, _cfg(max_fetches=2))
    assert report.fetched == 2 and len(entries) == 2


def test_an_index_whose_children_are_all_undated_still_descends():
    """No dates means no ordering signal, and the rung must still work rather than refuse."""
    routes = {"https://www.npr.org/s.xml": _index_of(None, None),
              "https://www.npr.org/s0.xml": _fx("news_sitemap.xml"),
              "https://www.npr.org/s1.xml": _fx("news_sitemap.xml")}
    entries, report = _crawl(routes, _cfg(max_fetches=3))
    assert report.fetched == 3 and len(entries) == 2, "same two articles, deduped across children"


def test_the_fetch_budget_bounds_a_pathological_sitemap_index():
    """A sitemap index pointing at hundreds of children must not turn one cycle into hundreds of
    requests. The budget is the number an operator can state in advance."""
    children = "".join(f"<sitemap><loc>https://www.npr.org/s{i}.xml</loc></sitemap>"
                       for i in range(50))
    routes = {"https://www.npr.org/s.xml":
              f"<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>{children}</sitemapindex>"}
    routes.update({f"https://www.npr.org/s{i}.xml": _fx("news_sitemap.xml") for i in range(50)})
    _entries, report = _crawl(routes, _cfg(max_fetches=3))
    assert report.fetched == 3


def test_a_rung_that_is_refused_by_robots_is_skipped_and_counted_not_crawled_anyway():
    cfg = _cfg(sources=(crawler.DiscoverySource("sitemap", "https://www.npr.org/s.xml"),))
    c = crawler.PublisherCrawler(
        cfg, robots=_policy({"https://www.npr.org/robots.txt": _fx("robots_deny.txt")}),
        limiter=_no_wait(),
        fetch=_fetcher({"https://www.npr.org/s.xml": _fx("news_sitemap.xml")}))
    entries, report = c.crawl()
    assert entries == [] and report.robots_blocked == 1 and report.fetched == 0


def test_one_dead_index_child_costs_that_child_not_its_siblings():
    """Daily Maverick's index carries a stale entry — a sitemap delisted but not removed. Before
    this, that single 404 aborted the whole publisher and threw away every sibling's articles."""
    routes = {"https://www.npr.org/s.xml": _index_of("2026-08-19", "2026-08-18"),
              "https://www.npr.org/s0.xml": urllib.error.HTTPError(
                  "https://www.npr.org/s0.xml", 404, "Not Found", {}, None),
              "https://www.npr.org/s1.xml": _fx("news_sitemap.xml")}
    entries, report = _crawl(routes, _cfg(max_fetches=4))
    assert len(entries) == 2, "the surviving sibling still produced articles"
    assert report.fetch_errors == 1 and "404" in report.errors[0]
    assert report.error is None, "a cycle that recovered must not report a failure it survived"


def test_a_broken_rung_falls_through_to_the_next_one():
    """The ladder exists so a broken sitemap drops to the section page. Aborting the publisher on
    the first failure discarded exactly the fallback the design promises."""
    cfg = _cfg(sources=(crawler.DiscoverySource("sitemap", "https://www.npr.org/dead.xml"),
                        crawler.DiscoverySource("section", "https://www.npr.org/sections/news/")))
    routes = {"https://www.npr.org/sections/news/": _fx("section.html")}
    entries, report = _crawl(routes, cfg)
    assert report.rungs_tried == ["sitemap", "section"] and report.rung_used == "section"
    assert entries and report.fetch_errors == 1 and report.error is None


def test_when_every_rung_fails_the_publisher_reports_the_first_reason():
    cfg = _cfg(sources=(crawler.DiscoverySource("sitemap", "https://www.npr.org/dead.xml"),
                        crawler.DiscoverySource("section", "https://www.npr.org/gone/")))
    entries, report = _crawl({}, cfg)
    assert entries == [] and report.fetch_errors == 2
    assert report.error and "dead.xml" in report.error


def test_the_error_list_is_bounded_so_a_broken_index_cannot_flood_the_report():
    children = _index_of(*[f"2026-08-{d:02d}" for d in range(1, 20)])
    entries, report = _crawl({"https://www.npr.org/s.xml": children}, _cfg(max_fetches=12))
    assert entries == [] and report.fetch_errors == 11 and len(report.errors) == 5


def test_a_failing_rung_records_the_error_instead_of_raising():
    """One publisher's outage must not abort the cycle for the others."""
    _entries, report = _crawl({"https://www.npr.org/s.xml": OSError("boom")})
    assert report.error and "OSError" in report.error


def test_max_urls_caps_a_cycle_and_says_how_much_it_dropped():
    _entries, report = _crawl({"https://www.npr.org/s.xml": _fx("news_sitemap.xml")},
                              _cfg(max_urls=1))
    assert report.accepted == 1 and report.capped == 1


def test_the_report_accounts_for_every_discovered_url():
    """A cycle that returns nothing must say WHICH gate closed. The fixture has 4 URLs: 2 good,
    1 off-domain, 1 that fails the article pattern."""
    _entries, r = _crawl({"https://www.npr.org/s.xml": _fx("news_sitemap.xml")})
    assert r.discovered == 4
    assert r.accepted + r.off_domain + r.pattern_rejected + r.duplicate_in_cycle \
        + r.already_in_catalog + r.capped == r.discovered


# --------------------------------------------------------------------------- #
# The terminus — ingest_entries, unchanged
# --------------------------------------------------------------------------- #
def test_the_adapter_terminates_at_ingest_entries_and_the_catalog_gets_real_rows():
    """The whole point of the framework: after discovery it is an ordinary source. Scoring, dedup,
    media and persistence are reached through the same choke point RSS uses."""
    st = store_mod.Store("sqlite://")
    adapter = crawler.CrawlAdapter(
        _cfg(), robots=_policy(ALLOW), limiter=_no_wait(),
        fetch=_fetcher({"https://www.npr.org/s.xml": _fx("news_sitemap.xml")}), store_=st)
    agg = adapter.poll_once(st, rss_ingest.make_scorer())
    assert agg["new"] == 2 and agg["ok"] == 1
    assert st.count_feed_articles() == 2
    row = st.get_feed_article(
        ingest.canonical_url("https://www.npr.org/2026/08/11/1234567/senate-budget-vote"))
    assert row is not None and row["publisher"] == "NPR"


def test_re_crawling_the_same_sitemap_creates_no_duplicates():
    """Canonical-URL dedup is not the crawler's to implement — it already lives behind the choke
    point, and this proves the crawler reaches it rather than routing around it."""
    st = store_mod.Store("sqlite://")

    def run():
        return crawler.CrawlAdapter(
            _cfg(), robots=_policy(ALLOW), limiter=_no_wait(),
            fetch=_fetcher({"https://www.npr.org/s.xml": _fx("news_sitemap.xml")})
        ).poll_once(st, rss_ingest.make_scorer())

    run()
    second = run()
    assert st.count_feed_articles() == 2 and second["new"] == 0 and second["duplicates"] == 2


def test_the_adapter_records_health_under_a_crawl_scoped_key():
    adapter = crawler.CrawlAdapter(_cfg())
    assert adapter.health_key == "crawl://npr" and adapter.source_type == "crawl"


def test_plan_is_read_only_and_never_reaches_the_catalog():
    """The POC's default mode. It reads ``existing_feed_urls`` and writes nothing — the property
    that makes this safe to point at production data."""
    st = store_mod.Store("sqlite://")
    rows = crawler.plan([_cfg()], robots=_policy(ALLOW), limiter=_no_wait(),
                        fetch=_fetcher({"https://www.npr.org/s.xml": _fx("news_sitemap.xml")}),
                        store_=st)
    assert st.count_feed_articles() == 0, "plan must not ingest"
    assert rows[0]["accepted"] == 2 and len(rows[0]["sample"]) == 2


# --------------------------------------------------------------------------- #
# Shadow mode — the existing-vs-new measurement that decides whether this ships
# --------------------------------------------------------------------------- #
def _seed(st, *urls):
    for u in urls:
        st.upsert_feed_article(
            canonical_url=ingest.canonical_url(u), url=u, publisher="NPR",
            source_publisher="NPR", title="t", description="", body=None,
            published_at="2026-08-11T09:15:00+00:00", source_feed="f",
            scored={"article_id": ingest.canonical_url(u), "outlet": "NPR", "lean": 0.0})


def _plan(store_=None, cfg=None, routes=None):
    routes = routes or {"https://www.npr.org/s.xml": _fx("news_sitemap.xml")}
    return crawler.plan([cfg or _cfg()], robots=_policy(ALLOW), limiter=_no_wait(),
                        fetch=_fetcher(routes), store_=store_)


def test_shadow_splits_candidates_into_already_held_and_genuinely_new():
    """The fixture yields 2 on-domain article URLs; seeding one makes the split 1/1."""
    st = store_mod.Store("sqlite://")
    _seed(st, "https://www.npr.org/2026/08/11/1234567/senate-budget-vote")
    row = _plan(st)[0]
    assert row["candidates"] == 2
    assert row["already_in_catalog"] == 1
    assert row["genuinelyNew"] == 1
    assert row["marginalValue"] == 0.5


def test_a_catalog_that_already_holds_everything_reports_zero_marginal_value():
    """The answer that should stop the rollout. It has to be reachable and unambiguous — a crawler
    that rediscovers what RSS already delivered is cost without benefit."""
    st = store_mod.Store("sqlite://")
    _seed(st, "https://www.npr.org/2026/08/11/1234567/senate-budget-vote",
          "https://www.npr.org/2026/08/11/7654321/climate-report-released")
    row = _plan(st)[0]
    assert row["already_in_catalog"] == 2 and row["genuinelyNew"] == 0
    assert row["marginalValue"] == 0.0


def test_the_cap_does_not_flatter_the_marginal_value_ratio():
    """`max_urls` truncates what we would INGEST, not what we discovered. Measuring the ratio after
    the cap would report 100% new whenever the cap binds — the number would look best exactly when
    the crawler is drowning in already-held URLs."""
    st = store_mod.Store("sqlite://")
    _seed(st, "https://www.npr.org/2026/08/11/1234567/senate-budget-vote")
    row = _plan(st, _cfg(max_urls=1))[0]
    assert row["candidates"] == 2, "the denominator is pre-cap"
    assert row["marginalValue"] == 0.5


def test_without_a_store_every_url_counts_as_new_which_is_why_the_cli_warns():
    """Not a bug — there is nothing to compare against. It is dangerous only if reported as if it
    were a measurement, so the CLI says so and this pins the behaviour it warns about."""
    row = _plan(None)[0]
    assert row["already_in_catalog"] == 0 and row["marginalValue"] == 1.0


def test_a_publisher_that_discovered_nothing_reports_none_not_zero():
    """"We found nothing to compare" and "we found things and none were new" are different
    answers. Reporting the first as 0.0 would blame the crawler's value for a broken config."""
    row = _plan(None, _cfg(article_pattern=r"/never-matches/"))[0]
    assert row["candidates"] == 0 and row["marginalValue"] is None


def test_shadow_summary_aggregates_across_publishers_without_averaging_averages():
    """Totals are computed from the raw counts, not by averaging per-publisher ratios — otherwise a
    publisher with 3 candidates would weigh as much as one with 300."""
    rows = [{"publisher": "A", "candidates": 100, "already_in_catalog": 90, "genuinelyNew": 10,
             "marginalValue": 0.1, "rung_used": "rss", "fetched": 1, "error": None},
            {"publisher": "B", "candidates": 4, "already_in_catalog": 0, "genuinelyNew": 4,
             "marginalValue": 1.0, "rung_used": "sitemap", "fetched": 2, "error": None}]
    t = crawler.shadow_summary(rows)["totals"]
    assert t["candidates"] == 104 and t["genuinelyNew"] == 14
    assert t["marginalValue"] == round(14 / 104, 3) == 0.135
    assert t["marginalValue"] < 0.55, "a naive mean of 0.1 and 1.0 would report 55%"


def test_shadow_summary_carries_skipped_publishers_through_rather_than_dropping_them():
    out = crawler.shadow_summary([{"publisher": "Off", "skipped": "disabled"}])
    assert out["publishers"][0]["skipped"] == "disabled"
    assert out["totals"]["candidates"] == 0 and out["totals"]["marginalValue"] is None


def test_dated_counts_candidates_carrying_a_publication_date():
    """The sitemap fixture states a date for both accepted articles."""
    row = _plan(None)[0]
    assert row["candidates"] == 2 and row["dated"] == 2


def test_a_section_page_yields_candidates_with_no_dates_at_all():
    """The measurement that makes `dated` worth having. A section index states no publication date,
    so everything it yields ingests with `published_at` of None — missing metadata, no position in
    Latest, nothing for clustering to order by. Two rungs can return the same COUNT of articles and
    be worth very different amounts, and `candidates` alone cannot tell them apart."""
    cfg = _cfg(sources=(crawler.DiscoverySource("section", "https://www.npr.org/sections/news/"),))
    row = _plan(None, cfg, {"https://www.npr.org/sections/news/": _fx("section.html")})[0]
    assert row["candidates"] == 2 and row["dated"] == 0
    assert crawler.shadow_summary([row])["publishers"][0]["datedShare"] == 0.0


def test_dated_is_a_subset_of_candidates_on_the_same_set():
    """Counted at the same point as `candidates` — before the catalog check and before max_urls
    truncates — so the two are directly comparable rather than two different denominators."""
    st = store_mod.Store("sqlite://")
    _seed(st, "https://www.npr.org/2026/08/11/1234567/senate-budget-vote")
    row = _plan(st, _cfg(max_urls=1))[0]
    assert row["dated"] <= row["candidates"] == 2
    assert row["dated"] == 2, "a known article still counts toward dated — same set as candidates"


def test_shadow_summary_reports_the_dated_share_per_publisher_and_in_total():
    rows = [{"publisher": "A", "candidates": 100, "dated": 100, "already_in_catalog": 0,
             "genuinelyNew": 100, "marginalValue": 1.0, "rung_used": "sitemap", "fetched": 1,
             "error": None},
            {"publisher": "B", "candidates": 100, "dated": 0, "already_in_catalog": 0,
             "genuinelyNew": 100, "marginalValue": 1.0, "rung_used": "section", "fetched": 1,
             "error": None}]
    out = crawler.shadow_summary(rows)
    assert [p["datedShare"] for p in out["publishers"]] == [1.0, 0.0]
    assert out["totals"]["dated"] == 100 and out["totals"]["datedShare"] == 0.5, (
        "two publishers with identical volume are NOT worth the same, and the total says so")


# --------------------------------------------------------------------------- #
# max_age_days — an article older than the clustering window can never become product
# --------------------------------------------------------------------------- #
_NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _aged_sitemap(*dates) -> str:
    """A sitemap whose entries carry the given `published_at` values (None = no date at all)."""
    urls = []
    for i, d in enumerate(dates):
        news = (f"<news:news><news:publication_date>{d}</news:publication_date>"
                f"<news:title>T{i}</news:title></news:news>") if d else ""
        urls.append(f"<url><loc>https://www.npr.org/2026/08/20/{i}00000/story-{i}</loc>{news}</url>")
    return ("<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9' "
            "xmlns:news='http://www.google.com/schemas/sitemap-news/0.9'>"
            + "".join(urls) + "</urlset>")


def _aged_plan(body, *, max_age_days):
    cfg = _cfg(max_age_days=max_age_days)
    c = crawler.PublisherCrawler(
        cfg, robots=_policy(ALLOW), limiter=_no_wait(),
        fetch=_fetcher({"https://www.npr.org/s.xml": body}), now=lambda: _NOW)
    return c.crawl()


def test_a_recent_article_passes_the_age_filter():
    _entries, r = _aged_plan(_aged_sitemap("2026-08-19T09:00:00Z"), max_age_days=7)
    assert r.candidates == 1 and r.too_old == 0 and r.undated == 0


def test_an_article_older_than_the_limit_is_excluded_and_counted():
    """The case that motivated this: SCMP's declared sitemap returned 19,962 URLs spanning years,
    and story clustering only reaches back 6 days — so an old article ingests, takes a row, and can
    never appear in a story."""
    _entries, r = _aged_plan(_aged_sitemap("2026-01-05T09:00:00Z"), max_age_days=7)
    assert r.candidates == 0 and r.too_old == 1 and r.undated == 0


def test_an_undated_article_is_excluded_when_an_age_limit_is_set():
    """Fail closed, the same reading the robots gate takes. An operator who set an age limit asked
    for recent articles, and "we cannot tell how old this is" does not answer that. Without this,
    pointing a limited publisher at a section page would silently readmit the whole undated archive
    the limit exists to keep out."""
    _entries, r = _aged_plan(_aged_sitemap(None), max_age_days=7)
    assert r.candidates == 0 and r.undated == 1 and r.too_old == 0


def test_an_undated_article_passes_when_no_age_limit_is_set():
    """Absence of a limit means the operator did not ask about age, so nothing is judged on it —
    the rule must not leak into publishers that never opted into it."""
    _entries, r = _aged_plan(_aged_sitemap(None), max_age_days=0)
    assert r.candidates == 1 and r.undated == 0 and r.too_old == 0


def test_old_and_undated_are_counted_separately_because_they_mean_opposite_things():
    """A pile of `too_old` says the sitemap is an archive; a pile of `undated` says the rung is a
    section page. Collapsing them would hide which fix is needed."""
    body = _aged_sitemap("2026-08-19T09:00:00Z", "2026-01-05T09:00:00Z", None, None)
    _entries, r = _aged_plan(body, max_age_days=7)
    assert (r.candidates, r.too_old, r.undated) == (1, 1, 2)


def test_an_unparseable_date_is_treated_as_undated_not_as_recent():
    """A date we cannot read is not evidence of recency, so it takes the undated path rather than
    being waved through."""
    _entries, r = _aged_plan(_aged_sitemap("not-a-date"), max_age_days=7)
    assert r.candidates == 0 and r.undated == 1


def test_the_boundary_is_inclusive_of_exactly_the_limit():
    just_inside = (_NOW - timedelta(days=7) + timedelta(minutes=1)).isoformat()
    just_outside = (_NOW - timedelta(days=7) - timedelta(minutes=1)).isoformat()
    _e, inside = _aged_plan(_aged_sitemap(just_inside), max_age_days=7)
    _e, outside = _aged_plan(_aged_sitemap(just_outside), max_age_days=7)
    assert inside.candidates == 1 and outside.too_old == 1


def test_age_drops_keep_the_report_accounting_for_every_discovered_url():
    body = _aged_sitemap("2026-08-19T09:00:00Z", "2026-01-05T09:00:00Z", None)
    _entries, r = _aged_plan(body, max_age_days=7)
    assert (r.accepted + r.off_domain + r.pattern_rejected + r.duplicate_in_cycle
            + r.already_in_catalog + r.capped + r.too_old + r.undated) == r.discovered


def test_the_summary_reports_age_drops_per_publisher_and_in_total():
    body = _aged_sitemap("2026-08-19T09:00:00Z", "2026-01-05T09:00:00Z", None)
    cfg = _cfg(max_age_days=7)
    rows = crawler.plan([cfg], robots=_policy(ALLOW), limiter=_no_wait(),
                        fetch=_fetcher({"https://www.npr.org/s.xml": body}), now=lambda: _NOW)
    out = crawler.shadow_summary(rows)
    p = out["publishers"][0]
    # three entries: one recent, one older than the limit, one with no date at all
    assert (p["candidates"], p["tooOld"], p["undated"], p["filteredByAge"]) == (1, 1, 1, 2)
    assert out["totals"]["filteredByAge"] == 2 and out["totals"]["tooOld"] == 1


def test_an_archive_sitemap_is_named_as_the_reason_a_publisher_went_empty():
    body = _aged_sitemap("2020-01-05T09:00:00Z", "2019-01-05T09:00:00Z")
    cfg = _cfg(max_age_days=7)
    rows = crawler.plan([cfg], robots=_policy(ALLOW), limiter=_no_wait(),
                        fetch=_fetcher({"https://www.npr.org/s.xml": body}))
    note = crawler.shadow_summary(rows)["publishers"][0]["note"]
    assert "older than" in note and "archive sitemap" in note


def test_a_section_page_under_an_age_limit_says_to_use_a_news_sitemap():
    cfg = _cfg(sources=(crawler.DiscoverySource("section", "https://www.npr.org/sections/news/"),),
               max_age_days=7)
    rows = crawler.plan([cfg], robots=_policy(ALLOW), limiter=_no_wait(),
                        fetch=_fetcher({"https://www.npr.org/sections/news/": _fx("section.html")}))
    note = crawler.shadow_summary(rows)["publishers"][0]["note"]
    assert "no publication date" in note and "news sitemap" in note


def test_an_empty_publisher_says_which_gate_closed():
    """`0 candidates` with no reason is undiagnosable without re-running against the publisher —
    the one thing this framework exists to avoid doing casually."""
    cfg = _cfg(article_pattern=r"/never-matches/")
    note = crawler.shadow_summary(_plan(None, cfg))["publishers"][0]["note"]
    assert "article_pattern" in note and "ingest nothing" in note


def test_a_robots_refusal_is_named_as_the_reason_not_left_blank():
    rows = crawler.plan([_cfg()],
                        robots=_policy({"https://www.npr.org/robots.txt": _fx("robots_deny.txt")}),
                        limiter=_no_wait(),
                        fetch=_fetcher({"https://www.npr.org/s.xml": _fx("news_sitemap.xml")}))
    note = crawler.shadow_summary(rows)["publishers"][0]["note"]
    assert "robots refused" in note


def test_a_healthy_publisher_carries_no_note():
    assert crawler.shadow_summary(_plan(None))["publishers"][0]["note"] is None


def test_shadow_planning_still_writes_nothing_to_the_catalog():
    st = store_mod.Store("sqlite://")
    _seed(st, "https://www.npr.org/2026/08/11/1234567/senate-budget-vote")
    before = st.count_feed_articles()
    _plan(st)
    assert st.count_feed_articles() == before == 1


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
#: Lint codes that are the EXPECTED state for a shadow-lane candidate rather than a defect.
#: `unknown_publisher` is why the outlet is in shadow at all — it is unrated, so M8 measures it for
#: 14 days before M9 proposes anything. `no_article_pattern` is a stated, bounded position: an
#: INVENTED pattern is worse than none (a pattern matching 0% of discovered URLs makes the crawler
#: ingest nothing while every gate reports healthy), and a news sitemap contains articles by
#: specification.
_SHADOW_EXPECTED = {"unknown_publisher", "no_article_pattern"}


def _shadow_bound(cfg) -> bool:
    import corpus
    host = cfg.domains[0] if cfg.domains else cfg.publisher
    return corpus.is_shadow(cfg.publisher, f"https://{host}/")


def test_the_shipped_config_is_clean(monkeypatch):
    """Clean for registry publishers; for shadow-bound ones, only the two expected codes.

    Amended when `CrawlAdapter` was wired into the poller. The original bar assumed every configured
    publisher was hand-picked and registry-known. M7 discovers UNRATED outlets and routes them to
    shadow, which is the whole point of the lane — so "no lint output at all" would now forbid the
    use case the milestone exists for."""
    monkeypatch.setenv("RWE_CORPUS_SHADOW", "kait8.com,kwch.com")
    import corpus
    corpus._index.cache_clear()
    try:
        for problem in crawler.lint_config(crawler.load_config()):
            cfg = next(c for c in crawler.load_config() if c.publisher == problem["publisher"])
            assert _shadow_bound(cfg) and problem["code"] in _SHADOW_EXPECTED, problem
    finally:
        corpus._index.cache_clear()


def test_every_configured_publisher_resolves_to_a_registry_canonical_name(monkeypatch):
    """Resolves to a registry canonical name **or is bound for the shadow lane.**

    The original reasoning — "a name the registry does not know ingests with NO lean, adding volume
    the product cannot describe" — is true for Tier A and **false for shadow**: a shadow article
    reaches no reader surface and no story (`corpus.sql_exclusions` covers shadow, and
    `store.search_feed_articles` excludes it by default), so it describes nothing to anyone. It is
    evidence for M8 and nothing else.

    The protection is not weakened, it is relocated: `CrawlAdapter.enabled()` now REFUSES to run a
    publisher that is not in `RWE_CORPUS_SHADOW`, so an unrated source cannot reach Tier A by
    construction rather than by this test's vigilance."""
    import corpus
    import outlet_registry
    monkeypatch.setenv("RWE_CORPUS_SHADOW", "kait8.com,kwch.com")
    corpus._index.cache_clear()
    reg = outlet_registry.default_registry()
    try:
        for c in crawler.load_config():
            assert reg.canonical(c.publisher) == c.publisher or _shadow_bound(c), c.publisher
    finally:
        corpus._index.cache_clear()


def test_the_poc_covers_at_least_three_publishers_with_more_than_one_discovery_shape():
    configs = crawler.load_config()
    assert len(configs) >= 3
    assert len({tuple(s.kind for s in c.sources) for c in configs}) > 1


#: URL shapes observed on the LIVE sites by verify_crawler_config.py (2026-08-15). These are real
#: evidence, not invented examples, and they exist because BBC's configured pattern matched 3% of
#: what BBC actually publishes — the crawler's quietest failure mode, since every gate reports
#: healthy while it ingests nothing.
_LIVE_URL_SHAPES = {
    "BBC": (
        # accept: the current scheme, and the legacy numeric one that is still live
        ["https://www.bbc.co.uk/news/articles/c9982znvyk4o?at_medium=RSS&at_campaign=rss",
         "https://www.bbc.co.uk/news/articles/cr59jg1ypd3o",
         "https://www.bbc.co.uk/news/world-europe-12345678"],
        # reject: section indexes and chrome the section page links to
        ["https://www.bbc.co.uk/news", "https://www.bbc.co.uk/news/world",
         "https://www.bbc.co.uk/sport", "https://www.bbc.co.uk/news/articles/"],
    ),
    "Associated Press": (
        ["https://apnews.com/article/senate-budget-vote-a1b2c3"],
        ["https://apnews.com/newsletter/morning-wire/august-14-2026",
         "https://apnews.com/photo-gallery/ugliest-dog-contest-photos-e5110fb3781f",
         "https://apnews.com/hub/world-news"],
    ),
    "The Guardian": (
        ["https://www.theguardian.com/world/2026/aug/15/some-headline-here"],
        ["https://www.theguardian.com/world#maincontent",
         "https://www.theguardian.com/email-newsletters",
         "https://profile.theguardian.com/signin?INTCMP=x"],
    ),
    # HuffPost is UNVERIFIED against the live site — these shapes are convention, not observation,
    # so this case pins intent rather than evidence. It still earns its place: it fails loudly if
    # someone later widens the pattern into section or author pages.
    "HuffPost": (
        ["https://www.huffpost.com/entry/some-story-slug_n_1234abcd",
         "https://www.huffingtonpost.com/entry/legacy-story-slug_n_9876"],
        ["https://www.huffpost.com/news", "https://www.huffpost.com/entry/",
         "https://www.huffpost.com/section/politics",
         "https://www.huffpost.com/author/jane-doe"],
    ),
}


@pytest.mark.parametrize("publisher", sorted(_LIVE_URL_SHAPES))
def test_the_shipped_pattern_matches_what_the_publisher_actually_publishes(publisher):
    cfg = next(c for c in crawler.load_config() if c.publisher == publisher)
    accept, reject = _LIVE_URL_SHAPES[publisher]
    for url in accept:
        assert cfg.pattern.search(url), f"{publisher}: should accept {url}"
    for url in reject:
        assert not cfg.pattern.search(url), f"{publisher}: should reject {url}"


def test_bbc_accepts_the_current_article_scheme_not_only_the_legacy_one():
    """The specific regression. `/news/[a-z-]*-?\\d{6,}` assumed `/news/world-europe-12345678`;
    BBC now publishes `/news/articles/c9982znvyk4o`, which contains no run of digits at all — so
    the old pattern rejected 97% of the newsroom while every gate reported healthy."""
    cfg = next(c for c in crawler.load_config() if c.publisher == "BBC")
    assert cfg.pattern.search("https://www.bbc.co.uk/news/articles/c9982znvyk4o")
    assert not re.search(r"/news/[a-z-]*-?\d{6,}", "https://www.bbc.co.uk/news/articles/c9982znvyk4o"), (
        "the old pattern really did miss this — the test is not asserting a tautology")


_SHADOW_CONFIGS = ("crawler_publishers_experiment.json", "crawler_publishers_round2.json")


@pytest.mark.parametrize("name", _SHADOW_CONFIGS)
def test_a_shadow_config_is_loadable_and_lint_clean(name):
    """The shadow sets are not shipped config — nothing loads them without --config — but they are
    committed, so a broken entry would sit there silently until someone ran the experiment and lost
    an hour to it.

    Checked for the same things as the real config, and NOT for a publisher count: pinning the
    number would fail on the next addition while telling you nothing about validity.
    """
    configs = crawler.load_config(str(ROOT / "examples" / "data" / name))
    assert configs and crawler.lint_config(configs) == []


@pytest.mark.parametrize("name", _SHADOW_CONFIGS)
def test_every_shadow_publisher_is_outside_our_configured_rss_feeds(name):
    """The experiment's premise is publishers we do NOT already cover. An outlet already arriving
    by RSS would report a low marginal value that says something about our feed list rather than
    about the crawler, which is the confound this whole exercise exists to avoid."""
    import outlet_registry
    reg = outlet_registry.default_registry()
    feeds = (ROOT / "deploy" / "rss_feeds.example.txt").read_text(encoding="utf-8")
    covered = set()
    for line in feeds.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        pub = line.split("|", 1)[0] if "|" in line else line
        canon = reg.canonical(pub.strip())
        if canon:
            covered.add(canon)
    overlap = {c.publisher for c in crawler.load_config(str(ROOT / "examples" / "data" / name))} & covered
    assert not overlap, f"already covered by RSS: {sorted(overlap)}"


#: Publishers dropped from round 2 after the live round-1 verification, and the reason each was
#: dropped. These are decisions, not data — so they are pinned here rather than left in a comment
#: where re-adding one would look like an ordinary config edit.
_ROUND2_EXCLUDED = {
    "Le Monde": "blocks ClaudeBot + anthropic-ai",
    "Frankfurter Allgemeine Zeitung": "blocks ClaudeBot + anthropic-ai",
    "El País": "blocks ClaudeBot",
    "Corriere della Sera": "blocks ClaudeBot + anthropic-ai",
    "NZZ": "blocks ClaudeBot + anthropic-ai",
    "Financial Times": "blocks ClaudeBot + anthropic-ai, and answers 403",
    "Al Jazeera": "blocks ClaudeBot + anthropic-ai",
    "ABC Australia": "blocks ClaudeBot + anthropic-ai",
    "The Times of Israel": "allows in robots.txt then answers 403 — the enforcing mechanism said no",
    "CBC News": "robots.txt unreadable (timeout); the gate fails closed",
}


def test_round_two_excludes_every_publisher_that_said_no():
    """The editorial core of round 2. Each of these either published a position on AI crawlers or
    refused us at the HTTP layer, and none of them is on our user-agent block list by name — which
    is exactly why the exclusion has to be recorded somewhere that fails loudly. A future edit that
    re-adds one should have to delete a line that states the reason."""
    path = ROOT / "examples" / "data" / "crawler_publishers_round2.json"
    present = {c.publisher for c in crawler.load_config(str(path))}
    readded = present & set(_ROUND2_EXCLUDED)
    assert not readded, "re-added despite saying no: " + ", ".join(
        f"{p} ({_ROUND2_EXCLUDED[p]})" for p in sorted(readded))


def test_round_two_leads_with_a_sitemap_wherever_the_publisher_declares_one():
    """The whole point of round 2: round 1 returned 810 candidates with ZERO dates because every
    rung used was a section page. A publisher with a sitemap must try it FIRST, or the dated share
    stays at nothing."""
    path = ROOT / "examples" / "data" / "crawler_publishers_round2.json"
    for c in crawler.load_config(str(path)):
        kinds = [s.kind for s in c.sources]
        if "sitemap" in kinds:
            assert kinds[0] == "sitemap", f"{c.publisher}: section would win and yield no dates"


def test_a_typo_in_the_config_is_rejected_rather_than_silently_defaulted(tmp_path):
    """`max_url` taking the default of `max_urls` is how a crawler ends up looking healthy while
    doing the wrong thing."""
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"publishers": [{"publisher": "NPR", "max_url": 5}]}))
    with pytest.raises(ValueError, match="unknown key"):
        crawler.load_config(str(p))


def test_lint_catches_the_configuration_mistakes_that_matter():
    bad = crawler.PublisherCrawlConfig(publisher="Nowhere Gazette", domains=(), sources=(),
                                       article_pattern="", min_interval=0.1)
    codes = {p["code"] for p in crawler.lint_config([bad])}
    assert {"unknown_publisher", "no_domains", "no_sources",
            "no_article_pattern", "interval_too_low"} <= codes


def test_a_discovery_host_off_every_declared_domain_is_flagged():
    cfg = crawler.PublisherCrawlConfig(
        publisher="NPR", domains=("npr.org",),
        sources=(crawler.DiscoverySource("rss", "https://feeds.example.com/npr.xml"),),
        article_pattern=r"/\d{4}/")
    assert any(p["code"] == "source_off_domain" for p in crawler.lint_config([cfg]))


def test_a_feed_host_may_be_declared_without_widening_where_articles_may_live():
    """The BBC serves feeds from `bbci.co.uk` and journalism from `bbc.co.uk`. Folding the feed
    host into `domains` to make the config validate would let any `bbci.co.uk` URL be ingested as
    BBC journalism — so the two lists stay separate."""
    cfg = crawler.PublisherCrawlConfig(
        publisher="BBC", domains=("bbc.co.uk",), discovery_domains=("bbci.co.uk",),
        sources=(crawler.DiscoverySource("rss", "https://feeds.bbci.co.uk/news/rss.xml"),),
        article_pattern=r"/news/\d+")
    assert crawler.lint_config([cfg]) == []
    assert crawler._host_allowed("feeds.bbci.co.uk", cfg.domains) is False, (
        "declaring a fetchable host must not make it an article host")


def test_crawler_does_not_reach_into_the_recommendation_or_ui_layers():
    for banned in ("health_report", "rwe", "simulate_users", "personalize", "narrate_report",
                   "api_server", "story_service", "corpus_refresh"):
        assert not hasattr(crawler, banned), f"crawler must not import {banned}"
