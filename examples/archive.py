"""archive.py — append-only, versioned JSONL partitions of what the hot database is about to forget.

The hot database is bounded on purpose (``RWE_RETENTION_MAX_COUNT``, per-tier ages, the story
window) and it must stay that way — every capacity document says so. But a product that sells
history cannot lose it, and today the only copy of an article older than the cap is a database
backup nobody can query. This module writes what retention is about to delete — and, on demand,
the story history and the publisher table — as gzipped JSON lines under a schema-versioned layout:

    <root>/v1/<kind>/dt=YYYY-MM-DD/<timestamp>-<hex>.jsonl.gz
    <root>/v1/<kind>/dt=YYYY-MM-DD/<timestamp>-<hex>.manifest.json    rows · bytes · sha256 · versions

``<root>`` is ``RWE_ARCHIVE_DIR`` (default: ``archive/`` beside the SQLite file, i.e. on the data
volume, which the existing off-host sync ships to S3 under ``archive/`` — outside the backup
lifecycle prefix, so it is never tiered away or expired). Article rows never carry ``body``: the
archive is what a licensing export reads from, and full text is not ours to license.

The retention hook (``corpus_health.run_retention``) runs archive-BEFORE-delete when
``RWE_ARCHIVE_ON_PRUNE=1`` and FAILS CLOSED: if the archive write raises, nothing is deleted this
pass and the reason is logged. Keeping too much is the safe failure.

Design: docs/NEWS_INTELLIGENCE_INFRASTRUCTURE.md §C.2 / §E.5.
"""

from __future__ import annotations

import glob
import gzip
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import store as _store

SCHEMA = "v1"


class ArchiveUnavailable(RuntimeError):
    """No archive location is configured or reachable — the caller must not delete."""


def enabled_on_prune() -> bool:
    """Whether retention archives rows before deleting them. Default OFF."""
    return os.environ.get("RWE_ARCHIVE_ON_PRUNE", "").strip().lower() in {"1", "true", "yes", "on"}


def root_for(store_=None) -> Optional[str]:
    """The archive root: ``RWE_ARCHIVE_DIR``, else ``archive/`` beside a file-backed database.
    ``None`` for an in-memory store with nothing configured."""
    env = os.environ.get("RWE_ARCHIVE_DIR", "").strip()
    if env:
        return env
    if store_ is None:
        return None
    path = _store.sqlite_path(getattr(store_, "url", "") or "")
    return os.path.join(os.path.dirname(os.path.abspath(path)), "archive") if path else None


def _day(ts: "str | None" = None) -> str:
    return (ts or datetime.now(timezone.utc).isoformat())[:10]


def default_versions() -> dict:
    """The algorithm versions in force, for the manifest. Lazy imports keep this module light."""
    out: dict = {"archive": SCHEMA}
    try:
        import ingest
        out["scorer"] = ingest.SCORER_VERSION
    except Exception:
        pass
    try:
        import identity
        out["registry"] = identity.registry_version()
    except Exception:
        pass
    try:
        import story_service
        out["build"] = story_service.BUILD_VERSION
        out["buildConfig"] = story_service.build_config_hash()
    except Exception:
        pass
    return out


def write_partition(root: str, kind: str, rows: list, *, day: "str | None" = None,
                    versions: "dict | None" = None) -> dict:
    """Write ``rows`` as one gzipped JSONL part + its manifest. Atomic per file (tmp + rename);
    a partition is never half-visible. Returns the manifest (``rows == 0`` writes nothing)."""
    if not rows:
        return {"schema": SCHEMA, "kind": kind, "rows": 0, "file": None}
    day = day or _day()
    d = os.path.join(root, SCHEMA, kind, f"dt={day}")
    os.makedirs(d, exist_ok=True)
    stem = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    path = os.path.join(d, stem + ".jsonl.gz")
    tmp = path + ".tmp"
    n = 0
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True, default=str) + "\n")
            n += 1
    os.replace(tmp, path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    manifest = {"schema": SCHEMA, "kind": kind, "day": day, "rows": n,
                "bytes": os.path.getsize(path), "sha256": h.hexdigest(),
                "versions": versions or {}, "writtenAt": datetime.now(timezone.utc).isoformat(),
                "file": os.path.basename(path)}
    mpath = os.path.join(d, stem + ".manifest.json")
    with open(mpath + ".tmp", "w", encoding="utf-8") as f:
        json.dump(manifest, f, sort_keys=True, indent=1)
    os.replace(mpath + ".tmp", mpath)
    return manifest


def article_rows(store_, canonical_urls) -> list:
    """Catalogue rows in archive shape: the row minus ``body``, plus provenance, entities and
    event countries. Batched lookups, chunked by the store."""
    urls = [u for u in dict.fromkeys(canonical_urls) if u]
    if not urls:
        return []
    # Accept any URL form the alias table knows (retention passes canonical URLs; an operator
    # may pass what a feed listed): resolve through the aliases, fall back to the input.
    try:
        known = store_.article_meta_for_urls(urls)
        urls = list(dict.fromkeys(known[u]["canonicalUrl"] if u in known else u for u in urls))
    except Exception:                       # noqa: BLE001 — identity is additive
        pass
    rows = store_.feed_articles_by_urls(urls)
    prov = store_.provenance_for_urls(urls)
    try:
        ents = store_.entities_for_urls(urls, kinds=_store.ENTITY_KINDS)
    except Exception:
        ents = {}
    try:
        countries = store_.event_countries_for_urls(urls)
    except Exception:
        countries = {}
    out = []
    for r in rows:
        r = dict(r)
        r.pop("body", None)
        u = r.get("canonicalUrl")
        r["provenance"] = prov.get(u, [])
        r["entities"] = ents.get(u, {})
        r["eventCountries"] = sorted(countries.get(u, ()) or ())
        out.append(r)
    return out


def archive_articles(store_, canonical_urls, *, root: "str | None" = None,
                     versions: "dict | None" = None) -> dict:
    """Archive the named catalogue rows. Raises :class:`ArchiveUnavailable` when there is
    nowhere to write — the caller must then keep the rows."""
    root = root or root_for(store_)
    if not root:
        raise ArchiveUnavailable("no archive location: set RWE_ARCHIVE_DIR")
    rows = article_rows(store_, canonical_urls)
    return write_partition(root, "articles", rows, versions=versions or default_versions())


def archive_story_history(store_, history: dict, *, root: "str | None" = None,
                          versions: "dict | None" = None) -> dict:
    """Archive the story-history rows :meth:`store.Store.story_history_older_than` returned."""
    root = root or root_for(store_)
    if not root:
        raise ArchiveUnavailable("no archive location: set RWE_ARCHIVE_DIR")
    versions = versions or default_versions()
    return {kind: write_partition(root, kind, history.get(key) or [], versions=versions)
            for kind, key in (("story_snapshots", "snapshots"), ("story_membership", "membership"),
                              ("stories", "stories"), ("story_builds", "builds"))}


def archive_publishers(store_, *, root: "str | None" = None,
                       versions: "dict | None" = None) -> dict:
    """A full snapshot of the publisher table + hosts (the publisher-graph delivery's substrate)."""
    root = root or root_for(store_)
    if not root:
        raise ArchiveUnavailable("no archive location: set RWE_ARCHIVE_DIR")
    rows, _total = store_.list_publishers(limit=10 ** 7)
    for r in rows:
        r["hosts"] = store_.publisher_hosts(r["publisherId"])
    return write_partition(root, "publishers", rows, versions=versions or default_versions())


def list_manifests(root: str) -> list:
    """Every manifest under ``root``, oldest first — what ``archive_export.py --stats`` prints."""
    out = []
    for path in sorted(glob.glob(os.path.join(root, SCHEMA, "*", "dt=*", "*.manifest.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                m = json.load(f)
            m["path"] = path
            out.append(m)
        except (OSError, ValueError):
            continue
    return out


def verify(manifest_path: str) -> bool:
    """Re-hash a partition against its manifest — the check a delivery runs before shipping."""
    with open(manifest_path, encoding="utf-8") as f:
        m = json.load(f)
    part = os.path.join(os.path.dirname(manifest_path), m["file"])
    h = hashlib.sha256()
    with open(part, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest() == m["sha256"]


__all__ = ["SCHEMA", "ArchiveUnavailable", "enabled_on_prune", "root_for", "default_versions",
           "write_partition", "article_rows", "archive_articles", "archive_story_history",
           "archive_publishers", "list_manifests", "verify"]
