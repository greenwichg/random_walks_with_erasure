"""source_validation.py — Stage 2: the eight gates, and the one stage that touches a publisher.

**M7 of `docs/SCALE_ROADMAP.md`, the network half.** No store, no environment, no writes — and **no
network unless a caller hands it one.** ``fetch`` has no default and is never constructed here; a
run without it executes the offline gates and reports every online gate as ``UNKNOWN``.

That is a structural property, not a convention. The alternative — a default fetcher, disabled by a
flag — puts the entire ToS question behind somebody remembering to pass ``--dry-run``. Here, an
offline run **cannot** silently look like a validated one, because the module has nothing to call.

## The eight gates, and which half is free

| # | gate | needs the network |
|---|---|---|
| 1 | `robots.txt` permits our agent | ✅ |
| 2 | a feed is discoverable and parses | ✅ |
| 3 | ≥ 10 items in the feed | ✅ |
| 4 | ≥ 80% of items carry a publication date | ✅ |
| 5 | article URLs are stable and on the declared host | ✅ |
| 6 | language identified | — (from the catalog evidence) |
| 7 | host is not already a tracked outlet's | — |
| 8 | host is not an aggregator or proxy | — |

Gates 6–8 run in `source_discovery` **before** this module is reached, so the host list that gets
probed is already filtered. Every request not made is ToS exposure not incurred.

**Gate 4 cannot be answered offline, and the roadmap says so explicitly:** `_fetch` is
time-windowed, so every catalog row has a date *by construction*, and an offline probe would report
zero rejections whatever the feeds actually serve. A gate that cannot fail is not a gate — the same
defect M8 shipped and had to correct — so it is `UNKNOWN` until a feed is read.

## Fail-closed, inherited rather than re-argued

`crawler.RobotsPolicy` already treats an absent or unparseable robots.txt as a **refusal**, with the
reasoning that the conventional "no robots.txt means crawl freely" default is a search engine's norm
and not a reasonable reading for a commercial reader of newsrooms that has never spoken to the
publisher. This module reuses that class rather than restating the argument, and the same goes for
`RateLimiter` (per host, because the limit protects a *server*) and `_fetch_text` (the shared
429/5xx retry budget). Four drifted definitions have been corrected in this audit series; a second
robots parser would be the fifth.
"""

from __future__ import annotations

import urllib.parse
from typing import Callable, NamedTuple, Optional

import crawler

#: Items a feed must carry before it is worth ingesting. The roadmap's Stage 2 gate 3.
MIN_FEED_ITEMS = 10

#: Share of a feed's items that must carry a publication date. Below this the outlet's articles
#: cannot be placed in a time window, which is what every downstream measurement is built on.
MIN_DATED_SHARE = 0.8

#: Share of a feed's article URLs that must sit on the declared host. Below it we cannot say who
#: published them — the same bar `source_evaluation.HOST_STABILITY_FLOOR` applies after ingestion,
#: applied here before it.
MIN_ON_HOST = 0.8

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"


class Gate(NamedTuple):
    """One gate's outcome. ``UNKNOWN`` is a first-class result and never counts as a pass."""
    number: int
    name: str
    status: str
    detail: str = ""

    @property
    def blocking(self) -> bool:
        return self.status != PASS


def _offline_gates(cand: dict) -> "list[Gate]":
    """Gates 6, 7 and 8 — answered from the catalog evidence `source_discovery` gathered.

    **Gate 6 reports ``UNKNOWN`` when the catalog has no language, never ``FAIL``**, and the first
    production run of M7 is why. An absent ``language`` measures *our ingestion metadata*, not the
    source: it is populated from the feed entry and most feeds do not supply one —
    `audit_source_cohort` already had to abandon a whole analysis over this, reporting "language
    known for N of M outlets above the floor … TOO SPARSE TO CONCLUDE".

    Failing on it would reject `goal.com`, `vietnamnet.vn` and `gujaratsamachar.com` — real
    publishers — for a gap in our own records, and it would do so *silently*, because a candidate
    with a failed offline gate is never probed. The run would have promised 348 requests and
    quietly made fewer, having "rejected" hosts the table listed as passing every offline gate.

    ``UNKNOWN`` is the fail-honest answer, and the probe can then settle it: a feed usually declares
    its own language, which is better evidence than our record of it either way."""
    lang = (cand.get("language") or "").strip()
    return [
        Gate(6, "language identified", PASS if lang else UNKNOWN,
             lang or "the catalog has no language for this host — a gap in OUR metadata, not "
                     "evidence about the source; the feed can settle it"),
        Gate(7, "not already tracked", FAIL if cand.get("tracked") else PASS,
             "the registry already resolves this host" if cand.get("tracked") else ""),
        Gate(8, "not an aggregator or proxy", FAIL if cand.get("proxy") else PASS,
             "its articles are someone else's" if cand.get("proxy") else ""),
    ]


def _online_gates_unknown() -> "list[Gate]":
    """What the network gates report when no fetcher was supplied.

    ``UNKNOWN``, never ``PASS``. An offline run that reported passes would be claiming a publisher's
    robots.txt permits us without having read it, which is the exact shape of error this repo keeps
    finding in its own instruments — a gate that cannot fire reading as a gate that passed."""
    return [Gate(1, "robots.txt permits our agent", UNKNOWN, "no fetcher supplied"),
            Gate(2, "feed discoverable and parses", UNKNOWN, "no fetcher supplied"),
            Gate(3, f"feed carries >= {MIN_FEED_ITEMS} items", UNKNOWN, "no fetcher supplied"),
            Gate(4, f">= {MIN_DATED_SHARE:.0%} of items dated", UNKNOWN, "no fetcher supplied"),
            Gate(5, "article URLs on the declared host", UNKNOWN, "no fetcher supplied")]


def feed_urls(body: str, host: str) -> "list[str]":
    """Feed URLs advertised by a landing page, absolute and on the declared host.

    Restricted to the declared host deliberately: a page can advertise anyone's feed, and following
    one off-host would turn a two-request probe of a known candidate into an unbounded crawl.

    Parsed here rather than with `crawler._LinkExtractor`, which collects ``<a>`` anchors for
    section-page discovery — a different question. The feed itself is still parsed by
    `crawler.discover_rss`, which is `rss_ingest.parse_feed` verbatim: this module adds a way to
    FIND a feed, never a second way to read one.
    """
    out, seen = [], set()
    for href in _link_feeds(body):
        url = urllib.parse.urljoin(f"https://{host}/", href)
        h = (urllib.parse.urlsplit(url).hostname or "").lower()
        if url not in seen and (h == host or h.endswith("." + host)):
            seen.add(url)
            out.append(url)
    return out


def feed_language(body: str) -> str:
    """The language a feed declares for itself — RSS ``<language>`` or Atom ``xml:lang``.

    Read here because `rss_ingest.parse_feed` **discards it**: `FeedEntry.language` is populated only
    by the non-RSS adapters (NewsAPI, GDELT supply it per item), so an RSS-sourced row carries no
    language at all. That is the gap the first production M7 run surfaced as `?` against `goal.com`,
    `vietnamnet.vn` and `gujaratsamachar.com`.

    This reads **one element the existing parser does not surface** — it is not a second feed parser,
    and the entries themselves still go through `crawler.discover_rss`. Teaching
    `rss_ingest.parse_feed` to return channel language would be the better fix and would improve
    ingestion metadata for every RSS row, not just validation's — but it changes a production
    ingestion path for a validation-only need, so it wants its own change and its own measurement."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(body.encode("utf-8") if isinstance(body, str) else body)
    except Exception:
        return ""
    lang = (root.get("{http://www.w3.org/XML/1998/namespace}lang") or "").strip()
    if lang:
        return lang                                        # Atom: xml:lang on the feed element
    for el in root.iter():
        if crawler._local(el.tag) == "language" and (el.text or "").strip():
            return (el.text or "").strip()
        if crawler._local(el.tag) == "item":
            break                                          # channel metadata precedes the items
    return ""


def _link_feeds(body: str) -> "list[str]":
    """``<link rel="alternate" type="...rss|atom...">`` hrefs, in document order."""
    import html.parser

    class _P(html.parser.HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.found = []

        def handle_starttag(self, tag, attrs):
            if tag.lower() != "link":
                return
            a = {k.lower(): (v or "") for k, v in attrs}
            rel, typ = a.get("rel", "").lower(), a.get("type", "").lower()
            if "alternate" in rel and ("rss" in typ or "atom" in typ or "xml" in typ) and a.get("href"):
                self.found.append(a["href"])

    p = _P()
    try:
        p.feed(body or "")
    except Exception:
        pass                        # a malformed page yields what was parsed before it broke
    return p.found


def validate(cand: dict, *, fetch: Optional[Callable[[str], str]] = None,
             robots=None, limiter=None) -> dict:
    """Run the gates for one candidate. **Touches the network only when ``fetch`` is given.**

    Returns ``{host, gates, verdict, feed, requests}``. ``verdict`` is ``ADMIT`` only when every gate
    passed — an ``UNKNOWN`` anywhere yields ``INCOMPLETE``, never an admission, because the whole
    point of Stage 2 is that these questions were actually asked.

    ``requests`` counts what this call spent on the publisher's server, so a run can report its own
    cost rather than estimate it."""
    host = cand["host"]
    gates = list(_offline_gates(cand))
    spent = 0

    if fetch is None:
        gates.extend(_online_gates_unknown())
    elif any(g.status == FAIL for g in gates):
        # Offline gates already rejected it. Spending a request now would be paying a publisher's
        # bandwidth to confirm a decision that is already made.
        gates.extend(Gate(g.number, g.name, UNKNOWN, "not probed — an offline gate already failed")
                     for g in _online_gates_unknown())
    else:
        robots = robots or crawler.RobotsPolicy(fetch=fetch)
        limiter = limiter or crawler.RateLimiter()
        gates, spent = _probe(host, cand, gates, fetch, robots, limiter)

    verdict = ("ADMIT" if all(g.status == PASS for g in gates)
               else "REJECT" if any(g.status == FAIL for g in gates)
               else "INCOMPLETE")
    feed = next((g.detail for g in gates if g.number == 2 and g.status == PASS), "")
    return {"host": host, "gates": gates, "verdict": verdict, "feed": feed, "requests": spent}


def _probe(host, cand, gates, fetch, robots, limiter) -> tuple:
    """The network pass. Ordered so the cheapest refusal comes first: robots before anything else,
    and nothing at all if robots says no."""
    spent = 0
    landing = f"https://{host}/"

    limiter.wait(landing)
    decision = robots.check(landing)
    spent += 1                                          # robots.txt itself
    gates.append(Gate(1, "robots.txt permits our agent", PASS if decision.allowed else FAIL,
                      decision.reason or "allowed"))
    if not decision.allowed:
        gates.extend(Gate(g.number, g.name, UNKNOWN, "not probed — robots.txt refused")
                     for g in _online_gates_unknown()[1:])
        return gates, spent

    try:
        limiter.wait(landing, decision.crawl_delay)
        body = fetch(landing)
        spent += 1
    except Exception as e:
        gates.append(Gate(2, "feed discoverable and parses", FAIL,
                          f"landing page unreachable ({type(e).__name__})"))
        gates.extend(Gate(g.number, g.name, UNKNOWN, "not probed — no landing page")
                     for g in _online_gates_unknown()[2:])
        return gates, spent

    urls = feed_urls(body, host)
    if not urls:
        gates.append(Gate(2, "feed discoverable and parses", FAIL,
                          "no <link rel=alternate> feed on the landing page"))
        gates.extend(Gate(g.number, g.name, UNKNOWN, "not probed — no feed found")
                     for g in _online_gates_unknown()[2:])
        return gates, spent

    try:
        limiter.wait(urls[0], decision.crawl_delay)
        feed_body = fetch(urls[0])
        entries = crawler.discover_rss(feed_body)
        spent += 1
    except Exception as e:
        gates.append(Gate(2, "feed discoverable and parses", FAIL,
                          f"{urls[0]} did not parse as a feed ({type(e).__name__})"))
        gates.extend(Gate(g.number, g.name, UNKNOWN, "not probed — feed unparseable")
                     for g in _online_gates_unknown()[2:])
        return gates, spent

    gates.append(Gate(2, "feed discoverable and parses", PASS, urls[0]))
    n = len(entries)
    gates.append(Gate(3, f"feed carries >= {MIN_FEED_ITEMS} items",
                      PASS if n >= MIN_FEED_ITEMS else FAIL, f"{n} items"))

    dated = sum(1 for e in entries if getattr(e, "published_at", None))
    share = dated / max(1, n)
    gates.append(Gate(4, f">= {MIN_DATED_SHARE:.0%} of items dated",
                      PASS if share >= MIN_DATED_SHARE else FAIL,
                      f"{share:.0%} of {n} items — the gate that CANNOT be asked offline"))

    on_host = sum(1 for e in entries
                  if (lambda h: h == host or h.endswith("." + host))(
                      (urllib.parse.urlsplit(getattr(e, "url", "") or "").hostname or "").lower()))
    hshare = on_host / max(1, n)
    gates.append(Gate(5, "article URLs on the declared host",
                      PASS if hshare >= MIN_ON_HOST else FAIL,
                      f"{hshare:.0%} of {n} items on {host}"))

    # Gate 6 was UNKNOWN offline whenever our catalog had no language for the host. The feed itself
    # is better evidence than our record of it, so settle it here rather than leaving a permanent
    # UNKNOWN that could never become an ADMIT.
    for i, g in enumerate(gates):
        if g.number == 6 and g.status == UNKNOWN:
            declared = feed_language(feed_body) or next(
                (l for l in ((getattr(e, "language", "") or "").strip() for e in entries) if l), "")
            gates[i] = Gate(6, "language identified", PASS if declared else FAIL,
                            f"{declared} (declared by the feed)" if declared
                            else "neither the catalog nor the feed states a language")
            break
    return gates, spent
