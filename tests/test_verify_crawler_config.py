"""Tests for the pre-rollout crawl verification gate (verify_crawler_config.py).

The verifier's whole job is to be trusted about a publisher we cannot otherwise inspect, so the
behaviours pinned here are the ones where a verifier lies: reporting crawlable when robots.txt
could not be read, missing a pattern that matches nothing, or letting one dead URL end the run.

Offline — every fetch is injected. The script itself is meant to be run against live publishers
from an environment with real egress; these tests prove the logic it will apply there.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import crawler                      # noqa: E402
import verify_crawler_config as vc  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "crawler"


def _fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _cfg(**over):
    base = dict(publisher="NPR", domains=("npr.org",),
                sources=(crawler.DiscoverySource("sitemap", "https://www.npr.org/s.xml"),),
                article_pattern=r"/\d{4}/\d{2}/\d{2}/", min_interval=0.0)
    base.update(over)
    return crawler.PublisherCrawlConfig(**base)


def _install(monkeypatch, routes):
    """Point the verifier's single fetch seam at a ``{url: body|Exception}`` map."""
    def fake(url, timeout=20.0):
        if url not in routes:
            raise OSError(f"404 {url}")
        body = routes[url]
        if isinstance(body, Exception):
            raise body
        return body
    monkeypatch.setattr(crawler, "_fetch_text", fake)
    return fake


def _run(monkeypatch, routes, cfg=None, **kw):
    fake = _install(monkeypatch, routes)
    policy = crawler.RobotsPolicy(fetch=lambda u, **_k: fake(u))
    limiter = crawler.RateLimiter(0.0, sleep=lambda s: None, clock=lambda: 0.0)
    return vc.verify(cfg or _cfg(), policy=policy, limiter=limiter, skip_tos=kw.pop("skip_tos", True))


ROBOTS = "https://www.npr.org/robots.txt"
SITEMAP = "https://www.npr.org/s.xml"


# --------------------------------------------------------------------------- #
# The verdict must never be optimistic about what it could not check
# --------------------------------------------------------------------------- #
def test_an_unreachable_robots_txt_is_reported_as_not_crawlable():
    """The gate fails closed, so the verifier must too — anything else would green-light a rollout
    the crawler itself would then refuse to perform."""
    import pytest
    mp = pytest.MonkeyPatch()
    try:
        v = _run(mp, {ROBOTS: OSError("connection refused")})
    finally:
        mp.undo()
    assert v.crawlable is False
    assert any("fails closed" in b for b in v.blockers)


def test_a_200_that_is_not_a_policy_is_not_crawlable(monkeypatch):
    v = _run(monkeypatch, {ROBOTS: "<html>404</html>"})
    assert v.crawlable is False and "not a robots policy" in v.robots_status


def test_a_publisher_that_disallows_us_is_not_crawlable(monkeypatch):
    v = _run(monkeypatch, {ROBOTS: _fx("robots_deny.txt"), SITEMAP: _fx("news_sitemap.xml")})
    assert v.crawlable is False
    assert any("robots" in (r.get("reason") or "") for r in v.sources)


def test_a_healthy_publisher_verifies_crawlable(monkeypatch):
    v = _run(monkeypatch, {ROBOTS: _fx("robots_allow.txt"), SITEMAP: _fx("news_sitemap.xml")})
    assert v.crawlable is True and v.blockers == []
    assert v.sources[0]["ok"] is True and v.sources[0]["entries"] == 4


# --------------------------------------------------------------------------- #
# The checks that turn a failure into a specific correction
# --------------------------------------------------------------------------- #
def test_a_sitemap_declared_in_robots_is_reported_when_ours_is_broken(monkeypatch):
    """A configured sitemap that 404s usually means we guessed the path, not that they have none.
    Reporting what the publisher declares turns a red check into the URL to paste in."""
    robots = _fx("robots_allow.txt") + "\nSitemap: https://www.npr.org/sitemaps/news.xml\n"
    v = _run(monkeypatch, {ROBOTS: robots})       # SITEMAP itself is unmapped -> 404
    assert v.sitemaps_declared == ["https://www.npr.org/sitemaps/news.xml"]
    assert any("declares sitemaps we are not using" in c for c in v.corrections)


def test_a_pattern_that_matches_nothing_is_a_blocker_not_a_warning(monkeypatch):
    """This is the silent failure the whole verifier exists for: every gate reports healthy and the
    crawler ingests zero articles forever."""
    cfg = _cfg(article_pattern=r"/this-will-never-match/")
    v = _run(monkeypatch, {ROBOTS: _fx("robots_allow.txt"), SITEMAP: _fx("news_sitemap.xml")}, cfg)
    assert v.crawlable is False
    assert v.pattern_match_rate == 0.0
    assert any("matched 0 of" in b for b in v.blockers)


def test_the_pattern_match_rate_is_measured_against_real_discovered_urls(monkeypatch):
    """The fixture yields 3 on-domain URLs, 2 of which are articles. Both halves are reported:
    the misses say what the pattern excludes, the hits are the evidence an enabled config must
    carry (recorded verbatim in test_crawl_adapter_wiring.py) — a report with misses only left
    nothing to record for AP and CNN on 2026-09-02."""
    v = _run(monkeypatch, {ROBOTS: _fx("robots_allow.txt"), SITEMAP: _fx("news_sitemap.xml")})
    assert v.pattern_match_rate == round(2 / 3, 3)
    assert v.pattern_sample_misses == ["https://www.npr.org/sections/politics/"]
    assert len(v.pattern_sample_hits) == 2
    assert all("npr.org/2026/" in u for u in v.pattern_sample_hits)
    assert "hit:  " in vc._render([v]) and "miss: " in vc._render([v])


def test_a_publishers_crawl_delay_is_compared_against_our_configured_interval(monkeypatch):
    """The fixture states `Crawl-delay: 4` and the config says 0 — the config is understating what
    we owe them, and the report says so rather than silently honouring it at runtime."""
    v = _run(monkeypatch, {ROBOTS: _fx("robots_allow.txt"), SITEMAP: _fx("news_sitemap.xml")})
    assert v.crawl_delay == 4.0
    assert any("Crawl-delay 4.0s" in c for c in v.corrections)


def test_a_url_that_parses_to_zero_entries_is_called_out_as_the_wrong_document(monkeypatch):
    v = _run(monkeypatch, {ROBOTS: _fx("robots_allow.txt"),
                           SITEMAP: "<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'/>"})
    assert v.crawlable is False
    assert any("0 entries" in c for c in v.corrections)


def test_named_ai_crawler_blocks_are_reported_as_posture_not_as_our_verdict(monkeypatch):
    """`User-agent: GPTBot / Disallow: /` does not bind our UA, but a publisher who wrote it has a
    stated position on automated ingestion — which is context for the ToS review, not a rule we
    can quietly route around."""
    robots = _fx("robots_allow.txt") + "\nUser-agent: GPTBot\nDisallow: /\nUser-agent: CCBot\nDisallow: /\n"
    v = _run(monkeypatch, {ROBOTS: robots, SITEMAP: _fx("news_sitemap.xml")})
    assert v.ai_agents_blocked == ["CCBot", "GPTBot"]
    assert v.crawlable is True, "their AI-bot policy does not itself disallow our user-agent"
    assert any("stated posture" in c for c in v.corrections)


def test_one_dead_url_does_not_end_the_run(monkeypatch):
    cfg = _cfg(sources=(crawler.DiscoverySource("rss", "https://www.npr.org/dead.xml"),
                        crawler.DiscoverySource("sitemap", SITEMAP)))
    v = _run(monkeypatch, {ROBOTS: _fx("robots_allow.txt"), SITEMAP: _fx("news_sitemap.xml")}, cfg)
    assert len(v.sources) == 2
    assert v.sources[0]["ok"] is False and v.sources[1]["ok"] is True
    assert v.crawlable is True, "one broken rung is a correction, not a blocker"


def test_the_robots_host_comes_from_a_configured_url_not_a_guessed_www_prefix(monkeypatch):
    """AP serves on the bare `apnews.com`. Probing `www.` + domain reported an unreachable
    robots.txt and a false NOT-CRAWLABLE for a publisher that was fine — a verifier whose failures
    are its own is worse than no verifier."""
    cfg = _cfg(publisher="Associated Press", domains=("apnews.com",),
               sources=(crawler.DiscoverySource("sitemap", "https://apnews.com/news-sitemap.xml"),),
               article_pattern=r"/article/")
    routes = {"https://apnews.com/robots.txt": _fx("robots_allow.txt"),
              "https://apnews.com/news-sitemap.xml":
                  "<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                  "<url><loc>https://apnews.com/article/x-1</loc></url></urlset>"}
    v = _run(monkeypatch, routes, cfg)
    assert v.robots_url == "https://apnews.com/robots.txt"
    assert v.crawlable is True


def test_a_feed_subdomain_is_not_mistaken_for_the_newsroom(monkeypatch):
    """`feeds.npr.org` IS a subdomain of the article domain, so a naive "first matching host" picks
    it — and reports a feed endpoint's robots.txt as if it governed the newsroom. The apex/www host
    wins."""
    assert vc._primary_host(["feeds.npr.org", "www.npr.org"], ("npr.org",)) == "www.npr.org"
    assert vc._primary_host(["feeds.npr.org"], ("npr.org",)) == "feeds.npr.org", (
        "with nothing better available, say which host we actually checked")
    assert vc._primary_host(["apnews.com"], ("apnews.com",)) == "apnews.com"


def test_a_feed_host_is_not_used_to_locate_robots_when_an_article_host_exists(monkeypatch):
    """BBC's feeds live on `bbci.co.uk` and its journalism on `bbc.co.uk`. The robots policy that
    governs whether we may CRAWL BBC is the one on the article host."""
    cfg = _cfg(publisher="BBC", domains=("bbc.co.uk",), discovery_domains=("bbci.co.uk",),
               sources=(crawler.DiscoverySource("rss", "https://feeds.bbci.co.uk/news/rss.xml"),
                        crawler.DiscoverySource("section", "https://www.bbc.co.uk/news")),
               article_pattern=r"/news/[a-z-]*-?\d{6,}")
    routes = {"https://www.bbc.co.uk/robots.txt": _fx("robots_allow.txt"),
              "https://feeds.bbci.co.uk/robots.txt": _fx("robots_allow.txt"),
              "https://feeds.bbci.co.uk/news/rss.xml": _fx("feed.xml")}
    v = _run(monkeypatch, routes, cfg)
    assert v.robots_url == "https://www.bbc.co.uk/robots.txt"


# --------------------------------------------------------------------------- #
# ToS surfacing — locates clauses, decides nothing
# --------------------------------------------------------------------------- #
def test_tos_clauses_about_automated_access_are_surfaced_for_a_human(monkeypatch):
    tos = ("<html><body><h1>Terms</h1><p>" + "Padding sentence to clear the length floor. " * 20 +
           "You may not use any robot, spider, or other automated means to access the Service. "
           "Systematic extraction of content for commercial use is prohibited.</p></body></html>")
    routes = {ROBOTS: _fx("robots_allow.txt"), SITEMAP: _fx("news_sitemap.xml"),
              "https://www.npr.org/terms": tos}
    v = _run(monkeypatch, routes, skip_tos=False)
    assert v.tos_url == "https://www.npr.org/terms"
    assert any("automated means" in c for c in v.tos_clauses)


def test_a_tos_clause_never_flips_the_verdict_by_itself(monkeypatch):
    """A regex has no opinion about contract law. Surfacing a clause is a prompt for a human, and
    treating it as a verdict would be exactly the overreach this tool must not commit."""
    tos = ("<html><body><p>" + "Filler to clear the floor. " * 20 +
           "You may not use any robot or spider to access the Service.</p></body></html>")
    routes = {ROBOTS: _fx("robots_allow.txt"), SITEMAP: _fx("news_sitemap.xml"),
              "https://www.npr.org/terms": tos}
    v = _run(monkeypatch, routes, skip_tos=False)
    assert v.tos_clauses and v.crawlable is True


def test_a_missing_tos_is_a_correction_not_a_silent_pass(monkeypatch):
    v = _run(monkeypatch, {ROBOTS: _fx("robots_allow.txt"), SITEMAP: _fx("news_sitemap.xml")},
             skip_tos=False)
    assert any("ToS" in c for c in v.corrections)


# --------------------------------------------------------------------------- #
# The verifier is read-only and reuses the crawler's real code
# --------------------------------------------------------------------------- #
def test_the_verifier_never_fetches_an_article(monkeypatch):
    """It fetches robots, the configured discovery documents, and ToS — nothing else. An article
    fetch here would be the very behaviour the framework refuses to do."""
    seen = []
    routes = {ROBOTS: _fx("robots_allow.txt"), SITEMAP: _fx("news_sitemap.xml")}

    def fake(url, timeout=20.0):
        seen.append(url)
        if url not in routes:
            raise OSError("404")
        return routes[url]

    monkeypatch.setattr(crawler, "_fetch_text", fake)
    vc.verify(_cfg(), policy=crawler.RobotsPolicy(fetch=lambda u, **_k: fake(u)),
              limiter=crawler.RateLimiter(0.0, sleep=lambda s: None, clock=lambda: 0.0),
              skip_tos=True)
    assert all(u in (ROBOTS, SITEMAP) for u in seen), seen
    assert not any("/2026/" in u for u in seen), "an article URL was fetched"


def test_the_verifier_uses_the_crawlers_own_parsers_not_a_second_implementation():
    """If the verifier parsed sitemaps its own way, a green report would only prove two
    implementations agree with each other — not that the crawler works."""
    assert vc.crawler._DISCOVERY is crawler._DISCOVERY
    assert vc.crawler.RobotsPolicy is crawler.RobotsPolicy


def test_verify_touches_no_store_or_ingest_symbols():
    for banned in ("store", "ingest_entries", "rss_ingest", "sources"):
        assert not hasattr(vc, banned), f"verifier must not reach {banned}"


def test_a_named_publisher_is_verified_whatever_its_switch_says_but_a_sweep_verifies_what_runs(monkeypatch, capsys):
    """Naming a publisher is how it EARNS the evidence that flips `enabled`, so the switch cannot
    be a precondition for verifying it — production 2026-09-02 reported "0/0 verified" for the
    three configs waiting on exactly this run. A sweep still covers only what runs.

    Mutation check: restoring `if c.enabled` on the verify line fails the first block (0
    verdicts for the disabled name); dropping the sweep filter fails the second."""
    import crawler

    class _Verdict:
        def __init__(self, name):
            self.publisher, self.crawlable = name, True

        def as_dict(self):
            return {"publisher": self.publisher, "crawlable": True}

    off = crawler.PublisherCrawlConfig(publisher="Reuters", domains=("reuters.com",),
                                       sources=(crawler.DiscoverySource("sitemap", "https://www.reuters.com/s.xml"),),
                                       enabled=False)
    on = crawler.PublisherCrawlConfig(publisher="KAIT", domains=("kait8.com",),
                                      sources=(crawler.DiscoverySource("sitemap", "https://www.kait8.com/s.xml"),),
                                      enabled=True)
    monkeypatch.setattr(crawler, "load_config", lambda path=None, **kw: [off, on])
    monkeypatch.setattr(vc, "verify", lambda c, skip_tos=False: _Verdict(c.publisher))

    assert vc.main(["--publisher", "Reuters", "--json"]) == 0
    assert [v["publisher"] for v in json.loads(capsys.readouterr().out)] == ["Reuters"]

    assert vc.main(["--json"]) == 0
    assert [v["publisher"] for v in json.loads(capsys.readouterr().out)] == ["KAIT"]


def test_the_shipped_config_can_be_verified_at_all():
    """Every enabled publisher must be loadable and lint-clean; the live answers come from running
    this against the real sites.

    Deliberately no count assertion. Pinning `len(configs) == 5` made adding a publisher fail a
    test about verifiability, which says nothing about whether the config is verifiable — the
    failure carries no information beyond "the number changed".
    """
    configs = [c for c in crawler.load_config() if c.enabled]
    assert configs, "an empty config would make every other check here vacuous"
    assert all(c.sources and c.domains for c in configs)
    # `article_pattern` is required where discovery is an HTML index — a section page links to tags,
    # authors and the shop. A NEWS SITEMAP contains articles by specification, so a pattern there is
    # optional, and an invented one is worse than none: `CRAWLER_DESIGN.md`'s sharpest warning is
    # that a pattern matching 0% of discovered URLs makes the crawler ingest nothing while every
    # gate reports healthy.
    for c in configs:
        if any(s.kind == "section" for s in c.sources):
            assert c.article_pattern, f"{c.publisher}: section discovery needs a pattern"
