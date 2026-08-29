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


#: Every acquisition channel, as stored in ``store.SourceAdmission.channel``.
#:
#: Phase 2 of the 50k roadmap is a **portfolio**: no single channel plausibly supplies the ~45,000
#: outlets Tier B needs, so the design has to carry several and the yield of each has to be
#: comparable. These names are that comparison's key.
#:
#: ``catalogue``  the original channel — hosts already in our catalogue with enough observed
#:                articles to justify a request. **It cannot add an outlet**: every candidate is a
#:                host we already ingest, so admitting one reclassifies rather than acquires.
#: ``directory``  bulk import from a structured list (a media register, a per-country index). Nearly
#:                request-free, and it brings country and language with it.
#: ``web``        gap-driven search of the open web. The only channel with the range to reach 10⁴,
#:                and the one whose fetcher is supplied separately and deliberately.
#: ``link``       outbound links seen while crawling a publisher we already carry.
CHANNELS = ("catalogue", "directory", "web", "link")


def catalogue_evidence(rows: list) -> "list[dict]":
    """Per-host evidence from catalog rows. **The catalogue channel's half of discovery.**

    One entry per HOST rather than per publisher string. A host is what Stage 2 would send a request
    to, and it is also the thing that survives the publisher-name variation this catalog is full of
    — the same outlet arrives as ``Sportskeeda``, ``sportskeeda.com`` and ``SPORTSKEEDA`` and they
    are one candidate, not three.

    Split out of :func:`candidates` so the gates below can serve every channel. What stayed here is
    the only part that is channel-specific: what we know about a host and how we came to know it. A
    directory import knows a country and a language and no articles at all; a web result knows the
    query that found it. Each supplies its own evidence and its own admissibility predicate, and
    they all meet at :func:`gate`."""
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
        out.append({
            "host": host,
            "articles": len(arts),
            "publishers": [n for n, _ in names.most_common(5)],
            "language": langs.most_common(1)[0][0] if langs else "",
            # WARNING: for rows from `story_service._fetch` this is 1.0 BY CONSTRUCTION — the query
            # filters `published_at >= date_from`, so an undated row cannot be in the window.
            # Measured on the first production run: 30 of 30 candidates at 100%. It is real data for
            # an unwindowed caller and an empty ritual for the runner, which is why the runner does
            # not print it. Gate 4 asks the same question of the FEED, where it can actually fail.
            "datedShare": dated / max(1, len(arts)),
            "sampleUrls": [a.get("url") or a.get("canonicalUrl") for a in arts[:3]],
        })
    return out


def volume_floor(floor: int = VOLUME_FLOOR):
    """The catalogue channel's admissibility predicate: ``>= floor`` observed articles.

    Returned as a callable because the question it answers — *is a network request justified for
    this host?* — is the same for every channel while the evidence that answers it is not. A
    directory row has no articles and never will; requiring them would reject the entire channel for
    lacking evidence the channel does not produce, which is how a floor stops being a cost bound and
    starts being an accident."""
    def admissible(rec: dict) -> "tuple[bool, str]":
        n = int(rec.get("articles") or 0)
        if n < floor:
            return False, f"{n} articles, below the {floor}-article floor"
        return True, ""
    admissible.floor = floor                    # for the census, which reports `belowFloor`
    return admissible


def always_admissible(rec: dict) -> "tuple[bool, str]":
    """A channel whose own act of discovery IS the evidence — a directory entry, a search hit.

    Not a weaker bar, a different one: the shared gates (already tracked, aggregator/proxy) still
    run, and every network gate in `source_validation` still has to pass before anything is
    admitted. What this says is that the channel has no *prior* volume signal to threshold, so the
    probe is where the question gets settled."""
    return True, ""


def gate(records: list, reg, *, admissible=None, channel: str = "catalogue") -> "list[dict]":
    """`(host, evidence)` records -> candidates, richest evidence first. **Shared by every channel.**

    Applies the two offline gates that are pure functions of a host and the registry — gate 7
    (already tracked) and gate 8 (aggregator/proxy) — plus the channel's own ``admissible``
    predicate, and stamps the channel so the campaign can report yield per channel later.

    Everything is reported, including candidates the gates reject; ``eligible`` says whether a
    request is justified. A discovery run that silently dropped its rejections could not be audited,
    and the rejection counts are the cheapest evidence that the gates are doing anything at all."""
    admissible = admissible or volume_floor()
    out = []
    for rec in records:
        host = rec.get("host") or ""
        # Proxy is read BEFORE tracked, because a host can be both and "aggregator" is the more
        # informative half: `news.google.com` IS in the registry, and reporting it as merely
        # "already tracked" would suggest we carry it as a publisher rather than that its articles
        # are other publishers'. Same ordering principle `source_evaluation.evaluate` uses — the
        # disqualifying fact is read before the procedural one.
        proxy = is_proxy_host(host, reg)
        tracked = not proxy and is_tracked(host, reg)
        ok, why = admissible(rec)
        below = not ok
        out.append({
            **rec,
            "articles": int(rec.get("articles") or 0),
            "publishers": rec.get("publishers") or [],
            "language": rec.get("language") or "",
            "channel": channel,
            "tracked": tracked,
            "proxy": proxy,
            "belowFloor": below,
            "eligible": not (tracked or proxy or below),
            "reason": ("aggregator/proxy host — its articles are someone else's" if proxy else
                       "already tracked by the registry" if tracked else
                       why if below else
                       "no offline gate rejects it"),
        })
    return sorted(out, key=lambda c: (not c["eligible"], -c["articles"]))


def candidates(rows: list, reg, *, floor: int = VOLUME_FLOOR) -> "list[dict]":
    """The catalogue channel, end to end. Unchanged behaviour; now one caller of :func:`gate`."""
    return gate(catalogue_evidence(rows), reg, admissible=volume_floor(floor), channel="catalogue")


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


def probe_cost(cands: list, *, requests_per_host: int = 3,
               seconds_per_request: float = 2.0) -> dict:
    """What Stage 2 would cost in requests and wall time, stated BEFORE it is authorised.

    **Three** per host, corrected from two against what the first live crawl actually spent:
    ``robots.txt``, the landing page, and the discovery document. The old figure omitted the landing
    page and would have understated a 182-host run by 182 requests — an estimate put in front of a
    human to authorise crawling has to be the real number, not the optimistic one.

    Up to **five** where the sitemap rung fires and its target is an index needing one descent. That
    is the worst case, not the typical one: it only runs when there is no feed to find."""
    n = len(worklist(cands))
    reqs = n * requests_per_host
    return {"hosts": n, "requests": reqs, "seconds": reqs * seconds_per_request,
            "minutes": round(reqs * seconds_per_request / 60.0, 1)}
