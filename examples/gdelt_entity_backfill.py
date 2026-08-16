"""gdelt_entity_backfill.py — one-shot ENTITY backfill from GDELT GKG history (X5, rung 2).

The steady-state enricher covers new articles going forward (``RWE_GDELT_ENTITIES=1``); this CLI
covers the articles already in the clustering window, so the X5 separability measurement
(``audit_entity_separability.py``) has data to measure the day it runs instead of six days later.

    python examples/gdelt_entity_backfill.py --hours 48          # last 48h of GKG files

**Production-data neutrality, stated as a contract:** this tool writes ONLY the
``article_entities`` side table — which nothing in the serving path reads — and never touches
event locations or images. A deep location backfill would change geoCoherence, cluster trust and
blindspot withholding mid-experiment, which is a decision this CLI must not make on the side.

**Request-rate honesty:** one GKG file per 15 minutes means ``--hours 48`` is 193 sequential
downloads of multi-megabyte zips in one burst. That is the same shape as the enricher's one-time
cold-start backfill and nothing like the sustained 97-requests-per-15-minutes misconfiguration
that once rate-limited the DOC adapter (see ``sources._warn_if_window_cost_is_high``) — but it
is still a real transfer (~1–2 GB), so the depth is an explicit argument with a modest default,
not a hidden constant. A missing or corrupt window is counted and skipped, never fatal — GDELT
has gaps.

Idempotent: ``replace_article_entities`` is per-source replace, so re-running the backfill (or
overlapping it with steady-state cycles) converges instead of duplicating.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gdelt_gkg     # noqa: E402 — the parsers, candidates and manifest logic live there
import store as store_mod  # noqa: E402

_UA = {"User-Agent": "InformationHealth/1.0 (+https://hidden-view.com)"}


def _fetch_bytes(url: str, *, timeout: float = 30.0, retries: int = 2) -> bytes:
    """Small self-contained fetcher (urllib + linear retry) so the CLI does not import the whole
    sources chassis for two hosts' worth of GETs."""
    last: Exception = RuntimeError("unreachable")
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:           # noqa: BLE001 — any transport failure retries alike
            last = exc
            time.sleep(1.0 * (attempt + 1))
    raise last


def backfill(store_, *, fetch_bytes, windows: int, max_bytes: int | None = None,
             progress=None) -> dict:
    """Fetch ``windows`` 15-minute GKG files (newest first), match records to the catalog, and
    persist entities. Pure function of its inputs apart from the store writes; the CLI wraps it.

    Newest window wins a duplicate URL (``setdefault``, same discipline as the enricher), and the
    match set is resolved in ONE batched ``existing_feed_urls`` call at the end so the store sees
    the dedup key exactly once per candidate."""
    limit = max_bytes if max_bytes is not None else gdelt_gkg._max_bytes()
    manifest = fetch_bytes(gdelt_gkg.LASTUPDATE_URL).decode("utf-8", errors="replace")
    latest = gdelt_gkg.parse_lastupdate(manifest)
    if not latest:
        return {"windows": 0, "windowErrors": 0, "records": 0, "matched": 0,
                "articlesWritten": 0, "rowsWritten": 0, "skipped": "no gkg file in manifest"}
    ents_by_canonical: dict = {}
    processed = errors = records_total = 0
    urls = gdelt_gkg.window_urls(latest, windows)
    for n, gkg_url in enumerate(urls, 1):
        try:
            blob = fetch_bytes(gkg_url)
            if len(blob) > limit:
                errors += 1
                continue
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                name = z.namelist()[0]
                with z.open(name) as member:
                    records = gdelt_gkg.parse_gkg_entity_lines(
                        io.TextIOWrapper(member, encoding="utf-8", errors="replace"))
        except Exception:                   # one bad window is a count, not a crash
            errors += 1
            continue
        processed += 1
        records_total += len(records)
        for url, ents in records:           # newest first → setdefault keeps the newest
            for cand in gdelt_gkg._candidates(url):
                ents_by_canonical.setdefault(cand, ents)
        if progress and n % 25 == 0:
            progress(f"  {n}/{len(urls)} windows, {records_total:,} entity records, "
                     f"{len(ents_by_canonical):,} distinct URLs so far")

    known = store_.existing_feed_urls(list(ents_by_canonical))
    articles = rows = 0
    for canonical in sorted(known):
        written = store_.replace_article_entities(canonical, ents_by_canonical[canonical])
        if written:
            articles += 1
            rows += written
    return {"windows": processed, "windowErrors": errors, "records": records_total,
            "matched": len(known), "articlesWritten": articles, "rowsWritten": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hours", type=float, default=48.0,
                    help="GKG history depth (4 files per hour; default 48h = 193 downloads)")
    ap.add_argument("--windows", type=int, default=None,
                    help="exact 15-minute window count (overrides --hours)")
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)

    windows = args.windows if args.windows is not None else max(1, int(args.hours * 4))
    st = store_mod.Store(args.db)
    before = st.count_article_entities()
    print(f"backfilling entities from {windows} GKG windows "
          f"(~{windows / 4:.0f}h; entity rows before: {before:,})")
    stats = backfill(st, fetch_bytes=_fetch_bytes, windows=windows, progress=print)
    after = st.count_article_entities()
    print(f"windows ok/err     : {stats['windows']}/{stats['windowErrors']}")
    print(f"entity records     : {stats['records']:,}")
    print(f"catalog matches    : {stats['matched']:,}")
    print(f"articles written   : {stats['articlesWritten']:,} ({stats['rowsWritten']:,} rows)")
    print(f"entity rows        : {before:,} -> {after:,}")
    print("locations/images   : untouched by design (production-data neutrality)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
