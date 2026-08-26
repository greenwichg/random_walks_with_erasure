"""The robots gate and our identity — `examples/robots.py`.

F1 and F2 of the M7 Stage 2 audit, both about code that was live **today** rather than blocked:

* **F1** — `rss_ingest`, `sources`, `feed_service` and `feed_schedule` contained no reference to
  robots. It existed only in `crawler.py`, M7's validation modules, and `verify_crawler_config.py`,
  **none of which has ever run against a real host.** The unrun POC was more compliant than
  production.
* **F2** — the RSS poller identified itself as `(+https://code.claude.com)`, a documentation site
  belonging to another organisation. A publisher trying to find out who was polling their newsroom
  was sent to the wrong company.

What these tests pin is that *"Hidden View respects robots.txt"* is now a true sentence, and that
the live path's deliberate departure from the crawler's fail-closed posture is bounded and
reversible rather than a quiet weakening.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import robots                       # noqa: E402

ALLOW = "User-agent: *\nAllow: /\n"
DENY_US = "User-agent: HiddenView-RSS\nDisallow: /\n"
DENY_ALL = "User-agent: *\nDisallow: /\n"


def _fetcher(body=None, *, fail=False):
    calls = []

    def fetch(url):
        calls.append(url)
        if fail:
            raise OSError("unreachable")
        return body
    fetch.calls = calls
    return fetch


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Every test states its own posture; a leaked RWE_ROBOTS_* would change what these assert."""
    for k in ("RWE_ROBOTS_ENFORCE", "RWE_ROBOTS_STRICT"):
        monkeypatch.delenv(k, raising=False)
    robots.reset_cache()
    yield
    robots.reset_cache()


# --------------------------------------------------------------------------- identity (F2)

def test_every_agent_names_hidden_view_and_one_contact_url():
    """The fix for F2. Composed in one place so no path can identify us as somebody else again."""
    for product in ("RSS", "Crawler", "Robots"):
        ua = robots.user_agent(product)
        assert ua.startswith(f"HiddenView-{product}/")
        assert ua.endswith(f"(+{robots.CONTACT_URL})")
    assert "hidden-view.com" in robots.CONTACT_URL


def test_no_live_agent_still_claims_to_be_someone_else():
    """Checks the VALUE each module actually sends, not its source text.

    The first draft grepped the source for the old string and failed on the comment recording the
    defect — the third time this session a prose mention has tripped its own grep. What matters is
    what goes out on the wire, and that is a runtime value."""
    import crawler
    import rss_ingest
    for agent in (rss_ingest._USER_AGENT, crawler.USER_AGENT, robots.user_agent("Robots")):
        assert "claude.com" not in agent
        assert agent.startswith("HiddenView-") and robots.CONTACT_URL in agent


def test_the_contact_url_is_actually_served():
    """An agent string pointing at a 404 is barely better than one pointing at the wrong company.
    Pinned structurally: the route file must exist for the URL to mean anything."""
    assert (ROOT / "web" / "app" / "crawler" / "page.tsx").exists()
    assert (ROOT / "web" / "app" / "robots.txt" / "route.ts").exists()


def test_we_serve_our_own_robots_txt_naming_our_agents():
    """We ask publishers to publish one; not serving our own would be asking for a courtesy we do
    not extend."""
    body = (ROOT / "web" / "app" / "robots.txt" / "route.ts").read_text()
    for agent in ("HiddenView-RSS", "HiddenView-Crawler", "HiddenView-Robots"):
        assert agent in body
    assert robots.CONTACT_URL in body


# --------------------------------------------------------------------------- the three outcomes

def test_an_explicit_disallow_for_our_agent_refuses():
    """The case the whole gate exists for: a publisher answering us."""
    robots.reset_cache(fetch=_fetcher(DENY_US))
    d = robots.check("https://x.example/feed.xml")
    assert d.allowed is False and d.known is True
    with pytest.raises(robots.RobotsRefused):
        robots.enforce("https://x.example/feed.xml")


def test_a_wildcard_disallow_refuses_too():
    robots.reset_cache(fetch=_fetcher(DENY_ALL))
    assert robots.check("https://x.example/feed.xml").allowed is False


def test_an_allow_permits():
    robots.reset_cache(fetch=_fetcher(ALLOW))
    d = robots.check("https://x.example/feed.xml")
    assert d.allowed is True and d.known is True
    robots.enforce("https://x.example/feed.xml")          # does not raise


def test_an_unreadable_policy_is_UNKNOWN_and_does_not_refuse_by_default():
    """**The deliberate departure from the crawler's fail-closed posture, and its justification.**
    Refusing here means a CDN hiccup silently stops ingestion from a publisher who never objected.
    Allowing means a brief window where we poll someone whose objection we could not read — which
    self-corrects the moment the file is readable again. The two failure modes are not symmetric."""
    robots.reset_cache(fetch=_fetcher(fail=True))
    d = robots.check("https://x.example/feed.xml")
    assert d.allowed is False and d.known is False
    robots.enforce("https://x.example/feed.xml")          # reported, not enforced


@pytest.mark.parametrize("code, expect", [(404, "HTTP 404"), (403, "HTTP 403"), (503, "HTTP 503")])
def test_an_http_failure_reports_its_status_not_just_its_exception_type(code, expect):
    """**From the first live probe.** It reported `robots.txt unavailable (HTTPError)` for a host,
    and that string cannot be acted on: `HTTPError` covers 404 (no robots.txt at all — RFC 9309
    reads it as no restrictions), 403 (the origin refused US, a *stronger* signal than a Disallow),
    and 5xx (an outage, which says nothing). Filing all three under one label loses the only
    distinction an operator would act on."""
    import urllib.error

    def fetch(url):
        raise urllib.error.HTTPError(url, code, "nope", {}, None)

    robots.reset_cache(fetch=fetch)
    d = robots.check("https://x.example/feed.xml")
    assert expect in d.reason and d.known is False


def test_the_posture_is_unchanged_by_the_more_precise_reason():
    """A 404 is still a refusal for discovery. `CRAWLER_DESIGN.md` declines the
    404-means-crawl-freely convention for a commercial reader of newsrooms, and reporting the
    status code more precisely is not a licence to start acting on it differently."""
    import crawler
    import urllib.error

    def fetch(url):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    assert crawler.RobotsPolicy(fetch=fetch).check("https://x.example/").allowed is False


def test_a_200_that_is_not_a_robots_policy_is_UNKNOWN_not_permission():
    """The most common way robots.txt is "missing" is an origin that returns 200 and an HTML page
    for every path. `RobotFileParser` reads that as a policy with no rules, and no rules means
    allow-all — fail-OPEN wearing the costume of fail-closed."""
    robots.reset_cache(fetch=_fetcher("<html><body>404</body></html>"))
    assert robots.check("https://x.example/feed.xml").known is False


def test_strict_mode_refuses_on_unknown_too():
    """The crawler's posture, available to an operator who wants it on the live path."""
    robots.reset_cache(fetch=_fetcher(fail=True))
    import os
    os.environ["RWE_ROBOTS_STRICT"] = "1"
    try:
        with pytest.raises(robots.RobotsRefused):
            robots.enforce("https://x.example/feed.xml")
    finally:
        os.environ.pop("RWE_ROBOTS_STRICT")


def test_the_kill_switch_disables_the_gate_entirely(monkeypatch):
    """A change that can stop ingestion needs one, and it must be reachable from compose."""
    robots.reset_cache(fetch=_fetcher(DENY_US))
    monkeypatch.setenv("RWE_ROBOTS_ENFORCE", "0")
    robots.enforce("https://x.example/feed.xml")          # refused, but not enforced


# --------------------------------------------------------------------------- caching

def test_a_policy_is_fetched_once_per_host_not_once_per_feed():
    """Without the cache, gating every feed fetch would pull robots.txt once per feed per poll
    cycle — hundreds of needless requests a day onto the newsrooms this is meant to spare."""
    f = _fetcher(ALLOW)
    robots.reset_cache(fetch=f)
    for path in ("/a.xml", "/b.xml", "/c.xml"):
        robots.check(f"https://x.example{path}")
    assert f.calls == ["https://x.example/robots.txt"]


def test_a_failed_refresh_keeps_the_last_policy_we_successfully_read(monkeypatch):
    """**What `/crawler` promises publishers**, so it had better be true: "we keep to the last
    policy we successfully read". RFC 9309 permits reusing a cached policy past the TTL while
    robots.txt is unreachable, and discarding a real `Disallow` over a transient would be the worst
    possible direction to fail in."""
    robots.reset_cache(fetch=_fetcher(DENY_US))
    assert robots.check("https://x.example/feed.xml").allowed is False

    monkeypatch.setattr(robots, "CACHE_TTL_SECONDS", -1)   # force every lookup to be stale
    robots._live_fetch = _fetcher(fail=True)               # ...and every refresh to fail
    d = robots.check("https://x.example/feed.xml")
    assert d.allowed is False and d.known is True, "a transient must not erase a real refusal"


def test_a_host_never_read_successfully_stays_unknown(monkeypatch):
    """The retention rule keeps a GOOD policy, not a bad one — it must not manufacture permission
    for a host we have never managed to ask."""
    robots.reset_cache(fetch=_fetcher(fail=True))
    assert robots.check("https://x.example/feed.xml").known is False
    monkeypatch.setattr(robots, "CACHE_TTL_SECONDS", -1)
    assert robots.check("https://x.example/feed.xml").known is False


# --------------------------------------------------------------------------- the live path (F1)

def test_both_live_fetch_seams_are_gated():
    """**F1 itself.** These are the two functions where a request leaves for a publisher's host on
    the live path, and neither checked robots. Driven through the real functions rather than by
    grepping for a call, so moving the gate somewhere still-correct keeps the test passing while
    removing it does not."""
    import rss_ingest
    robots.reset_cache(fetch=_fetcher(DENY_US))
    with pytest.raises(robots.RobotsRefused):
        rss_ingest.fetch_feed("https://x.example/feed.xml")
    with pytest.raises(robots.RobotsRefused):
        rss_ingest.fetch_feed_conditional("https://x.example/feed.xml")


def test_a_refusal_is_counted_apart_from_a_network_failure():
    """A refusal and a network error mean opposite things — one is the publisher answering us, the
    other is us not reaching them. An aggregate that merged them would hide the compliance signal
    in the noise of ordinary flakiness."""
    import rss_ingest
    robots.reset_cache(fetch=_fetcher(DENY_US))
    agg = rss_ingest.ingest_all([("X", "https://x.example/feed.xml")], None, None,
                                fetch=rss_ingest.fetch_feed)
    assert agg["robotsRefused"] == 1
    assert agg["failed"] == 1 and agg["ok"] == 0


def test_a_refusal_is_visible_in_the_run_summary():
    """It was counted and never printed, which is the shape of a feed going silent with nobody
    seeing why. Shown only when non-zero — but when it fires it is the most important line in the
    run, because it is a publisher answering us rather than a machine failing."""
    import rss_ingest
    agg = {"feeds": 9, "ok": 8, "failed": 1, "robotsRefused": 1, "new": 3, "duplicates": 0,
           "skipped": 0, "unknown_outlet": 0, "blocked": 0}
    out = rss_ingest._format_run_summary(agg, 100, 103, 1.0)
    assert "refused by robots.txt" in out and "robots.txt REFUSED" in out

    quiet = rss_ingest._format_run_summary({**agg, "robotsRefused": 0, "failed": 0, "ok": 9},
                                           100, 103, 1.0)
    assert "robots" not in quiet, "a zero line every run is noise; its absence is the report"


def test_the_background_poller_does_not_retry_a_refusal():
    """A refusal is an ANSWER, not a transient. The poller wraps fetches in an exponential backoff
    ladder catching bare Exception, so a refusal was retried — burning the ladder on a decision that
    cannot change. The cached policy meant no extra request reached the publisher, but "we were told
    no, so we asked again" is not a posture to leave in the code that implements respecting
    robots.txt."""
    import feed_service
    import rss_ingest
    robots.reset_cache(fetch=_fetcher(DENY_US))
    svc = feed_service.FeedPoller.__new__(feed_service.FeedPoller)
    svc.timeout, svc.retries, svc.backoff = 5.0, 3, 0.01
    import threading
    svc._stop = threading.Event()

    calls = []
    real = rss_ingest.fetch_feed

    def counting(url, timeout=None):
        calls.append(url)
        return real(url)

    rss_ingest.fetch_feed = counting
    try:
        with pytest.raises(robots.RobotsRefused):
            svc._make_fetch()("https://x.example/feed.xml")
    finally:
        rss_ingest.fetch_feed = real
    assert len(calls) == 1, f"a refusal must not be retried; it was attempted {len(calls)} times"


def test_an_injected_fetcher_bypasses_the_gate():
    """Correct, and worth pinning: a fake fetch reaches no publisher, so gating it would only make
    the suite ask the network for permission to use a fixture."""
    import rss_ingest
    robots.reset_cache(fetch=_fetcher(DENY_US))
    agg = rss_ingest.ingest_all([("X", "https://x.example/feed.xml")], None, None,
                                fetch=lambda _url: b"<rss><channel></channel></rss>")
    assert agg["robotsRefused"] == 0


# --------------------------------------------------------------------------- the crawler keeps its posture

def test_the_crawler_still_fails_closed_on_an_unreadable_policy():
    """The move must not have weakened discovery. `crawler.RobotsPolicy` reads `allowed` alone, so
    an absent policy is still a refusal there — a one-shot probe of a stranger is a different act
    from a recurring poll of an operator-chosen feed."""
    import crawler
    policy = crawler.RobotsPolicy(fetch=_fetcher(fail=True))
    assert policy.check("https://x.example/feed.xml").allowed is False


def test_the_crawler_and_the_live_gate_share_one_parser():
    """Four drifted definitions have been corrected in this audit series. A second robots parser
    would be the fifth, and it is exactly the shape that produced F1: strict rules in one place,
    none in the other."""
    import crawler
    assert issubclass(crawler.RobotsPolicy, robots.RobotsPolicy)
    assert crawler._looks_like_robots is robots._looks_like_robots
    assert crawler.RobotsDecision is robots.RobotsDecision
