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
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import sibling modules
import ingest                # reuse: canonical_url (the ONE dedup key), has_host
import outlet_registry       # reuse: publisher identity + the domains that belong to it
import robots                # reuse: the ONE robots policy — shared with the live poller
import rss_ingest            # reuse: FeedEntry, parse_feed, ingest_entries (the choke point)
import sources               # reuse: SourceAdapter/SourceBatch chassis + the retry discipline

#: Identifies us to publishers, with a contact path. A crawler that cannot be identified cannot be
#: rate-limited or blocked by the site it is reading, which is the site's only recourse — and the
#: contact URL has to RESOLVE for that to be true, which is why `web/app/crawler` now exists.
USER_AGENT = robots.user_agent("Crawler")

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
    #: Drop articles published longer ago than this. ``0`` disables the rule entirely, matching the
    #: ``RWE_RETENTION_MAX_AGE_DAYS`` convention.
    #:
    #: This exists because a publisher's *archive* sitemap and its *news* sitemap are the same file
    #: format and only the contents tell them apart — SCMP's declared sitemap returned 19,962 URLs
    #: spanning years. Story clustering draws from a rolling window (``story_service.scan_days``,
    #: 6 days by default), so an article older than that ingests, occupies a row, and can never
    #: appear in a story. `max_urls` bounds the volume but caps arbitrarily rather than by age; this
    #: bounds it by the only thing that decides whether an article can still become product.
    max_age_days: int = 0
    max_urls: int = 60                   # per cycle, after dedup
    min_interval: float = DEFAULT_MIN_INTERVAL
    max_fetches: int = DEFAULT_MAX_FETCHES
    enabled: bool = True

    @property
    def pattern(self):
        return re.compile(self.article_pattern) if self.article_pattern else None


def load_config(path: "str | None" = None, *, store_=None) -> "list[PublisherCrawlConfig]":
    """Publisher configs from JSON, plus the M11 admission table when a store is supplied.

    Unknown JSON keys are rejected loudly rather than ignored — a typo'd ``max_url`` silently taking
    the default is exactly the kind of quiet misconfiguration that makes a crawler look like it is
    working while it crawls the wrong thing.

    ``store_`` is the M11 seam. `examples/data/crawler_publishers.json` is baked into the image, so
    before M11 admitting a source was a code change and a deploy — which does not scale past the
    eight hand-verified publishers in it, let alone to 1,173 candidates. Admitted rows are
    **appended**, and the JSON wins on a duplicate publisher: those eight were verified against the
    live sites by hand, and a table row must not silently override a checked ``article_pattern`` with
    an empty one.

    Without a store this is byte-identical to what it was, which is what every existing caller
    (`verify_crawler_config.py`, the CLI, the tests) gets."""
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
    if store_ is not None:
        out.extend(admitted_configs(store_, exclude={c.publisher for c in out}))
    return out


def admitted_configs(store_, *, exclude=frozenset()) -> "list[PublisherCrawlConfig]":
    """M11-admitted sources as crawl configs. **Every one is re-checked against its assigned tier.**

    That re-check is the point, not a formality. `CrawlAdapter.in_shadow` already refuses to run an
    unassigned publisher, but it reads `corpus`, which caches its admitted-host snapshot for a
    minute. Filtering the config list through the same predicate means the crawl set is always a
    subset of the assigned set *as corpus currently sees it* — so cache skew can only ever remove a
    source from the crawl, never add one that is unassigned. Any disagreement resolves to "do not
    crawl", which is the fail-safe direction and the one `corpus.DEFAULT_TIER == "A"` demands.

    Both admissible lanes pass. This asked `corpus.is_shadow`, which was correct while shadow was
    the only tier an admission could assign and became a silent hole the moment Tier B existed: a
    Tier B source would be registered, validated and admitted, and then get no crawl config at all —
    carrying no articles, while every step reported success.

    Failures return what was built so far rather than raising: `sources._crawl_adapters` already
    treats a broken crawl config as "no crawling" rather than as "no ingestion", and a store that is
    mid-migration must not take the RSS poller down."""
    import corpus
    import source_admission
    configs = []
    try:
        rows = store_.admitted_crawl_rows()
    except Exception:
        return configs
    for row in rows:
        host = row["host"]
        if not corpus.is_assigned(row.get("publisher") or host, f"https://{host}/"):
            continue
        fields = source_admission.crawl_config_fields(row)
        if fields["publisher"] in exclude:
            continue
        srcs = tuple(DiscoverySource(kind=s["kind"], url=s["url"]) for s in fields.pop("sources"))
        configs.append(PublisherCrawlConfig(sources=srcs, **fields))
    return configs


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
            # Name the kinds ACTUALLY configured. The old wording cited section discovery for every
            # publisher, including sitemap-only ones that configure no section source — and a
            # warning that does not describe your config is a warning people learn to skip.
            kinds = sorted({s.kind for s in c.sources}) or ["(none)"]
            risk = ("an HTML index links to tags, authors and the shop" if "section" in kinds
                    else "a sitemap may list section and tag pages alongside articles")
            problems.append({"code": "no_article_pattern", "publisher": c.publisher,
                             "detail": f"no pattern, and {'/'.join(kinds)} discovery is configured "
                                       f"— {risk}"})
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
# --------------------------------------------------------------------------- #
# Robots — the rules live in `robots.py`, imported rather than redefined.
# --------------------------------------------------------------------------- #
#
# They moved because the LIVE ingestion path needs them and `crawler` imports `rss_ingest`, so
# `rss_ingest` cannot import `crawler` back (F1 of the M7 Stage 2 audit: robots existed only here,
# in a POC that has never run, while the poller running every cycle had no gate at all).
#
# Re-exported so every existing caller — `verify_crawler_config`, `source_validation`, the tests —
# is unchanged, and the crawler keeps its FAIL-CLOSED reading: it acts on `allowed` alone, so an
# absent policy is still a refusal here whatever the live path chooses to do.
RobotsDecision = robots.RobotsDecision
RobotsRefused = robots.RobotsRefused
_looks_like_robots = robots._looks_like_robots


class RobotsPolicy(robots.RobotsPolicy):
    """The shared policy, fetched through this module's retry discipline.

    Only the default fetcher differs from :class:`robots.RobotsPolicy`: discovery goes through
    ``_fetch_text`` (and therefore ``sources._request``'s 429/5xx budget), while the live gate uses
    a plain stdlib GET. The RULES are the same object, which is the point of the move."""

    def __init__(self, fetch: "Callable[[str], str] | None" = None, *,
                 user_agent: str = USER_AGENT):
        super().__init__(fetch or _fetch_text, user_agent=user_agent)

    def check(self, url: str) -> "robots.RobotsDecision":
        """Whether we may fetch ``url``, and how long to wait between requests to its host.

        An absent policy is a REFUSAL, not a permission. The conventional crawler default (no
        robots.txt means crawl freely) is a reasonable reading for a search engine with a
        decades-old norm behind it; it is not a reasonable reading for a commercial reader of
        newsrooms that has never spoken to the publisher.
        """
        return super().check(url)


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


#: Sort sentinel for an entry with no readable date — orders after every real timestamp.
_UNDATED_SORTS_LAST = datetime(1, 1, 1, tzinfo=timezone.utc)


def _published_utc(value) -> Optional[datetime]:
    """An entry's ``published_at`` as an aware UTC datetime, or ``None`` when it cannot be read.

    Unreadable and absent collapse to the same answer deliberately: the age rule cannot act on a
    date it does not have, and a value it fails to parse is not evidence of recency.
    """
    s = (value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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
    #: Unique on-domain article URLs this cycle — the denominator of the shadow-mode question
    #: "what fraction of what we would crawl is genuinely new?". Counted BEFORE the catalog check
    #: and before ``max_urls`` truncates, because a cap applied to the numerator would flatter the
    #: ratio: cap at 10, find 10 new ones out of 500 candidates, and report 100% marginal value.
    candidates: int = 0
    #: How many of those candidates carry a publication date. Counted on the SAME set as
    #: ``candidates`` so the two are directly comparable.
    #:
    #: This is a quality measure, not a volume one, and it decides whether a rung is worth using.
    #: A section page states no date, so an article discovered there ingests with ``published_at``
    #: of None: `ingest_entries` counts it as missing metadata, it has no position in Latest, and
    #: clustering has nothing to order it by. A news sitemap carries the publisher's own timestamp.
    #: Two rungs can therefore return the same number of articles and not be worth the same.
    dated: int = 0
    #: Dropped by ``max_age_days``: had a readable date, published before the cutoff.
    too_old: int = 0
    #: Dropped by ``max_age_days`` for having NO usable date. Separate from ``too_old`` because the
    #: two mean opposite things about the configuration: a pile of `too_old` says the sitemap is an
    #: archive, a pile of `undated` says the rung is a section page and the fix is a different URL.
    undated: int = 0
    already_in_catalog: int = 0
    robots_blocked: int = 0
    accepted: int = 0
    capped: int = 0
    waited_seconds: float = 0.0
    latency_ms: float = 0.0
    #: Every fetch/parse failure this cycle. A publisher can survive several of these and still
    #: return articles, so they are diagnostics rather than an outcome.
    fetch_errors: int = 0
    errors: list = field(default_factory=list)
    #: The headline reason this publisher produced NOTHING. Set only when the cycle ends empty, so
    #: a run that recovered on a later rung does not report an error it survived.
    error: Optional[str] = None
    robots_reason: str = ""

    def note_error(self, detail: str) -> None:
        self.fetch_errors += 1
        if len(self.errors) < 5:          # enough to diagnose, bounded so a broken index can't flood
            self.errors.append(detail)

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
                 store_=None, now: "Callable[[], datetime] | None" = None):
        self.config = config
        self.robots = robots if robots is not None else RobotsPolicy()
        self.limiter = limiter if limiter is not None else RateLimiter(config.min_interval)
        self._fetch = fetch or _fetch_text
        self.store = store_
        self._now = now or (lambda: datetime.now(timezone.utc))

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
        for src in self.config.sources:
            if report.fetched >= self.config.max_fetches:
                break
            report.rungs_tried.append(src.kind)
            try:
                entries = self._run_rung(src, report)
            except Exception as e:
                # A rung that fails is a rung that failed — not a publisher that failed. The ladder
                # exists precisely so a broken sitemap falls through to the section page, and an
                # abort here threw that away: Daily Maverick lost a whole cycle to one 404 on one
                # index child, section fallback and all. `sources.py` already treats one adapter's
                # outage as isolated from the others; this is the same rule one level down.
                report.note_error(f"{src.kind} {src.url}: {type(e).__name__}: {e}")
                continue
            kept = self._filter(entries, seen, report)
            if kept:
                report.rung_used = src.kind
                accepted = kept
                break
        if not accepted and report.errors:
            report.error = report.errors[0]
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
        # NEWEST CHILD FIRST. A sitemap index names more sitemaps and is conventionally ordered
        # oldest-first, so taking them in document order spends the whole fetch budget on the
        # deepest archive and never reaches this week. Daily Maverick and Premium Times both
        # returned 100% `too_old` for exactly this reason — a defect here, not an archive-only
        # publisher.
        #
        # The ordering key costs nothing to obtain: an index entry carries `<lastmod>`, which
        # `discover_sitemap` already reads into `published_at`. Children a publisher leaves undated
        # sort last — they are not evidence of recency, the same reading the age filter takes.
        children.sort(key=lambda e: _published_utc(e.published_at) or _UNDATED_SORTS_LAST,
                      reverse=True)
        out = []
        for child in children:
            if report.fetched >= self.config.max_fetches:
                break
            # A stale entry in an index — a sitemap that has been removed but not delisted — is
            # ordinary, and it must cost that child rather than its siblings. Daily Maverick's
            # index carries one; before this, it cost the entire publisher.
            try:
                body = self._get(child.url, report)
            except Exception as e:
                report.note_error(f"sitemap child {child.url}: {type(e).__name__}: {e}")
                continue
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
        max_age = self.config.max_age_days
        cutoff = self._now() - timedelta(days=max_age) if max_age > 0 else None
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
            if cutoff is not None:
                published = _published_utc(e.published_at)
                if published is None:
                    # Undated is EXCLUDED, not waved through. An operator who set an age limit
                    # asked for recent articles, and "we cannot tell how old this is" is not an
                    # answer to that — the same fail-closed reading the robots gate takes. Without
                    # it, pointing a limited publisher at a section page would silently readmit
                    # the entire undated archive the limit exists to keep out.
                    report.undated += 1
                    continue
                if published < cutoff:
                    report.too_old += 1
                    continue
            canon = ingest.canonical_url(url)
            if canon in seen:
                report.duplicate_in_cycle += 1
                continue
            seen.add(canon)
            staged.append((canon, e))
        report.candidates += len(staged)
        report.dated += sum(1 for _c, e in staged if (e.published_at or "").strip())
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

    #: M6.2 — this adapter's `fetch`/`normalize` touch NO store, so the poller runs them off the
    #: ingest write lock. Verified rather than assumed: `sources.default_registry` constructs
    #: `CrawlAdapter(c)` with no `store_`, so `PublisherCrawler.store` is None and the one read the
    #: ladder can make (`existing_feed_urls`, its politeness dedup) is skipped entirely. The whole
    #: of `fetch()` is a robots check plus a discovery-document GET.
    #:
    #: This is the class that has to reach thousands of instances, and it was the measured cost:
    #: ~2.4 s of lock per poll, four polls an hour, against a lock budget that saturates at ~327
    #: sources. If `store_` is ever wired in here, re-derive this flag — a read is still safe under
    #: WAL (API requests already read while adapters write), but a write would not be.
    FETCH_IS_STORE_FREE = True

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
        """Both switches must be on: the global flag **and** this publisher's own.

        ``RWE_CRAWL_ENABLED`` defaults to OFF, so registering this adapter changes nothing until an
        operator turns it on. That matters more here than for the keyed adapters: those need an API
        key to do anything, which is an accidental safety catch this has no equivalent of — a crawl
        config is just a file, and the file already contains six publishers whose URLs
        `CRAWLER_DESIGN.md` calls unverified guesses. Without the global flag, wiring this into the
        registry would have started crawling the BBC, NPR, AP and the Guardian on paths nobody has
        ever checked."""
        if not (sources._bool_env("RWE_CRAWL_ENABLED") and bool(self.config.enabled)):
            return False
        return self.in_shadow()

    def in_shadow(self) -> bool:
        """Whether this publisher sits in an assigned lane — **a hard precondition for crawling it.**

        `corpus.DEFAULT_TIER` is ``"A"``. So an outlet we crawl that nobody has assigned does not
        land somewhere neutral: its articles go straight into the clustering corpus and start
        forming and voting in stories. That is *promotion*, arrived at by omission rather than by
        decision, and it is the one failure this wiring could cause that nobody would notice until a
        crawled outlet turned up in a blindspot claim.

        **The question is "has anyone decided", not "which lane".** This asked `corpus.is_shadow`
        while shadow was the only tier an admission could assign. Now that admission also assigns
        Tier B — the searchable lane a 50,000-outlet corpus is mostly made of — a Tier B source
        would have been registered, validated, and then never crawled, so it could carry no
        articles. `corpus.is_assigned` is the same guard against the same danger, over both lanes;
        neither can reach the story builder, and Tier A is still refused.

        The name is kept because `sources.config_warnings` and the wiring tests reach for it, and a
        rename would be churn against a method whose meaning widened rather than changed."""
        import corpus
        host = self.config.domains[0] if self.config.domains else self.config.publisher
        return corpus.is_assigned(self.config.publisher, f"https://{host}/")

    def shadow_warning(self) -> "str | None":
        """Why this publisher is configured but not crawling, when the reason is its tier."""
        if not (sources._bool_env("RWE_CRAWL_ENABLED") and bool(self.config.enabled)):
            return None
        if self.in_shadow():
            return None
        return (f"{self.config.publisher} is enabled for crawling but is assigned to NO tier — not "
                f"RWE_CORPUS_SHADOW, not RWE_CORPUS_TIER_B, and not the admission table. Tier A is "
                f"the default, so crawling it would put its articles straight into the clustering "
                f"corpus — promotion by omission. Assign it to a lane first.")

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
def plan(configs, *, robots=None, limiter=None, fetch=None, store_=None,
         now=None) -> "list[dict]":
    """Run every configured publisher's discovery and report, WITHOUT calling ``ingest_entries``.

    This is the POC's default mode and the reason the module is safe to run against production
    data: the only store access is the read-only ``existing_feed_urls`` lookup.

    ``now`` is passed through to :class:`PublisherCrawler` so the **age filter can be pinned**. It
    exists because a test that reached this function could not pin it and therefore ran against the
    real clock: a fixture dated seven days before a hard-coded "recent" date passed for a week and
    then began failing, mid-session, when the wall clock crossed the boundary. A test that expires
    is a latent failure, and the seam was already there one layer down.
    """
    out = []
    for c in configs:
        if not c.enabled:
            out.append({"publisher": c.publisher, "skipped": "disabled"})
            continue
        crawler = PublisherCrawler(c, robots=robots, limiter=limiter, fetch=fetch, store_=store_,
                                   **({"now": now} if now is not None else {}))
        entries, report = crawler.crawl()
        row = report.as_dict()
        row["genuinelyNew"] = max(report.candidates - report.already_in_catalog, 0)
        row["marginalValue"] = (round(row["genuinelyNew"] / report.candidates, 3)
                                if report.candidates else None)
        row["sample"] = [{"url": e.url, "title": e.title, "publishedAt": e.published_at}
                         for e in entries[:5]]
        out.append(row)
    return out


def _why_empty(r: dict) -> str:
    """Which gate closed, for a publisher that yielded no candidates.

    A shadow run reporting ``0 candidates`` with no reason is undiagnosable without re-running it
    against the publisher — the one thing this framework exists to avoid doing casually. The gates
    are named in the order they fire, so the first non-zero counter is the one that mattered.
    """
    if r.get("error"):
        return f"error: {r['error']}"
    if r.get("robots_blocked"):
        return f"robots refused {r['robots_blocked']} fetch(es): {r.get('robots_reason') or 'disallowed'}"
    if not r.get("fetched"):
        return "no discovery document was fetched"
    if not r.get("discovered"):
        return "discovery documents fetched but parsed to 0 entries — wrong URL or wrong kind"
    if r.get("off_domain") and not r.get("pattern_rejected"):
        return f"all {r['off_domain']} discovered URLs were off-domain — check `domains`"
    if r.get("undated") and not r.get("too_old"):
        return (f"all {r['undated']} URLs had no publication date and `max_age_days` is set — this "
                f"rung is almost certainly a section page; point it at a news sitemap")
    if r.get("too_old"):
        return (f"{r['too_old']} URLs were older than `max_age_days` — likely an archive sitemap "
                f"rather than a news one")
    if r.get("pattern_rejected"):
        return (f"all {r['pattern_rejected']} on-domain URLs failed `article_pattern` "
                f"— the crawler would ingest nothing")
    return "no candidates survived filtering"


def shadow_summary(rows) -> dict:
    """Aggregate the shadow question: of what we would crawl, how much is genuinely new?

    ``marginalValue`` is ``genuinelyNew / candidates``. It is the number that decides whether this
    framework ships at all — if the crawler mostly rediscovers what RSS already delivered, a low
    ratio here is the cheapest possible place to learn it, and stopping is the correct outcome
    rather than a failure.

    ``None`` rather than ``0.0`` when a publisher yielded no candidates: "we found nothing to
    compare" and "we found things and none were new" are different answers, and averaging the first
    into the second as a zero would understate the crawler's value on the strength of a broken
    config.
    """
    per = []
    tot_c = tot_new = tot_dated = tot_old = tot_undated = 0
    for r in rows:
        if "candidates" not in r:                     # a disabled publisher was skipped
            per.append({"publisher": r.get("publisher"), "skipped": r.get("skipped")})
            continue
        c, new = r["candidates"], r["genuinelyNew"]
        tot_c += c
        tot_new += new
        tot_dated += r.get("dated", 0)
        tot_old += r.get("too_old", 0)
        tot_undated += r.get("undated", 0)
        per.append({"publisher": r["publisher"], "candidates": c, "dated": r.get("dated", 0),
                    "datedShare": (round(r.get("dated", 0) / c, 3) if c else None),
                    "tooOld": r.get("too_old", 0), "undated": r.get("undated", 0),
                    "filteredByAge": r.get("too_old", 0) + r.get("undated", 0),
                    "alreadyInCatalog": r["already_in_catalog"], "genuinelyNew": new,
                    "marginalValue": r["marginalValue"], "rungUsed": r.get("rung_used"),
                    "fetches": r.get("fetched", 0), "fetchErrors": r.get("fetch_errors", 0),
                    "errors": r.get("errors", []), "error": r.get("error"),
                    "note": _why_empty(r) if c == 0 else None})
    return {"publishers": per, "totals": {
        "candidates": tot_c, "dated": tot_dated, "tooOld": tot_old, "undated": tot_undated,
        "filteredByAge": tot_old + tot_undated,
        "datedShare": round(tot_dated / tot_c, 3) if tot_c else None,
        "alreadyInCatalog": tot_c - tot_new, "genuinelyNew": tot_new,
        "marginalValue": round(tot_new / tot_c, 3) if tot_c else None,
        "fetches": sum(r.get("fetched", 0) for r in rows if "candidates" in r)}}


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
    ap.add_argument("--db", default=None,
                    help="database URL for the catalog comparison (default: RWE_DB_URL). Used by "
                         "`plan` for a read-only existing-vs-new measurement; without it every "
                         "discovered URL is reported as new.")
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

    # Shadow mode's whole question is "how much of this is genuinely new?", which cannot be
    # answered without the catalog. Running without --db silently reports every URL as new — a
    # 100% marginal value that would argue for a rollout on no evidence — so say so loudly rather
    # than emitting a confident number derived from an absent comparison.
    store_ = None
    if args.db or os.environ.get("RWE_DB_URL"):
        import store as store_mod
        store_ = store_mod.Store(args.db)
    rows = plan(configs, store_=store_)
    summary = shadow_summary(rows)
    if args.json:
        print(json.dumps({"summary": summary, "publishers": rows}, indent=2))
        return 0
    if store_ is None:
        print("!! no --db and no RWE_DB_URL: every URL is reported as new. The existing-vs-new\n"
              "!! measurement is meaningless without the catalog to compare against.\n")
    print(f"{'publisher':<20} {'rung':<9} {'cand':>6} {'dated':>6} {'dated%':>7} "
          f"{'filtered':>9} {'known':>6} {'new':>6} {'new%':>6}  fetches")
    for p in summary["publishers"]:
        if "candidates" not in p:
            print(f"{p['publisher']:<20} skipped ({p.get('skipped')})")
            continue
        mv = "-" if p["marginalValue"] is None else f"{p['marginalValue']:.0%}"
        ds = "-" if p["datedShare"] is None else f"{p['datedShare']:.0%}"
        print(f"{p['publisher']:<20} {str(p['rungUsed']):<9} {p['candidates']:>6} "
              f"{p['dated']:>6} {ds:>7} {p['filteredByAge']:>9} {p['alreadyInCatalog']:>6} "
              f"{p['genuinelyNew']:>6} {mv:>6}  {p['fetches']}")
        if p.get("note"):
            print(f"{'':<20} -> {p['note']}")
    t = summary["totals"]
    tmv = "-" if t["marginalValue"] is None else f"{t['marginalValue']:.0%}"
    tds = "-" if t["datedShare"] is None else f"{t['datedShare']:.0%}"
    print(f"{'TOTAL':<20} {'':<9} {t['candidates']:>6} {t['dated']:>6} {tds:>7} "
          f"{t['filteredByAge']:>9} {t['alreadyInCatalog']:>6} {t['genuinelyNew']:>6} "
          f"{tmv:>6}  {t['fetches']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
