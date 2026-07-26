"""GDELT GKG event-geography enricher — the Phase-2 SUPPLY rung (Location Intelligence).

The DOC artlist we ingest carries only ``sourcecountry`` (publisher-level). Event geography
lives in GDELT's Global Knowledge Graph: every 15 minutes a ``*.gkg.csv.zip`` file whose
records carry ``V1Locations`` — the places GDELT's own extraction found in each monitored
article. This module downloads the latest file, matches records to articles ALREADY in our
catalog (any provider: an RSS-ingested BBC article GDELT also monitors gets located too), and
persists normalized event countries with ``"gdelt-gkg"`` provenance through the same resolver +
side table as every other location fact. Enrichment only — it never creates articles.

Provider-specific mapping stays HERE (the adapter layer), per the platform contract:

* **The FIPS trap.** ``V1Locations`` country codes are FIPS 10-4, NOT ISO — FIPS ``AS`` is
  Australia while ISO ``AS`` is American Samoa; FIPS ``GM`` is Germany while ISO ``GM`` is
  Gambia. Feeding those codes to the resolver would mis-locate silently, so we never do:
  instead each block's ``FullName`` ("Sydney, New South Wales, Australia" / "Germany") ends
  with the country NAME, which ``location.normalize_country`` already understands. An unknown
  name is dropped (fail-honest), never mis-mapped.
* **Salience.** A GKG record lists EVERY place an article mentions. We keep only the article's
  DOMINANT country(-ies) — the location-block-count winner, ties kept — so one stray mention
  never locates an article. Story-level member consensus narrows further.

Default OFF (``RWE_GDELT_GKG``); country-level only by design (Phase-2 v1); the poller wiring
lives in :mod:`sources` (``GDELTGKGEnricher``), the logic here so it stays offline-testable.
"""
from __future__ import annotations

import io
import os
import sys
import zipfile
from collections import Counter
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ingest    # noqa: E402  — canonical_url: the SAME dedup key the catalog uses
import location  # noqa: E402

LASTUPDATE_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"

#: GKG 2.1 CSV columns we read (tab-delimited, no header).
_COL_COLLECTION = 2        # V2SOURCECOLLECTIONIDENTIFIER — 1 = WEB (URL DocumentIdentifier)
_COL_DOCUMENT = 4          # V2DOCUMENTIDENTIFIER — the article URL for WEB records
_COL_V1LOCATIONS = 9       # V1LOCATIONS — Type#FullName#FIPS#ADM1#Lat#Long#FeatureID; ';'-joined


def parse_lastupdate(manifest: str) -> Optional[str]:
    """The ``.gkg.csv.zip`` URL out of GDELT's 3-line ``lastupdate.txt`` (size, hash, url per
    line). ``None`` when the manifest carries no GKG entry — the caller skips the cycle."""
    for line in (manifest or "").splitlines():
        parts = line.split()
        if parts and parts[-1].endswith(".gkg.csv.zip"):
            return parts[-1]
    return None


def _dominant_places(v1locations: str) -> list:
    """One GKG record's V1LOCATIONS field -> the dominant country place dict(s).

    Votes are counted per NORMALIZED country (block FullNames' trailing country name through
    ``location.normalize_country`` — never the FIPS code); the winner(s) by block count are
    returned, each carrying the first block's lat/lon and ``gdelt-gkg`` provenance."""
    votes: Counter = Counter()
    first_block: dict = {}
    for block in (v1locations or "").split(";"):
        fields = block.split("#")
        if len(fields) < 7:
            continue
        name = fields[1].split(",")[-1].strip()
        iso = location.normalize_country(name)
        if iso is None:
            continue                                     # unknown name: dropped, never mis-mapped
        votes[iso] += 1
        if iso not in first_block:
            try:
                lat = float(fields[4]) if fields[4] else None
                lon = float(fields[5]) if fields[5] else None
            except ValueError:
                lat = lon = None
            first_block[iso] = {"lat": lat, "lon": lon}
    if not votes:
        return []
    top = max(votes.values())
    return [{"country": iso, "lat": first_block[iso]["lat"], "lon": first_block[iso]["lon"],
             "source": "gdelt-gkg"}
            for iso, n in sorted(votes.items()) if n == top]


def parse_gkg_csv(text: str) -> list:
    """GKG CSV -> ``[(document_url, places), …]`` for WEB records with a resolvable dominant
    country. Malformed rows and non-WEB collections (citations etc.) are skipped."""
    out = []
    for line in (text or "").splitlines():
        cols = line.split("\t")
        if len(cols) <= _COL_V1LOCATIONS:
            continue
        if cols[_COL_COLLECTION].strip() != "1":
            continue
        url = cols[_COL_DOCUMENT].strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        places = _dominant_places(cols[_COL_V1LOCATIONS])
        if places:
            out.append((url, places))
    return out


def _candidates(url: str) -> list:
    """Canonical catalog keys a GKG URL may match: the shared ``ingest.canonical_url`` form plus
    its scheme-flipped twin (GKG sometimes records http where we ingested https, or vice
    versa — the canonicalizer deliberately keeps the scheme)."""
    canon = ingest.canonical_url(url)
    if canon.startswith("https://"):
        return [canon, "http://" + canon[len("https://"):]]
    if canon.startswith("http://"):
        return [canon, "https://" + canon[len("http://"):]]
    return [canon]


def enrich_from_latest(store_, *, fetch_bytes: Callable[[str], bytes],
                       max_bytes: Optional[int] = None) -> dict:
    """One enrichment cycle: latest GKG file -> parse -> match against the catalog -> persist.

    Returns counted facts for health: ``records`` parsed (with a dominant country), ``matched``
    catalog articles, ``located`` articles written this cycle. Never creates articles; writes go
    through ``replace_article_event_locations`` (per-source idempotent, so re-running a cycle is
    harmless and other providers' rows are never touched)."""
    gkg_url = parse_lastupdate(fetch_bytes(LASTUPDATE_URL).decode("utf-8", errors="replace"))
    if not gkg_url:
        return {"records": 0, "matched": 0, "located": 0, "skipped": "no gkg file in manifest"}
    blob = fetch_bytes(gkg_url)
    limit = max_bytes if max_bytes is not None else _max_bytes()
    if len(blob) > limit:
        return {"records": 0, "matched": 0, "located": 0,
                "skipped": f"gkg file {len(blob)}B exceeds cap {limit}B"}
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = z.namelist()[0]
        text = z.read(name).decode("utf-8", errors="replace")
    records = parse_gkg_csv(text)

    by_canonical: dict = {}
    for url, places in records:
        for cand in _candidates(url):
            by_canonical.setdefault(cand, places)
    known = store_.existing_feed_urls(list(by_canonical))
    located = 0
    for canonical in sorted(known):
        events = location.resolve_event_locations(by_canonical[canonical])
        if events:
            store_.replace_article_event_locations(canonical, events)
            located += 1
    return {"records": len(records), "matched": len(known), "located": located}


def _max_bytes() -> int:
    try:
        return int(os.environ.get("RWE_GDELT_GKG_MAX_BYTES", "") or 64 * 1024 * 1024)
    except ValueError:
        return 64 * 1024 * 1024
