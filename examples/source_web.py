"""source_web.py — the WEB acquisition channel: gap-driven outlet discovery. Pure logic, no fetcher.

**Phase 2 of `docs/SCALE_ROADMAP.md`, and the only channel with the range to reach 10⁴.** It reads
where the corpus is thin, builds queries for those gaps, and turns whatever a search interface
returns into `source_discovery` evidence records.

## `search` has no default, and that is the whole safety design

This module **cannot reach the network**. :func:`discover` takes a ``search`` callable and there is
no fallback; without one it returns nothing and says so. That is the same structural choice
`source_validation` makes about its own fetcher, and its docstring gives the reason this module
inherits rather than restates: *"the alternative — a default fetcher, disabled by a flag — puts the
entire ToS question behind somebody remembering to pass --dry-run."*

It matters more here than there. Querying a search interface is the **one act in this pipeline that
is genuinely new**: fetching robots.txt, a landing page and a feed are things the probe already
does, for hosts an operator chose. Searching for outlets we have never heard of is a different act
with different terms attached, and it is the act the ToS review is about. So the seam is the review's
attachment point: clearing the review means supplying a fetcher, not editing this file.

## What a gap is, and why it is not a guess

`source_evaluation` and the registry already know which countries and languages the corpus is thin
in. M14 stopped on its own pre-registered bar — the peer-density hypothesis failed — but its
*instrument* survived, and the finding that survived with it is that ~16% of the window is
unlabelled and therefore invisible to targeting. So a gap here is stated as a (country, language)
pair with its current outlet count, and the count is reported alongside every candidate it produced:
a channel that cannot say which gap it was filling cannot be told from a channel that wandered.

## What this module does NOT do

It does not fetch article pages, crawl, or ingest. It produces **candidates**, and every one still
goes through the shared offline gates and then the full network probe before anything is admitted —
and admission assigns Tier B or shadow, never Tier A.

    python examples/source_web.py --gaps --db "$RWE_DB_URL"     # what gaps exist, no requests
    python examples/source_web.py --queries --country ZA        # what it WOULD ask, no requests
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import outlet_registry
import source_discovery as sd

#: Query templates, ``{country}`` / ``{language}`` substituted. Deliberately plain: a search string
#: is not the place for cleverness, and each of these is a phrase a person looking for local news
#: outlets would actually type. Kept as data so adding a phrasing is not a code change.
QUERY_TEMPLATES = (
    "local news websites in {country}",
    "{country} newspapers online",
    "{language} language news site {country}",
    "regional news outlets {country}",
)

#: Hosts a search result may never become a candidate for, beyond `source_discovery.PROXY_HOSTS`.
#: Search engines return encyclopaedias, directories, social platforms and their own properties far
#: more readily than they return a small newsroom, and every one of those that reaches the probe is
#: three requests spent proving something we already knew.
NON_OUTLET_HOSTS = frozenset({
    "wikipedia.org", "wikimedia.org", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "linkedin.com", "youtube.com", "tiktok.com", "pinterest.com", "quora.com", "amazon.com",
    "google.com", "bing.com", "yahoo.com", "duckduckgo.com", "archive.org", "issuu.com",
    "similarweb.com", "crunchbase.com", "glassdoor.com", "indeed.com", "tripadvisor.com",
    "britannica.com", "imdb.com", "github.com", "researchgate.net", "academia.edu",
})


def is_non_outlet(host: str) -> bool:
    """Whether ``host`` is a platform or reference site rather than a newsroom. Subdomain-tolerant,
    the same dot-anchored rule `source_discovery.is_proxy_host` uses — a bare ``endswith`` also
    matches ``notwikipedia.org``."""
    h = (host or "").lower().lstrip(".")
    if not h:
        return True
    return any(h == p or h.endswith("." + p) for p in NON_OUTLET_HOSTS)


def gaps(counts: dict, *, floor: int = 5) -> "list[dict]":
    """Coverage gaps, thinnest first, from ``{(country, language): outlet_count}``.

    ``floor`` is what counts as covered. Five is a deliberately low bar and not a target: it is the
    point below which a country cannot produce a story with `min_publishers = 2` on any but the
    biggest events, so it marks *structural* absence rather than thin coverage.

    Pure — the caller supplies the counts, so this is testable without a store and the same function
    serves the CLI, a test fixture and a future scheduled run."""
    out = []
    for key, n in counts.items():
        country, language = (key if isinstance(key, tuple) else (key, ""))
        if int(n or 0) >= floor:
            continue
        out.append({"country": (country or "").upper(), "language": (language or "").lower(),
                    "outlets": int(n or 0)})
    return sorted(out, key=lambda g: (g["outlets"], g["country"], g["language"]))


def queries(gap: dict, *, templates=QUERY_TEMPLATES) -> "list[str]":
    """The search strings for one gap. Reported before they are asked, so a run can be reviewed as
    a set of questions rather than after the fact as a set of requests.

    **Names, not codes**, via `location.country_name` / `location.language_name`: nobody searches
    for "local news websites in ZA", and "af language news site" is not a phrase. An unknown code
    resolves to empty and its templates are skipped rather than rendered with the raw code.

    **A country-less gap yields NO queries.** Every template is country-shaped, and formatting one
    with an empty country produced "local news websites in" — a dangling preposition that asks the
    open web nothing in particular and returns global listicles. Worse, it rendered *identically*
    for every country-less gap, so a run would send the same meaningless string once per gap and
    spend the provider quota discovering nothing. Returning nothing is the honest answer: the gap is
    real, we just cannot phrase a question for it, and `discover` reports it as unqueryable."""
    import location
    country = location.country_name(gap.get("country"))
    language = location.language_name(gap.get("language"))
    if not country:
        return []
    out = []
    for t in templates:
        if "{language}" in t and not language:
            continue
        q = " ".join(t.format(country=country, language=language).split())
        if q and q not in out:
            out.append(q)
    return out


def hosts_from_results(results, *, gap: "dict | None" = None) -> "list[dict]":
    """Search results -> evidence records, deduplicated by canonical host.

    ``results`` is a sequence of ``{"url": ..., "title": ...}`` — the shape every search interface
    can be adapted to, chosen so this module never learns a provider's payload. A result without a
    usable URL is dropped rather than guessed at.

    Three filters run here, before anything reaches the shared gates, and each is a request not
    spent: a URL with no host, a platform or reference site (:func:`is_non_outlet`), and an
    aggregator or proxy (`source_discovery.is_proxy_host`, which the shared gate applies again with
    the registry in hand — this is the cheap pre-pass, not a second policy)."""
    out: dict = {}
    for r in results or ():
        url = ((r or {}).get("url") or "").strip()
        if not url:
            continue
        host = outlet_registry._host_of(url)
        if not host or "." not in host:
            continue
        if is_non_outlet(host) or sd.is_proxy_host(host):
            continue
        title = ((r or {}).get("title") or "").strip()
        prev = out.get(host)
        if prev is None:
            out[host] = {
                "host": host,
                "articles": 0,
                "publishers": [title] if title else [],
                "language": (gap or {}).get("language", "") or "",
                "country": ((gap or {}).get("country", "") or "").upper(),
                "sampleUrls": [f"https://{host}/"],
                # WHICH gap produced this. A channel that cannot say what it was looking for cannot
                # be told from one that wandered, and the gap is also the only evidence this record
                # carries — there are no articles behind it.
                "gap": {"country": (gap or {}).get("country", ""),
                        "language": (gap or {}).get("language", ""),
                        "outlets": (gap or {}).get("outlets")} if gap else None,
            }
            continue
        if title and title not in prev["publishers"]:
            prev["publishers"].append(title)
    return list(out.values())


# --------------------------------------------------------------------------- #
# The search adapter. Configured, never assumed — and it returns None until it is.
# --------------------------------------------------------------------------- #
#: Known search APIs, as ``{endpoint template, key header, results path, url field, title field}``.
#:
#: **These are documented JSON APIs, not results-page scrapers.** Parsing a search engine's HTML is
#: the thing that actually breaches most search providers' terms, and building one here would bake a
#: policy decision into code that an operator cannot change. An endpoint template plus a JSON path
#: keeps the choice of provider — and its terms — with the person who signed up for it.
#:
#: ``results`` is a dotted path into the payload. ``{query}`` is URL-encoded on substitution.
SEARCH_PROVIDERS = {
    "brave": {
        "endpoint": "https://api.search.brave.com/res/v1/web/search?q={query}&count={count}",
        "header": "X-Subscription-Token", "results": "web.results",
        "url_field": "url", "title_field": "title",
    },
    "google_cse": {
        # Needs both a key and a search-engine id; the id goes in the endpoint via RWE_WEB_SEARCH_CX.
        "endpoint": ("https://www.googleapis.com/customsearch/v1"
                     "?q={query}&num={count}&key={key}&cx={cx}"),
        "header": "", "results": "items", "url_field": "link", "title_field": "title",
    },
    "serpapi": {
        "endpoint": "https://serpapi.com/search.json?q={query}&num={count}&api_key={key}",
        "header": "", "results": "organic_results", "url_field": "link", "title_field": "title",
    },
}


def _dig(payload, path: str):
    """Follow a dotted path into a decoded payload, returning ``[]`` rather than raising."""
    cur = payload
    for part in (path or "").split("."):
        if not part:
            continue
        if not isinstance(cur, dict):
            return []
        cur = cur.get(part)
    return cur if isinstance(cur, list) else []


def search_adapter(*, get_json=None):
    """A ``search(query) -> [{"url", "title"}]`` callable from the environment, or **None**.

    ``None`` when nothing is configured, and that is the load-bearing default: :func:`discover`
    treats a missing callable as "plan, do not ask", so an unconfigured deployment cannot make a
    search request no matter what else is switched on. Configuring one is a deliberate act by an
    operator who has decided the provider's terms are acceptable.

    Env surface::

        RWE_WEB_SEARCH_PROVIDER   brave | google_cse | serpapi   (or leave unset and give ENDPOINT)
        RWE_WEB_SEARCH_API_KEY    the provider's key
        RWE_WEB_SEARCH_CX         google_cse only: the search-engine id
        RWE_WEB_SEARCH_ENDPOINT   override the template; {query} {count} {key} {cx} substituted
        RWE_WEB_SEARCH_RESULTS    dotted path to the results array
        RWE_WEB_SEARCH_URL_FIELD / RWE_WEB_SEARCH_TITLE_FIELD
        RWE_WEB_SEARCH_COUNT      results per query (default 20)

    Requests go through ``sources._get_json``, so this inherits the 429/5xx retry budget the source
    adapters already have rather than growing a second one.
    """
    name = (os.environ.get("RWE_WEB_SEARCH_PROVIDER") or "").strip().lower()
    preset = dict(SEARCH_PROVIDERS.get(name) or {})
    endpoint = (os.environ.get("RWE_WEB_SEARCH_ENDPOINT") or preset.get("endpoint") or "").strip()
    if not endpoint:
        return None
    key = (os.environ.get("RWE_WEB_SEARCH_API_KEY") or "").strip()
    cx = (os.environ.get("RWE_WEB_SEARCH_CX") or "").strip()
    header = (os.environ.get("RWE_WEB_SEARCH_HEADER") or preset.get("header") or "").strip()
    results_path = (os.environ.get("RWE_WEB_SEARCH_RESULTS")
                    or preset.get("results") or "results").strip()
    url_field = (os.environ.get("RWE_WEB_SEARCH_URL_FIELD")
                 or preset.get("url_field") or "url").strip()
    title_field = (os.environ.get("RWE_WEB_SEARCH_TITLE_FIELD")
                   or preset.get("title_field") or "title").strip()
    try:
        count = max(1, int(os.environ.get("RWE_WEB_SEARCH_COUNT") or 20))
    except ValueError:
        count = 20

    if get_json is None:                                # imported lazily: keeps this module pure
        import sources                                  # for every caller that never configures one
        get_json = sources._get_json

    def search(query: str):
        url = endpoint.format(query=urllib.parse.quote_plus(query), count=count,
                              key=urllib.parse.quote_plus(key), cx=urllib.parse.quote_plus(cx))
        headers = {"Accept": "application/json"}
        if header and key:
            headers[header] = key
        payload = get_json(url, headers=headers)
        out = []
        for item in _dig(payload, results_path):
            if not isinstance(item, dict):
                continue
            out.append({"url": (item.get(url_field) or "").strip(),
                        "title": (item.get(title_field) or "").strip()})
        return out

    search.provider = name or "custom"
    return search


def search_config_warning() -> "str | None":
    """Why a configured-looking search is still disabled. Surfaced so a half-set provider reads as a
    misconfiguration rather than as "the channel found nothing"."""
    name = (os.environ.get("RWE_WEB_SEARCH_PROVIDER") or "").strip().lower()
    if not name and not (os.environ.get("RWE_WEB_SEARCH_ENDPOINT") or "").strip():
        return None
    if name and name not in SEARCH_PROVIDERS and not os.environ.get("RWE_WEB_SEARCH_ENDPOINT"):
        return (f"RWE_WEB_SEARCH_PROVIDER={name!r} is not one of "
                f"{', '.join(sorted(SEARCH_PROVIDERS))} and no RWE_WEB_SEARCH_ENDPOINT is set")
    if not (os.environ.get("RWE_WEB_SEARCH_API_KEY") or "").strip():
        return "RWE_WEB_SEARCH_PROVIDER is set but RWE_WEB_SEARCH_API_KEY is missing/empty"
    if name == "google_cse" and not (os.environ.get("RWE_WEB_SEARCH_CX") or "").strip():
        return "google_cse needs RWE_WEB_SEARCH_CX (the search-engine id) as well as a key"
    return None


def discover(gap_list, *, search=None, per_gap: int = 1, max_hosts: int = 0) -> dict:
    """Run the channel over ``gap_list``. **Returns nothing useful without a ``search`` callable.**

    ``search(query) -> [{"url", "title"}]`` is supplied by the caller and has no default. An
    offline run reports every query it *would* have asked and zero hosts, which is a plan rather
    than a failure — and it is what makes this module reviewable before it is ever enabled.

    ``per_gap`` caps queries per gap, and ``max_hosts`` caps the whole run (0 = unbounded). Both
    default low: this channel's cost is a search request per query plus three probe requests per
    surviving host, and an unbounded first run is how a discovery pipeline becomes the thing the
    politeness ceiling exists to prevent.

    Returns ``{"records", "queries", "searched", "gaps", "offline"}`` — the plan and the result in
    one object, so a caller reports what it asked as readily as what it got."""
    planned, records, searched = [], {}, 0
    asked: set = set()
    unqueryable = 0
    for gap in gap_list or ():
        qs = queries(gap)
        if not qs:
            # A gap we cannot phrase a question for — an unknown or absent country code. Counted so
            # a run that produced few queries says WHY, rather than looking like a quiet corpus.
            unqueryable += 1
            continue
        for q in qs[:max(0, per_gap)]:
            # Deduplicated ACROSS gaps, not just within one. Two gaps in the same country render the
            # same string, and a provider charges for each — this was measured at 200 identical
            # sends when every gap was country-less.
            if q in asked:
                continue
            asked.add(q)
            planned.append({"query": q, "gap": gap})
            if search is None:
                continue
            if max_hosts and len(records) >= max_hosts:
                break
            try:
                results = search(q)
            except Exception as exc:               # one bad query must not end the run
                planned[-1]["error"] = f"{type(exc).__name__}: {exc}"
                continue
            searched += 1
            for rec in hosts_from_results(results, gap=gap):
                records.setdefault(rec["host"], rec)
    out = list(records.values())
    if max_hosts:
        out = out[:max_hosts]
    return {"records": out, "queries": planned, "searched": searched,
            "gaps": list(gap_list or ()), "offline": search is None,
            # Gaps we could not phrase a question for. A run reporting few queries must say whether
            # the corpus is well covered or whether the country codes were simply missing.
            "unqueryableGaps": unqueryable}


def evidence(gap_list, *, search=None, per_gap: int = 1, max_hosts: int = 0) -> "list[dict]":
    """:func:`discover`'s records alone, for a caller that only wants to hand them to
    `source_discovery.gate`."""
    return discover(gap_list, search=search, per_gap=per_gap, max_hosts=max_hosts)["records"]


# --------------------------------------------------------------------------- #
# Gap counts from the live corpus — read-only, and the only part that touches a store.
# --------------------------------------------------------------------------- #
def corpus_gap_counts(store_, reg=None) -> dict:
    """``{(country, language): outlet_count}`` over the retained catalogue. Read-only.

    Counted per HOST rather than per article, because the question is how many outlets serve a
    place, not how loud the ones we have are. A single prolific outlet is not coverage — that is the
    same distinction `source_evaluation` draws between volume and breadth.

    Reads `store.list_coverage_rows`, which carries ``country``. It used to read
    `list_discovery_rows`, which does not — so it hardcoded an empty country, every gap came out
    country-less, and every query built from one came out as "local news websites in". The queries
    were malformed at the source, and nothing downstream could tell.

    A host is counted under the (country, language) pair it MOST OFTEN carries, not the first one
    seen: row order is arbitrary, so first-seen made the whole coverage map depend on how SQLite
    happened to return rows.
    """
    from collections import Counter
    per_host: dict = {}
    for r in store_.list_coverage_rows():
        host = outlet_registry._host_of(r.get("canonicalUrl") or "")
        if not host:
            continue
        cc = (r.get("country") or "").strip().upper()[:2]
        lang = (r.get("language") or "").strip().lower()[:2]
        per_host.setdefault(host, Counter())[(cc, lang)] += 1

    counts: dict = {}
    for host, pairs in per_host.items():
        # Deterministic: most articles wins, ties broken on the pair itself rather than on
        # insertion order, so two runs over the same catalogue agree.
        key = max(pairs.items(), key=lambda kv: (kv[1], kv[0]))[0]
        counts[key] = counts.get(key, 0) + 1
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gaps", action="store_true", help="report coverage gaps from the catalogue")
    ap.add_argument("--queries", action="store_true", help="print the queries a gap would ask")
    ap.add_argument("--country", default="")
    ap.add_argument("--language", default="")
    ap.add_argument("--floor", type=int, default=5)
    ap.add_argument("--per-gap", type=int, default=1)
    ap.add_argument("--search", action="store_true",
                    help="run ONE gap's queries against the configured provider and print what "
                         "comes back. Writes nothing; contacts no publisher.")
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    args = ap.parse_args(argv)

    if args.queries:
        gap = {"country": args.country, "language": args.language, "outlets": 0}
        print(f"=== queries for {args.country or '(no country)'} / "
              f"{args.language or '(no language)'} ===")
        for q in queries(gap):
            print(f"  {q}")
        print("\n  NOTHING WAS ASKED. `discover` has no default search callable, so this module")
        print("  cannot reach the network. Supplying one is the ToS review's attachment point.")
        return 0

    if args.gaps:
        import store as store_mod
        st = store_mod.Store(args.db)
        found = gaps(corpus_gap_counts(st), floor=args.floor)
        print(f"=== coverage gaps (fewer than {args.floor} outlets) ===")
        for g in found[:40]:
            print(f"  {g['country'] or '--':<3} {g['language'] or '--':<3}  {g['outlets']:>5} outlets")
        print(f"\n  {len(found)} gap(s). Read-only: no request was made and nothing was written.")
        return 0

    if args.search:
        warning = search_config_warning()
        if warning:
            print(f"SEARCH IS MISCONFIGURED: {warning}")
            return 2
        search = search_adapter()
        if search is None:
            print("No search provider configured. Set RWE_WEB_SEARCH_PROVIDER "
                  f"({', '.join(sorted(SEARCH_PROVIDERS))}) and RWE_WEB_SEARCH_API_KEY, or give "
                  "RWE_WEB_SEARCH_ENDPOINT directly. Nothing was asked.")
            return 2
        gap = {"country": args.country, "language": args.language, "outlets": 0}
        qs = queries(gap)[:max(1, args.per_gap)]
        print(f"=== one search, provider {getattr(search, 'provider', '?')} ===")
        results = []
        for q in qs:
            print(f"  query: {q}")
            try:
                r = search(q)
            except Exception as exc:
                print(f"    FAILED: {type(exc).__name__}: {exc}")
                continue
            print(f"    {len(r)} result(s)")
            results.extend(r)
        recs = hosts_from_results(results, gap=gap)
        print(f"\n  {len(results)} raw result(s) -> {len(recs)} candidate host(s) after "
              f"dedup, platform and aggregator filtering:")
        for rec in recs[:25]:
            print(f"    {rec['host']:<40} {(rec['publishers'][0] if rec['publishers'] else '')[:34]}")
        print("\n  NOTHING WAS WRITTEN and no publisher was contacted — these were search requests")
        print("  only. Seed them with:  source_campaign.py seed --channel web --search")
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
