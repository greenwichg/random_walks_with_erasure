# Publisher metadata enrichment (Wikipedia + Wikidata)

The Publisher page composes three kinds of fact, and the whole design turns on keeping them apart.

| source | what it supplies | authority |
|---|---|---|
| `curated` | `outlet_registry` — canonical name, AllSides lean, home country/region/city, scope | hand-verified; **authoritative** |
| `counted` | measured from our own catalog — the host we actually observe them publishing from | a fact about us, not a claim by anyone |
| `wikipedia` | the article — prose description, lead image | third-party, verified before use |
| `wikimedia` | Wikidata claims + Commons — inception, HQ, country, website, parent, logo file | third-party, verified before use |

## The merge rule

**Curated data is never overwritten.** Enrichment fills gaps and does nothing else.

The brief that prompted this allowed overwriting "when the new data is more complete". For a scalar
field that comparison is not meaningful — `"US"` is not more complete than `"US"`, and a Wikidata
value is not more trustworthy than one a human checked. So the implemented rule is the strict
reading: filling an empty field is unambiguously more complete; replacing a curated one never is.
Enrichment's real contribution is the fields the registry has no column for — `founded`, `parent`,
`description` — where there is nothing to overwrite.

Provenance is recorded **per field**, not per record, because a merged profile genuinely has mixed
sourcing. One record-level `source: wikipedia` label would misdescribe the curated half of it.

```
GET /api/publishers/BBC%20News
{
  "about": {
    "description": "…",           "founded": "1922",
    "headquarters": "London",     "country": "GB",
    "website": "https://bbc.co.uk", "parent": "BBC",
    "sources": { "description": "wikipedia", "founded": "wikimedia",
                 "headquarters": "curated",  "country": "curated",
                 "website": "counted",       "parent": "wikimedia" },
    "wikipediaUrl": "…", "status": "ok", "refreshedAt": "…"
  }
}
```

## Verification — why most candidates are refused

A publisher page already carries a lean rating and counted coverage claims. Attaching another
organisation's founding year and parent company to one would be a factual error wearing the same
confidence as a counted fact. A wrong match is worse than no match, so `publisher_wiki.verify`
accepts only on evidence:

1. **Domain agreement** — Wikidata's official-website host (P856) matches the host we observe this
   publisher publishing from. Decisive, and it is independent evidence rather than another name
   string. This is why the catalog's counted host is passed into every lookup.
2. **Domain conflict** — both known and different → refused. This is the case that would otherwise
   put Fox Corporation's facts on a Fox Sports page.
3. **No domain to compare** — the article title must match the publisher's name **and** the item
   must look like an organisation, either by carrying an organisational claim (website / inception /
   HQ / parent) or by its `instance of` (P31) being a media/organisation class. Both halves matter:
   the title check alone lets a common-noun masthead ("Mirror", "Metro", "The Sun") bind to the
   everyday-object article, and the organisation check alone lets any similarly-named company
   through.

Domains are compared at the **brand label** — `bbc.co.uk`, `bbc.com` and `news.bbc.co.uk` all reduce
to `bbc`. Whole-domain comparison shipped first and cost real recall: on the live catalog's busiest
20 publishers, 5 of 8 "conflicts" were one organisation reached by two spellings (`bbc.co.uk` vs
`bbc.com`, `dailymail.com` vs `dailymail.co.uk`, `aol.co.uk` vs `aol.com`, `unitaid.eu` vs
`unitaid.org`, `newsinfo.inquirer.net` vs `inquirer.com.ph`). The label still separates genuinely
different organisations — the true refusals in the same run (`aktiencheck` vs `tomshardware`,
`pagesix` vs `nypost`, `foxsports` vs `foxcorporation`) differ at the label too.

Publisher names that ARE domains (`marketbeat.com`, `aol.co.uk`, `thestar.com.my` — a large share of
the catalog) are reduced the same way before the title comparison, so `marketbeat.com` can match the
article titled "MarketBeat" instead of failing on the `.com`.

Anything else is recorded as `ambiguous` — a distinct state from `no_match`, because it marks the
outlets a human could resolve by hand. The **`reason`** column records which rule fired (`domain`,
`title`, `domain_conflict`, `not_an_organisation`, `disambiguation`, `unverified`, `no_page`), so
triaging the backlog is a query rather than a re-run of every lookup.

## Statuses and refresh cadence

| status | meaning | TTL |
|---|---|---|
| `ok` | found, verified, parsed | 30 d |
| `no_match` | searched, nothing plausible exists (a negative cache entry) | 30 d |
| `ambiguous` | candidates existed, none confirmable — the curation backlog | 14 d |
| `error` | the lookup itself failed; says nothing about the outlet | 6 h |

**Idempotence** lives in `publisher_metadata.pending()`: a publisher whose row is still fresh is not
returned, so a rerun does no work and makes no requests. That is a property of the data, not a flag
— there is no "already done" state to corrupt, which is what makes the refresh safe on a cron or
after a partial failure.

**One asymmetry:** a *failed* lookup never replaces a *successful* row. Wikipedia is edited live —
an article can be briefly redirected to a disambiguation page, moved mid-rename, or 503 for a
minute — and none of that is a reason to discard facts verified an hour ago. Without this rule a
single bad minute upstream silently empties publisher pages.

## Where it runs

`sources.PublisherMetadataEnricher`, an adapter in the default registry, alongside the GKG
event-geography enricher. It is an adapter rather than a `_post_cycle` hook for three reasons:

- it needs its **own cadence** (a 30-day TTL does not want re-checking every ingest cycle);
- it needs the poller's **per-source health and failure backoff**, so a Wikimedia outage throttles
  this and not the catalog;
- `fetch_json` has to be **injectable** so the suite never touches the network.

That last one is not hypothetical. This first shipped inside `MultiSourcePoller._post_cycle`, where
it built its own HTTP call — every poller test then made real Wikipedia requests, the suite went
from 60 s to 194 s, and an unrelated adapter-isolation test failed on timing.

Off in code, on in production via `RWE_PUBLISHER_WIKI=1` in `deploy/docker-compose.yml` — the same
convention GKG follows. A module that reaches a third-party API should never do so merely because
it was imported.

### Request budget

One publisher costs 2–4 requests; `RWE_PUBLISHER_WIKI_BATCH` (default 5) run per
`RWE_PUBLISHER_WIKI_INTERVAL` (default 900 s) = 480 publishers/day. The live catalog holds **~3,600
distinct publishers**, so a cold start takes **~7 days** and ~11,000 requests in total. Steady state
is far cheaper because fresh rows are skipped without a request — once the catalog is covered a
cycle costs one SQL query and zero HTTP. Raise the batch for a faster fill; request rate scales
linearly with it.

Wikimedia's User-Agent policy requires a descriptive agent with a contact address; requests without
one are refused with 403. `RWE_WIKI_CONTACT` supplies it.

## Manual refresh

```bash
docker exec -i deploy-api-1 python examples/refresh_publisher_metadata.py --stats
docker exec -i deploy-api-1 python examples/refresh_publisher_metadata.py --dry-run
docker exec -i deploy-api-1 python examples/refresh_publisher_metadata.py --limit 50
docker exec -i deploy-api-1 python examples/refresh_publisher_metadata.py --publisher "BBC News"
docker exec -i deploy-api-1 python examples/refresh_publisher_metadata.py --force --limit 20
```

`--stats` prints coverage by status — the number to watch is `ambiguous`, which is the list of
outlets worth curating by hand in `examples/data/outlet_registry.csv`.

## Logos

Precedence is curated → enriched → favicon. The enriched logo sits **above** the favicon because a
favicon is a 16px browser icon that frequently 404s, while a Commons logo file is the outlet's
actual mark; it sits **below** curation because a hand-picked logo is a decision somebody made on
purpose. A Commons file named by claim P154 is preferred over the article's lead image, which is
often a headquarters photo rather than a logo.

The page keeps its existing `onError` fallback to the building icon, so a dead logo URL degrades to
the same placeholder it always did.

## Known limits

- **The public-suffix list is a hand-maintained short list**, not the real PSL (which is a
  dependency plus a data file to keep current). An unlisted two-part suffix makes `domain_label`
  take one label too few, which makes a match *fail* rather than falsely succeed — the safe
  direction — but it does cost recall for outlets on unusual ccTLDs.
- **`_ORG_CLASSES` is a short allow-list of Wikidata classes**, not a subclass-tree walk. It only
  ever adds acceptances, so an unlisted class degrades to the previous behaviour.
- **English Wikipedia only.** Non-anglophone outlets will show a lower match rate than their
  prominence suggests.
- **`country` requires an ISO alpha-2** (Wikidata P297). Every other country field in the product
  speaks alpha-2, so a bare label is dropped rather than shown — it would join with nothing.
