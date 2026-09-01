"""publisher_logo.py — resolve and cache each outlet's best SITE logo, with the crawler's manners.

The client already walks a guessed chain per outlet (Apple touch icon → precomposed → favicon.ico)
and drops to a monogram when it runs out. That walk is honest but blind: it never sees the icons a
site actually DECLARES (``<link rel="icon" sizes="192x192">``, a web-app manifest's 512px icon), it
re-runs in every reader's browser, and a 16px favicon that "loads" is still a blur. This module does
the walk ONCE, server-side, from the site's own declarations, verifies what it finds, and caches the
verdict — including the negative one, so an outlet that exposes nothing is not re-asked every cycle.

Manners are the crawler's, not a fresh set: the same ``robots.RobotsPolicy`` (an absent policy is
a refusal), the same per-host ``RateLimiter``, the same User-Agent and retry chassis. A logo fetch
is a fetch of a publisher's origin and gets no exemption for being small.

Tiers are unchanged and ``media.pick_best_logo`` still decides: curated → Wikimedia/Wikipedia →
**site (this module)** → guessed site icons → glyph/monogram. A verified site logo sits below a
Commons file (the outlet's real mark at real resolution) and above the client's guesses (which it
supersedes with a URL that is known to exist and known to be big enough).

Idempotent by construction: :func:`pending` returns only publishers whose row is stale for its
status, so a rerun costs one query and zero requests once the catalog is covered.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Callable, Optional

import media
import outlet_registry
import publisher_metadata
import robots
# `crawler` and `sources` are imported LAZILY at their call sites: sources imports this module
# (the poller adapter), and crawler subclasses sources.SourceAdapter — a top-level import here
# would close that cycle before sources finished defining it.

#: Provenance label stored with the row and shown beside the mark (``publisherLogoSource``).
SOURCE = "site"

#: Smallest usable mark, in the larger dimension. The client rejects anything that would upscale
#: past ~2x into a 36px box; 48 is the floor at which a site icon is a mark rather than a favicon.
MIN_PX = 48

#: Widest a "logo" may be before it is a banner. A wordmark is fine; a 1200x100 masthead is not.
MAX_ASPECT = 4.0

#: Byte cap for an ICON — an icon is small; a "logo" larger than this is a photo or a mistake, and
#: is skipped rather than fatal.
MAX_BYTES = 512 * 1024

#: Read cap for the HOMEPAGE. Its icon declarations live in <head>, at the top, so an oversize page
#: is TRUNCATED and parsed, never refused: the first production pass recorded a third of outlets as
#: `error` because a 512 KB cap treated an ordinary news homepage as a failure.
HTML_MAX_BYTES = 2 * 1024 * 1024

#: How many declared candidates to actually download per outlet. Declarations are cheap to read;
#: each verification is a request to the publisher's origin.
MAX_VERIFY = 4

#: Freshness per verdict. A found logo is re-verified rarely (marks change rarely); a negative
#: result is re-asked monthly (sites add manifests); a transport error retries after a day.
TTL_DAYS = {"ok": 90.0, "none": 30.0, "error": 1.0}
DEFAULT_TTL_DAYS = 30.0
DEFAULT_BATCH = 10

#: The crawler's own User-Agent (crawler.USER_AGENT is this same call): a logo fetch is a fetch of a
#: publisher's origin and identifies itself exactly as every other fetch of that origin does.
USER_AGENT = robots.user_agent("Crawler")

#: Hosts a logo must NEVER come from — ours. An outlet's mark is the outlet's; the only ways our
#: own icon could end up cached against a publisher are a bug or a test stand-in, and neither may
#: reach a reader. Refused at resolution AND at serving, so an old row cannot leak either.
OWN_HOSTS = ("hidden-view.com", "localhost", "127.0.0.1")


def is_our_host(url: Optional[str]) -> bool:
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in OWN_HOSTS)


#: Vector marks scale to any box; reported at this sentinel size so the client's too-small rule
#: never rejects one.
_SVG_PX = 1024

#: Rank of a declaration when it names no size. Apple's touch icon is 180 by convention; a bare
#: ``rel=icon`` is usually the 32px favicon in disguise; ``mask-icon`` is an SVG.
_DEFAULT_PX = {"apple-touch-icon": 180, "apple-touch-icon-precomposed": 180, "mask-icon": _SVG_PX,
               "icon": 32}
_ICON_RELS = frozenset(_DEFAULT_PX) | {"shortcut icon", "shortcut", "fluid-icon"}


# --------------------------------------------------------------------------- #
# Discovery — what the page DECLARES, ranked by declared size.
# --------------------------------------------------------------------------- #
class _HeadLinks(HTMLParser):
    """Collect ``<link>`` icon/manifest declarations and ``<base href>`` from the document head.
    Stops at ``</head>``: icons are declared there, and a homepage body can be a megabyte."""

    def __init__(self):
        super().__init__()
        self.links: list = []
        self.base: Optional[str] = None
        self.done = False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "base" and a.get("href") and self.base is None:
            self.base = a["href"]
        elif tag == "link":
            self.links.append(a)

    def handle_endtag(self, tag):
        if tag == "head":
            self.done = True


def _declared_px(sizes: str, kind: str) -> int:
    """The largest dimension a ``sizes`` attribute declares, or the kind's conventional default."""
    best = 0
    for token in (sizes or "").lower().split():
        if token == "any":
            return _SVG_PX
        m = re.match(r"^(\d+)x(\d+)$", token)
        if m:
            best = max(best, int(m.group(1)), int(m.group(2)))
    return best or _DEFAULT_PX.get(kind, 0)


def discover_candidates(html: str, base_url: str) -> list:
    """Icon candidates a page declares, best-declared first, then the two conventional guesses.

    Each is ``{"url", "kind", "px", "mime"}``; ``px`` is DECLARED size (a claim), not measured —
    :func:`verify` measures. ``manifest`` links come back as their own kind so the caller can fetch
    and expand them: a manifest's 192/512px icons are usually the best mark a site exposes."""
    p = _HeadLinks()
    try:
        p.feed(html or "")
    except Exception:
        pass
    base = urllib.parse.urljoin(base_url, p.base) if p.base else base_url
    out: list = []
    seen: set = set()

    def add(url, kind, px, mime=None):
        u = urllib.parse.urljoin(base, (url or "").strip())
        if not u.startswith(("http://", "https://")) or u in seen:
            return
        seen.add(u)
        out.append({"url": u, "kind": kind, "px": px, "mime": (mime or None)})

    for a in p.links:
        rels = (a.get("rel") or "").lower().split()
        href = a.get("href")
        if not href:
            continue
        if "manifest" in rels:
            add(href, "manifest", 0, a.get("type"))
            continue
        kind = next((r for r in ("apple-touch-icon-precomposed", "apple-touch-icon", "mask-icon", "icon")
                     if r in rels), None)
        if kind is None and rels and (set(rels) & _ICON_RELS):
            kind = "icon"
        if kind is None:
            continue
        add(href, kind, _declared_px(a.get("sizes", ""), kind), a.get("type"))

    icons = [c for c in out if c["kind"] != "manifest"]
    manifests = [c for c in out if c["kind"] == "manifest"]
    icons.sort(key=lambda c: -c["px"])
    # The conventional locations, last: a declaration beats a guess, but a site with an empty head
    # still usually serves /apple-touch-icon.png.
    for path, px in (("/apple-touch-icon.png", 180), ("/favicon.ico", 32)):
        add(urllib.parse.urljoin(base, path), "guess", px)
    guesses = [c for c in out if c["kind"] == "guess"]
    return icons + manifests + guesses


def parse_manifest(text: str, base_url: str) -> list:
    """Icon candidates from a web-app manifest's ``icons`` list, best-declared first."""
    try:
        doc = json.loads(text or "")
    except Exception:
        return []
    out = []
    for icon in (doc.get("icons") or []) if isinstance(doc, dict) else []:
        if not isinstance(icon, dict) or not icon.get("src"):
            continue
        u = urllib.parse.urljoin(base_url, str(icon["src"]).strip())
        if u.startswith(("http://", "https://")):
            out.append({"url": u, "kind": "manifest-icon",
                        "px": _declared_px(str(icon.get("sizes") or ""), "icon"),
                        "mime": icon.get("type") or None})
    out.sort(key=lambda c: -c["px"])
    return out


# --------------------------------------------------------------------------- #
# Verification — measure what actually came back.
# --------------------------------------------------------------------------- #
def image_dims(data: bytes, mime: Optional[str] = None) -> Optional[tuple]:
    """``(width, height)`` of an image payload, the SVG sentinel for vectors, ``None`` for anything
    that is not an image — an HTML error page served with a 200 is the common case."""
    if not data:
        return None
    head = data[:512].lstrip().lower()
    if (mime and "svg" in mime) or head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:4096].lower()):
        return (_SVG_PX, _SVG_PX)
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            # An .ico is a container; PIL opens its largest frame, which is the one we want.
            w, h = im.size
        return (int(w), int(h)) if w and h else None
    except Exception:
        return None


def usable(dims: Optional[tuple]) -> bool:
    """Big enough to be a mark, and not a banner."""
    if not dims:
        return False
    w, h = dims
    return max(w, h) >= MIN_PX and (max(w, h) / max(1, min(w, h))) <= MAX_ASPECT


# --------------------------------------------------------------------------- #
# Fetching — the crawler's chassis, byte-capped.
# --------------------------------------------------------------------------- #
def default_fetch_bytes(url: str, *, timeout: float = 15.0) -> bytes:
    """GET through ``sources._request`` (429/5xx discipline) with the crawler's User-Agent. Reads at
    most HTML_MAX_BYTES and never raises for size: a homepage past the cap is parsed truncated (its
    <head> is at the top), and an icon past MAX_BYTES is judged — and skipped — by :func:`resolve`."""
    import sources                                       # lazy: see the import note above
    return sources._request(
        url, read=lambda r: r.read(HTML_MAX_BYTES),
        headers={"User-Agent": USER_AGENT, "Accept": "image/*, application/json;q=0.8, text/html;q=0.7, */*;q=0.5"},
        timeout=timeout)


def _gate(url: str, policy, limiter) -> Optional[str]:
    """Robots + rate limit for one URL. Returns a refusal reason, or None when the fetch may go."""
    decision = policy.check(url)
    if not decision.allowed:
        return f"robots: {decision.reason or 'refused'}"
    limiter.wait(url, decision.crawl_delay)
    return None


def resolve(site_url: str, fetch_bytes: Callable[[str], bytes], *, policy, limiter) -> dict:
    """Find and verify the best logo one site exposes. Never raises for a publisher-side problem.

    Returns ``{"status": "ok", "url", "width", "height", "tried"}`` or
    ``{"status": "none"|"error", "reason", "tried"}``. ``none`` is a verdict about the site (it
    declares nothing usable, or robots refuses us) and is cached for TTL_DAYS["none"]; ``error`` is
    a verdict about the network and retries after a day."""
    tried = 0
    refused = _gate(site_url, policy, limiter)
    if refused:
        return {"status": "none", "reason": refused, "tried": 0}
    try:
        html = fetch_bytes(site_url).decode("utf-8", errors="replace")
    except Exception as e:
        return {"status": "error", "reason": f"{type(e).__name__}: {e}"[:200], "tried": 1}
    tried += 1

    candidates = discover_candidates(html, site_url)
    expanded: list = []
    for c in candidates:
        if c["kind"] != "manifest":
            expanded.append(c)
            continue
        if _gate(c["url"], policy, limiter):
            continue
        try:
            expanded = parse_manifest(fetch_bytes(c["url"]).decode("utf-8", errors="replace"), c["url"]) + expanded
            tried += 1
        except Exception:
            pass
    expanded.sort(key=lambda c: (-c["px"], c["kind"] == "guess"))

    verified = 0
    for c in expanded:
        if verified >= MAX_VERIFY:
            break
        if is_our_host(c["url"]) or _gate(c["url"], policy, limiter):
            continue
        verified += 1
        tried += 1
        try:
            data = fetch_bytes(c["url"])
        except Exception:
            continue
        if len(data) > MAX_BYTES:                          # a "logo" that big is a photo, not a mark
            continue
        dims = image_dims(data, c.get("mime"))
        if usable(dims):
            return {"status": "ok", "url": c["url"], "width": dims[0], "height": dims[1], "tried": tried}
    return {"status": "none", "reason": "no usable icon among declarations", "tried": tried}


# --------------------------------------------------------------------------- #
# Scheduling — who is due, and where their site is.
# --------------------------------------------------------------------------- #
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def enabled() -> bool:
    """Off in code, ON in production via ``RWE_PUBLISHER_LOGOS=1`` — the same rule the Wikipedia
    enricher follows: a module that fetches publishers' origins never does so merely because it
    was imported."""
    return os.environ.get("RWE_PUBLISHER_LOGOS", "").strip().lower() in {"1", "true", "yes", "on"}


def batch_size() -> int:
    try:
        return max(1, int(os.environ.get("RWE_PUBLISHER_LOGOS_BATCH", "") or DEFAULT_BATCH))
    except ValueError:
        return DEFAULT_BATCH


def ttl_days(status: Optional[str]) -> float:
    return TTL_DAYS.get(status or "", DEFAULT_TTL_DAYS)


def _parse(ts) -> Optional[datetime]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def is_stale(row: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    """Never resolved, or older than its status's TTL."""
    if not row:
        return True
    checked = _parse(row.get("checkedAt"))
    if checked is None:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - checked) > timedelta(days=ttl_days(row.get("status")))


def has_better_logo(store_, publisher: str, wiki_rows: Optional[dict] = None) -> bool:
    """Whether a HIGHER tier already supplies this outlet's mark — a curated override, or a
    Commons/Wikipedia logo in the metadata cache. Then a site fetch would be a request to the
    publisher's origin for a mark we would never show."""
    picked = media.pick_best_logo(publisher)
    if picked.get("publisherLogoSource") == "registry":
        return True
    rows = wiki_rows if wiki_rows is not None else store_.publisher_metadata_many([publisher])
    return publisher_metadata.logo_from_cache(rows.get(store_.publisher_key(publisher))) is not None


def pending(store_, *, limit: int, now: Optional[datetime] = None) -> list:
    """The next publishers due for a site-logo pass, busiest first. Idempotence lives here: a
    fresh row — positive or negative — is simply not returned."""
    candidates = store_.catalog_publishers()
    names = [c["publisher"] for c in candidates]
    rows = store_.publisher_logo_many(names)
    wiki = store_.publisher_metadata_many(names)
    out = []
    for c in candidates:
        key = store_.publisher_key(c["publisher"])
        if not is_stale(rows.get(key), now=now):
            continue
        if has_better_logo(store_, c["publisher"], wiki):
            continue
        out.append(c)
        if len(out) >= limit:
            break
    return out


def site_for(store_, publisher: str) -> Optional[str]:
    """The site to ask: the outlet's own majority host as observed in the catalog (aggregators
    excluded), else the registry's first domain alias for a curated outlet that has not published
    yet. None when we have no idea where they live — then there is nothing honest to fetch."""
    host = publisher_metadata.observed_host(store_, publisher)
    if not host:
        try:
            outlet = outlet_registry.resolve(publisher)
            domains = outlet_registry.default_registry().domains(outlet.canonical) if outlet else []
            host = domains[0] if domains else None
        except Exception:
            host = None
    return f"https://{host}/" if host else None


def resolve_publisher(store_, publisher: str, *, fetch_bytes: Callable[[str], bytes], policy,
                      limiter, now: Optional[datetime] = None) -> dict:
    """Resolve one outlet and cache the verdict. Returns the written row."""
    site = site_for(store_, publisher)
    if not site:
        return store_.upsert_publisher_logo(publisher, status="none", reason="no known host", at=now)
    try:
        result = resolve(site, fetch_bytes, policy=policy, limiter=limiter)
    except Exception as e:                                # belt: resolve() already catches
        result = {"status": "error", "reason": f"{type(e).__name__}: {e}"[:200]}
    return store_.upsert_publisher_logo(
        publisher, status=result["status"], url=result.get("url"), width=result.get("width"),
        height=result.get("height"), reason=result.get("reason"), at=now)


def run_resolution(store_, *, fetch_bytes: Callable[[str], bytes], limit: Optional[int] = None,
                   policy=None, limiter=None, log: Optional[Callable[..., None]] = None,
                   now: Optional[datetime] = None) -> dict:
    """One bounded pass. Fail-soft like every poller side-job: a publisher that cannot be reached
    becomes an ``error`` row, never an exception into the poll loop."""
    limit = batch_size() if limit is None else limit
    if policy is None or limiter is None:
        import crawler                                   # lazy: see the import note above
        policy = policy or crawler.RobotsPolicy()
        limiter = limiter or crawler.RateLimiter()
    due = pending(store_, limit=limit, now=now)
    counts: dict = {}
    t0 = time.perf_counter()
    for c in due:
        row = resolve_publisher(store_, c["publisher"], fetch_bytes=fetch_bytes, policy=policy,
                                limiter=limiter, now=now)
        status = (row or {}).get("status", "error")
        counts[status] = counts.get(status, 0) + 1
    summary = {"considered": len(due), "byStatus": counts,
               "durationMs": round((time.perf_counter() - t0) * 1000.0, 1)}
    if log is not None and due:
        log(logging.INFO, "publisher_logos", **summary)
    return summary


# --------------------------------------------------------------------------- #
# Serving — where the cached verdict enters the existing tiers.
# --------------------------------------------------------------------------- #
def logo_tuple(row: Optional[dict]) -> Optional[tuple]:
    """``(url, "site")`` for a positive cached verdict, else None."""
    if row and row.get("status") == "ok" and row.get("url") and not is_our_host(row["url"]):
        return (row["url"], SOURCE)
    return None


def best_enriched(wiki: Optional[tuple], site_row: Optional[dict]) -> Optional[tuple]:
    """The ``enriched`` argument for ``media.pick_best_logo``: a Commons/Wikipedia mark first
    (the outlet's real logo at real resolution), the verified site mark otherwise."""
    return wiki or logo_tuple(site_row)


def attach_coverage_logos(store_, rows: list) -> list:
    """Put ``publisherLogo`` + ``publisherLogoFallbacks`` on story coverage rows, two bulk reads
    for the whole list. The client's chip walk then starts from a mark known to exist and to be
    large enough, and only falls back to guessing for outlets nobody has resolved yet."""
    names = {r.get("publisher") for r in rows if r.get("publisher")}
    if not names:
        return rows
    wiki = store_.publisher_metadata_many(names)
    site = store_.publisher_logo_many(names)
    for r in rows:
        pub = r.get("publisher")
        if not pub:
            continue
        key = store_.publisher_key(pub)
        enriched = best_enriched(publisher_metadata.logo_from_cache(wiki.get(key)), site.get(key))
        picked = media.pick_best_logo(pub, r.get("url"), enriched=enriched)
        if picked.get("publisherLogo"):
            r["publisherLogo"] = picked["publisherLogo"]
            r["publisherLogoFallbacks"] = picked.get("publisherLogoFallbacks")
    return rows
