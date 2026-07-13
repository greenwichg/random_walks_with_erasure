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
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import sibling modules
import ingest
import enrich
import store
import media                     # image SELECTION (pick_best_image) — metadata only, never downloads
import text_utils                # the ONE canonical HTML→text normalizer (used by FeedEntry below)

_USER_AGENT = "InformationHealth-RSS/0.1 (+https://code.claude.com)"


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


def _to_iso(value: str) -> Optional[str]:
    """Normalise an RSS ``pubDate`` (RFC 822) or Atom timestamp (RFC 3339) to an ISO string, or
    ``None`` if it can't be parsed."""
    s = (value or "").strip()
    if not s:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(s)      # RFC 822: "Wed, 02 Oct 2002 08:00:00 GMT"
        if dt is not None:
            return dt.isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()   # RFC 3339 / ISO
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
            category=_entry_categories(entry)), entry, is_atom=True)
    except Exception:
        return None


def parse_feed(data: bytes) -> "tuple[str, list[FeedEntry]]":
    """Parse RSS/Atom bytes into ``(channel_title, entries)``. Raises ``ValueError`` on invalid XML;
    individual malformed entries are skipped rather than failing the whole feed."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ValueError(f"invalid feed XML: {e}") from e
    root_name = _local(root.tag)
    if root_name == "feed":                             # Atom
        title = _text(_first(root, "title"))
        entries = [_atom_entry(e) for e in _children(root, "entry")]
    else:                                               # RSS 2.0 (<rss><channel>) or RSS 1.0 (<rdf:RDF>)
        # ``_first`` returns an Element or None; test that explicitly rather than the Element's
        # truthiness (deprecated in ElementTree — it reflects child count, not existence).
        channel = _first(root, "channel")
        if channel is None:                             # RSS 1.0 <rdf:RDF> carries items at the root
            channel = root
        title = _text(_first(channel, "title"))
        items = _children(channel, "item") or _children(root, "item")
        entries = [_rss_item(it) for it in items]
    return title, [e for e in entries if e and e.url]


# --------------------------------------------------------------------------- #
# Fetch + config
# --------------------------------------------------------------------------- #
def fetch_feed(url: str, timeout: float = 15.0) -> bytes:
    """Fetch a feed's bytes (operator-configured URL; not user input)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


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
    observational quality metrics (``missing_metadata`` = no title or no publication date; ``newest`` /
    ``oldest`` publication dates). Quality metrics are collected only — they never drop an article.

    ``source_type`` / ``source_provider`` are the batch-level attribution a non-RSS adapter passes; a
    per-entry value on the :class:`FeedEntry` (set by the adapter during normalization) overrides them.
    RSS callers pass neither and their entries carry no per-entry values, so behaviour is unchanged."""
    stats = {"entries": 0, "new": 0, "duplicates": 0, "skipped": 0,
             "missing_metadata": 0, "newest": None, "oldest": None}
    for e in entries:
        stats["entries"] += 1
        if not (e.title or "").strip() or not (e.published_at or "").strip():
            stats["missing_metadata"] += 1
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
        scored = ingest.score_with_cache(raw, scorer, store_)      # same scorer + shared cache as reads
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
            external_id=e.external_id)
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
    agg = {"feeds": 0, "ok": 0, "failed": 0, "entries": 0, "new": 0, "duplicates": 0,
           "skipped": 0, "errors": []}
    for name, url in feeds:
        agg["feeds"] += 1
        t0 = time.perf_counter()
        result, error = None, None
        try:
            result = ingest_feed(url, name, scorer, store_, fetch=fetch, source_type=source_type)
        except Exception as e:                          # network/parse error on one feed
            error = e
        latency_ms = (time.perf_counter() - t0) * 1000.0
        if error is None:
            agg["ok"] += 1
            for k in ("entries", "new", "duplicates", "skipped"):
                agg[k] += result[k]
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
def cmd_run(args) -> int:
    feeds = load_feeds(args.feeds)
    if not feeds:
        print("no feeds configured (use --feeds FILE|LIST, or set RWE_RSS_FEEDS)", file=sys.stderr)
        return 1
    store_ = store.Store(args.db)
    t0 = time.perf_counter()
    agg = ingest_all(feeds, make_scorer(), store_)
    dt = time.perf_counter() - t0
    print(f"ingested {agg['feeds']} feed(s) in {dt:.1f}s: {agg['new']} new, "
          f"{agg['duplicates']} duplicate, {agg['skipped']} skipped "
          f"({agg['ok']} ok / {agg['failed']} failed); "
          f"catalog now {store_.count_feed_articles()} articles")
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
