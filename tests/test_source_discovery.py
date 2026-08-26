"""Source discovery and validation — M7 of docs/SCALE_ROADMAP.md.

M7 is the first milestone that touches a publisher, so what these tests pin is mostly about
**restraint**:

1. **An offline run cannot be mistaken for a validated one.** `source_validation` has no fetcher of
   its own, so without one every network gate reports `UNKNOWN` — never `PASS`. Claiming a
   publisher's robots.txt permits us without having read it is the exact shape of error this audit
   series keeps finding in its own instruments.
2. **No request is spent on a decision already made.** The three offline gates run first, and a
   candidate they reject is never probed.
3. **robots.txt is fail-closed** — inherited from `crawler.RobotsPolicy` rather than re-argued, and
   pinned here because M7 is what will finally exercise it.
4. **The aggregator gate exists because of a measured failure** — 996 of 1,246 newly-attributed
   articles landing on "Google News" from real local broadcasters proxied through one host.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import outlet_registry              # noqa: E402
import source_discovery as sd       # noqa: E402
import source_validation as sv      # noqa: E402


@pytest.fixture
def reg():
    return outlet_registry.default_registry()


def _row(url, publisher="", language="en", published="2026-08-26T12:00:00+00:00"):
    return {"url": url, "canonicalUrl": url, "publisher": publisher,
            "language": language, "publishedAt": published, "title": "t"}


# --------------------------------------------------------------------------- discovery

def test_candidates_group_by_host_not_by_publisher_string(reg):
    """The same outlet arrives as `Sportskeeda`, `sportskeeda.com` and `SPORTSKEEDA`. A host is what
    Stage 2 would send a request to, and it is also what survives the name variation this catalog is
    full of — one candidate, not three."""
    rows = ([_row(f"https://vertical.example/a{i}", "Vertical") for i in range(5)]
            + [_row(f"https://vertical.example/b{i}", "vertical.example") for i in range(5)])
    cands = sd.candidates(rows, reg, floor=10)
    assert [c["host"] for c in cands] == ["vertical.example"]
    assert cands[0]["articles"] == 10 and cands[0]["eligible"] is True


def test_the_floor_is_a_cost_bound_and_says_so(reg):
    """3,442 of 4,083 identities sit below it with a MEDIAN of one article. Spending a request on
    each is how a discovery pipeline becomes a crawl of the whole internet."""
    rows = [_row("https://tiny.example/a", "Tiny")]
    c = sd.candidates(rows, reg, floor=10)[0]
    assert c["eligible"] is False and "below the 10-article floor" in c["reason"]


@pytest.mark.parametrize("host", ["news.google.com", "foo.news.google.com", "apple.news",
                                  "flipboard.com", "www.msn.com"])
def test_the_aggregator_gate_catches_proxies_and_their_subdomains(host):
    """**Gate 8, and it exists because of a measured failure.** The outlet-resolution counterfactual
    found 996 of 1,246 newly-attributed articles landing on "Google News" from `10tv.com @
    news.google.com` — real local broadcasters proxied through one host. A discovery pipeline
    without this discovers aggregators and calls them publishers."""
    assert sd.is_proxy_host(host) is True


def test_the_registry_is_asked_first_and_the_static_list_covers_what_it_lacks(reg):
    """Measured, and the split is why both halves exist: the registry resolves `news.google.com` to
    `Google News kind=aggregator`, and knows none of `apple.news`, `flipboard.com`, `msn.com` or
    `substack.com`. Its `EXCLUDED_KINDS` covers the outlets it has; `PROXY_HOSTS` covers the ones it
    does not — which is precisely the population discovery works on."""
    assert reg.resolve("news.google.com").kind in outlet_registry.EXCLUDED_KINDS
    assert [h for h in ("apple.news", "flipboard.com", "msn.com", "substack.com")
            if reg.resolve(h) is not None] == []
    assert all(sd.is_proxy_host(h, reg) for h in
               ("news.google.com", "apple.news", "flipboard.com", "msn.com"))


def test_a_host_that_is_both_a_proxy_and_tracked_reports_the_proxy_reason(reg):
    """`news.google.com` is both. "Already tracked" would suggest we carry it as a publisher, when
    the point is that its articles are other publishers' — the same ordering principle
    `source_evaluation.evaluate` uses, where the disqualifying fact is read before the procedural
    one."""
    rows = [_row(f"https://news.google.com/x{i}", "Google News") for i in range(20)]
    c = sd.candidates(rows, reg, floor=10)[0]
    assert c["proxy"] is True and c["tracked"] is False
    assert "aggregator/proxy" in c["reason"]


@pytest.mark.parametrize("host", ["notnews.google.com.evil.example", "google.com", "bbc.co.uk"])
def test_the_aggregator_gate_does_not_over_match(host):
    """Subdomain-tolerant, not substring-matching: `notnews.google.com.evil.example` ends with a
    label that merely CONTAINS a proxy host, and must not be rejected as one."""
    assert sd.is_proxy_host(host) is False


def test_an_already_tracked_host_is_not_a_candidate(reg):
    """Gate 7. Re-discovering an outlet we have would create a second identity for it, and the tier
    configuration would then name one of the two."""
    rows = [_row(f"https://reuters.com/a{i}", "Reuters") for i in range(20)]
    c = sd.candidates(rows, reg, floor=10)[0]
    assert c["tracked"] is True and c["eligible"] is False


def test_the_census_counts_every_rejection_reason(reg):
    """A run that silently dropped its rejections could not be audited, and the counts are the
    cheapest evidence the gates do anything at all."""
    rows = ([_row(f"https://reuters.com/a{i}", "Reuters") for i in range(20)]
            + [_row(f"https://news.google.com/x{i}", "Google News") for i in range(20)]
            + [_row("https://tiny.example/a", "Tiny")]
            + [_row(f"https://vertical.example/a{i}", "Vertical") for i in range(20)])
    stats = sd.census(sd.candidates(rows, reg, floor=10))
    assert stats == {"total": 4, "tracked": 1, "proxy": 1, "belowFloor": 1, "eligible": 1}


def test_probe_cost_is_computed_from_the_eligible_hosts_only(reg):
    """The number that goes in front of a human before any request. Counting rejected hosts would
    overstate it and make the review harder to grant than it needs to be."""
    rows = ([_row(f"https://reuters.com/a{i}", "Reuters") for i in range(20)]
            + [_row(f"https://vertical.example/a{i}", "Vertical") for i in range(20)])
    cost = sd.probe_cost(sd.candidates(rows, reg, floor=10), seconds_per_request=2.0)
    assert cost == {"hosts": 1, "requests": 2, "seconds": 4.0, "minutes": 0.1}


# --------------------------------------------------------------------------- validation, offline

def _cand(host="vertical.example", **kw):
    base = {"host": host, "language": "en", "tracked": False, "proxy": False,
            "belowFloor": False, "eligible": True}
    return {**base, **kw}


def test_without_a_fetcher_every_network_gate_is_UNKNOWN_and_the_verdict_is_never_ADMIT():
    """**The safety property, and it is structural rather than a convention.** The module has no
    fetcher of its own, so an offline run has nothing to call. The alternative — a default fetcher
    disabled by a flag — puts the whole ToS question behind somebody remembering to pass it."""
    r = sv.validate(_cand())
    assert r["verdict"] == "INCOMPLETE"
    assert r["requests"] == 0
    online = [g for g in r["gates"] if g.number in (1, 2, 3, 4, 5)]
    assert all(g.status == sv.UNKNOWN for g in online)
    assert not any(g.status == sv.PASS for g in online)


def test_an_absent_language_is_UNKNOWN_not_a_rejection():
    """**The defect the first production run of M7 exposed.** An absent `language` measures OUR
    ingestion metadata, not the source — it comes from the feed entry and most feeds omit one;
    `audit_source_cohort` already abandoned a whole analysis over exactly this sparsity.

    Failing on it would reject `goal.com`, `vietnamnet.vn` and `gujaratsamachar.com` — real
    publishers — for a gap in our own records, and SILENTLY, because a candidate with a failed
    offline gate is never probed. The run would have promised 348 requests and quietly made fewer."""
    r = sv.validate(_cand(language=""))
    gate6 = next(g for g in r["gates"] if g.number == 6)
    assert gate6.status == sv.UNKNOWN
    assert r["verdict"] != "REJECT"


def test_an_unknown_language_candidate_is_still_probed():
    """The consistency the run got wrong: discovery counted these hosts in its 348-request estimate
    while validation would have skipped them for free. UNKNOWN does not block the probe, so the
    priced cost and the spent cost describe the same set of hosts."""
    fetch = _fetcher({"/robots.txt": ROBOTS_OK, "/feed.xml": FEED % _items(12),
                      "vertical.example/": LANDING})
    sv.validate(_cand(language=""), fetch=fetch)
    assert any("robots.txt" in c for c in fetch.calls), "an unknown language must not skip the probe"


def test_the_feed_settles_a_language_the_catalog_did_not_know():
    """A permanent UNKNOWN could never become an ADMIT. The feed usually declares its own language,
    which is better evidence than our record of it either way."""
    feed = FEED % ("<language>vi</language>\n" + _items(12))
    fetch = _fetcher({"/robots.txt": ROBOTS_OK, "/feed.xml": feed, "vertical.example/": LANDING})
    r = sv.validate(_cand(language=""), fetch=fetch)
    gate6 = next(g for g in r["gates"] if g.number == 6)
    assert gate6.status == sv.PASS and "vi" in gate6.detail
    assert r["verdict"] == "ADMIT"


def test_feed_language_is_now_a_lookup_on_the_parsed_entries_not_a_second_parser():
    """It used to parse the feed body here. `rss_ingest.parse_feed` now fills each entry's language
    from the channel's own declaration, so the answer arrives on the normalized shape — and fixing
    it in the parser fixed it for INGESTION too, which is where the gap actually was."""
    import rss_ingest
    _title, entries = rss_ingest.parse_feed(
        (FEED % ("<language>vi</language>\n" + _items(3))).encode())
    assert sv.feed_language(entries) == "vi"
    assert sv.feed_language([]) == ""


def test_a_source_neither_we_nor_the_feed_can_place_fails_gate_6():
    """UNKNOWN is honest, not permissive: once the feed has been read and still states nothing, the
    question HAS been asked and the answer is no."""
    fetch = _fetcher({"/robots.txt": ROBOTS_OK, "/feed.xml": FEED % _items(12),
                      "vertical.example/": LANDING})
    r = sv.validate(_cand(language=""), fetch=fetch)
    assert next(g for g in r["gates"] if g.number == 6).status == sv.FAIL
    assert r["verdict"] == "REJECT"


def test_dated_share_is_1_0_by_construction_for_a_windowed_fetch(reg):
    """Why the runner prints no `dated` column. `story_service._fetch` filters
    `published_at >= date_from`, so an undated row cannot be in the window — measured on the first
    production run as 30 of 30 candidates at 100%. A column that can only hold one value is not a
    measurement, and printing it invites reading it as one."""
    windowed = [_row(f"https://vertical.example/a{i}", "V") for i in range(12)]
    assert sd.candidates(windowed, reg, floor=10)[0]["datedShare"] == 1.0

    # It is still real data for a caller that did NOT window its rows.
    mixed = windowed + [_row(f"https://vertical.example/b{i}", "V", published="") for i in range(12)]
    assert sd.candidates(mixed, reg, floor=10)[0]["datedShare"] == 0.5


def test_the_runner_does_not_print_the_dated_column():
    """Structural, because the column is the kind of thing that gets re-added by someone tidying the
    table up. Gate 4 asks the same question of the FEED, where it can actually fail."""
    src = (ROOT / "examples" / "audit_source_discovery.py").read_text()
    assert "datedShare" not in src


def test_the_module_constructs_no_fetcher_of_its_own():
    """Structural. A default would make the offline guarantee a matter of call-site discipline."""
    import inspect
    sig = inspect.signature(sv.validate)
    assert sig.parameters["fetch"].default is None
    src = inspect.getsource(sv)
    assert "fetch or crawler._fetch_text" not in src
    assert "fetch=crawler._fetch_text" not in src


def test_an_offline_rejection_spends_no_request():
    """No publisher's bandwidth is paid to confirm a decision that is already made."""
    calls = []
    r = sv.validate(_cand(proxy=True), fetch=lambda u: calls.append(u) or "")
    assert calls == [] and r["requests"] == 0 and r["verdict"] == "REJECT"


# --------------------------------------------------------------------------- validation, network

FEED = """<?xml version="1.0"?><rss version="2.0"><channel>
%s
</channel></rss>"""


def _items(n, *, host="vertical.example", dated=True):
    return "\n".join(
        f"<item><title>Story number {i} about something</title>"
        f"<link>https://{host}/article-{i}</link>"
        + (f"<pubDate>Tue, 26 Aug 2026 1{i % 10}:00:00 GMT</pubDate>" if dated else "")
        + "</item>" for i in range(n))


ROBOTS_OK = "User-agent: *\nAllow: /\n"
ROBOTS_NO = "User-agent: *\nDisallow: /\n"
LANDING = ('<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml">'
           '</head><body>hi</body></html>')


def _fetcher(pages):
    calls = []

    def fetch(url):
        calls.append(url)
        for key, body in pages.items():
            if url.endswith(key):
                return body
        raise RuntimeError(f"unexpected fetch: {url}")
    fetch.calls = calls
    return fetch


def test_a_clean_host_passes_every_gate_and_is_ADMITted():
    fetch = _fetcher({"/robots.txt": ROBOTS_OK, "/feed.xml": FEED % _items(12),
                      "vertical.example/": LANDING})
    r = sv.validate(_cand(), fetch=fetch)
    assert r["verdict"] == "ADMIT", [g for g in r["gates"] if g.blocking]
    assert r["feed"] == "https://vertical.example/feed.xml"


def test_robots_refusal_stops_before_anything_else_is_fetched():
    """The cheapest refusal comes first. A publisher who said no must not then have their landing
    page and feed pulled anyway."""
    fetch = _fetcher({"/robots.txt": ROBOTS_NO})
    r = sv.validate(_cand(), fetch=fetch)
    assert r["verdict"] == "REJECT"
    assert fetch.calls == ["https://vertical.example/robots.txt"]


def test_an_absent_robots_policy_is_a_refusal_not_a_permission():
    """Fail-closed, inherited from `crawler.RobotsPolicy`: the conventional "no robots.txt means
    crawl freely" default is a search engine's norm, not a reasonable reading for a commercial
    reader of newsrooms that has never spoken to the publisher. A 200 that returns an HTML page —
    the most common way robots.txt is "missing" — must not read as blanket permission."""
    fetch = _fetcher({"/robots.txt": "<html><body>404 not found</body></html>"})
    assert sv.validate(_cand(), fetch=fetch)["verdict"] == "REJECT"


def test_gate_4_can_actually_fail_which_is_the_point_of_asking_it_online():
    """The roadmap flags this one specifically: `_fetch` is time-windowed, so every catalog row has
    a date BY CONSTRUCTION and an offline probe would report zero rejections whatever the feeds
    serve. A gate that cannot fail is not a gate — the defect M8 shipped and had to correct."""
    fetch = _fetcher({"/robots.txt": ROBOTS_OK, "/feed.xml": FEED % _items(12, dated=False),
                      "vertical.example/": LANDING})
    r = sv.validate(_cand(), fetch=fetch)
    gate4 = next(g for g in r["gates"] if g.number == 4)
    assert gate4.status == sv.FAIL and r["verdict"] == "REJECT"


def test_gate_5_rejects_a_feed_whose_articles_live_elsewhere():
    """If the articles are on someone else's host we cannot say who published them — the same bar
    `source_evaluation.HOST_STABILITY_FLOOR` applies after ingestion, applied here before it."""
    fetch = _fetcher({"/robots.txt": ROBOTS_OK,
                      "/feed.xml": FEED % _items(12, host="somewhere-else.example"),
                      "vertical.example/": LANDING})
    r = sv.validate(_cand(), fetch=fetch)
    assert next(g for g in r["gates"] if g.number == 5).status == sv.FAIL


def test_gate_3_rejects_a_feed_with_too_few_items():
    fetch = _fetcher({"/robots.txt": ROBOTS_OK, "/feed.xml": FEED % _items(3),
                      "vertical.example/": LANDING})
    r = sv.validate(_cand(), fetch=fetch)
    assert next(g for g in r["gates"] if g.number == 3).status == sv.FAIL


def test_a_host_with_no_advertised_feed_is_rejected_without_further_requests():
    fetch = _fetcher({"/robots.txt": ROBOTS_OK, "vertical.example/": "<html><body>no feed</body></html>"})
    r = sv.validate(_cand(), fetch=fetch)
    assert next(g for g in r["gates"] if g.number == 2).status == sv.FAIL
    assert len(fetch.calls) == 2                     # robots + landing, and nothing more


def test_feed_urls_never_leave_the_declared_host():
    """A page can advertise anyone's feed. Following one off-host would turn a two-request probe of
    a known candidate into an unbounded crawl."""
    body = ('<link rel="alternate" type="application/rss+xml" href="/mine.xml">'
            '<link rel="alternate" type="application/rss+xml" href="https://elsewhere.example/x.xml">'
            '<link rel="alternate" type="application/rss+xml" href="https://cdn.vertical.example/y.xml">')
    assert sv.feed_urls(body, "vertical.example") == [
        "https://vertical.example/mine.xml", "https://cdn.vertical.example/y.xml"]


def test_sitemaps_and_crawl_delay_are_reported_from_the_policy_already_fetched():
    """Both come out of the robots.txt gate 1 already read, so reporting them costs ZERO extra
    requests. A probe that fetched the file and discarded half of what the publisher chose to say
    would be leaving the cheapest evidence on the floor — and a declared sitemap we are not using is
    `CRAWLER_DESIGN.md`'s own signal that a configured path was a guess."""
    robots_body = ("Sitemap: https://vertical.example/news.xml\n"
                   "User-agent: *\nAllow: /\nCrawl-delay: 5\n")
    fetch = _fetcher({"/robots.txt": robots_body, "/feed.xml": FEED % _items(12),
                      "vertical.example/": LANDING})
    r = sv.validate(_cand(), fetch=fetch)
    assert r["sitemaps"] == ["https://vertical.example/news.xml"]
    gate1 = next(g for g in r["gates"] if g.number == 1)
    assert "Crawl-delay: 5s" in gate1.detail and "1 sitemap" in gate1.detail
    # Three requests and no more: robots.txt, the landing page, the feed. Never an article.
    assert r["requests"] == 3


def test_an_offline_run_reports_no_sitemaps_it_never_fetched():
    """The symmetric honesty rule. Sitemaps are evidence from a file we read; with no fetcher there
    is no file, so the list must be empty rather than stale or invented."""
    assert sv.validate(_cand())["sitemaps"] == []


def test_a_publishers_crawl_delay_is_honoured_not_just_printed():
    """Reporting a `Crawl-delay` we then ignore would be worse than not reading it. The limiter
    takes the publisher's number for every request after the one that revealed it."""
    slept = []
    limiter = __import__("crawler").RateLimiter(default_interval=2.0, sleep=slept.append,
                                                clock=iter([0.0] * 12).__next__)
    fetch = _fetcher({"/robots.txt": "User-agent: *\nAllow: /\nCrawl-delay: 30\n",
                      "/feed.xml": FEED % _items(12), "vertical.example/": LANDING})
    sv.validate(_cand(), fetch=fetch, limiter=limiter)
    assert max(slept) >= 30, f"the publisher asked for 30s between requests; waits were {slept}"


def test_the_probe_is_rate_limited_per_host():
    """The limit protects the publisher's SERVER, so it is keyed on host — `crawler.RateLimiter`'s
    rule, reused rather than restated."""
    slept = []
    limiter = __import__("crawler").RateLimiter(default_interval=2.0, sleep=slept.append,
                                                clock=iter([0.0] * 12).__next__)
    fetch = _fetcher({"/robots.txt": ROBOTS_OK, "/feed.xml": FEED % _items(12),
                      "vertical.example/": LANDING})
    sv.validate(_cand(), fetch=fetch, limiter=limiter)
    assert sum(slept) > 0, "a multi-request probe of one host must wait between requests"
