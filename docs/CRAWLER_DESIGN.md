# Publisher Crawler Framework — Design

Status: **read-only POC, not wired into ingestion.** Nothing in the production ingest path imports
`examples/crawler.py`. It writes to the catalog only when a caller explicitly invokes
`CrawlAdapter.poll_once`, which no production code does.

## Why

RSS and the keyed-JSON providers give us breadth but not completeness. Feeds are partial (a
publisher's `/rss.xml` often carries a section, not the newsroom), sometimes stale, and sometimes
absent — AP's public feeds are thin enough that we rely on aggregators for a wire service whose own
site publishes everything. The gap shows up downstream as a publisher with a profile page and very
few articles behind it, which weakens exactly the coverage-comparison and blind-spot features that
depend on knowing what an outlet *did* publish.

A crawler closes that gap using what publishers already publish for machines: sitemaps and section
indexes.

## What this is not

**It is not an article scraper.** The crawler fetches discovery documents — `robots.txt`, feeds,
sitemaps, section pages — and never an article page. Three reasons, in order of weight:

1. **Licensing.** Article text is the copyrighted asset. We hold no licence to it, and the MBFC
   conversation is a live reminder that "we technically could fetch it" and "we may hold and
   display it" are different questions. Discovery documents are published *for* machine consumption
   and carry only the URL, headline, and publication date.
2. **Volume.** Fetching index pages costs ~1–10 requests per publisher per cycle. Fetching every
   article costs one request per article — two orders of magnitude more load on newsrooms whose
   goodwill we depend on.
3. **We don't need it.** `FeedEntry.body` is already `None` for most RSS, and the pipeline handles
   that. Body would improve clustering and Coverage Comparison, but it is a separate decision with
   its own legal review, not something to half-build here.

Article-body extraction is deliberately **out of scope** and is not partially implemented.

## Architecture

The crawler is a discovery layer that terminates at the existing choke point. After that boundary
nothing downstream knows a crawler was involved.

```
robots.txt gate ─→ discovery ladder ─→ filter ─→ FeedEntry[]
                   rss → sitemap        domain          │
                        → section       pattern         ▼
                                        dedup    ingest_entries()
                                        catalog         │
                                                        ▼
                                    score · canonical dedup · media · persist
                                                        │
                                                        ▼
                                    clustering · stories · recommendations
```

It is a `sources.SourceAdapter` subclass, so it inherits the chassis every other provider uses:
per-source quota, `ingest_entries`, health recording, and the "one source's outage never affects
another" error discipline. `CrawlAdapter` is short precisely because everything after the
`FeedEntry` list is already solved.

### Reuse, by design

| Concern | Where it already lives | Crawler's part |
|---|---|---|
| Canonical URL dedup | `ingest.canonical_url` | calls it; adds no second notion of sameness |
| Scoring / lean | `ingest.score_with_cache` | none — `publisher_hint` only |
| Catalog persistence | `store.upsert_feed_article` | none |
| Block list | `ingest.is_blocked_from_catalog` | none — enforced behind the choke point |
| Retry / backoff / 429 | `sources._request` | reuses it |
| Health metrics | `store.record_feed_health` | records under `crawl://<publisher>` |
| Publisher identity | `outlet_registry` | config is lint-checked against it |
| HTML sanitising | `text_utils.clean_html` | free via `FeedEntry.__post_init__` |

The crawler adds exactly four things that did not exist: **robots.txt compliance**, **per-host rate
limiting**, **the discovery ladder**, and **per-publisher configuration**.

### The discovery ladder

Ordered, and it **stops at the first rung that yields articles**:

| Rung | Cost | Carries | Why here |
|---|---|---|---|
| `rss` | 1 request | title, date, description, media | the publisher's own machine-readable offer |
| `sitemap` | 1–N | title, date (news sitemaps) | complete; the reason the framework exists |
| `section` | 1 | title only | last resort — **no publication date** |

A publisher with a healthy feed is never sitemap-crawled. Section pages sit last because they state
no publication date; the crawler leaves `published_at` as `None` rather than defaulting to "now",
which would put a five-year-old feature at the top of Latest. `ingest_entries` counts that as
`missing_metadata`.

A sitemap *index* is followed exactly one level down. Deeper recursion is how one cycle silently
becomes a thousand requests, and `max_fetches` bounds the pathological case regardless.

### robots.txt — fails closed

**An unreachable, unparseable, or non-policy `robots.txt` means we do not crawl that publisher.**

The conventional crawler default — absent robots.txt means crawl freely — is a reading search
engines earned over decades of established norms. It is not available to a commercial product
reading newsrooms it has never spoken to. "We could not determine whether we were allowed" is not a
licence.

One subtlety worth stating because it is the most likely way this gets silently defeated:
`urllib.robotparser` parses an HTML 404 page into a policy with **no rules**, and a policy with no
rules answers `can_fetch` with `True`. So the most common form of "robots.txt is unavailable" — an
origin that returns 200 and a web page for every path — would read as blanket permission. The gate
therefore validates that the body is actually a policy (contains a `User-agent:` line) before
trusting it. Without that check, fail-closed is fail-open wearing a costume. There is a test named
for this.

`Crawl-delay` is honoured and can only ever **slow us down** — it is combined with our floor via
`max()`, never `min()`.

### Rate limiting

Per **host**, not per publisher: the limit protects the publisher's server, and two configured
publishers can sit behind one CDN. Default floor 2s, raised by the publisher's own `Crawl-delay`.
`sleep` and `clock` are injected so tests assert the waits without taking them.

### Dedup, in three layers

1. **In-cycle, canonical.** A homepage links the same story bare, with a trailing slash, and with a
   tracking param. Three strings, one article — so dedup uses `ingest.canonical_url`, the same
   function the catalog will later key on, rather than a second notion of sameness that could
   disagree with it.
2. **Against the catalog, before fetching further.** One batched `store.existing_feed_urls` call.
   A publisher's sitemap is mostly yesterday's articles; skipping what we already hold is the
   single biggest politeness win available.
3. **At the choke point.** `ingest_entries` dedups on `canonical_url` as it always has. Layers 1–2
   are politeness; layer 3 is correctness, and it stays where it already was.

### Configuration

`examples/data/crawler_publishers.json` — data, not code. Adding a publisher is a config edit.

`domains` (where articles may live) and `discovery_domains` (where discovery documents may be
served) are **separate lists**, and this matters more than it looks. The BBC serves feeds from
`bbci.co.uk` and journalism from `bbc.co.uk`. Folding the feed host into `domains` to make the
config validate would widen the set of hosts allowed to yield *articles* — and since a discovered
URL is attributed to the configured publisher and inherits that publisher's lean, that widening is
how an impersonator's URL gets published under a trusted outlet's name. Subdomain matching is
anchored on a dot boundary for the same reason: a bare `endswith("npr.org")` also accepts
`notnpr.org`.

`article_pattern` is applied to **every** rung, not just section pages, so a misconfigured sitemap
cannot inject tag pages, author pages, or the shop into the catalog either.

`crawler.py lint` checks all of it against the outlet registry and fails the build on a typo'd key
rather than silently taking a default.

### Crawl health

Every cycle produces a `CrawlReport` that accounts for **every discovered URL** under the reason it
was dropped: `off_domain`, `pattern_rejected`, `duplicate_in_cycle`, `already_in_catalog`,
`robots_blocked`, `capped`, `accepted`. A crawler that returns zero articles must be able to say
*which gate closed* — otherwise diagnosing it means re-running it against the publisher, which is
the one thing we should not do casually. There is a test asserting the counters sum to
`discovered`.

Per-publisher health lands in the existing `feed_health` table under `crawl://<publisher>`, so
crawled publishers appear in the same monitoring as every feed.

## POC scope

Five publishers, chosen for different discovery shapes:

| Publisher | Ladder | Why |
|---|---|---|
| BBC | rss → sitemap → section | separate feed host — the `discovery_domains` case |
| NPR | rss → sitemap → section | conventional news sitemap |
| The Guardian | rss → sitemap → section | strong feeds; should never leave rung 1 |
| Associated Press | sitemap → section | thin public RSS — the case the ladder exists for |
| Texas Tribune | rss → section | small non-profit; checks we aren't tuned for big infra |

```bash
python examples/crawler.py config                  # list configured publishers
python examples/crawler.py lint                    # validate config against the registry
python examples/crawler.py robots --publisher NPR  # show the resolved robots decision
python examples/crawler.py plan --publisher NPR    # dry run: what WOULD be ingested
```

`plan` is the default mode and is **read-only**: its only store access is `existing_feed_urls`.

## What this POC has NOT proven

Stated plainly, because the gap between "the framework works" and "it works against real
publishers" is where crawler projects go wrong.

- **No live crawl has been run.** Outbound egress to publisher domains is blocked from this
  environment (`bbc.co.uk`, `npr.org`, `apnews.com` all fail to connect). Every test is
  fixture-driven. The framework's logic is verified; its contact with real HTML is not.
- **The configured URLs and `article_pattern`s are from public convention, not observation.** They
  are unverified guesses at sitemap paths and URL shapes and should be assumed wrong until checked.
- **No publisher's robots.txt has been read.** Any of these five may disallow us outright. That is
  a real possible outcome of step 1 below, not a formality.
- **No ToS review has been done.** Several major publishers prohibit automated access in their
  terms regardless of what robots.txt permits. robots.txt is a technical signal, not a licence.

### Update (2026-08-26): three of those four are now closed

Recorded here rather than left standing, because a stale "never run" is the same defect as a stale
gate — a claim about the system that stopped being true and kept being printed.

- **A live crawl has run**, from production, under the beta-development policy: 7 hosts across three
  runs (`e24d754`, `0e50bf3`, `ca97347`), no ingestion and no writes. The transport is proven.
- **Robots.txt has been read**, and **not one publisher disallowed us**. The only gate-1 REJECT was
  `nysun.com` serving HTTP 429 — an *unavailable* file, not a refusal.
- **Two publishers' URL shapes are now observed rather than guessed**: `kait8.com` and `kwch.com`
  carry `article_pattern` `/\d{4}/\d{2}/\d{2}/`, written from six real URLs the probe returned. The
  other five publishers' patterns remain unverified guesses and their configs remain `enabled:
  false`.
- **The ToS review is still outstanding**, and is the one item on this list that live probing cannot
  close.

## ⚠ The gap this document did not notice, and its fix

Everything above describes the discipline built for a crawler that **had not yet run**. An audit of
M7 Stage 2 found that the ingestion **running in production every cycle** had none of it:

* **F1 — no robots gate on the live path.** `rss_ingest`, `sources`, `feed_service` and
  `feed_schedule` contained no reference to robots at all. It existed only in `crawler.py`, in M7's
  validation modules, and in `verify_crawler_config.py` — none of which had ever contacted a real
  host. **The unrun proof-of-concept was more compliant than production**, and *"Hidden View
  respects robots.txt"* was not a true sentence.
* **F2 — we misidentified ourselves.** The poller's agent was
  `InformationHealth-RSS/0.1 (+https://code.claude.com)` — a documentation site belonging to another
  organisation. A publisher reading their logs to find out who was polling them, or wanting to ask
  us to stop, was sent to the wrong company. The crawler's own agent named `hidden-view.com/crawler`,
  which returned 404.

This document also framed the crawler as *"the first thing that touches a publisher"*. True of the
crawler; false of the product, which polls nine newsroom hosts continuously.

**Fixed** in `examples/robots.py` — the rules extracted there so both the POC and the live poller
share one definition (`crawler` imports `rss_ingest`, so they could not live in `crawler`), both
seams gated, both agents composed from one function, and `/crawler` + `/robots.txt` now served.

The live path deliberately differs from this document's fail-closed posture on **one** outcome:

| outcome | crawler (discovery) | live poller |
|---|---|---|
| policy says **Disallow** | refuse | **refuse** |
| policy **unreadable** | refuse | report; refuse only under `RWE_ROBOTS_STRICT=1` |

A one-shot probe of a stranger and a recurring poll of an operator-chosen feed are different acts
with asymmetric failure costs: refusing on *unreadable* lets a CDN hiccup silently stop ingestion
from a publisher who never objected, while allowing it opens a window that self-corrects the moment
the file is readable again. RFC 9309 treats unreachability and prohibition as distinct for the same
reason. `RWE_ROBOTS_ENFORCE=0` is the kill switch; both flags are in the compose allowlist, because
a switch that cannot be turned off in an incident is not a switch.

## Recommended order

**1. Verify the ground truth before writing more code.** `examples/verify_crawler_config.py` does
this and is the gate for everything below:

```bash
python examples/verify_crawler_config.py            # exit 0 only if every publisher verified
python examples/verify_crawler_config.py --json > crawl-verification.json
```

Per publisher it checks robots.txt (reachable, a real policy, allows our UA, states which
`Crawl-delay`), each configured discovery URL (exists, parses, yields entries), the
`article_pattern` **against URLs actually discovered**, and locates ToS clauses containing
automated-access language for a human to read. It reuses the crawler's own policy and parsers, so a
green report means *that code* works against the real site. It never fetches an article, never
touches the store, and obeys the same robots gate it is testing.

Two of its checks exist for failures that are otherwise invisible: a pattern matching **0%** of
discovered URLs makes the crawler ingest nothing while every gate reports healthy, and robots.txt
declaring `Sitemap:` URLs we are not using usually means our configured path was a guess.

**Run it from an environment with real outbound access.** It cannot tell you anything from a
sandbox whose egress proxy refuses the hosts — which is exactly what happened when this POC was
built (see "What this POC has not proven"). A publisher that disallows us is removed from the
config, not worked around.

**2. Legal position.** Same question the MBFC work raised, different asset. robots.txt permission
is not a content licence. Confirm that ingesting headline + URL + timestamp as we already do for
RSS is materially the same act when the URL came from a sitemap — I believe it is, since we store
identical fields either way, but that is a judgement to confirm rather than assume.

**3. Shadow mode.** Run `plan` against the real sites, writing nothing:

```bash
python examples/crawler.py plan --db "$RWE_DB_URL" --json > shadow-$(date +%F).json
python examples/crawler.py plan --db "$RWE_DB_URL"            # human-readable table
```

```
publisher            rung        cand  known    new   new%  fetches
NPR                  sitemap      412    381     31     8%  2
BBC                  rss           60     58      2     3%  1
TOTAL                              472    439     33     7%  3
```

`cand` counts unique on-domain URLs that pass `article_pattern`; `known` is what the catalog
already holds; `new%` is the marginal value. **`--db` is required for the measurement to mean
anything** — without it every URL is reported as new, a 100% marginal value derived from an absent
comparison, so the CLI prints a warning banner rather than a confident number.

The denominator is counted **before** `max_urls` truncates. Measuring after the cap would report
100% new whenever the cap binds — the ratio would look best exactly when the crawler is drowning in
already-held URLs.

A publisher yielding zero candidates prints which gate closed (robots refusal, pattern matching
nothing, everything off-domain), because a bare `0` is undiagnosable without re-running against the
publisher — the thing this framework exists not to do casually.

**If `new%` is low, stop here.** A crawler that rediscovers what RSS already delivered is cost
without benefit, and this is the cheapest possible place to learn it. That is a real possible
answer, not a failure of the exercise.

Store access: `plan` performs one `SELECT` per publisher (`existing_feed_urls`) and no writes.
Constructing `Store` runs the same idempotent `create_all` + `_ensure_*_columns` schema check the
API server runs at every boot — no-ops on an already-migrated database, but not literally
read-only. Point it at a replica or a restored snapshot if you want that guarantee absolutely.

**4. Promote `sources._request`** to a public shared helper. The crawler reuses it today through a
private name, which is a deliberate seam left visible rather than hidden — it keeps this POC from
touching a production ingestion file.

**5. Wire one publisher.** Register a single `CrawlAdapter` in `SourceRegistry` behind
`RWE_CRAWL_ENABLED`, default off. Watch `crawl://` health and catalog growth for a week.

**6. Widen** only on evidence from step 5.

## Open questions

- **Attribution of crawled articles.** They currently carry `source_type: "crawl"`. Should a
  publisher profile distinguish "we got this from their feed" from "we found this on their
  sitemap"? Provenance is a value the product already takes seriously elsewhere.
- **Interaction with retention.** Sitemaps can reach back years. `max_urls` bounds a cycle, but a
  crawl of a deep sitemap could pull articles far older than the story scan window, which would
  ingest rows that no story build will ever cluster.
- **Wire-service duplication.** AP articles reprinted by other outlets already cluster; crawling
  AP directly changes the ratio of wire to original copy in the catalog, which may shift
  Coverage Comparison in ways worth measuring before widening.
