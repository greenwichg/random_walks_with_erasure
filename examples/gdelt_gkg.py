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

Flagged by ``RWE_GDELT_GKG`` (bare code default: off; the production compose sets it ON —
``deploy/.env`` is the kill switch). Country-level only by design (Phase-2 v1); the poller
wiring lives in :mod:`sources` (``GDELTGKGEnricher``), the logic here so it stays
offline-testable; first-cycle verification is docs/AWS_EC2_DEPLOYMENT_GUIDE.md §6a.
"""
from __future__ import annotations

import io
import os
import re
import sys
import zipfile
from collections import Counter
from datetime import datetime, timedelta
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ingest    # noqa: E402  — canonical_url: the SAME dedup key the catalog uses
import location  # noqa: E402

LASTUPDATE_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"

#: GKG 2.1 CSV columns we read (tab-delimited, no header).
_COL_COLLECTION = 2        # V2SOURCECOLLECTIONIDENTIFIER — 1 = WEB (URL DocumentIdentifier)
_COL_DOCUMENT = 4          # V2DOCUMENTIDENTIFIER — the article URL for WEB records
_COL_V1LOCATIONS = 9       # V1LOCATIONS — Type#FullName#FIPS#ADM1#Lat#Long#FeatureID; ';'-joined
_COL_V1PERSONS = 11        # V1PERSONS — ';'-joined person names (X5, rung 2)
_COL_V1ORGS = 13           # V1ORGANIZATIONS — ';'-joined organization names (X5, rung 2)
_COL_SHARING_IMAGE = 18    # V2.1SHARINGIMAGE — the article's social/OG image URL GDELT extracted

#: Names kept per article per kind. A bound, not a preference: V1 lists are usually short, and
#: an outlier record listing hundreds of names is exactly the kind of page (a roster, a listing)
#: whose names are NOT what the article is about. First-N in order of appearance, like
#: ``clustering.description_tokens`` — deterministic, no corpus state.
ENTITY_CAP = 24


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
    """String façade over :func:`parse_gkg_lines` (fixtures/tests hand in small strings)."""
    return parse_gkg_lines((text or "").splitlines())


def parse_gkg_lines(lines) -> list:
    """GKG CSV lines -> ``[(document_url, places, sharing_image), …]`` for WEB records carrying a
    resolvable dominant country and/or a sharing image (V2.1SHARINGIMAGE — the social/OG image
    GDELT extracted; the thumbnail supply for articles whose feed carried no media). Malformed
    rows and non-WEB collections (citations etc.) are skipped. Takes any line iterable so the
    enricher can STREAM a decompressing zip member — a 15-minute GKG file inflates to hundreds
    of MB, which must never be materialized as one string."""
    out = []
    for line in lines or ():
        cols = line.rstrip("\r\n").split("\t")
        if len(cols) <= _COL_V1LOCATIONS:
            continue
        if cols[_COL_COLLECTION].strip() != "1":
            continue
        url = cols[_COL_DOCUMENT].strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        places = _dominant_places(cols[_COL_V1LOCATIONS])
        image = cols[_COL_SHARING_IMAGE].strip() if len(cols) > _COL_SHARING_IMAGE else ""
        if not image.lower().startswith(("http://", "https://")):
            image = ""
        if places or image:
            out.append((url, places, image or None))
    return out


def _split_entities(field: str, cap: int = ENTITY_CAP) -> list:
    """One V1PERSONS / V1ORGANIZATIONS field -> normalized distinct names, order preserved.

    Lower-cased and whitespace-collapsed so identity comparisons need no re-normalization
    downstream; names shorter than three characters are dropped as parse noise. Dedup happens
    BEFORE the cap, same argument as ``description_tokens``: repetition is not evidence, and a
    repetitive record must not get a smaller signal."""
    seen: list = []
    for raw in (field or "").split(";"):
        name = " ".join(raw.strip().lower().split())
        if len(name) >= 3 and name not in seen:
            seen.append(name)
            if len(seen) >= cap:
                break
    return seen


def parse_gkg_entity_lines(lines) -> list:
    """GKG CSV lines -> ``[(document_url, {"person": [...], "org": [...]}), …]`` for WEB records
    carrying at least one name (X5, rung 2). A SEPARATE streaming pass rather than a change to
    :func:`parse_gkg_lines`, so the location/image path — and every consumer of its record shape
    — stays byte-identical; the zip member is simply opened twice when both are wanted. Same
    URL and collection discipline as the main parser."""
    out = []
    for line in lines or ():
        cols = line.rstrip("\r\n").split("\t")
        if len(cols) <= _COL_V1ORGS:
            continue
        if cols[_COL_COLLECTION].strip() != "1":
            continue
        url = cols[_COL_DOCUMENT].strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        persons = _split_entities(cols[_COL_V1PERSONS])
        orgs = _split_entities(cols[_COL_V1ORGS])
        if persons or orgs:
            out.append((url, {"person": persons, "org": orgs}))
    return out


def entities_enabled() -> bool:
    """Whether the steady-state enrichment cycle ALSO persists entities — **ON in production**
    (X5b adoption, 2026-08-16: the merge pass consumes ``article_entities`` every build, so the
    table must stay current or the recall silently ages out with the backfill;
    ``deploy/docker-compose.yml`` defaults ``RWE_GDELT_ENTITIES=1``, and ``0`` is the kill
    switch). UNSET falls back to off, where the entity pass over the zip member is skipped
    entirely and costs nothing."""
    return (os.environ.get("RWE_GDELT_ENTITIES", "") or "").strip().lower() in {"1", "true", "yes", "on"}


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


def window_urls(latest_url: str, windows: int) -> list:
    """The GKG file URLs for the latest window plus the ``windows - 1`` before it (GDELT
    publishes one file per 15 minutes with the timestamp in the name), newest first. The
    lookback exists because articles enter OUR catalog minutes-to-hours after GDELT processed
    them into a (then-current) GKG file — the latest file alone would almost never overlap the
    catalog, and ``matched`` would sit near zero forever."""
    m = re.search(r"(\d{14})\.gkg\.csv\.zip$", latest_url)
    if not m:
        return [latest_url]
    stamp = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    prefix = latest_url[: m.start(1)]
    return [f"{prefix}{(stamp - timedelta(minutes=15 * k)).strftime('%Y%m%d%H%M%S')}.gkg.csv.zip"
            for k in range(max(1, windows))]


def enrich_from_latest(store_, *, fetch_bytes: Callable[[str], bytes],
                       max_bytes: Optional[int] = None, windows: Optional[int] = None,
                       allow_backfill: bool = True) -> dict:
    """One enrichment cycle: the last N GKG windows -> parse -> match the catalog -> persist.

    Returns counted facts for health: ``windows`` processed (+ ``windowErrors`` skipped —
    GDELT occasionally has gaps, and one missing file must not fail the cycle), ``records``
    parsed with a dominant country, ``matched`` catalog articles, ``located`` articles written.
    Never creates articles; writes go through ``replace_article_event_locations`` (per-source
    idempotent, so overlapping lookbacks between cycles are harmless). The newest window wins
    when the same URL appears in several. First-enable backfill: run one cycle with
    ``RWE_GDELT_GKG_WINDOWS=96`` (24 h) — see docs/AWS_EC2_DEPLOYMENT_GUIDE.md §6a."""
    latest = parse_lastupdate(fetch_bytes(LASTUPDATE_URL).decode("utf-8", errors="replace"))
    if not latest:
        return {"windows": 0, "windowErrors": 0, "records": 0, "matched": 0, "located": 0,
                "skipped": "no gkg file in manifest"}
    limit = max_bytes if max_bytes is not None else _max_bytes()
    # Cold-start auto-backfill: a BARELY-located catalog (fewer event rows than the threshold —
    # not just zero: a few steady-state cycles may already have trickled rows in before the
    # first deep pass, the production lesson) means the bulk of the catalog was processed by
    # GDELT hours-to-days ago, beyond the steady-state lookback. One deep cycle covers it
    # automatically; no manual env override, no restart pair, no revert to forget. An empty
    # catalog skips it (nothing to locate), and the ADAPTER passes allow_backfill only on its
    # first cycle per process — so a catalog that legitimately never crosses the threshold
    # (low GDELT overlap) deep-scans at most once per container start, never every 15 minutes.
    backfill = False
    if windows is None:
        windows = _windows()
        if (allow_backfill and _backfill_windows() > windows
                and store_.count_event_locations() < _backfill_threshold()
                and store_.count_feed_articles() > 0):
            windows, backfill = _backfill_windows(), True
    by_canonical: dict = {}
    ents_by_canonical: dict = {}
    want_entities = entities_enabled()
    processed = errors = records_total = 0
    for gkg_url in window_urls(latest, windows):
        try:
            blob = fetch_bytes(gkg_url)
            if len(blob) > limit:
                errors += 1
                continue
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                name = z.namelist()[0]
                # Stream-decode the member: peak memory stays at the compressed blob + one line,
                # not the whole inflated file — what makes default-on safe on a small instance.
                with z.open(name) as member:
                    records = parse_gkg_lines(
                        io.TextIOWrapper(member, encoding="utf-8", errors="replace"))
                ent_records = []
                if want_entities:
                    # Second streaming pass over the SAME downloaded blob (X5 opt-in) — one
                    # extra decompression, zero extra HTTP, and the location/image record shape
                    # above stays byte-identical for its consumers.
                    with z.open(name) as member:
                        ent_records = parse_gkg_entity_lines(
                            io.TextIOWrapper(member, encoding="utf-8", errors="replace"))
        except Exception:                       # one missing/corrupt window never fails the cycle
            errors += 1
            continue
        processed += 1
        records_total += len(records)
        for url, places, image in records:     # newest window first → setdefault keeps it
            for cand in _candidates(url):
                by_canonical.setdefault(cand, (places, image))
        for url, ents in ent_records:
            for cand in _candidates(url):
                ents_by_canonical.setdefault(cand, ents)

    known = store_.existing_feed_urls(list(by_canonical))
    located = images = 0
    for canonical in sorted(known):
        places, image = by_canonical[canonical]
        if places:
            events = location.resolve_event_locations(places)
            if events:
                store_.replace_article_event_locations(canonical, events)
                located += 1
        # Thumbnail supply: GDELT's extracted sharing image fills articles whose feed carried no
        # media — backfill-when-empty only (a feed-provided image is never overwritten), so the
        # story hero picker finally has a candidate for feeds that ship no media tags.
        if image and store_.backfill_article_image(canonical, image, source="gdelt-gkg"):
            images += 1
    out = {"windows": processed, "windowErrors": errors, "records": records_total,
           "matched": len(known), "located": located, "images": images}
    if want_entities:
        ent_known = store_.existing_feed_urls(list(ents_by_canonical))
        entity_articles = 0
        for canonical in sorted(ent_known):
            if store_.replace_article_entities(canonical, ents_by_canonical[canonical]):
                entity_articles += 1
        out["entities"] = entity_articles
    if backfill:
        out["backfill"] = True          # visible in health/CLI: this was the cold-start deep cycle
    return out


DEFAULT_WINDOWS = 4


def _windows() -> int:
    """Steady-state lookback per cycle (15-minute GKG windows). Default 4 = one hour: with the
    DOC artlist polled every ≤30 minutes, every GDELT-ingested article's GKG window is covered
    by the next enrichment cycle.

    **This is a per-cycle HTTP download count.** Each window is one multi-megabyte zip, so raising
    it multiplies our request rate against GDELT — the cold-start deep scan belongs to
    ``_backfill_windows`` (automatic, once), not here. See :func:`windows_per_cycle`."""
    try:
        return max(1, int(os.environ.get("RWE_GDELT_GKG_WINDOWS", "") or DEFAULT_WINDOWS))
    except ValueError:
        return DEFAULT_WINDOWS


def windows_per_cycle() -> int:
    """The steady-state lookback this process will use — public so the poller can warn when it has
    been left at a backfill-sized value. Production once ran with 96 permanently: 97 requests every
    15 minutes, ~9,300/day, against a GDELT API that then rate-limited the DOC adapter into a 60%
    success rate. The one-time backfill it was copied from is now automatic."""
    return _windows()


def _backfill_windows() -> int:
    """Cold-start depth (default 96 = 24 h), used automatically for the FIRST cycle over a
    barely-located catalog. 0 disables auto-backfill (manual control via RWE_GDELT_GKG_WINDOWS)."""
    try:
        return max(0, int(os.environ.get("RWE_GDELT_GKG_BACKFILL_WINDOWS", "") or 96))
    except ValueError:
        return 96


def _backfill_threshold() -> int:
    """"Barely located": fewer stored event rows than this (default 25) still counts as a cold
    start — steady-state cycles may have trickled a handful in before the first deep pass."""
    try:
        return max(1, int(os.environ.get("RWE_GDELT_GKG_BACKFILL_THRESHOLD", "") or 25))
    except ValueError:
        return 25


def _max_bytes() -> int:
    try:
        return int(os.environ.get("RWE_GDELT_GKG_MAX_BYTES", "") or 64 * 1024 * 1024)
    except ValueError:
        return 64 * 1024 * 1024
