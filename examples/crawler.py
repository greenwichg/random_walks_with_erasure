"""crawler.py — the publisher crawl framework (read-only POC).

Supplements RSS/API ingestion for publishers whose feeds are partial, stale, or absent. It is a
**discovery** layer, not a scraper: it finds article URLs + the metadata publishers already publish
about them, normalizes them into :class:`rss_ingest.FeedEntry`, and terminates at the existing
``rss_ingest.ingest_entries`` choke point. After that boundary nothing downstream — scoring,
canonical-URL dedup, media selection, persistence, clustering, recommendations — learns that a
crawler was involved.

    robots.txt gate -> discovery ladder (rss -> sitemap -> section) -> FeedEntry[]
                    -> ingest_entries() -> FeedArticle -> everything else

Three decisions shape everything here, and each is a deliberate narrowing:

**It never fetches an article page.** Discovery documents (feeds, sitemaps, section indexes) carry
the URL, headline, and publication date that :class:`rss_ingest.FeedEntry` needs, and publishers
publish them *for* machine consumption. Article pages carry the copyrighted text we have no licence
to hold, and fetching them would multiply request volume by the number of articles instead of the
number of index pages. So a cycle costs ~1-10 requests per publisher, not hundreds, and the body
stays ``None`` — which the pipeline already handles, because most RSS has no body either. Body
extraction is a separate, separately-gated question (see ``docs/CRAWLER_DESIGN.md``); it is not
half-built here.

**robots.txt is a hard gate and it fails CLOSED.** A conventional crawler treats an unreachable
robots.txt as permission. This one treats it as refusal: an unfetchable or unparseable policy means
that publisher is skipped for the cycle. We are a commercial product reading other people's
newsrooms, and "we could not determine whether we were allowed" is not a licence.

**The catalog decides what is new, not the crawler.** Canonical-URL dedup already lives in
``ingest.canonical_url`` behind ``ingest_entries``. The crawler re-uses that same function to skip
URLs it has *already discovered this cycle* and URLs the catalog *already holds*
(``store.existing_feed_urls``) — before fetching anything further. Dedup here is politeness, not
correctness; correctness stays where it already was.

Dependency-free (stdlib ``urllib`` + ``xml.etree`` + ``html.parser``), and every network seam is
injectable, so the whole framework is exercised offline against fixtures::

    python examples/crawler.py plan --publisher NPR       # dry run: what WOULD be ingested
    python examples/crawler.py robots --publisher NPR     # show the resolved robots decision
    python examples/crawler.py config                     # list configured publishers

**This module is not wired into the poller.** Nothing imports it from the ingestion path; it writes
to the catalog only when a caller explicitly invokes ``CrawlAdapter.poll_once``, which no production
code does yet. See ``docs/CRAWLER_DESIGN.md`` for the staged rollout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import sibling modules
import ingest                # reuse: canonical_url (the ONE dedup key), has_host
import outlet_registry       # reuse: publisher identity + the domains that belong to it
import rss_ingest            # reuse: FeedEntry, parse_feed, ingest_entries (the choke point)
import sources               # reuse: SourceAdapter/SourceBatch chassis + the retry discipline

#: Identifies us to publishers, with a contact path. A crawler that cannot be identified cannot be
#: rate-limited or blocked by the site it is reading, which is the site's only recourse.
USER_AGENT = "InformationHealth-Crawler/0.1 (+https://hidden-view.com/crawler)"

#: Conservative floor between two requests to the SAME host, in seconds. Overridden upward (never
#: downward) by a publisher's own ``Crawl-delay``.
DEFAULT_MIN_INTERVAL = 2.0

#: Ceiling on discovery documents fetched per publisher per cycle. The ladder stops early on
#: success, so this bounds the pathological case (a sitemap index pointing at 400 child sitemaps).
DEFAULT_MAX_FETCHES = 6

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                            "crawler_publishers.json")


# --------------------------------------------------------------------------- #
# Configuration — one entry per publisher, data not code.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DiscoverySource:
    """One rung of the ladder. ``kind`` selects the parser; ``url`` is operator-configured."""
    kind: str          # "rss" | "sitemap" | "section"
    url: str


@dataclass(frozen=True)
class PublisherCrawlConfig:
    """Everything publisher-specific, kept as data so adding a publisher is a config edit.

    ``article_pattern`` is the guard that keeps section-page discovery honest: a newsroom index
    links to tag pages, author pages, live blogs, and the shop, and only a URL shape the operator
    has confirmed is an article gets through. It is applied to EVERY rung, not just section pages,
    so a mis-configured sitemap cannot inject section indexes into the catalog either.

    ``domains`` and ``discovery_domains`` are deliberately separate. Publishers routinely serve
    feeds from a different host than their articles — the BBC's feeds are on ``bbci.co.uk`` while
    its journalism is on ``bbc.co.uk``. Folding the feed host into ``domains`` to make the config
    validate would widen the set of hosts allowed to yield ARTICLES, which is the one boundary that
    protects a publisher's identity and lean from being attached to a URL it did not publish. So
    the feed host is declared as somewhere we may FETCH, never as somewhere articles may live.
    """
    publisher: str                       # registry canonical name — resolved, never invented
    domains: tuple = ()                  # hosts ARTICLES may live on — the security boundary
    discovery_domains: tuple = ()        # extra hosts allowed to SERVE discovery documents
    sources: tuple = ()                  # the ordered ladder
    article_pattern: str = ""            # regex every accepted article URL must match
    max_urls: int = 60                   # per cycle, after dedup
    min_interval: float = DEFAULT_MIN_INTERVAL
    max_fetches: int = DEFAULT_MAX_FETCHES
    enabled: bool = True

    @property
    def pattern(self):
        return re.compile(self.article_pattern) if self.article_pattern else None


def load_config(path: "str | None" = None) -> "list[PublisherCrawlConfig]":
    """Publisher configs from JSON. Unknown keys are rejected loudly rather than ignored — a typo'd
    ``max_url`` silently taking the default is exactly the kind of quiet misconfiguration that makes
    a crawler look like it is working while it crawls the wrong thing."""
    p = path or _CONFIG_PATH
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    allowed = set(PublisherCrawlConfig.__dataclass_fields__) - {"sources"}
    out = []
    for row in raw.get("publishers", []):
        unknown = set(row) - allowed - {"sources"}
        if unknown:
            raise ValueError(f"{p}: unknown key(s) {sorted(unknown)} for {row.get('publisher')!r}")
        srcs = tuple(DiscoverySource(kind=s["kind"], url=s["url"]) for s in row.get("sources", []))
        out.append(PublisherCrawlConfig(**{k: v for k, v in row.items() if k != "sources"},
                                        sources=srcs))
    return out


def lint_config(configs) -> "list[dict]":
    """Config problems as ``{code, publisher, detail}``. Reused by the tests and the CLI so a broken
    config is caught before it is a crawl, not after."""
    problems = []
    reg = outlet_registry.default_registry()
    for c in configs:
        canon = reg.canonical(c.publisher)
        if canon is None:
            problems.append({"code": "unknown_publisher", "publisher": c.publisher,
                             "detail": "not in the outlet registry — it would ingest with no lean"})
        elif canon != c.publisher:
            problems.append({"code": "non_canonical_publisher", "publisher": c.publisher,
                             "detail": f"registry canonical name is {canon!r}"})
        if not c.domains:
            problems.append({"code": "no_domains", "publisher": c.publisher,
                             "detail": "every discovered URL would be rejected as off-domain"})
        if not c.sources:
            problems.append({"code": "no_sources", "publisher": c.publisher,
                             "detail": "no discovery ladder configured"})
        for s in c.sources:
            if s.kind not in _DISCOVERY:
                problems.append({"code": "unknown_source_kind", "publisher": c.publisher,
                                 "detail": f"{s.kind!r} is not one of {sorted(_DISCOVERY)}"})
            host = urllib.parse.urlsplit(s.url).hostname or ""
            fetchable = tuple(c.domains) + tuple(c.discovery_domains)
            if fetchable and not _host_allowed(host, fetchable):
                problems.append({"code": "source_off_domain", "publisher": c.publisher,
                                 "detail": f"{s.url} is on neither domains nor discovery_domains "
                                           f"{list(fetchable)} — usually a typo"})
        if not c.article_pattern:
            problems.append({"code": "no_article_pattern", "publisher": c.publisher,
                             "detail": "section discovery would accept tag/author/index pages"})
        else:
            try:
                re.compile(c.article_pattern)
            except re.error as e:
                problems.append({"code": "bad_article_pattern", "publisher": c.publisher,
                                 "detail": f"{c.article_pattern!r}: {e}"})
        if c.min_interval < 1.0:
            problems.append({"code": "interval_too_low", "publisher": c.publisher,
                             "detail": f"{c.min_interval}s between requests is not polite"})
    return problems


def _host_allowed(host: str, domains) -> bool:
    """Whether ``host`` belongs to one of ``domains`` — exact match or a subdomain of it.

    Suffix matching is anchored on a dot boundary deliberately: a bare ``endswith("bbc.co.uk")``
    also accepts ``notbbc.co.uk``, which is how a crawler ends up ingesting an impersonator under a
    trusted publisher's name and lean.
    """
    h = (host or "").lower().lstrip(".")
    if h.startswith("www."):
        h = h[4:]
    return any(h == d or h.endswith("." + d) for d in (d.lower() for d in domains))


# --------------------------------------------------------------------------- #
# robots.txt — the gate. Fails closed.
# --------------------------------------------------------------------------- #
@dataclass
class RobotsDecision:
    allowed: bool
    reason: str
    crawl_delay: Optional[float] = None


def _looks_like_robots(body: "str | None") -> bool:
    """Whether a 200 response is actually a robots policy.

    This check is load-bearing, not defensive tidiness. ``RobotFileParser`` parses an HTML 404
    page, a captive-portal login, or a CDN error into a policy with **no rules**, and a policy with
    no rules answers ``can_fetch`` with *True* — so without this, the most common way for
    robots.txt to be unavailable (a server that returns 200 and a web page for everything) reads as
    blanket permission. That is fail-OPEN wearing the costume of fail-closed.

    The bar is one ``User-agent:`` line. A whitespace-only body is refused too: it is a valid
    allow-all in principle, but an empty 200 is also what a broken origin returns, and a publisher
    who means "allow everything" writes ``User-agent: *`` — the cost of refusing the ambiguous case
    is that we skip a publisher and say so in the report.
    """
    for line in (body or "").splitlines():
        if line.split("#", 1)[0].strip().lower().startswith("user-agent:"):
            return True
    return False


class RobotsPolicy:
    """Per-host robots.txt, fetched once per run and cached.

    ``fetch(url) -> str`` is injected so this is testable without a network, and so the one place
    that talks to a publisher's origin stays visible.
    """

    def __init__(self, fetch: "Callable[[str], str] | None" = None, *, user_agent: str = USER_AGENT):
        self._fetch = fetch or _fetch_text
        self._user_agent = user_agent
        self._cache: "dict[str, tuple]" = {}

    def _policy_for(self, host: str):
        if host in self._cache:
            return self._cache[host]
        url = f"https://{host}/robots.txt"
        try:
            body = self._fetch(url)
        except Exception as e:                       # unreachable, 5xx, TLS failure, timeout
            entry = (None, f"robots.txt unavailable ({type(e).__name__})")
        else:
            if not _looks_like_robots(body):
                entry = (None, "robots.txt is not a robots policy")
            else:
                rp = urllib.robotparser.RobotFileParser()
                try:
                    rp.parse(body.splitlines())
                    entry = (rp, "")
                except Exception as e:               # a body that is not robots.txt at all
                    entry = (None, f"robots.txt unparseable ({type(e).__name__})")
        self._cache[host] = entry
        return entry

    def check(self, url: str) -> RobotsDecision:
        """Whether we may fetch ``url``, and how long to wait between requests to its host.

        An absent policy is a REFUSAL, not a permission. The conventional crawler default (no
        robots.txt means crawl freely) is a reasonable reading for a search engine with a
        decades-old norm behind it; it is not a reasonable reading for a commercial reader of
        newsrooms that has never spoken to the publisher.
        """
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if not host:
            return RobotsDecision(False, "no host")
        rp, err = self._policy_for(host)
        if rp is None:
            return RobotsDecision(False, err or "no robots policy")
        try:
            ok = rp.can_fetch(self._user_agent, url)
        except Exception as e:
            return RobotsDecision(False, f"robots evaluation failed ({type(e).__name__})")
        delay = None
        try:
            d = rp.crawl_delay(self._user_agent)
            delay = float(d) if d is not None else None
        except Exception:
            delay = None
        return RobotsDecision(bool(ok), "" if ok else "disallowed by robots.txt", delay)


# --------------------------------------------------------------------------- #
# Rate limiting — per host, never per publisher.
# --------------------------------------------------------------------------- #
class RateLimiter:
    """A minimum interval between requests to the same HOST.

    Keyed on host rather than publisher because the limit protects the publisher's *server*, and
    two configured publishers can share one (a group's titles behind one CDN). ``sleep`` is
    injected so tests assert the waits without taking them.
    """

    def __init__(self, default_interval: float = DEFAULT_MIN_INTERVAL, *,
                 sleep: "Callable[[float], None] | None" = None,
                 clock: "Callable[[], float] | None" = None):
        self.default_interval = default_interval
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._last: "dict[str, float]" = {}
        self.waited_seconds = 0.0

    def wait(self, url: str, interval: "float | None" = None) -> float:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        gap = self.default_interval if interval is None else max(interval, 0.0)
        now = self._clock()
        last = self._last.get(host)
        slept = 0.0
        if last is not None:
            remaining = gap - (now - last)
            if remaining > 0:
                self._sleep(remaining)
                slept = remaining
                self.waited_seconds += remaining
        self._last[host] = self._clock()
        return slept


# --------------------------------------------------------------------------- #
# Fetching — one seam, reusing the retry discipline the source adapters already have.
# --------------------------------------------------------------------------- #
def _fetch_text(url: str, *, timeout: float = 15.0) -> str:
    """GET a discovery document as text, with the shared 429/5xx/connection retry policy.

    Reuses ``sources._request`` rather than reimplementing backoff: that function already encodes
    decisions this crawler must not relitigate (429 is retried only with a ``Retry-After`` we are
    willing to honour; a single call has a total sleep budget). It is private today — promoting it
    to a shared helper is a Phase-2 item in the design doc, deliberately not done here so this POC
    changes no production ingestion file.
    """
    return sources._request(
        url, read=lambda r: r.read().decode("utf-8", errors="replace"),
        headers={"User-Agent": USER_AGENT,
                 "Accept": "application/xml, text/xml, application/rss+xml, text/html;q=0.9, */*;q=0.5"},
        timeout=timeout)


# --------------------------------------------------------------------------- #
# Discovery — each rung returns FeedEntry[], the normalized shape the pipeline already speaks.
# --------------------------------------------------------------------------- #
def _local(tag) -> str:
    """A namespace-stripped, lower-cased local tag name."""
    return str(tag).rsplit("}", 1)[-1].lower()


def discover_rss(body: str, _base: str = "") -> "list[rss_ingest.FeedEntry]":
    """Reuse the existing feed parser verbatim — a crawled feed is a feed."""
    _title, entries = rss_ingest.parse_feed(body.encode("utf-8") if isinstance(body, str) else body)
    return list(entries)


def discover_sitemap(body: str, base: str = "") -> "list[rss_ingest.FeedEntry]":
    """Parse a ``urlset`` (article URLs) or a ``sitemapindex`` (pointers to more sitemaps).

    A ``sitemapindex`` yields entries whose URL is another *sitemap* — the caller (``_run_ladder``)
    recognises this and descends one level. Google News sitemaps carry ``news:title`` and
    ``news:publication_date``, which is a real headline and a real timestamp rather than something
    inferred; when only ``lastmod`` is present it is used, because a sitemap's ``lastmod`` is the
    publisher's own statement about the document.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    out = []
    is_index = _local(root.tag) == "sitemapindex"
    for node in list(root):
        if _local(node.tag) not in ("url", "sitemap"):
            continue
        loc = title = published = None
        for child in node.iter():
            name = _local(child.tag)
            text = (child.text or "").strip()
            if not text:
                continue
            if name == "loc" and loc is None:
                loc = text
            elif name == "title" and title is None:          # news:title
                title = text
            elif name == "publication_date" and published is None:
                published = text
            elif name == "lastmod" and published is None:
                published = text
        if not loc:
            continue
        out.append(rss_ingest.FeedEntry(
            url=loc, title=title or "", published_at=_iso_or_none(published),
            source_type="sitemap-index" if is_index else "crawl"))
    return out


class _LinkExtractor(HTMLParser):
    """Anchors from a section page: ``(href, anchor text)``.

    Anchor text is the only headline a section index offers. It is often the real headline; it is
    sometimes "Read more". :class:`rss_ingest.FeedEntry` sanitizes it on construction, and
    ``ingest_entries`` counts a missing title in ``missing_metadata`` rather than dropping the
    article — so a weak title degrades a record instead of losing it.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: "list[tuple[str, str]]" = []
        self._href: Optional[str] = None
        self._text: "list[str]" = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._href, self._text = href, []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href, self._text = None, []


def discover_section(body: str, base: str = "") -> "list[rss_ingest.FeedEntry]":
    """Article links from a section/index page, resolved against ``base``.

    No publication date: a section page does not state one, and inventing "now" would put a
    five-year-old feature at the top of Latest. ``published_at`` stays ``None`` and
    ``ingest_entries`` records it as missing metadata — the honest outcome, and the reason this
    rung sits LAST on the ladder.
    """
    p = _LinkExtractor()
    try:
        p.feed(body)
    except Exception:
        return []
    out, seen = [], set()
    for href, text in p.links:
        url = urllib.parse.urljoin(base, href)
        if url in seen:
            continue
        seen.add(url)
        out.append(rss_ingest.FeedEntry(url=url, title=text, source_type="crawl"))
    return out


_DISCOVERY = {"rss": discover_rss, "sitemap": discover_sitemap, "section": discover_section}


def _iso_or_none(value) -> Optional[str]:
    """A sitemap timestamp as the pipeline's ISO-UTC form, or ``None``. Reuses the feed parser's
    own date handling so a crawled date and a feed date are the same kind of value."""
    if not value:
        return None
    return rss_ingest._to_iso(value) or None


# --------------------------------------------------------------------------- #
# The crawl itself
# --------------------------------------------------------------------------- #
@dataclass
class CrawlReport:
    """What one publisher's cycle did. Every drop is counted under the reason it was dropped —
    a crawler that returns 0 articles must be able to say WHICH gate closed."""
    publisher: str
    fetched: int = 0
    rungs_tried: list = field(default_factory=list)
    rung_used: Optional[str] = None
    discovered: int = 0
    off_domain: int = 0
    pattern_rejected: int = 0
    duplicate_in_cycle: int = 0
    already_in_catalog: int = 0
    robots_blocked: int = 0
    accepted: int = 0
    capped: int = 0
    waited_seconds: float = 0.0
    latency_ms: float = 0.0
    error: Optional[str] = None
    robots_reason: str = ""

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["waited_seconds"] = round(self.waited_seconds, 2)
        d["latency_ms"] = round(self.latency_ms, 1)
        return d


class PublisherCrawler:
    """Runs one publisher's discovery ladder under the robots gate and the rate limiter.

    Produces :class:`rss_ingest.FeedEntry` and stops. It does not score, persist, or decide what is
    new — those already have one home each, behind ``ingest_entries``.
    """

    def __init__(self, config: PublisherCrawlConfig, *, robots: "RobotsPolicy | None" = None,
                 limiter: "RateLimiter | None" = None,
                 fetch: "Callable[[str], str] | None" = None,
                 store_=None):
        self.config = config
        self.robots = robots if robots is not None else RobotsPolicy()
        self.limiter = limiter if limiter is not None else RateLimiter(config.min_interval)
        self._fetch = fetch or _fetch_text
        self.store = store_

    def _get(self, url: str, report: CrawlReport) -> Optional[str]:
        """One gated fetch: robots, then rate limit, then the request."""
        decision = self.robots.check(url)
        if not decision.allowed:
            report.robots_blocked += 1
            report.robots_reason = report.robots_reason or decision.reason
            return None
        interval = max(self.config.min_interval, decision.crawl_delay or 0.0)
        report.waited_seconds += self.limiter.wait(url, interval)
        report.fetched += 1
        return self._fetch(url)

    def crawl(self) -> "tuple[list, CrawlReport]":
        """Walk the ladder until a rung yields accepted articles. Returns ``(entries, report)``.

        Stopping at the first rung that WORKS is the whole point of the ordering: RSS is the
        publisher's own machine-readable offer and costs one request, so a publisher whose feed is
        healthy is never sitemap-crawled. The ladder exists for the days the feed is empty.
        """
        t0 = time.perf_counter()
        report = CrawlReport(publisher=self.config.publisher)
        accepted: "list[rss_ingest.FeedEntry]" = []
        seen: "set[str]" = set()
        try:
            for src in self.config.sources:
                if report.fetched >= self.config.max_fetches:
                    break
                report.rungs_tried.append(src.kind)
                entries = self._run_rung(src, report)
                kept = self._filter(entries, seen, report)
                if kept:
                    report.rung_used = src.kind
                    accepted = kept
                    break
        except Exception as e:
            report.error = f"{type(e).__name__}: {e}"
        if len(accepted) > self.config.max_urls:
            report.capped = len(accepted) - self.config.max_urls
            accepted = accepted[:self.config.max_urls]
        report.accepted = len(accepted)
        report.latency_ms = (time.perf_counter() - t0) * 1000.0
        return accepted, report

    def _run_rung(self, src: DiscoverySource, report: CrawlReport) -> list:
        body = self._get(src.url, report)
        if body is None:
            return []
        entries = _DISCOVERY[src.kind](body, src.url)
        report.discovered += len(entries)
        if src.kind != "sitemap":
            return entries
        # A sitemap index names more sitemaps rather than articles. Descend exactly one level:
        # deeper recursion is how a crawler quietly turns one cycle into a thousand requests.
        children = [e for e in entries if e.source_type == "sitemap-index"]
        if not children:
            return entries
        out = []
        for child in children:
            if report.fetched >= self.config.max_fetches:
                break
            body = self._get(child.url, report)
            if body is None:
                continue
            grand = discover_sitemap(body, child.url)
            report.discovered += len(grand)
            out.extend(e for e in grand if e.source_type != "sitemap-index")
        return out

    def _filter(self, entries, seen: set, report: CrawlReport) -> list:
        """Domain -> article-pattern -> in-cycle dedup -> already-in-catalog.

        Ordered cheapest-first, and the catalog check is LAST because it is the only one that
        touches the database. Each rejection is counted separately so an empty cycle is diagnosable
        without re-running it.
        """
        pattern = self.config.pattern
        staged: "list[tuple[str, rss_ingest.FeedEntry]]" = []
        for e in entries:
            url = ingest.normalize_url(e.url)
            if not ingest.has_host(url):
                report.off_domain += 1
                continue
            host = urllib.parse.urlsplit(url).hostname or ""
            if not _host_allowed(host, self.config.domains):
                report.off_domain += 1
                continue
            if pattern is not None and not pattern.search(url):
                report.pattern_rejected += 1
                continue
            canon = ingest.canonical_url(url)
            if canon in seen:
                report.duplicate_in_cycle += 1
                continue
            seen.add(canon)
            staged.append((canon, e))
        if staged and self.store is not None:
            # One batched read, not one per URL. Skipping what the catalog already holds is the
            # single biggest politeness win available: a publisher's sitemap is mostly articles we
            # ingested yesterday.
            try:
                known = self.store.existing_feed_urls([c for c, _ in staged])
            except Exception:
                known = set()
            if known:
                report.already_in_catalog += sum(1 for c, _ in staged if c in known)
                staged = [(c, e) for c, e in staged if c not in known]
        for _canon, e in staged:
            e.publisher_hint = e.publisher_hint or self.config.publisher
            e.source_type = "crawl"
            e.source_provider = self.config.publisher
        return [e for _c, e in staged]


# --------------------------------------------------------------------------- #
# The adapter — the seam onto the existing pipeline. Not yet registered anywhere.
# --------------------------------------------------------------------------- #
class CrawlAdapter(sources.SourceAdapter):
    """One configured publisher as a :class:`sources.SourceAdapter`.

    Inheriting the chassis means ``poll_once`` already does quota -> ``ingest_entries`` -> health,
    identically to every other source. The crawler contributes discovery and nothing else, which is
    why this class is short: everything after the FeedEntry list is a solved problem here.
    """

    source_type = "crawl"

    def __init__(self, config: PublisherCrawlConfig, *, robots=None, limiter=None,
                 fetch=None, store_=None):
        self.config = config
        self.provider = config.publisher
        self._robots, self._limiter, self._fetch_fn, self._store = robots, limiter, fetch, store_
        self.last_report: Optional[CrawlReport] = None

    @property
    def health_key(self) -> str:
        return f"crawl://{self.config.publisher.lower().replace(' ', '-')}"

    def enabled(self) -> bool:
        return bool(self.config.enabled)

    def interval(self) -> float:
        return sources._float_env("RWE_CRAWL_INTERVAL", 900.0)

    def max_articles(self) -> Optional[int]:
        return self.config.max_urls

    def _crawler(self, store_=None) -> PublisherCrawler:
        return PublisherCrawler(self.config, robots=self._robots, limiter=self._limiter,
                                fetch=self._fetch_fn, store_=store_ or self._store)

    def fetch(self):
        entries, report = self._crawler().crawl()
        self.last_report = report
        if report.error:
            raise RuntimeError(report.error)
        return entries

    def normalize(self, raw) -> sources.SourceBatch:
        return sources.SourceBatch(provider=self.provider, source_type=self.source_type,
                                   fetched_at=sources._now_iso(), entries=list(raw),
                                   raw_count=(self.last_report.discovered if self.last_report else 0))


# --------------------------------------------------------------------------- #
# Read-only planning — what a cycle WOULD ingest, without ingesting it.
# --------------------------------------------------------------------------- #
def plan(configs, *, robots=None, limiter=None, fetch=None, store_=None) -> "list[dict]":
    """Run every configured publisher's discovery and report, WITHOUT calling ``ingest_entries``.

    This is the POC's default mode and the reason the module is safe to run against production
    data: the only store access is the read-only ``existing_feed_urls`` lookup.
    """
    out = []
    for c in configs:
        if not c.enabled:
            out.append({"publisher": c.publisher, "skipped": "disabled"})
            continue
        crawler = PublisherCrawler(c, robots=robots, limiter=limiter, fetch=fetch, store_=store_)
        entries, report = crawler.crawl()
        row = report.as_dict()
        row["sample"] = [{"url": e.url, "title": e.title, "publishedAt": e.published_at}
                         for e in entries[:5]]
        out.append(row)
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _select(configs, publisher: "str | None"):
    if not publisher:
        return configs
    want = publisher.strip().lower()
    return [c for c in configs if c.publisher.lower() == want]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Publisher crawl framework (read-only POC).")
    ap.add_argument("command", choices=("plan", "robots", "config", "lint"))
    ap.add_argument("--publisher", help="limit to one configured publisher")
    ap.add_argument("--config", help="path to crawler_publishers.json")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    configs = _select(load_config(args.config), args.publisher)
    if args.publisher and not configs:
        print(f"no configured publisher named {args.publisher!r}")
        return 2

    if args.command == "config":
        rows = [{"publisher": c.publisher, "enabled": c.enabled, "domains": list(c.domains),
                 "ladder": [s.kind for s in c.sources], "maxUrls": c.max_urls,
                 "minInterval": c.min_interval} for c in configs]
        print(json.dumps(rows, indent=2) if args.json else
              "\n".join(f"{r['publisher']:<20} {'on ' if r['enabled'] else 'off'} "
                        f"{'->'.join(r['ladder']):<22} {r['domains']}" for r in rows))
        return 0

    if args.command == "lint":
        problems = lint_config(configs)
        print(json.dumps(problems, indent=2) if args.json else
              ("\n".join(f"{p['code']:<24} {p['publisher']:<20} {p['detail']}" for p in problems)
               or "config is clean"))
        return 1 if problems else 0

    if args.command == "robots":
        policy = RobotsPolicy()
        rows = []
        for c in configs:
            for s in c.sources:
                d = policy.check(s.url)
                rows.append({"publisher": c.publisher, "url": s.url, "allowed": d.allowed,
                             "reason": d.reason, "crawlDelay": d.crawl_delay})
        print(json.dumps(rows, indent=2) if args.json else
              "\n".join(f"{'ALLOW' if r['allowed'] else 'DENY '} {r['publisher']:<18} "
                        f"{r['url']}  {r['reason']}" for r in rows))
        return 0

    rows = plan(configs)
    print(json.dumps(rows, indent=2) if args.json else
          "\n".join(f"{r.get('publisher'):<20} rung={r.get('rung_used')} "
                    f"accepted={r.get('accepted')} discovered={r.get('discovered')} "
                    f"known={r.get('already_in_catalog')} err={r.get('error')}" for r in rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
