"""Wiring `CrawlAdapter` into the poller — the first change that lets crawled content reach the
production catalog.

Everything before this was read-only probing. This crosses into ingestion, so what these tests pin
is the set of things that must be **impossible to get wrong**, not merely documented:

1. **Off by default.** Deploying the wiring changes nothing until an operator says so.
2. **A crawled source must be in the shadow lane.** `corpus.DEFAULT_TIER` is `"A"`, so an outlet
   nobody put in `RWE_CORPUS_SHADOW` does not land somewhere neutral — its articles go straight into
   the clustering corpus and start voting in stories. That is promotion by omission.
3. **The six publishers that shipped in the config are unverified and must not run.**
4. **No article bodies.** The crawler fetches discovery documents, never an article page.
5. **A broken crawl config must not take the RSS poller down** — a supplement that can break the
   thing it supplements is worse than one that is absent.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import corpus       # noqa: E402
import crawler      # noqa: E402
import sources      # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("RWE_CRAWL_ENABLED", "RWE_CORPUS_SHADOW", "RWE_CORPUS_TIER_B"):
        monkeypatch.delenv(k, raising=False)
    corpus._index.cache_clear()
    yield
    corpus._index.cache_clear()


def _adapters():
    return [crawler.CrawlAdapter(c) for c in crawler.load_config()]


# --------------------------------------------------------------------------- off by default

def test_registering_the_adapter_changes_nothing_until_the_flag_is_set():
    """The keyed adapters need an API key to do anything — an accidental safety catch this has no
    equivalent of, because a crawl config is just a file. So the flag is the catch, and it defaults
    to off."""
    reg = sources.default_registry()
    assert [a for a in reg.adapters() if getattr(a, "source_type", "") == "crawl"] == []


def test_the_flag_alone_does_not_start_crawling(monkeypatch):
    """**The trap this wiring nearly shipped.** With the flag on and the config as it shipped, six
    adapters registered and all six were enabled — the BBC, NPR, AP and the Guardian, on paths
    `CRAWLER_DESIGN.md` calls unverified guesses. The flag gates *registration*; shadow membership
    gates *running*, and the config's own `enabled` gates the unverified ones."""
    monkeypatch.setenv("RWE_CRAWL_ENABLED", "1")
    reg = sources.default_registry()
    crawl = [a for a in reg.adapters() if getattr(a, "source_type", "") == "crawl"]
    assert crawl, "the flag should register them"
    assert [a.provider for a in crawl if a.enabled()] == [], "none may RUN without shadow"


# --------------------------------------------------------------------------- the shadow precondition

def test_a_crawled_source_must_be_in_the_shadow_lane(monkeypatch):
    """`corpus.DEFAULT_TIER` is "A". An outlet nobody put in RWE_CORPUS_SHADOW does not land
    somewhere neutral — its articles go straight into the clustering corpus and start forming and
    voting in stories. Enforced rather than documented, because it is the one failure this change
    could cause that nobody would notice until a crawled outlet turned up in a blindspot claim."""
    monkeypatch.setenv("RWE_CRAWL_ENABLED", "1")
    assert [a.provider for a in _adapters() if a.enabled()] == []

    monkeypatch.setenv("RWE_CORPUS_SHADOW", "kait8.com,kwch.com")
    corpus._index.cache_clear()
    assert [a.provider for a in _adapters() if a.enabled()] == ["kait8.com", "kwch.com"]


def test_the_default_tier_really_is_A_so_the_precondition_is_load_bearing():
    """If the default were anything else this guard would be theatre. It is not."""
    assert corpus.DEFAULT_TIER == "A"


def test_a_configured_but_unshadowed_publisher_says_why(monkeypatch):
    """Silently not running is indistinguishable from a broken config. The reason names the fix."""
    monkeypatch.setenv("RWE_CRAWL_ENABLED", "1")
    warnings = [a.shadow_warning() for a in _adapters()]
    named = [w for w in warnings if w and "kait8.com" in w]
    assert named and "RWE_CORPUS_SHADOW" in named[0] and "promotion by omission" in named[0]


def test_no_warning_when_the_flag_is_off():
    """A publisher that is not crawling because crawling is off is not a misconfiguration."""
    assert [a.shadow_warning() for a in _adapters()] == [None] * len(_adapters())


# --------------------------------------------------------------------------- the shipped config

def test_the_six_unverified_publishers_are_disabled():
    """`verify_crawler_config.py` has never been run against them and `CRAWLER_DESIGN.md` calls
    their URLs and patterns unverified guesses. They were harmless while nothing registered the
    adapter; the moment it registers, `enabled: true` is live."""
    cfg = json.loads((ROOT / "examples" / "data" / "crawler_publishers.json").read_text())
    unverified = {"BBC", "NPR", "The Guardian", "Associated Press", "HuffPost", "Texas Tribune"}
    for pub in cfg["publishers"]:
        if pub["publisher"] in unverified:
            assert pub["enabled"] is False, f"{pub['publisher']} was never verified"


def test_the_enabled_publishers_are_the_ones_the_live_probe_verified():
    """kait8.com and kwch.com, 2026-08-26: robots.txt allows HiddenView-Crawler, the declared
    news-sitemap-index parses and descends, 32 and 36 items at 100% dated and 100% on-host."""
    cfg = json.loads((ROOT / "examples" / "data" / "crawler_publishers.json").read_text())
    assert {p["publisher"] for p in cfg["publishers"] if p["enabled"]} == {"kait8.com", "kwch.com"}


def test_the_configured_source_is_the_DECLARED_index_not_the_child_it_descends_to():
    """The index is what robots.txt advertises and is therefore the stable address; the child is an
    implementation detail of that index, and the ladder descends to it on its own."""
    cfg = json.loads((ROOT / "examples" / "data" / "crawler_publishers.json").read_text())
    for pub in (p for p in cfg["publishers"] if p["enabled"]):
        url = pub["sources"][0]["url"]
        assert "news-sitemap-index" in url and pub["sources"][0]["kind"] == "sitemap"


def test_an_age_bound_is_set_so_an_index_descent_cannot_reach_an_archive():
    """Nothing older than the clustering window can ever become a story, and this is the guard
    against the SCMP failure the field exists for — a declared sitemap returning 19,962 URLs
    spanning years."""
    cfg = json.loads((ROOT / "examples" / "data" / "crawler_publishers.json").read_text())
    for pub in (p for p in cfg["publishers"] if p["enabled"]):
        assert 0 < pub["max_age_days"] <= 14


# --------------------------------------------------------------------------- no bodies

def test_sitemap_discovery_carries_no_article_body():
    """The constraint the whole design rests on: this is a discovery layer, not a scraper. Article
    text is the copyrighted asset and we hold no licence to it."""
    sm = ("<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9' "
          "xmlns:news='http://www.google.com/schemas/sitemap-news/0.9'>"
          "<url><loc>https://www.kait8.com/2026/08/26/story/</loc>"
          "<news:news><news:title>School board votes</news:title>"
          "<news:publication_date>2026-08-26T12:00:00Z</news:publication_date>"
          "</news:news></url></urlset>")
    entries = crawler.discover_sitemap(sm, "https://www.kait8.com/")
    assert entries and all(e.body is None for e in entries)
    assert entries[0].title and entries[0].published_at


# --------------------------------------------------------------------------- failure isolation

def test_a_broken_crawl_config_does_not_take_the_rss_poller_down(monkeypatch):
    """A supplement that can break the thing it supplements is worse than one that is absent."""
    monkeypatch.setenv("RWE_CRAWL_ENABLED", "1")
    monkeypatch.setattr(crawler, "load_config",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("malformed")))
    reg = sources.default_registry()
    assert [a for a in reg.adapters() if getattr(a, "source_type", "") == "crawl"] == []
    assert any(getattr(a, "source_type", "") == "rss" for a in reg.adapters()), "RSS must survive"


def test_the_lint_names_the_discovery_kinds_actually_configured():
    """An over-broad warning is one people learn to skip. The old wording cited *section* discovery
    for every publisher, including sitemap-only ones that configure no section source."""
    problems = crawler.lint_config(crawler.load_config())
    patt = [p for p in problems if p["code"] == "no_article_pattern"]
    assert patt and all("sitemap discovery is configured" in p["detail"] for p in patt)
    assert not any("an HTML index links to" in p["detail"] for p in patt)
