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
    """`publisher_hint` is what resolves the outlet — and therefore the lean — downstream."""
    entries, _ = _crawl({"https://www.npr.org/s.xml": _fx("news_sitemap.xml")})
    assert entries and all(e.publisher_hint == "NPR" for e in entries)
    assert all(e.source_type == "crawl" for e in entries)


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
def test_the_shipped_config_is_clean():
    assert crawler.lint_config(crawler.load_config()) == []


def test_every_configured_publisher_resolves_to_a_registry_canonical_name():
    """A name the registry does not know ingests with NO lean — the crawler would be adding volume
    the product cannot describe."""
    import outlet_registry
    reg = outlet_registry.default_registry()
    for c in crawler.load_config():
        assert reg.canonical(c.publisher) == c.publisher, c.publisher


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
