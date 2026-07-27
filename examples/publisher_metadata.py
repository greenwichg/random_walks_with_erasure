"""publisher_metadata.py — merge curated + enriched publisher facts, with per-field provenance.

The Publisher page draws on three kinds of fact and they are NOT interchangeable, so this module
keeps them apart and labels every field with where it came from:

    curated     :mod:`outlet_registry` — hand-verified identity and locality. Authoritative.
    counted     measured from our own catalog (the host we actually see them publish from).
    wikipedia   the Wikipedia article — prose description, lead image.
    wikimedia   Wikidata claims + Commons — inception, HQ, country, website, parent, logo file.

**Curated data is never overwritten.** The brief allowed overwriting "when the new data is more
complete", and for a scalar field that comparison is not meaningful — "US" is not more complete
than "US", and a Wikidata value is not more trustworthy than a value a human checked. So the rule
implemented here is the strict, defensible reading: enrichment FILLS GAPS and nothing else. Filling
an empty field is unambiguously more complete; replacing a curated one never is. The only sense in
which enriched data displaces anything is that it supplies fields the registry has no column for
(founded, parent, description), where there is nothing to overwrite.

Provenance is per FIELD rather than per record because a merged profile genuinely has mixed
sourcing — a curated country beside a Wikidata founding year — and one record-level "source:
wikipedia" label would misdescribe the curated half.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import publisher_wiki

#: Enriched fields, in the order the page presents them.
FIELDS = ("description", "founded", "headquarters", "country", "website", "parent")

#: How long a row stays fresh, by status. A verified match rarely changes, so re-asking weekly is
#: already generous; a miss is retried far less often (the answer is usually still "no"); an error
#: is retried soon because it says nothing about the outlet, only about the minute it happened in.
TTL_DAYS = {"ok": 30.0, "no_match": 30.0, "ambiguous": 14.0, "error": 0.25}
DEFAULT_TTL_DAYS = 30.0

#: Publishers enriched per poll cycle. Each costs 2-4 requests, so 5 is ~20 requests per cycle
#: against Wikimedia's APIs — a rounding error against their capacity, and it means a 400-publisher
#: catalog reaches full coverage in a few hours without ever looking like a scraper.
DEFAULT_BATCH = 5

#: Statuses that carry usable facts. Anything else is a recorded absence.
USABLE = ("ok",)


def _clean(v) -> Optional[str]:
    s = str(v).strip() if v is not None else ""
    return s or None


def curated_facts(outlet, *, site=None) -> dict:
    """What we already know without asking anyone: registry curation plus one counted fact.

    ``headquarters`` is composed from the registry's curated city/region because that IS the
    outlet's home — the registry simply stores it in two columns. ``website`` is the catalog's
    majority host: counted, not curated, and labelled as such."""
    out: dict = {}
    if outlet is not None:
        if getattr(outlet, "country", None):
            out["country"] = ("curated", outlet.country)
        city, region = getattr(outlet, "city", None), getattr(outlet, "region", None)
        place = ", ".join(p for p in (_clean(city), _clean(region)) if p)
        if place:
            out["headquarters"] = ("curated", place)
    if _clean(site):
        out["website"] = ("counted", _clean(site))
    return out


def _enriched_facts(cached: dict) -> dict:
    """Facts from a cached lookup row, each tagged with the provider that actually produced it.
    Only ``description`` comes from the article; the structured claims come from Wikidata."""
    if not cached or cached.get("status") not in USABLE:
        return {}
    out: dict = {}
    if _clean(cached.get("description")):
        out["description"] = ("wikipedia", _clean(cached["description"]))
    for field in ("founded", "headquarters", "country", "website", "parent"):
        if _clean(cached.get(field)):
            out[field] = ("wikimedia", _clean(cached[field]))
    return out


def merge(outlet, cached: "dict | None", *, site=None) -> dict:
    """The merged About block, or ``{}`` when there is nothing to show.

    Shape: the fields themselves, plus ``sources`` (field -> provenance), ``wikipediaUrl``,
    ``status`` and ``refreshedAt``. None-valued fields are omitted entirely rather than serialized
    as null, matching the profile's existing wire contract."""
    curated = curated_facts(outlet, site=site)
    enriched = _enriched_facts(cached or {})

    values: dict = {}
    sources: dict = {}
    for field in FIELDS:
        # Curated (or counted) first: enrichment only ever reaches a field nobody else filled.
        source, value = curated.get(field) or enriched.get(field) or (None, None)
        if value:
            values[field] = value
            sources[field] = source

    out = dict(values)
    if sources:
        out["sources"] = sources
    if cached:
        if _clean(cached.get("wikipediaUrl")) and cached.get("status") in USABLE:
            out["wikipediaUrl"] = cached["wikipediaUrl"]
        # Status and refresh time are reported even for a miss: "we looked and found nothing" is a
        # different, more useful statement than silence, and it is what makes a stale cache visible.
        out["status"] = cached.get("status")
        out["refreshedAt"] = cached.get("fetchedAt")
    return out


def logo_from_cache(cached: "dict | None") -> "tuple[str, str] | None":
    """``(url, source)`` for an enriched logo, or None. Source is ``wikimedia`` for a Commons logo
    file named by a Wikidata claim, ``wikipedia`` for the article's own lead image."""
    if not cached or cached.get("status") not in USABLE:
        return None
    url = _clean(cached.get("logo"))
    if not url:
        return None
    return url, (_clean(cached.get("logoSource")) or "wikimedia")


def should_replace(cached: "dict | None", new: dict) -> bool:
    """Whether a fresh lookup result should overwrite the cached row.

    A successful lookup always replaces — Wikidata is the source of truth for its own claims, and a
    dropped claim must propagate or the page keeps asserting a fact its source no longer does.

    A FAILED lookup must not replace a successful one. Wikipedia is edited live: an article can be
    briefly redirected to a disambiguation page, moved mid-rename, or 503 for a minute, and none of
    those are reasons to throw away facts that were verified an hour ago. The row keeps its data
    and gets retried on the next cycle. This is the only asymmetry in the cache, and without it a
    single bad minute upstream silently empties publisher pages."""
    if new.get("status") in USABLE:
        return True
    return not cached or cached.get("status") not in USABLE


# --------------------------------------------------------------------------- #
# Enrichment — the background pass that fills the cache.
# --------------------------------------------------------------------------- #
def _env_float(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def enabled() -> bool:
    """Off in code, ON in production via ``RWE_PUBLISHER_WIKI=1`` in deploy/docker-compose.yml —
    the same convention the GKG enricher follows.

    The default is not a judgement about the feature; it is that a module which reaches a third-party
    API must never do so merely because it was imported. Anything that runs it — a test, a script, a
    developer's local poller — has to say so first."""
    return os.environ.get("RWE_PUBLISHER_WIKI", "").strip().lower() in {"1", "true", "yes", "on"}


def batch_size() -> int:
    return _env_int("RWE_PUBLISHER_WIKI_BATCH", DEFAULT_BATCH)


def ttl_days(status: "str | None") -> float:
    """Freshness window for a status, overridable per status via
    ``RWE_PUBLISHER_WIKI_TTL_<STATUS>``."""
    default = TTL_DAYS.get(str(status or ""), DEFAULT_TTL_DAYS)
    return _env_float(f"RWE_PUBLISHER_WIKI_TTL_{str(status or 'OK').upper()}", default)


def _parse(ts) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_stale(row: "dict | None", *, now: "datetime | None" = None) -> bool:
    """Whether a cached row is due for another look. A row with no timestamp is stale (it predates
    the column, or was written by something that did not set it) — re-fetching is cheap and being
    wrong in this direction only costs one request."""
    if not row:
        return True
    fetched = _parse(row.get("fetchedAt"))
    if fetched is None:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - fetched) >= timedelta(days=ttl_days(row.get("status")))


def pending(store_, *, limit: int, now: "datetime | None" = None) -> list:
    """The next publishers due for enrichment, busiest first.

    Idempotence lives here: a publisher whose row is still fresh is simply not returned, so
    re-running the pass — on a schedule, by hand, twice in a row — does no work and makes no
    requests. That is what makes the refresh safe to rerun rather than merely harmless."""
    candidates = store_.catalog_publishers()
    cached = store_.publisher_metadata_many([c["publisher"] for c in candidates])
    out = []
    for c in candidates:
        row = cached.get(store_.publisher_key(c["publisher"]))
        if is_stale(row, now=now):
            out.append(c)
        if len(out) >= limit:
            break
    return out


def observed_host(store_, publisher: str) -> Optional[str]:
    """The host this publisher actually publishes from, counted from the catalog — the independent
    evidence :func:`publisher_wiki.verify` checks a candidate against."""
    try:
        stats = store_.publisher_catalog_stats(publisher) or {}
    except Exception:
        return None
    hosts = stats.get("hosts") or []
    return hosts[0]["label"] if hosts else None


def enrich_publisher(store_, publisher: str, *, fetch_json: Callable[[str], dict]) -> dict:
    """Look one publisher up and cache the result. Returns the written (or preserved) row.

    A transport failure is recorded as ``error`` rather than raised: one unreachable outlet must not
    abort a batch, and the status is what schedules the retry."""
    try:
        result = publisher_wiki.lookup(publisher, fetch_json,
                                       observed_host=observed_host(store_, publisher))
    except Exception as e:
        result = {"status": "error", "error": f"{type(e).__name__}: {e}"}

    cached = store_.publisher_metadata(publisher)
    if not should_replace(cached, result):
        return cached
    # lookup() speaks the wire vocabulary for identity (camelCase); the store speaks column names.
    fields = {
        "wikidata_id": result.get("wikidataId"),
        "wikipedia_title": result.get("wikipediaTitle"),
        "wikipedia_url": result.get("wikipediaUrl"),
        **{k: result.get(k) for k in ("description", "founded", "headquarters", "country",
                                      "website", "parent", "logo", "logo_source", "error")},
    }
    return store_.upsert_publisher_metadata(
        publisher, status=result.get("status", "error"), source=result.get("source"), **fields)


def run_enrichment(store_, *, fetch_json: Callable[[str], dict], limit: "int | None" = None,
                   log: "Callable[..., None] | None" = None,
                   now: "datetime | None" = None) -> dict:
    """One bounded enrichment pass. Returns counts by status plus how many were considered.

    Fail-soft by construction, like every other poller side-job: a provider outage produces a batch
    of ``error`` rows and a log line, never an exception into the poll loop."""
    limit = batch_size() if limit is None else limit
    due = pending(store_, limit=limit, now=now)
    counts: dict = {}
    t0 = time.perf_counter()
    for candidate in due:
        row = enrich_publisher(store_, candidate["publisher"], fetch_json=fetch_json)
        status = (row or {}).get("status", "error")
        counts[status] = counts.get(status, 0) + 1
    summary = {"considered": len(due), "byStatus": counts,
               "durationMs": round((time.perf_counter() - t0) * 1000.0, 1)}
    if log is not None and due:
        log(logging.INFO, "publisher_enrichment", **summary)
    return summary
