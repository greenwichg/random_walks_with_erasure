"""source_discovery.py — Stage 1: find candidate sources without touching a publisher.

**M7 of `docs/SCALE_ROADMAP.md`, the offline half.** Pure: no store, no network, no environment, no
writes. It takes catalog rows and returns `(host, evidence)` candidates.

## Why the first discovery channel needs no crawling at all

The expansion looks like it starts with a crawler. It does not, because **the crawl exhaust is
already in the catalog and nobody has read it.** `rss_ingest.ingest_entries` has no admission gate —
an unknown outlet ingests anyway — and GDELT delivers arbitrary-domain URLs. Measured on the live
catalog: **4,083 outlet identities in the window, 3,729 of them unrated.**

So the cheapest discovery channel is a `GROUP BY` over rows we already paid for, and it is the one
built here. Feed autodiscovery and outbound-link mining are network-bound and belong to Stage 2.

## The floor, and why it is not a quality judgement

A candidate is a `(host, evidence)` pair with **≥ 10 articles observed**. The 3,442 identities below
it have a *median of one article* — they are noise, and spending a network request on each is how a
discovery pipeline becomes a crawl of the whole internet. The floor is a **cost** bound, not a
quality bar: it says nothing about whether a one-article host is a good publisher, only that we have
no evidence either way and cannot justify a request to find out.

## The gates that run here, before any request

Two of Stage 2's eight gates are answerable offline, and running them *first* is the difference
between probing 151 hosts and probing however many the catalog happens to contain:

``already tracked``   the registry resolves the host — we have it, and re-discovering it would
                      create a second identity for an outlet that already has one.
``aggregator/proxy``  the `news.google.com` gate, and it exists because of a **measured** failure.
                      The outlet-resolution counterfactual found *996 of 1,246* newly-attributed
                      articles landing on "Google News" from `10tv.com @ news.google.com`,
                      `12news.com @ news.google.com` — real local broadcasters proxied through one
                      host. A discovery pipeline without this gate discovers aggregators and calls
                      them publishers.

Every request not made is ToS exposure not incurred, which is why the cheap gates go first rather
than last.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Optional

import outlet_registry

#: Articles observed on a host before it is worth a network request. The measured floor: 3,442 of
#: 4,083 identities sit below it with a MEDIAN of one article.
VOLUME_FLOOR = 10

#: Hosts that carry other publishers' articles under their own name. Not a blocklist of bad actors —
#: several are useful services — but a list of hosts whose articles are *someone else's*, so
#: discovering them yields an aggregator wearing a publisher's clothes.
#:
#: The registry's own ``EXCLUDED_KINDS`` covers the outlets it already knows; this catches the ones
#: it does not, which is precisely the population discovery works on.
PROXY_HOSTS = frozenset({
    "news.google.com", "news.yahoo.com", "flipboard.com", "getpocket.com", "apple.news",
    "msn.com", "smartnews.com", "newsbreak.com", "reddit.com", "t.co", "medium.com",
    "substack.com", "blogspot.com", "wordpress.com", "feedproxy.google.com", "feedburner.com",
})

#: Path fragments that mark a URL as syndicated *through* a host rather than published *by* it.
PROXY_PATH_MARKERS = ("/rss/articles/", "/__i/rss/", "/url?", "/amp/")


def _host(row) -> str:
    return outlet_registry._host_of(row.get("canonicalUrl") or row.get("url") or "")


def is_proxy_host(host: str, reg=None) -> bool:
    """Gate 8. The **registry first**, then the static list for what it does not know.

    Measured, because the split matters: the registry resolves ``news.google.com`` to
    ``Google News kind=aggregator``, and knows none of ``apple.news``, ``flipboard.com``,
    ``msn.com`` or ``substack.com``. So the registry's own ``EXCLUDED_KINDS`` covers the outlets it
    has, and :data:`PROXY_HOSTS` covers the ones it does not — which is precisely the population
    discovery works on. Asking the registry first also means a curated ``kind`` correction
    automatically improves this gate instead of being shadowed by a hard-coded list.

    Subdomain-tolerant, the same rule ``corpus._host_match`` uses: a candidate on
    ``news.google.com`` and one on ``foo.news.google.com`` are the same problem."""
    h = (host or "").lower().lstrip(".")
    if not h:
        return False
    if reg is not None:
        o = reg.resolve(h)
        if o is not None and o.kind in outlet_registry.EXCLUDED_KINDS:
            return True
    return any(h == p or h.endswith("." + p) for p in PROXY_HOSTS)


def is_tracked(host: str, reg) -> bool:
    """Gate 7. Whether the registry already resolves this host to an outlet we carry.

    Resolved through the registry rather than by string comparison against a name list, so an alias
    or a second domain the registry knows counts as tracked — otherwise discovery re-proposes
    outlets we have, under a second identity, and the tier configuration then names one of them."""
    return bool(host) and reg.resolve(host) is not None


def candidates(rows: list, reg, *, floor: int = VOLUME_FLOOR) -> "list[dict]":
    """`(host, evidence)` candidates from catalog rows, richest evidence first.

    One entry per HOST rather than per publisher string. A host is what Stage 2 would send a request
    to, and it is also the thing that survives the publisher-name variation this catalog is full of
    — the same outlet arrives as ``Sportskeeda``, ``sportskeeda.com`` and ``SPORTSKEEDA`` and they
    are one candidate, not three.

    Everything is reported, including candidates the gates reject; ``eligible`` says whether a
    request is justified. A discovery run that silently dropped its rejections could not be audited,
    and the rejection counts are the cheapest evidence that the gates are doing anything at all."""
    by_host = defaultdict(list)
    for r in rows:
        h = _host(r)
        if h:
            by_host[h].append(r)

    out = []
    for host, arts in by_host.items():
        names = Counter((a.get("publisher") or "").strip() for a in arts if (a.get("publisher") or "").strip())
        langs = Counter((a.get("language") or "").strip().lower() for a in arts
                        if (a.get("language") or "").strip())
        dated = sum(1 for a in arts if (a.get("publishedAt") or "").strip())
        # Proxy is read BEFORE tracked, because a host can be both and "aggregator" is the more
        # informative half: `news.google.com` IS in the registry, and reporting it as merely
        # "already tracked" would suggest we carry it as a publisher rather than that its articles
        # are other publishers'. Same ordering principle `source_evaluation.evaluate` uses — the
        # disqualifying fact is read before the procedural one.
        proxy = is_proxy_host(host, reg)
        tracked = not proxy and is_tracked(host, reg)
        below = len(arts) < floor
        out.append({
            "host": host,
            "articles": len(arts),
            "publishers": [n for n, _ in names.most_common(5)],
            "language": langs.most_common(1)[0][0] if langs else "",
            "datedShare": dated / max(1, len(arts)),
            "sampleUrls": [a.get("url") or a.get("canonicalUrl") for a in arts[:3]],
            "tracked": tracked,
            "proxy": proxy,
            "belowFloor": below,
            "eligible": not (tracked or proxy or below),
            "reason": ("aggregator/proxy host — its articles are someone else's" if proxy else
                       "already tracked by the registry" if tracked else
                       f"{len(arts)} articles, below the {floor}-article floor" if below else
                       "no offline gate rejects it"),
        })
    return sorted(out, key=lambda c: (not c["eligible"], -c["articles"]))


def worklist(cands: list) -> "list[dict]":
    """Only the candidates a network request is justified for — what Stage 2 receives."""
    return [c for c in cands if c["eligible"]]


def census(cands: list) -> dict:
    """Counts by rejection reason, so a run says what its gates did rather than only what survived."""
    c = Counter()
    for cand in cands:
        c["total"] += 1
        c["eligible" if cand["eligible"] else
          "proxy" if cand["proxy"] else
          "tracked" if cand["tracked"] else "belowFloor"] += 1
    return dict(c)


def probe_cost(cands: list, *, requests_per_host: int = 2,
               seconds_per_request: float = 2.0) -> dict:
    """What Stage 2 would cost in requests and wall time, stated BEFORE it is authorised.

    Two requests per host — ``robots.txt`` and one autodiscovery fetch — at the crawler's configured
    politeness interval. This is the number that goes in front of a human, because "how much of a
    publisher's bandwidth are we about to spend" is the question a ToS review is actually asking."""
    n = len(worklist(cands))
    reqs = n * requests_per_host
    return {"hosts": n, "requests": reqs, "seconds": reqs * seconds_per_request,
            "minutes": round(reqs * seconds_per_request / 60.0, 1)}
