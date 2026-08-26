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


def feed_language(entries) -> str:
    """The language the feed declared, as carried on its parsed entries.

    **This used to parse the feed body itself, and no longer does.** `rss_ingest.parse_feed` now
    fills each entry's ``language`` from the channel's own ``<language>`` / ``xml:lang``, so the
    answer arrives on the normalized shape and this is a lookup rather than a second parser.

    That was the right place for it: the gap was never validation-specific. Nothing populated
    ``language`` for RSS at all, so **every** RSS-ingested row in the catalog carried NULL — which is
    what made `audit_source_cohort` abandon a whole analysis as "TOO SPARSE TO CONCLUDE" and what
    showed up as `?` in M7's discovery table. Fixing it in the parser fixes it for ingestion too."""
    return next((l for l in ((getattr(e, "language", "") or "").strip() for e in entries) if l), "")


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


def _declared_sitemaps(policy, host: str) -> list:
    """``Sitemap:`` lines the host's robots.txt declares, from the policy already in hand.

    Free — the file was fetched for gate 1. A declared sitemap is also the strongest hint that our
    guessed discovery paths are wrong: `CRAWLER_DESIGN.md` notes that robots.txt naming sitemaps we
    are not using "usually means our configured path was a guess"."""
    try:
        rp, _reason = policy._policy_for(host)      # cached from gate 1 — no second fetch
        return list(rp.site_maps() or []) if rp is not None else []
    except Exception:
        return []


#: Ranking signals for choosing WHICH declared sitemap to read. A heuristic, and named as one — but
#: not an arbitrary one.
#:
#: ``news`` scores positively because the Google News sitemap convention is what carries
#: ``news:title`` and ``news:publication_date`` — a real headline and a real timestamp, which is
#: exactly what gates 3-5 need and what a bare ``<urlset>`` of ``<loc>`` + ``<lastmod>`` does not
#: have. It is also bounded to recent content by that spec, where a site's full index spans years.
#:
#: The negatives are document types that are not articles at all. Deliberately NOT including
#: "category" or "index": `kait8.com` declares its news sitemap as
#: ``news-sitemap-index/category/news/``, so a negative on either word would reject the very file we
#: want. Positive signal outranks negative by construction.
_SITEMAP_PREFER = ("news",)
_SITEMAP_AVOID = ("video", "image", "author", "tag")


def rank_sitemaps(urls) -> list:
    """Declared sitemaps, best candidate first.

    Stable: ties keep declaration order, so the choice does not move between runs on a site whose
    robots.txt lists several equally-plausible files."""
    def score(u: str) -> int:
        low = u.lower()
        return (2 * sum(k in low for k in _SITEMAP_PREFER)
                - sum(k in low for k in _SITEMAP_AVOID))
    return [u for _s, _i, u in sorted(((-score(u), i, u) for i, u in enumerate(urls)))]


def _sitemap_entries(host: str, sitemaps, fetch, limiter, delay) -> tuple:
    """``(entries, source_url, requests_spent, reason)`` from the best declared sitemap.

    **The second rung of the ladder, and the reason kait8.com and kwch.com were false negatives.**
    Both allow us and both declare a news sitemap; neither advertises ``<link rel=alternate>``. Gate
    2 was asking "is there an RSS feed?" when the question is "is there a discovery document?"

    Descends one level into a ``sitemapindex``, **newest child first**. That ordering is not a
    preference — it is a defect `crawler._run_ladder` already had to fix: an index is conventionally
    oldest-first, so document order spends the whole budget on the deepest archive and never reaches
    this week. Daily Maverick and Premium Times both returned 100% `too_old` for exactly that.
    Re-deriving it here would have re-earned the same bug.

    At most two fetches: the chosen sitemap, and one child if it turns out to be an index."""
    if not sitemaps:
        return [], "", 0, "no sitemap declared in robots.txt"
    target = rank_sitemaps(sitemaps)[0]
    spent = 0
    try:
        limiter.wait(target, delay)
        entries = crawler.discover_sitemap(fetch(target), target)
        spent += 1
    except Exception as e:
        return [], target, spent, f"{target} did not parse as a sitemap ({type(e).__name__})"

    children = [e for e in entries if e.source_type == "sitemap-index"]
    if children:
        children.sort(key=lambda e: crawler._published_utc(e.published_at)
                      or crawler._UNDATED_SORTS_LAST, reverse=True)
        child = children[0].url
        try:
            limiter.wait(child, delay)
            entries = [e for e in crawler.discover_sitemap(fetch(child), child)
                       if e.source_type != "sitemap-index"]
            spent += 1
            target = child
        except Exception as e:
            return [], target, spent, f"sitemap child {child} failed ({type(e).__name__})"

    # A sitemap of bare <loc> + <lastmod> yields URLs with no headline. Those cannot cluster —
    # `clustering.MIN_TITLE_TOKENS` means a title-less article can never join a story — so a
    # document that supplies none is not a usable source however many URLs it lists.
    titled = [e for e in entries if (e.title or "").strip()]
    if not titled:
        return [], target, spent, (f"{target} lists {len(entries)} URL(s) but no headlines — a "
                                   f"news sitemap carries news:title; a plain urlset does not")
    return titled, target, spent, ""


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
    found: dict = {}

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
        gates, spent = _probe(host, cand, gates, fetch, robots, limiter, found)
        # `robots` now holds the policy whose cache gate 1 populated, so the Sitemap: lines below
        # cost nothing. Bound here rather than at the top so an offline run cannot report sitemaps
        # it never fetched.

    verdict = ("ADMIT" if all(g.status == PASS for g in gates)
               else "REJECT" if any(g.status == FAIL for g in gates)
               else "INCOMPLETE")
    feed = found.get("url", "")
    sitemaps = (_declared_sitemaps(robots, host)
                if (fetch is not None and robots is not None) else [])
    return {"host": host, "gates": gates, "verdict": verdict, "feed": feed,
            "discoveredVia": found.get("kind", ""), "samples": found.get("samples", []),
            "sitemaps": sitemaps, "requests": spent}


def _probe(host, cand, gates, fetch, robots, limiter, found: dict) -> tuple:
    """The network pass. Ordered so the cheapest refusal comes first: robots before anything else,
    and nothing at all if robots says no."""
    spent = 0
    landing = f"https://{host}/"

    limiter.wait(landing)
    decision = robots.check(landing)
    spent += 1                                          # robots.txt itself
    # The Sitemap: lines and Crawl-delay come out of the policy we ALREADY fetched, so reporting
    # them costs zero extra requests. Both are things the publisher chose to tell crawlers, and a
    # probe that fetched robots.txt and then discarded half of what it said would be leaving the
    # cheapest evidence on the floor.
    detail = decision.reason or "allowed"
    if decision.crawl_delay is not None:
        detail += f"; Crawl-delay: {decision.crawl_delay:g}s"
    sitemaps = _declared_sitemaps(robots, host)
    if sitemaps:
        detail += f"; declares {len(sitemaps)} sitemap(s)"
    gates.append(Gate(1, "robots.txt permits our agent", PASS if decision.allowed else FAIL,
                      detail))
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

    # --- the ladder: RSS first, then the declared sitemap ------------------------------------
    #
    # RSS first because it is one fetch and needs no descent. The sitemap rung is the FALLBACK, and
    # it exists because the first live crawl found gate 2 rejecting publishers who allow us and
    # publish a news sitemap but no <link rel=alternate> — kait8.com and kwch.com, byte-identical
    # Arc XP shapes, which is a large share of US local television.
    #
    # The fallback fires only when the RSS rung yields NOTHING (no link, or unparseable). A feed
    # that parses but is short is reported by gate 3 rather than silently swapped for another
    # source: switching rungs on a threshold would make the answer depend on which document we
    # happened to prefer, which is the kind of thing that is invisible in a report.
    entries, source_url, why = None, "", ""
    urls = feed_urls(body, host)
    if urls:
        try:
            limiter.wait(urls[0], decision.crawl_delay)
            entries = crawler.discover_rss(fetch(urls[0]))
            spent += 1
            source_url = urls[0]
        except Exception as e:
            why = f"{urls[0]} did not parse as a feed ({type(e).__name__})"
    else:
        why = "no <link rel=alternate> feed on the landing page"

    if entries is None:
        sm_entries, sm_url, sm_spent, sm_why = _sitemap_entries(
            host, sitemaps, fetch, limiter, decision.crawl_delay)
        spent += sm_spent
        if sm_entries:
            entries, source_url = sm_entries, sm_url
            why = ""
        else:
            why = f"{why}; sitemap rung: {sm_why}"

    if not entries:
        gates.append(Gate(2, "feed or news sitemap discoverable", FAIL, why))
        gates.extend(Gate(g.number, g.name, UNKNOWN, "not probed — no discovery document")
                     for g in _online_gates_unknown()[2:])
        return gates, spent

    kind = "feed" if source_url in urls else "news sitemap"
    # Sample article URLs, so an `article_pattern` can be written from OBSERVATION rather than
    # guessed. `CRAWLER_DESIGN.md`'s sharpest warning is that a pattern matching 0% of discovered
    # URLs makes the crawler ingest nothing while every gate reports healthy — and the only way to
    # avoid inventing one is to have looked at real URLs first. Three is enough to see the shape.
    found.update(url=source_url, kind=kind,
                 samples=[e.url for e in entries[:3] if getattr(e, "url", "")])
    gates.append(Gate(2, "feed or news sitemap discoverable", PASS, f"{source_url} ({kind})"))
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
            declared = feed_language(entries)
            gates[i] = Gate(6, "language identified", PASS if declared else FAIL,
                            f"{declared} (declared by the feed)" if declared
                            else "neither the catalog nor the feed states a language")
            break
    return gates, spent
