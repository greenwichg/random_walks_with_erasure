"""Wiring `CrawlAdapter` into the poller — the first change that lets crawled content reach the
production catalog.

Everything before this was read-only probing. This crosses into ingestion, so what these tests pin
is the set of things that must be **impossible to get wrong**, not merely documented:

1. **Off by default.** Deploying the wiring changes nothing until an operator says so.
2. **A crawled source must be in the shadow lane.** `corpus.DEFAULT_TIER` is `"A"`, so an outlet
   nobody put in `RWE_CORPUS_SHADOW` does not land somewhere neutral — its articles go straight into
   the clustering corpus and start voting in stories. That is promotion by omission. The one
   exception is promotion by DECISION: `tier: "A"` written on a hand-verified config (never
   derivable from the admission table), which exists so an outlet that already clusters — AP,
   Reuters, CNN — can be crawled without first demoting every article it has.
3. **The six publishers that shipped in the config are unverified and must not run.**
4. **No article bodies.** The crawler fetches discovery documents, never an article page.
5. **A broken crawl config must not take the RSS poller down** — a supplement that can break the
   thing it supplements is worse than one that is absent.
"""
import json
import pathlib
import re
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
    assert [a.provider for a in _adapters() if a.enabled()] == ["KAIT", "KWCH"]


def test_tier_a_by_decision_runs_without_a_lane_and_the_shipped_decisions_still_wait(monkeypatch):
    """The exception to the precondition, and its limit. A config that DECLARES `tier: "A"` is a
    decision, so it runs with no lane assigned; the three shipped configs that carry it are all
    `enabled: false` until the live probe has returned sample URLs for them, so the enabled set
    the test above pins is unchanged. Mutation check: dropping the `tier == "A"` branch in
    `CrawlAdapter.in_shadow` fails the first assertion."""
    monkeypatch.setenv("RWE_CRAWL_ENABLED", "1")
    decided = crawler.CrawlAdapter(crawler.PublisherCrawlConfig(
        publisher="Reuters", domains=("reuters.com",), tier="A", enabled=True,
        sources=(crawler.DiscoverySource("sitemap", "https://www.reuters.com/s.xml"),)))
    assert decided.enabled() is True and decided.shadow_warning() is None
    shipped = [c for c in crawler.load_config() if c.tier == "A"]
    assert {c.publisher for c in shipped} == {"Associated Press", "Reuters", "CNN"}
    assert all(c.enabled is False for c in shipped)
    assert [a.provider for a in _adapters() if a.enabled()] == []


def test_the_default_tier_really_is_A_so_the_precondition_is_load_bearing():
    """If the default were anything else this guard would be theatre. It is not."""
    assert corpus.DEFAULT_TIER == "A"


def test_a_configured_but_unshadowed_publisher_says_why(monkeypatch):
    """Silently not running is indistinguishable from a broken config. The reason names the fix."""
    monkeypatch.setenv("RWE_CRAWL_ENABLED", "1")
    warnings = [a.shadow_warning() for a in _adapters()]
    named = [w for w in warnings if w and "KAIT" in w]
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
    assert {p["publisher"] for p in cfg["publishers"] if p["enabled"]} == {"KAIT", "KWCH"}


def test_the_configured_source_is_the_DECLARED_index_not_the_child_it_descends_to():
    """The index is what robots.txt advertises and is therefore the stable address; the child is an
    implementation detail of that index, and the ladder descends to it on its own."""
    cfg = json.loads((ROOT / "examples" / "data" / "crawler_publishers.json").read_text())
    for pub in (p for p in cfg["publishers"] if p["enabled"]):
        url = pub["sources"][0]["url"]
        assert "news-sitemap-index" in url and pub["sources"][0]["kind"] == "sitemap"


#: Article URLs the live probe actually returned on 2026-08-26, three from each host's news
#: sitemap. Kept verbatim because they are the EVIDENCE the `article_pattern` was written from —
#: the alternative was inventing one, and `CRAWLER_DESIGN.md`'s sharpest warning is that a pattern
#: matching 0% of discovered URLs makes the crawler ingest nothing while every gate reports healthy.
OBSERVED_ARTICLES = [
    "https://www.kait8.com/2026/08/26/list-dozens-new-missouri-laws-take-effect-friday/",
    "https://www.kait8.com/2026/08/25/team-coverage-fans-remember-dolly-partons-legacy-worldwide/",
    "https://www.kait8.com/2026/08/26/after-multiple-inmate-escapes-howell-county-considers-options-new-jail/",
    "https://www.kwch.com/2026/08/26/walmart-addresses-incorrect-sales-tax-charges-hays-store/",
    "https://www.kwch.com/2026/08/26/wichita-city-council-adopts-2027-budget-despite-library-funding-concerns/",
    "https://www.kwch.com/2026/08/26/work-begins-wednesday-reduce-douglas-avenue-3-lanes/",
]


def test_the_article_pattern_matches_every_url_the_probe_actually_returned():
    """Written from observation, not convention. Six URLs across two independent hosts, and the
    resulting pattern is the same one already configured for NPR — a different publisher on the same
    Arc XP date-path convention, which is corroboration rather than coincidence."""
    cfg = json.loads((ROOT / "examples" / "data" / "crawler_publishers.json").read_text())
    for pub in (p for p in cfg["publishers"] if p["enabled"]):
        patt = pub["article_pattern"]
        assert patt, f"{pub['publisher']}: the samples are in hand, so the pattern should be set"
        host = pub["domains"][0]
        mine = [u for u in OBSERVED_ARTICLES if host in u]
        assert mine, f"no observed sample for {host}"
        assert all(re.search(patt, u) for u in mine), f"{pub['publisher']}: pattern misses real URLs"


@pytest.mark.parametrize("url", [
    "https://www.kait8.com/news/",
    "https://www.kait8.com/authors/jane-doe/",
    "https://www.kait8.com/tag/weather/",
    "https://www.kait8.com/video/",
])
def test_the_pattern_rejects_the_non_article_shapes_it_exists_for(url):
    """A pattern that accepted everything would not be filtering — the other half of the warning."""
    cfg = json.loads((ROOT / "examples" / "data" / "crawler_publishers.json").read_text())
    patt = next(p["article_pattern"] for p in cfg["publishers"] if p["publisher"] == "KAIT")
    assert not re.search(patt, url)


def test_setting_the_pattern_cleared_its_lint_warning(monkeypatch):
    """The lint is now SILENT on the shipped config. `no_article_pattern` was answered 2026-08-26;
    `unknown_publisher` — correct while KAIT/KWCH had no registry row — cleared 2026-09-01 when the
    crawl-campaign identity tranche registered them (blank lean; identity, not a rating). An empty
    lint on a hand-verified config is the intended end state, not a lint that stopped looking."""
    monkeypatch.setenv("RWE_CORPUS_SHADOW", "kait8.com,kwch.com")
    corpus._index.cache_clear()
    codes = {p["code"] for p in crawler.lint_config(crawler.load_config())}
    assert "no_article_pattern" not in codes
    assert codes == set()


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


@pytest.mark.parametrize("kind, expected", [
    ("sitemap", "a sitemap may list section and tag pages alongside articles"),
    ("section", "an HTML index links to tags, authors and the shop"),
])
def test_the_lint_names_the_discovery_kind_actually_configured(kind, expected):
    """An over-broad warning is one people learn to skip. The old wording cited *section* discovery
    for every publisher, including sitemap-only ones that configure no section source.

    Driven through synthetic configs rather than the shipped one: an earlier version of this test
    asserted the shipped config still HAD the gap, so it broke the moment the gap was closed. A test
    of the lint should depend on the lint, not on a config staying imperfect."""
    cfg = crawler.PublisherCrawlConfig(
        publisher="Example", domains=("example.com",), article_pattern="",
        sources=(crawler.DiscoverySource(kind=kind, url="https://example.com/x"),))
    problem = next(p for p in crawler.lint_config([cfg]) if p["code"] == "no_article_pattern")
    assert kind in problem["detail"] and expected in problem["detail"]


# --------------------------------------------------------------------------- the runner tells the truth

#: Sentences the discovery runner printed on a production run of a build that had already made them
#: false. Live output describing the system as it *was* is its own defect class — an operator has no
#: way to tell a stale instruction from a current one, and this series has now produced three.
RETIRED_CLAIMS = [
    "not wired into the poller",
    "no live crawl has ever run",
    "This is the first thing",
]


@pytest.mark.parametrize("claim", RETIRED_CLAIMS)
def test_the_runner_no_longer_prints_a_claim_that_stopped_being_true(claim):
    src = (ROOT / "examples" / "audit_source_discovery.py").read_text()
    assert claim not in src, f"the runner still tells operators: {claim!r}"


def test_the_admit_advice_names_BOTH_switches_in_the_order_they_must_be_set():
    """Naming only `RWE_CRAWL_ENABLED` would be worse than naming neither: it reads like the whole
    instruction, and following it crawls nothing while looking like it should work. Shadow comes
    first so articles land hidden from the first cycle rather than after a correction."""
    src = (ROOT / "examples" / "audit_source_discovery.py").read_text()
    assert "RWE_CRAWL_ENABLED" in src and "RWE_CORPUS_SHADOW" in src
    assert "crawler_publishers.json" in src, "the config file is the third thing that must change"


# --------------------------------------------------------------------------- the switch must REACH the container

def _compose_services():
    import yaml
    doc = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text())
    return doc["services"]


def test_every_service_given_the_crawl_switch_can_also_see_its_precondition():
    """`environment:` is an explicit allowlist and this stack has no `env_file:`, so a variable
    absent from a service's block never reaches that container whatever deploy/.env says.

    The wiring shipped `RWE_CRAWL_ENABLED` to both `api` and `ingest` but `RWE_CORPUS_SHADOW` only
    to `api`. That failed CLOSED — an outlet absent from the list is Tier A and `enabled()` demands
    shadow — but it would have refused for a reason the operator had already fixed, printing "not in
    RWE_CORPUS_SHADOW" about a variable they had just set. Both vars, because `corpus.tier_of` reads
    them together: passing one would resolve an outlet Tier B in the api and Tier A in ingest.

    `test_rec_flags_deployable.py` guards the same family and did NOT catch this: it greps the whole
    compose file for the name, so a variable declared in ANY service passes. That is the right shape
    for "can this flag ever reach a container"; it cannot see "reaches the wrong one". This is the
    first occurrence of the failure mode where the flag DID reach a container — just not the one
    whose code reads it — so the guard has to be per-service."""
    for name, svc in _compose_services().items():
        env = svc.get("environment") or {}
        if "RWE_CRAWL_ENABLED" not in env:
            continue
        missing = {"RWE_CORPUS_SHADOW", "RWE_CORPUS_TIER_B"} - set(env)
        assert not missing, f"service {name!r} gets the crawl switch but not {sorted(missing)}"


def test_the_service_that_actually_runs_the_poller_is_covered():
    """Guards the guard: a loop over "every service with the switch" proves nothing if no service
    has it. Both the long-running engine and the one-shot seeding run import the same adapters."""
    have = [n for n, s in _compose_services().items()
            if "RWE_CRAWL_ENABLED" in (s.get("environment") or {})]
    assert set(have) == {"api", "ingest"}, f"expected both pollers to carry the switch, got {have}"


# --------------------------------------------------------------------------- the warning has a caller

def _poller_with(monkeypatch, shadow=None):
    """A MultiSourcePoller over the real registry, started with threads stubbed out."""
    monkeypatch.setenv("RWE_CRAWL_ENABLED", "1")
    if shadow is not None:
        monkeypatch.setenv("RWE_CORPUS_SHADOW", shadow)
    corpus._index.cache_clear()
    lines = []
    poller = sources.MultiSourcePoller(None, registry=sources.default_registry(),
                                       log=lambda lvl, ev, **f: lines.append((ev, f)))
    monkeypatch.setattr(sources.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda s: None,
                                                       "is_alive": lambda s: False})())
    poller.start()
    return lines


def test_a_configured_but_unshadowed_publisher_is_REPORTED_not_just_silently_skipped(monkeypatch):
    """`shadow_warning()` existed with NO caller — defined, tested, and invoked by nothing. It made
    "turning on only the flag tells you why" an untrue sentence: the adapter refused and said
    nothing, which is indistinguishable from a broken config. Startup now emits it."""
    inert = [f for ev, f in _poller_with(monkeypatch) if ev == "source_adapter_inert"]
    named = [f for f in inert if f["provider"] == "KAIT"]
    assert named, "an enabled-but-unshadowed crawl publisher must say so"
    assert "RWE_CORPUS_SHADOW" in named[0]["reason"]
    assert "promotion by omission" in named[0]["reason"]


def test_no_such_warning_once_the_publisher_IS_shadowed(monkeypatch):
    """The mirror. A warning that fires when the config is correct is noise, and noise is how a
    real warning gets ignored."""
    lines = _poller_with(monkeypatch, shadow="kait8.com,kwch.com")
    assert [f for ev, f in lines if ev == "source_adapter_inert"] == []
    started = [f for ev, f in lines if ev == "multi_source_start"]
    assert started and {"KAIT", "KWCH"} <= set(started[0]["adapters"])
