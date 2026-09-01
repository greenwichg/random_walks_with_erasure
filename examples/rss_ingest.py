"""Configurable RSS/Atom news ingestion — the live-news CATALOG foundation.

Pulls articles from operator-configured RSS/Atom feeds, scores each one through the SAME pipeline the
reading path uses (``ingest.Scorer`` + the baseline enricher), and stores them in the ``feed_articles``
catalog (``store.FeedArticle``), deduplicated by canonical URL. It preserves what the scored model
does not: the real publisher article URL, the publisher, the publication timestamp, the title, the
description, and (when the feed provides it) the body. The feed's ``<category>`` tags are parsed and
handed to the scorer as the highest-confidence input to ``ingest.classify_topic``.

Deliberately scoped to *ingestion only*: it does **not** touch the recommendation corpus, the report,
the recommendation algorithms, or the UI. The recommender keeps using the existing corpus; a later
milestone will draw recommendations/discovery from this catalog.

Dependency-free (stdlib ``xml.etree`` + ``urllib``) — no feedparser/requests. Feeds are
operator-configured (not user-supplied), so fetching them is not a user-facing SSRF surface.

    # one-shot ingest of the configured feeds (a feeds file, a comma list, or RWE_RSS_FEEDS)
    python examples/rss_ingest.py run --feeds deploy/rss_feeds.example.txt
    python examples/rss_ingest.py status                 # catalog size + most-recent articles
    python examples/rss_ingest.py parse feed.xml         # parse a local file (offline; for testing)

Feeds config: one entry per line (or comma-separated), each ``url`` or ``Publisher Name|url``;
blank lines and ``#`` comments are ignored.
"""

from __future__ import annotations

import argparse
import dataclasses
import email.utils
import math
import os
import sys
import time
import urllib.error          # explicit: urllib.request only exposes it as an import side effect
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import sibling modules
import ingest
import enrich
import store
import media                     # image SELECTION (pick_best_image) — metadata only, never downloads
import text_utils                # the ONE canonical HTML→text normalizer (used by FeedEntry below)
import robots                    # the ONE robots policy + the ONE user-agent
import location                  # Location Resolver — canonical publisher country/language (Phase 0)

#: F2 of the M7 Stage 2 audit: this used to read
#: ``InformationHealth-RSS/0.1 (+https://code.claude.com)`` — a documentation site belonging to
#: another organisation entirely. A publisher trying to find out who was polling their newsroom, or
#: to ask us to stop, was sent to the wrong company. Composed in one place now, so no path can
#: identify us as somebody else again.
_USER_AGENT = robots.user_agent("RSS")


@dataclass
class FeedEntry:
    """One article as a source describes it — before scoring. The **single normalized shape** every
    ingestion source (RSS/Atom, NewsAPI, GDELT, future adapters) produces; downstream never learns
    where an entry originated."""
    url: str
    title: str = ""
    description: str = ""
    body: Optional[str] = None
    published_at: Optional[str] = None
    # Media metadata (additive; metadata only — no download, no Open Graph). All None when absent.
    image: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    image_mime: Optional[str] = None
    image_source: Optional[str] = None      # the winning media tag (media:content / enclosure / …)
    # Source attribution + hints (additive; populated by non-RSS adapters). All optional so RSS parsing
    # is unchanged. ``publisher_hint`` seeds outlet resolution when a source knows the outlet/domain.
    source_type: Optional[str] = None       # rss | newsapi | gdelt
    source_provider: Optional[str] = None   # feed name / "NewsAPI" / "GDELT"
    category: Optional[str] = None
    language: Optional[str] = None
    country: Optional[str] = None
    external_id: Optional[str] = None
    publisher_hint: Optional[str] = None
    # Where the reported EVENT happened (Location Intelligence Phase 2) — 0..n provider-supplied
    # places, each a mapping with "country" in the provider's own form (+ optional region/city/
    # lat/lon/source). Adapters only RELAY what their provider extracted (GDELT GKG, GeoRSS, …);
    # location.resolve_event_locations normalizes downstream. Empty for providers without
    # event geography — never synthesized from article text.
    event_locations: tuple = ()

    def __post_init__(self) -> None:
        """FeedEntry is the canonical *normalized* contract: every adapter (RSS/Atom, NewsAPI,
        GDELT, and any future source) constructs a FeedEntry, and construction normalizes the
        human-readable text through the single :func:`text_utils.clean_html` sanitizer. So every
        downstream consumer (scoring, dedup, persistence, media, Discover, Search, Stories,
        Recommendations, the coach) can assume ``title`` / ``description`` / ``body`` are already
        tag-free, entity-decoded, and whitespace-normalized — nobody sanitizes separately."""
        self.title = text_utils.clean_html(self.title)
        self.description = text_utils.clean_html(self.description)
        self.body = text_utils.clean_html(self.body) or None    # image-only/empty body → None
        self.category = text_utils.clean_html(self.category) or None


# --------------------------------------------------------------------------- #
# Parsing (namespace-agnostic: RSS 2.0 / RSS 1.0 / Atom, dc:date, content:encoded)
# --------------------------------------------------------------------------- #
def _local(tag) -> str:
    """The local tag name, lower-cased, with any XML namespace stripped."""
    return str(tag).rsplit("}", 1)[-1].lower()


def _children(el, name: str) -> list:
    return [c for c in list(el) if _local(c.tag) == name] if el is not None else []


def _first(el, name: str):
    ch = _children(el, name)
    return ch[0] if ch else None


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def to_utc_iso(dt: datetime) -> str:
    """A datetime as an ISO string in **UTC** (``+00:00``). A naive datetime is read as UTC — the
    only safe reading, since a feed that omits the offset gives us nothing better to use."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _to_iso(value: str) -> Optional[str]:
    """Normalise an RSS ``pubDate`` (RFC 822) or Atom timestamp (RFC 3339) to a **UTC** ISO string,
    or ``None`` if it can't be parsed.

    Normalising the OFFSET (not just the format) is load-bearing. ``published_at`` is a text column
    and ``store._search_order`` sorts it lexicographically, so a preserved offset made string order
    disagree with real time: ``2026-07-27T12:00:00-04:00`` (16:00Z) sorted BELOW
    ``2026-07-27T16:00:00+00:00`` (also 16:00Z). Every US-Eastern publisher was therefore ranked up
    to four hours late and pushed out of the newest-first clustering window ahead of its turn —
    measured at 21% of the catalog, and disproportionately the outlets carrying the political
    spectrum. In UTC, lexicographic order and chronological order coincide."""
    s = (value or "").strip()
    if not s:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(s)      # RFC 822: "Wed, 02 Oct 2002 08:00:00 GMT"
        if dt is not None:
            return to_utc_iso(dt)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return to_utc_iso(datetime.fromisoformat(s.replace("Z", "+00:00")))   # RFC 3339 / ISO
    except ValueError:
        return None


def _media_candidates(el, is_atom: bool) -> list:
    """Collect image media candidates from a feed item/entry: ``media:content`` / ``media:thumbnail``
    (both map to local names ``content``/``thumbnail`` — disambiguated by the ``url`` attribute, which
    Atom's own ``<content>`` lacks), ``enclosure``, and Atom ``<link rel="enclosure|image">``.
    Namespace-agnostic; metadata only, no download."""
    out = []
    for t in _children(el, "content") + _children(el, "thumbnail"):
        url = (t.get("url") or "").strip()
        if not url:
            continue
        medium = (t.get("medium") or "").lower()
        if medium and medium != "image":                # skip media:content medium="video"/"audio"
            continue
        out.append({"url": url, "width": t.get("width"), "height": t.get("height"),
                    "mime": t.get("type"),
                    "source": "media:thumbnail" if _local(t.tag) == "thumbnail" else "media:content"})
    for enc in _children(el, "enclosure"):
        url = (enc.get("url") or "").strip()
        if url:
            out.append({"url": url, "width": None, "height": None,
                        "mime": enc.get("type"), "source": "enclosure"})
    if is_atom:
        for link in _children(el, "link"):
            if link.get("rel") in ("enclosure", "image") and (link.get("href") or "").strip():
                out.append({"url": link.get("href").strip(), "width": None, "height": None,
                            "mime": link.get("type"), "source": "atom:link"})
    return out


def _apply_media(entry: FeedEntry, el, is_atom: bool) -> FeedEntry:
    """Select the best image for this entry from its media tags and attach the metadata (or leave the
    entry image-less). Selection is centralised in :func:`media.pick_best_image`."""
    best = media.pick_best_image(_media_candidates(el, is_atom))
    if best:
        entry.image = best["url"]
        entry.image_width = best.get("width")
        entry.image_height = best.get("height")
        entry.image_mime = best.get("mime")
        entry.image_source = best.get("source")
    return entry


def _entry_categories(el) -> Optional[str]:
    """Every ``<category>`` on an item/entry, joined ``"a; b; c"`` (deduped, order kept) — the
    publisher's own topic tags, the highest-confidence input to ``ingest.classify_topic``. RSS
    puts the tag in the element text; Atom in the ``term`` (display ``label``) attribute; a
    ``dc:subject`` maps to local name ``subject``."""
    seen, out = set(), []
    for c in _children(el, "category") + _children(el, "subject"):
        val = (_text(c) or (c.get("term") or "").strip() or (c.get("label") or "").strip())
        key = val.lower()
        if val and key not in seen:
            seen.add(key)
            out.append(val)
    return "; ".join(out) or None


def _rss_item(item) -> Optional[FeedEntry]:
    try:
        link = _text(_first(item, "link"))
        if not link:                                    # RSS with a permalink guid
            guid = _first(item, "guid")
            if guid is not None and guid.get("isPermaLink") != "false":
                link = _text(guid)
        pub = (_text(_first(item, "pubdate")) or _text(_first(item, "date"))
               or _text(_first(item, "published")))
        return _apply_media(FeedEntry(
            url=link,
            title=_text(_first(item, "title")),
            description=_text(_first(item, "description")) or _text(_first(item, "summary")),
            body=_text(_first(item, "encoded")) or None,   # content:encoded -> local name "encoded"
            published_at=_to_iso(pub),
            category=_entry_categories(item)), item, is_atom=False)
    except Exception:
        return None


def _atom_link(entry) -> str:
    links = _children(entry, "link")
    for link in links:                                  # prefer the alternate (canonical) link
        if link.get("rel") in (None, "alternate") and link.get("href"):
            return link.get("href").strip()
    for link in links:
        if link.get("href"):
            return link.get("href").strip()
    return ""


def _atom_entry(entry) -> Optional[FeedEntry]:
    try:
        return _apply_media(FeedEntry(
            url=_atom_link(entry),
            title=_text(_first(entry, "title")),
            description=_text(_first(entry, "summary")) or _text(_first(entry, "content")),
            body=_text(_first(entry, "content")) or None,
            published_at=_to_iso(_text(_first(entry, "published")) or _text(_first(entry, "updated"))),
            # `xml:lang` is inherited in XML and the nearest declaration governs, so an entry that
            # states its own language beats the feed's. A translated item in an otherwise
            # single-language feed is the case this gets right.
            language=(entry.get(_XML_LANG) or "").strip() or None,
            category=_entry_categories(entry)), entry, is_atom=True)
    except Exception:
        return None


#: The XML namespace ``xml:lang`` lives in. Atom inherits language down the document this way.
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def _feed_language(root, channel) -> Optional[str]:
    """The language a feed declares for ITSELF — RSS ``<language>`` or Atom ``xml:lang``.

    Read because nothing else does. ``_rss_item`` and ``_atom_entry`` never set ``language``, so
    **every RSS-ingested article in the catalog carries NULL** and the only language values present
    come from the GDELT and NewsAPI adapters, which supply their own per item.

    That gap is not cosmetic. It made `audit_source_cohort` abandon a whole analysis — *"language
    known for N of M outlets above the floor … TOO SPARSE TO CONCLUDE"* — and it is why M7's
    discovery table shows `?` against real publishers like `goal.com` and `vietnamnet.vn`. The
    feed's own declaration is the best evidence available and it was being thrown away.

    Only the CHANNEL element is consulted, never an item's: a per-item language describes that item,
    and treating one as the feed's would let a single translated article relabel the whole source."""
    for el in (channel, root):
        if el is None:
            continue
        lang = (el.get(_XML_LANG) or "").strip()
        if lang:
            return lang
    if channel is not None:
        # RSS <language> sits directly under <channel>; `_first` searches children, so an item's
        # own <language> is never reachable from here.
        for child in _children(channel, "language"):
            if (child.text or "").strip():
                return (child.text or "").strip()
    return None


def parse_feed(data: bytes) -> "tuple[str, list[FeedEntry]]":
    """Parse RSS/Atom bytes into ``(channel_title, entries)``. Raises ``ValueError`` on invalid XML;
    individual malformed entries are skipped rather than failing the whole feed.

    Each entry's ``language`` is filled from the feed's own declaration when the entry does not
    state one — see :func:`_feed_language`. Entry-level wins, which is correct XML semantics for
    ``xml:lang`` (it is inherited, and the nearest declaration governs)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ValueError(f"invalid feed XML: {e}") from e
    root_name = _local(root.tag)
    # A SITEMAP IS NOT A FEED, and saying so loudly is the point. M7's discovery now ADMITs sources
    # whose discovery document is a news sitemap (kait8.com, kwch.com and the Arc XP class), and the
    # obvious next step — pasting that URL into `rss_feeds.txt` — silently ingests NOTHING: a
    # `<urlset>` has no `<channel>` and no `<item>`, so this function returned zero entries, no
    # error, and the feed reported healthy forever. Sitemaps are ingested through `crawler.py`'s
    # ladder, which parses them with `discover_sitemap`; they are not interchangeable here.
    if root_name in ("urlset", "sitemapindex"):
        raise ValueError(
            f"this is a <{root_name}> sitemap, not an RSS/Atom feed — sitemap sources are ingested "
            f"through the crawler ladder (crawler.discover_sitemap), not the RSS feed list")
    if root_name == "feed":                             # Atom
        title = _text(_first(root, "title"))
        entries = [_atom_entry(e) for e in _children(root, "entry")]
        channel = root
    else:                                               # RSS 2.0 (<rss><channel>) or RSS 1.0 (<rdf:RDF>)
        # ``_first`` returns an Element or None; test that explicitly rather than the Element's
        # truthiness (deprecated in ElementTree — it reflects child count, not existence).
        channel = _first(root, "channel")
        if channel is None:                             # RSS 1.0 <rdf:RDF> carries items at the root
            channel = root
        title = _text(_first(channel, "title"))
        items = _children(channel, "item") or _children(root, "item")
        entries = [_rss_item(it) for it in items]

    feed_lang = _feed_language(root, channel)
    kept = [e for e in entries if e and e.url]
    if feed_lang:
        for e in kept:
            if not (e.language or "").strip():
                e.language = feed_lang
    return title, kept


# --------------------------------------------------------------------------- #
# Fetch + config
# --------------------------------------------------------------------------- #
def _feed_headers() -> dict:
    return {"User-Agent": _USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"}


def fetch_feed(url: str, timeout: float = 15.0) -> bytes:
    """Fetch a feed's bytes (operator-configured URL; not user input).

    **Gated on robots.txt** (F1 of the M7 Stage 2 audit). This and
    :func:`fetch_feed_conditional` are the two seams where a request leaves for a publisher's host
    on the live path, so the gate lives here rather than at each of the several callers — the same
    reason `store.search_feed_articles` owns the shadow-lane exclusion.

    Raises :class:`robots.RobotsRefused` on an explicit ``Disallow``. A policy that could not be
    read is reported, not enforced, unless ``RWE_ROBOTS_STRICT=1``; see `robots.py` for why the live
    path and the discovery crawler differ on that.

    Injected fetchers in tests bypass this, which is correct: a fake fetch reaches no publisher."""
    robots.enforce(url)
    req = urllib.request.Request(url, headers=_feed_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


@dataclass
class FeedFetch:
    """One conditional fetch's result. ``not_modified`` means the origin answered 304 and ``data``
    is empty — there is nothing to parse and nothing to ingest, which is the entire point."""
    data: bytes = b""
    not_modified: bool = False
    etag: Optional[str] = None
    last_modified: Optional[str] = None


def fetch_feed_conditional(url: str, *, etag: Optional[str] = None,
                           last_modified: Optional[str] = None,
                           timeout: float = 15.0) -> FeedFetch:
    """Fetch a feed, sending whatever validators we hold, and report what came back.

    A separate function rather than parameters on :func:`fetch_feed` because that one's signature
    is a CONTRACT: ``ingest_all``/``ingest_feed`` accept any ``fetch(url) -> bytes`` and the test
    suite injects fakes of exactly that shape. Widening it would either break those callers or
    grow optional parameters they must all learn about. This is the conditional path, used only
    when the scheduler is on; everything else keeps calling the plain one.

    ``urllib`` raises ``HTTPError`` on 304 rather than returning it, which is the trap here — a
    naive port treats the cheapest possible answer as a failure, marks the feed unhealthy, and
    backs off the one feed that is behaving perfectly. It is caught and translated."""
    robots.enforce(url)                 # the second live seam — see `fetch_feed`
    headers = dict(_feed_headers())
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return FeedFetch(data=resp.read(), not_modified=False,
                             etag=resp.headers.get("ETag"),
                             last_modified=resp.headers.get("Last-Modified"))
    except urllib.error.HTTPError as e:
        if e.code == 304:
            # Not an error: the feed is unchanged. Keep the validators we sent — a 304 need not
            # repeat them, and dropping them here would make every subsequent poll unconditional.
            return FeedFetch(data=b"", not_modified=True,
                             etag=e.headers.get("ETag") or etag,
                             last_modified=e.headers.get("Last-Modified") or last_modified)
        raise


def load_feeds(spec: "str | None" = None) -> "list[tuple[Optional[str], str]]":
    """Feeds from (in priority) an explicit spec (a file path or comma-list), then ``RWE_RSS_FEEDS``.
    Each entry is ``url`` or ``Name|url``; blanks and ``#`` comments are ignored."""
    raw = spec or os.environ.get("RWE_RSS_FEEDS") or ""
    if raw and os.path.exists(raw):
        with open(raw, encoding="utf-8") as f:
            text = f.read()
    else:
        text = raw.replace(",", "\n")
    feeds = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            name, url = line.split("|", 1)
            feeds.append((name.strip() or None, url.strip()))
        else:
            feeds.append((None, line))
    return feeds


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #
def make_scorer() -> "ingest.Scorer":
    """The same scorer the reading pipeline uses, pinned to the offline baseline enricher (no
    accidental LLM cost on bulk ingest)."""
    return ingest.Scorer(enricher=enrich.make_enricher("baseline"))


def ingest_entries(entries, source_publisher, source_feed, scorer, store_, *,
                   source_type=None, source_provider=None) -> dict:
    """Score + upsert a list of :class:`FeedEntry` into the catalog. Returns per-batch stats plus
    observational quality metrics (``missing_metadata`` = no title or no publication date;
    ``unknown_outlet`` = count whose outlet the registry doesn't know, so ``scored.lean`` is NaN and
    the article is not a recommendation candidate — with ``unknown_outlets`` a ``{outlet: count}``
    breakdown; ``newest`` / ``oldest`` publication dates). Quality metrics are collected only — they
    never drop an article, and outlet resolution / scoring is unchanged.

    ``blocked`` is the one counter that DOES drop articles: outlets configured out of the catalog
    entirely via ``RWE_CATALOG_BLOCKED_OUTLETS`` (see ``ingest.is_blocked_from_catalog``). It is
    reported rather than silent so an operator can see a block list working — or see it matching
    nothing, which is what a typo looks like.

    ``source_type`` / ``source_provider`` are the batch-level attribution a non-RSS adapter passes; a
    per-entry value on the :class:`FeedEntry` (set by the adapter during normalization) overrides them.
    RSS callers pass neither and their entries carry no per-entry values, so behaviour is unchanged."""
    stats = {"entries": 0, "new": 0, "duplicates": 0, "skipped": 0, "blocked": 0,
             "missing_metadata": 0, "unknown_outlet": 0, "unknown_outlets": {},
             "future_dated": 0, "newest": None, "oldest": None}
    # An article cannot be published AFTER we observed it. Some publishers stamp local wall-clock
    # time as UTC — youm7.com (+3) and kenh14.vn (+7), production 2026-09-01 — so their rows
    # "published" hours into the future and then surfaced at the top of every recency-sorted
    # surface exactly when the bogus timestamp came due, hours after ingestion (and, that day,
    # hours after the outlets were withdrawn). `_to_iso` already normalises HONEST offsets; this
    # clamp handles the dishonest ones. The 10-minute allowance covers clock skew and a feed
    # listing an item moments before its scheduled publish; beyond it, the observation time IS
    # the publication fact we can stand behind.
    now_utc = datetime.now(timezone.utc)
    horizon = (now_utc + timedelta(minutes=10)).isoformat()
    now_iso = now_utc.isoformat()
    for e in entries:
        stats["entries"] += 1
        if not (e.title or "").strip() or not (e.published_at or "").strip():
            stats["missing_metadata"] += 1
        if e.published_at and e.published_at > horizon:     # both ISO-UTC: lexical == chronological
            e.published_at = now_iso
            stats["future_dated"] += 1
        iso = e.published_at            # already ISO (see _to_iso); lexical min/max within a feed
        if iso:
            if stats["newest"] is None or iso > stats["newest"]:
                stats["newest"] = iso
            if stats["oldest"] is None or iso < stats["oldest"]:
                stats["oldest"] = iso
        url = ingest.normalize_url(e.url)
        if not ingest.has_host(url):
            stats["skipped"] += 1
            continue
        raw = ingest.RawRead(url=url, title=e.title or "", description=e.description or "",
                             outlet=e.publisher_hint or "", category=e.category or "")
        # Configured-out outlets stop HERE — before scoring, so a blocked article costs no
        # enrichment and leaves no scored-cache row either, and before the upsert, so nothing of it
        # reaches the catalog. Every producer (RSS, the source adapters, the browser extension)
        # funnels through this function, so one check covers them all. Empty setting -> no-op.
        if ingest.is_blocked_from_catalog(raw.outlet, url):
            stats["blocked"] += 1
            continue
        scored = ingest.score_with_cache(raw, scorer, store_)      # same scorer + shared cache as reads
        _lean = scored.lean                                        # NaN when the registry doesn't know the outlet
        if _lean is None or not math.isfinite(_lean):              # observational: the article still ingests
            stats["unknown_outlet"] += 1
            _o = scored.outlet or "(unresolved)"
            stats["unknown_outlets"][_o] = stats["unknown_outlets"].get(_o, 0) + 1
        # Location Resolver (location.py): provider metadata + the resolved outlet -> the ONE
        # canonical publisher-level location. Provider-agnostic — every adapter's entry passes
        # through this same line, so downstream never sees provider-specific location forms.
        loc = location.resolve_article_location(e.country, e.language, outlet=scored.outlet)
        created = store_.upsert_feed_article(
            canonical_url=scored.article_id, url=url, publisher=scored.outlet,
            source_publisher=source_publisher, title=e.title or scored.title,
            description=e.description or "", body=e.body, published_at=e.published_at,
            source_feed=source_feed, scored=dataclasses.asdict(scored),
            image=e.image, image_width=e.image_width, image_height=e.image_height,
            image_mime=e.image_mime, image_source=e.image_source,
            image_attribution=(source_publisher or scored.outlet or None),
            source_type=(e.source_type or source_type),
            source_provider=(e.source_provider or source_provider or source_publisher),
            external_id=e.external_id, country=loc.country, language=loc.language)
        # Event geography (Phase 2): persist provider-extracted places, normalized through the
        # same resolver. Written only when the entry CARRIES event locations — a provider
        # without geography never wipes another provider's rows for the same article.
        events = location.resolve_event_locations(e.event_locations)
        if events:
            store_.replace_article_event_locations(scored.article_id, events)
        stats["new" if created else "duplicates"] += 1
    return stats


def ingest_feed(feed_url, name, scorer, store_, fetch: Callable[[str], bytes] = fetch_feed,
                *, source_type: str = "rss") -> dict:
    data = fetch(feed_url)
    channel_title, entries = parse_feed(data)
    s = ingest_entries(entries, name or channel_title or None, feed_url, scorer, store_,
                       source_type=source_type)
    s["feed"] = feed_url
    return s


def ingest_all(feeds, scorer, store_, fetch: Callable[[str], bytes] = fetch_feed,
               on_feed: "Callable[..., None] | None" = None, *, source_type: str = "rss") -> dict:
    """Ingest every feed; one feed's failure never aborts the rest (errors are collected).

    Optional ``on_feed(name, url, stats_or_None, latency_ms, error_or_None)`` is called once per feed
    with its per-feed result + wall-clock latency — the seam feed-health monitoring records from. It is
    observational: an exception in ``on_feed`` is swallowed so it can never break polling."""
    # `robotsRefused` is counted apart from `failed` on purpose: a refusal and a network error mean
    # opposite things. One is a publisher answering us, the other is us not reaching them, and an
    # aggregate that merged them would hide the only one that is a compliance signal.
    agg = {"feeds": 0, "ok": 0, "failed": 0, "robotsRefused": 0, "entries": 0, "new": 0,
           "duplicates": 0, "skipped": 0, "blocked": 0, "unknown_outlet": 0, "errors": []}
    for name, url in feeds:
        agg["feeds"] += 1
        t0 = time.perf_counter()
        result, error = None, None
        try:
            result = ingest_feed(url, name, scorer, store_, fetch=fetch, source_type=source_type)
        except robots.RobotsRefused as e:               # the publisher said no — not a failure
            error = e
            agg["robotsRefused"] += 1
        except Exception as e:                          # network/parse error on one feed
            error = e
        latency_ms = (time.perf_counter() - t0) * 1000.0
        if error is None:
            agg["ok"] += 1
            for k in ("entries", "new", "duplicates", "skipped", "blocked", "unknown_outlet"):
                agg[k] += result.get(k, 0)
        else:
            agg["failed"] += 1
            agg["errors"].append({"feed": url, "error": f"{type(error).__name__}: {error}"})
        if on_feed is not None:
            try:
                on_feed(name, url, result, latency_ms, error)
            except Exception:                            # health recording must never break polling
                pass
    return agg


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _format_run_summary(agg: dict, before: int, after: int, seconds: float) -> str:
    """Render one ingest run's aggregate stats as a human-friendly multi-line summary.

    Presentation ONLY: every number is taken verbatim from ``agg`` (the counts
    :func:`ingest_all` returns) and the catalog size before/after — nothing is recomputed
    or reinterpreted. Kept pure (data in, string out) so the format is trivially testable
    without touching the store or the network."""
    unknown = agg.get("unknown_outlet", 0)
    # `blocked` is shown only when non-zero: a line reading 0 on every run for the deployments that
    # configure no block list is noise, while its ABSENCE is itself the honest report.
    blocked = agg.get("blocked", 0)
    rows = [("new articles", agg["new"]),
            ("existing (duplicate)", agg["duplicates"]),
            ("skipped", agg["skipped"]),
            ("unknown outlets", unknown)]
    if blocked:
        rows.append(("blocked (configured out)", blocked))
    # Shown only when non-zero, like `blocked` — but unlike `blocked` this one is a PUBLISHER
    # answering us, so when it fires it is the most important line in the run. Counting it without
    # printing it, which is what shipped, means a feed could go silent and nobody would see why.
    refused = agg.get("robotsRefused", 0)
    if refused:
        rows.append(("robots.txt REFUSED", refused))
    w = max(len(str(v)) for v in (agg["new"], agg["duplicates"], agg["skipped"], unknown,
                                  blocked, refused, before, after))
    lines = [f"RSS ingest: {agg['feeds']} feed(s) in {seconds:.1f}s  "
             f"({agg['ok']} ok, {agg['failed']} failed"
             + (f", {refused} refused by robots.txt" if refused else "") + ")"]
    lines += [f"  {label:<24}{value:>{w}}" for label, value in rows]
    lines.append(f"  {'catalog':<24}{before:>{w}} -> {after}  (+{after - before})")
    lines.append('  note: high "existing" counts are expected on repeat RSS polls;')
    lines.append("        dedup by canonical URL adds only genuinely new articles.")
    if unknown:
        lines.append(f"  note: {unknown} article(s) have an unresolved outlet (no lean) and are "
                     "excluded from recommendations;")
        lines.append("        run outlet_coverage.py to see which outlets to add to outlet_registry.csv.")
    return "\n".join(lines)


def cmd_run(args) -> int:
    feeds = load_feeds(args.feeds)
    if not feeds:
        print("no feeds configured (use --feeds FILE|LIST, or set RWE_RSS_FEEDS)", file=sys.stderr)
        return 1
    store_ = store.Store(args.db)
    before = store_.count_feed_articles()               # read-only; presentation baseline
    t0 = time.perf_counter()
    agg = ingest_all(feeds, make_scorer(), store_)
    dt = time.perf_counter() - t0
    print(_format_run_summary(agg, before, store_.count_feed_articles(), dt))
    for err in agg["errors"]:
        print(f"  ! {err['feed']}: {err['error']}", file=sys.stderr)
    return 0 if agg["failed"] == 0 else 2


def cmd_status(args) -> int:
    store_ = store.Store(args.db)
    print(f"catalog: {store_.count_feed_articles()} articles")
    for a in store_.list_feed_articles(limit=args.limit):
        print(f"  {(a['publishedAt'] or '?')[:25]:<25} {(a['publisher'] or '?')[:18]:<18} "
              f"{(a['title'] or '')[:56]}")
    return 0


def cmd_parse(args) -> int:
    with open(args.file, "rb") as f:
        title, entries = parse_feed(f.read())
    print(f"channel: {title!r}  ({len(entries)} entries)")
    for e in entries[: args.limit]:
        print(f"  {(e.published_at or '?')[:25]:<25} {e.url}")
        print(f"      {e.title[:70]}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    # `--db` on a shared parent, added to each subcommand (git/docker style: `run --db ...`).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=None, help="database URL (default: RWE_DB_URL or the repo file)")
    sub = ap.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", parents=[common], help="fetch + ingest the configured feeds")
    r.add_argument("--feeds", default=None, help="feeds file, comma-list, or unset for RWE_RSS_FEEDS")
    r.set_defaults(func=cmd_run)

    st = sub.add_parser("status", parents=[common], help="catalog size + most-recent articles")
    st.add_argument("--limit", type=int, default=20)
    st.set_defaults(func=cmd_status)

    p = sub.add_parser("parse", parents=[common], help="parse a local feed file (offline; for testing)")
    p.add_argument("file")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_parse)

    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
