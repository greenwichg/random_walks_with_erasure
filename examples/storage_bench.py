"""storage_bench.py — the M3 storage measurement harness for the 50,000-source target.

`stress_50k.py` answers "can the poller drive 50,000 sources"; this answers the question that comes
next and that nothing else measures: **what does the storage layer do as the catalogue grows**, and
which part of it stops being free first.

Every number in `docs/STORAGE_50K_DESIGN.md` comes from here. It measures, at a ladder of catalogue
sizes, the seven things M3 has to put a bar on:

    growth        bytes per article, attributed to table vs index via the `dbstat` vtable
    write         sustained articles/second through the REAL `ingest_entries`, at 1..N writers,
                  with the `database is locked` count and the p95 per-article latency
    retention     `storage_lifecycle.run_cleanup` wall time under no / count / age policy, and the
                  peak process RSS the age policy's whole-catalogue load costs
    probes        `count_feed_articles`, `catalog_fingerprint`, `storage_stats` — the three O(n)
                  reads that run on the hot path
    query         the Tier-A exclusion prefilter and the story scan window
    backup        `backup_database` + `integrity_ok` + gzip, and the WAL growth a backup causes
                  when writers are running underneath it
    restore       `restore_database` end to end from a compressed backup

## Offline, and never production

There is no `--db`. The catalogue is synthetic, built in a temp directory, and deleted at the end.
Nothing here touches the network or the production database; a harness that writes millions of rows
must be incapable of writing them anywhere that matters.

## Synthetic rows, calibrated against the real write path

The ladder is filled with `executemany` because the real path costs ~2.9 ms of CPU per article and
1.6 M of those is 78 minutes. That is only legitimate if the bulk rows are the same SHAPE as real
ones, so the harness does not assume it: `--calibrate` ingests a batch through the real
`rss_ingest.ingest_entries` and compares bytes-per-article against the bulk path. A divergence over
`CALIBRATION_TOLERANCE` is reported as a FAIL, because every growth number below would then be
measuring the harness rather than the product.

Usage::

    python examples/storage_bench.py                       # the full ladder
    python examples/storage_bench.py --rungs 25000,100000  # a quick pass
    python examples/storage_bench.py --skip-backup         # skip the slowest section
    python examples/storage_bench.py --json out.json       # machine-readable
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import pathlib
import random
import shutil
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

#: Catalogue sizes to measure at. Geometric on purpose: a growth curve is only extrapolatable to
#: 50,000 sources if it can be SHOWN linear, and three equally-spaced points cannot show that.
DEFAULT_RUNGS = (25_000, 100_000, 400_000, 1_600_000)

#: Concurrent-writer counts for the write bench. 16 is the pool width `docs/STRESS_50K_PLAN.md`
#: derives for 50,000 sources, so it is the number that has to hold.
DEFAULT_WRITERS = (1, 4, 16)

#: Articles each writer ingests in the write bench. Small enough that 16 writers finish quickly,
#: large enough that per-article latency has a distribution rather than a single sample.
WRITE_BATCH = 150

#: Bulk vs real bytes-per-article may differ by at most this fraction before the run is a FAIL.
CALIBRATION_TOLERANCE = 0.10

#: How many articles the calibration batch ingests through the real path.
CALIBRATION_ARTICLES = 2_000

_WORDS = ("policy budget senate ruling climate election court energy inflation border health "
          "trade markets housing labor privacy defense schools transit wildfire drought vote "
          "committee hearing statement report analysis official measure proposal amendment "
          "council governor federal appeal filing testimony oversight funding shortfall").split()


# --------------------------------------------------------------------------- synthetic content

def _sentence(rng: random.Random, n: int) -> str:
    return " ".join(rng.choice(_WORDS) for _ in range(n)).capitalize() + "."


def _host(i: int) -> str:
    """A per-source host. 50,000 sources means 50,000 distinct `publisher` values, which is what
    makes the publisher indexes and the Tier exclusion list expensive — a single host would measure
    a catalogue the product will never have."""
    return f"source{i:05d}.example"


def _scored_json(publisher: str, url: str, title: str) -> str:
    """The `scored` payload in the EXACT shape `ingest.score_with_cache` stores — key set, key
    order, and the embedded `article_id` + `title` included.

    It is the widest column on the row and two of the nine indexes are `json_extract` expressions
    over it, so its size is not an implementation detail. A plausible-looking shorthand here
    understated bytes-per-article by 23%, which is what `--calibrate` is for."""
    return json.dumps({
        "article_id": url, "outlet": publisher, "category": "Politics", "subcategory": "",
        "title": title, "lean": None, "political": True,
        "emotion": {"fear": 0.0, "outrage": 0.0, "analysis": 0.5, "positive": 0.0, "neutral": 0.5},
        "register": 0.72, "confidence": None, "read_at": None,
    })


def _rows(rng: random.Random, start: int, count: int, sources: int) -> list:
    """`count` catalogue rows in the exact column order `_FEED_COLUMNS` declares."""
    out = []
    for k in range(start, start + count):
        publisher = _host(k % sources)
        slug = "-".join(rng.choice(_WORDS) for _ in range(7))
        url = f"https://{publisher}/2026/08/{slug}-{k}"
        title = _sentence(rng, 11)[:120]
        desc = " ".join(_sentence(rng, 12) for _ in range(3))
        # Spread publication over the last 45 days so an age policy has both prunable and
        # protected rows at every rung, and the fresh floor has something to find.
        day = 1 + (k % 45)
        out.append((url, url, publisher, "bench", title, desc, None,
                    f"2026-07-{day:02d}T12:00:00+00:00", f"https://{publisher}/feed",
                    _scored_json(publisher, url, title), "rss", "bench", None, None,
                    "2026-08-20 12:00:00.000000", "2026-08-20 12:00:00.000000",
                    # Durable identity + licence, exactly as ingest stamps them (store.article_id_for
                    # is the same sha1; the publisher id is a hash of the host's identity key).
                    "ar_" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:20],
                    "pub_" + hashlib.sha1(f"d:{publisher}".encode("utf-8")).hexdigest()[:20],
                    "metadata_public", "1"))
    return out


#: Every NOT NULL column on `feed_articles` has to appear here. `created_at` is NOT NULL with a
#: PYTHON-side default (`default=_utcnow`), so SQLite has no default to supply and an insert that
#: omits it violates the constraint — which `INSERT OR IGNORE` then discards in silence. That is
#: how the first version of this harness benchmarked an empty catalogue while reporting timings,
#: and it is why `_fill` now verifies the row count instead of trusting the statement.
_FEED_COLUMNS = ("canonical_url", "url", "publisher", "source_publisher", "title", "description",
                 "body", "published_at", "source_feed", "scored", "source_type",
                 "source_provider", "country", "language", "fetched_at", "created_at",
                 "article_id", "publisher_id", "licence_class", "scorer_version")


# --------------------------------------------------------------------------- instrumentation

def _ms(fn):
    t0 = time.perf_counter()
    value = fn()
    return value, round((time.perf_counter() - t0) * 1000.0, 1)


def _file_bytes(path: str) -> int:
    total = 0
    for p in (path, path + "-wal", path + "-shm"):
        if os.path.exists(p):
            total += os.path.getsize(p)
    return total


def _checkpoint(path: str) -> None:
    """Fold the WAL back into the main file so a size reading is the real on-disk footprint and not
    an artefact of when the last checkpoint happened to run."""
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()


def _rss_mb() -> float:
    """Resident set size of this process, in MB, from `/proc/self/statm` (pages)."""
    try:
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except (OSError, IndexError, ValueError):
        return 0.0


@contextmanager
def _rss_peak(interval: float = 0.05):
    """Sample RSS on a background thread for the duration of the block. Yields a dict that is
    filled in on exit with `peakMb` and `deltaMb` (peak minus the reading taken at entry)."""
    result: dict = {"peakMb": 0.0, "deltaMb": 0.0}
    base = _rss_mb()
    peak = [base]
    stop = threading.Event()

    def sample():
        while not stop.wait(interval):
            peak[0] = max(peak[0], _rss_mb())

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        yield result
    finally:
        peak[0] = max(peak[0], _rss_mb())
        stop.set()
        thread.join(timeout=2)
        result["peakMb"] = round(peak[0], 1)
        result["deltaMb"] = round(peak[0] - base, 1)


def _dbstat(path: str) -> dict:
    """Bytes per table and per index, from the `dbstat` virtual table. Attribution matters: "the
    database is 5 GB" is not actionable, "3.1 GB of it is nine indexes" is."""
    con = sqlite3.connect(path)
    try:
        rows = con.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name").fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()
    return {name: int(size or 0) for name, size in sorted(rows, key=lambda r: -(r[1] or 0))}


@dataclass
class RungResult:
    rows: int
    sources: int
    fillSeconds: float = 0.0
    dbBytes: int = 0
    bytesPerArticle: float = 0.0
    indexBytes: int = 0
    indexShare: float = 0.0
    dbstat: dict = field(default_factory=dict)
    countMs: float = 0.0
    fingerprintMs: float = 0.0
    storageStatsMs: float = 0.0
    exclusionMs: float = 0.0
    scanMs: float = 0.0
    cleanupNoPolicyMs: float = 0.0
    cleanupCountPolicyMs: float = 0.0
    cleanupAgePolicyMs: float = 0.0
    cleanupPruned: dict = field(default_factory=dict)
    cleanupStepMs: dict = field(default_factory=dict)
    cleanupErrors: dict = field(default_factory=dict)
    retentionPeakRssMb: float = 0.0
    retentionRssDeltaMb: float = 0.0
    writes: dict = field(default_factory=dict)
    backup: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


# --------------------------------------------------------------------------- the benches

def _fill(path: str, start: int, count: int, sources: int, rng: random.Random,
          chunk: int = 20_000) -> float:
    """Bulk-insert `count` rows. Returns seconds. Uses one transaction per chunk so the harness
    itself never holds a multi-minute write lock (the shape it is here to measure)."""
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    sql = (f"INSERT OR IGNORE INTO feed_articles ({','.join(_FEED_COLUMNS)}) "
           f"VALUES ({','.join('?' * len(_FEED_COLUMNS))})")
    scored_sql = "INSERT OR IGNORE INTO scored_articles (url, scored, created_at) VALUES (?,?,?)"
    loc_sql = ("INSERT INTO article_event_locations (canonical_url, country, region, city, source) "
               "VALUES (?,?,?,?,?)")
    ent_sql = "INSERT INTO article_entities (canonical_url, kind, name, source) VALUES (?,?,?,?)"
    # The identity rows the real path writes per article (store._record_observation): one alias
    # (the bench's url IS its canonical form, so the two forms collapse to one row) and one
    # provenance row per (article, channel, source). Both are real per-article storage now.
    alias_sql = ("INSERT OR IGNORE INTO article_aliases (alias, article_id, canonical_url, kind, "
                 "first_seen) VALUES (?,?,?,?,?)")
    prov_sql = ("INSERT OR IGNORE INTO article_provenance (canonical_url, article_id, channel, "
                "provider, source_ref, external_id, licence_class, first_observed_at, "
                "last_observed_at, published_at_seen, observations) VALUES (?,?,?,?,?,?,?,?,?,?,?)")
    before = con.execute("SELECT COUNT(*) FROM feed_articles").fetchone()[0]
    t0 = time.perf_counter()
    try:
        done = 0
        while done < count:
            n = min(chunk, count - done)
            batch = _rows(rng, start + done, n, sources)
            con.executemany(sql, batch)
            # The real path writes a scored-cache row per article too (ingest.score_with_cache), and
            # that table is part of the database the backups copy. Omitting it would understate
            # growth by exactly the amount the 30-day cache costs.
            con.executemany(scored_sql, [(r[0], r[9], "2026-08-20 12:00:00.000000") for r in batch])
            # The two side tables keyed by canonical URL. Both are real per-article storage AND real
            # per-pass cost, and leaving them empty is why this harness reported the orphan reaper at
            # 19 ms while production measured 906 ms on 32,067 rows — the second most expensive step
            # in the whole cleanup pass, invisible here because the table did not exist.
            #
            # Rates are production's, measured 2026-08-27 against 150,076 catalogue articles:
            # 32,067 event locations (0.214/article) and 134,088 entity rows (0.893/article).
            con.executemany(loc_sql, [(r[0], "US", "Region", "City", "gdelt-gkg")
                                      for r in batch[::5]])
            con.executemany(ent_sql, [(r[0], "person", f"Name {i % 997}", "gdelt-gkg")
                                      for i, r in enumerate(batch) if i % 10 != 0])
            con.executemany(alias_sql, [(r[0], r[16], r[0], "url", "2026-08-20 12:00:00.000000")
                                        for r in batch])
            con.executemany(prov_sql, [(r[0], r[16], "rss", r[11], r[8], None, "metadata_public",
                                        "2026-08-20T12:00:00+00:00", "2026-08-20T12:00:00+00:00",
                                        r[7], 1) for r in batch])
            con.commit()
            done += n
        seconds = time.perf_counter() - t0
        after = con.execute("SELECT COUNT(*) FROM feed_articles").fetchone()[0]
    finally:
        con.close()
    # `INSERT OR IGNORE` discards a constraint violation as quietly as it discards a duplicate, so
    # the only proof the fill happened is the count. Without this the whole ladder can run green
    # against an empty table.
    if after - before < count * 0.99:
        raise RuntimeError(f"fill inserted {after - before} of {count} rows — the bulk INSERT is "
                           f"being rejected (check _FEED_COLUMNS against the model)")
    return round(seconds, 2)


def _calibrate(sources: int, rng: random.Random) -> dict:
    """Ingest `CALIBRATION_ARTICLES` through the REAL write path into a fresh database and compare
    bytes-per-article against the bulk path over the same count. The bulk fill is only a legitimate
    stand-in if this passes."""
    import rss_ingest
    import store as store_mod

    out = {}
    for label in ("real", "bulk"):
        d = tempfile.mkdtemp(prefix=f"ihbench-cal-{label}-")
        path = os.path.join(d, "cal.db")
        st = store_mod.Store(f"sqlite:///{path}")
        st.count_feed_articles()                       # force schema creation
        try:
            if label == "real":
                entries = []
                local = random.Random(101)
                for k in range(CALIBRATION_ARTICLES):
                    publisher = _host(k % sources)
                    slug = "-".join(local.choice(_WORDS) for _ in range(7))
                    entries.append(rss_ingest.FeedEntry(
                        url=f"https://{publisher}/2026/08/{slug}-{k}",
                        title=_sentence(local, 11)[:120],
                        description=" ".join(_sentence(local, 12) for _ in range(3)),
                        body=None, published_at="2026-07-20T12:00:00+00:00"))
                t0 = time.perf_counter()
                stats = rss_ingest.ingest_entries(entries, "cal", "https://cal.example/feed",
                                                  rss_ingest.make_scorer(), st,
                                                  source_type="rss", source_provider="cal")
                seconds = time.perf_counter() - t0
                written = stats["new"]
            else:
                seconds = _fill(path, 0, CALIBRATION_ARTICLES, sources, random.Random(101))
                written = CALIBRATION_ARTICLES
            _checkpoint(path)
            out[label] = {"bytesPerArticle": round(_file_bytes(path) / max(1, written), 1),
                          "articlesPerSecond": round(written / max(1e-9, seconds), 1)}
        finally:
            st.engine.dispose()
            shutil.rmtree(d, ignore_errors=True)
    real, bulk = out["real"]["bytesPerArticle"], out["bulk"]["bytesPerArticle"]
    drift = abs(real - bulk) / max(1.0, real)
    out["drift"] = round(drift, 4)
    out["verdict"] = "PASS" if drift <= CALIBRATION_TOLERANCE else "FAIL"
    return out


def _write_bench(path: str, writers: int, rng: random.Random, offset: int, sources: int) -> dict:
    """Sustained throughput through the REAL `ingest_entries` from `writers` threads at once.

    This is the write-contention measurement. SQLite has one writer; the ingest path opens a session
    and commits PER ARTICLE (`upsert_feed_article`) plus a second commit for the scored-cache row,
    so N pollers persisting at once contend on every article rather than once per batch."""
    import rss_ingest
    import store as store_mod

    st = store_mod.Store(f"sqlite:///{path}")
    scorer = rss_ingest.make_scorer()
    latencies: list = []
    locked = [0]
    errors: list = []
    lock = threading.Lock()
    start_gate = threading.Barrier(writers)

    def one(index: int) -> None:
        local = random.Random(9_000 + index)
        base = offset + index * WRITE_BATCH * 10
        entries = []
        for k in range(WRITE_BATCH):
            publisher = _host((base + k) % sources)
            slug = "-".join(local.choice(_WORDS) for _ in range(7))
            entries.append(rss_ingest.FeedEntry(
                url=f"https://{publisher}/2026/08/{slug}-w{base + k}",
                title=_sentence(local, 11)[:120],
                description=" ".join(_sentence(local, 12) for _ in range(3)),
                body=None, published_at="2026-07-20T12:00:00+00:00"))
        mine: list = []
        start_gate.wait()
        for e in entries:
            t0 = time.perf_counter()
            try:
                rss_ingest.ingest_entries([e], "bench", "https://bench.example/feed", scorer, st,
                                          source_type="rss", source_provider="bench")
            except Exception as exc:                          # noqa: BLE001 — the point of the bench
                with lock:
                    if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                        locked[0] += 1
                    else:
                        errors.append(f"{type(exc).__name__}: {exc}")
                continue
            mine.append((time.perf_counter() - t0) * 1000.0)
        with lock:
            latencies.extend(mine)

    threads = [threading.Thread(target=one, args=(i,), daemon=True) for i in range(writers)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    seconds = time.perf_counter() - t0
    st.engine.dispose()
    ordered = sorted(latencies)
    return {
        "writers": writers,
        "articles": len(latencies),
        "seconds": round(seconds, 2),
        "articlesPerSecond": round(len(latencies) / max(1e-9, seconds), 1),
        "p50Ms": round(statistics.median(ordered), 2) if ordered else None,
        "p95Ms": round(ordered[int(len(ordered) * 0.95)], 2) if len(ordered) >= 20 else None,
        "maxMs": round(ordered[-1], 2) if ordered else None,
        "locked": locked[0],
        "errors": errors[:3],
    }


def _retention_bench(path: str, rows: int) -> dict:
    """`run_cleanup` under three policies. The interesting one is the AGE policy: `run_retention`
    calls `list_feed_articles(limit=10_000_000)`, which materialises the ENTIRE catalogue as Python
    dicts before it decides anything, so its cost and its memory are both O(catalogue)."""
    import storage_lifecycle
    import store as store_mod

    out: dict = {}
    saved = {k: os.environ.get(k) for k in
             ("RWE_RETENTION_MAX_AGE_DAYS", "RWE_RETENTION_MAX_COUNT",
              "RWE_RETENTION_MAX_AGE_DAYS_TIER_B", "RWE_RETENTION_MAX_AGE_DAYS_SHADOW",
              "RWE_CORPUS_MIN_ARTICLES")}
    try:
        for key in saved:
            os.environ.pop(key, None)
        # A count cap ABOVE the catalogue, so the fast pre-gate is what gets measured; and an age
        # far beyond the synthetic publication spread, so the age pass plans a prune of zero rows.
        # Both are the steady state: retention's cost has to be paid on every pass, including the
        # overwhelming majority that delete nothing.
        cases = (("cleanupNoPolicyMs", {}),
                 ("cleanupCountPolicyMs", {"RWE_RETENTION_MAX_COUNT": str(rows * 10)}),
                 ("cleanupAgePolicyMs", {"RWE_RETENTION_MAX_AGE_DAYS": "3650"}))
        for label, env in cases:
            for key in saved:
                os.environ.pop(key, None)
            os.environ.update(env)
            st = store_mod.Store(f"sqlite:///{path}")
            if label == "cleanupAgePolicyMs":
                # Process RSS, not `tracemalloc`, and sampled rather than a high-water mark: the
                # question M3 has to answer is what a 4 GiB box sees while an age policy loads the
                # whole catalogue, and `ru_maxrss` never comes back down so it cannot separate this
                # pass from anything the harness did earlier.
                with _rss_peak() as peak:
                    res, ms = _ms(lambda: storage_lifecycle.run_cleanup(
                        st, log=lambda *a, **k: None))
                out["retentionPeakRssMb"] = peak["peakMb"]
                out["retentionRssDeltaMb"] = peak["deltaMb"]
            else:
                res, ms = _ms(lambda: storage_lifecycle.run_cleanup(st, log=lambda *a, **k: None))
            # `run_cleanup` is fail-soft by design: every step's exception is caught and recorded
            # rather than raised. A harness that ignored `errors` would time a pass that did nothing
            # and report it as a fast one — the measurement equivalent of a gate that cannot fire.
            if res.get("errors"):
                out.setdefault("errors", {})[label] = res["errors"]
            out.setdefault("pruned", {})[label] = res.get("total", 0)
            # Per-STEP attribution, not just the pass total. "Cleanup is 185 ms" is not actionable;
            # "three of the five prunes full-scan their table because the column they filter on is
            # unindexed" is, and only the step breakdown can say which.
            out.setdefault("stepMs", {})[label] = res.get("ms", {})
            out[label] = ms
            st.engine.dispose()
    finally:
        for key, value in saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
    return out


def _query_bench(path: str, sources: int) -> dict:
    """The two catalogue reads on the serving hot path: the Tier exclusion prefilter (an outlet
    NOT IN list that grows with the shadow corpus) and the story scan window."""
    import store as store_mod

    st = store_mod.Store(f"sqlite:///{path}")
    try:
        # Exclude 90% of hosts — the shape at 50,000 sources, where Tier A is a tiny admitted subset
        # and everything else is shadow.
        exclude = {_host(i) for i in range(int(sources * 0.9))}
        _, exclusion_ms = _ms(lambda: st.search_feed_articles(
            q="policy", exclude_publishers=exclude))
        _, scan_ms = _ms(lambda: st.list_feed_articles(limit=60_000))
    finally:
        st.engine.dispose()
    return {"exclusionMs": exclusion_ms, "scanMs": scan_ms, "excluded": len(exclude)}


def _backup_bench(path: str, workdir: str) -> dict:
    """`backup_database` + `integrity_ok` + gzip + restore, each timed separately, plus the WAL
    growth a backup causes while a writer runs underneath it.

    The three phases are timed apart because they scale differently and only one of them is the
    part everyone thinks of: the page copy is I/O over the file, `PRAGMA integrity_check` is a full
    structural walk of every page and index, and gzip is single-threaded CPU over the whole thing."""
    import store as store_mod

    out: dict = {}
    plain = os.path.join(workdir, "snapshot.db")
    _, backup_ms = _ms(lambda: store_mod.backup_database(path, plain))
    out["backupSeconds"] = round(backup_ms / 1000.0, 2)
    out["snapshotBytes"] = os.path.getsize(plain)
    ok, integrity_ms = _ms(lambda: store_mod.integrity_ok(plain))
    out["integritySeconds"] = round(integrity_ms / 1000.0, 2)
    out["integrityOk"] = bool(ok)

    gz = plain + ".gz"

    def _gzip():
        with open(plain, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)

    _, gzip_ms = _ms(_gzip)
    out["gzipSeconds"] = round(gzip_ms / 1000.0, 2)
    out["gzBytes"] = os.path.getsize(gz)
    out["gzRatio"] = round(out["snapshotBytes"] / max(1, out["gzBytes"]), 2)

    target = os.path.join(workdir, "restored.db")
    _, restore_ms = _ms(lambda: store_mod.restore_database(gz, target))
    out["restoreSeconds"] = round(restore_ms / 1000.0, 2)
    out["restoredBytes"] = os.path.getsize(target)
    for leftover in (plain, gz, target, target + ".pre-restore"):
        try:
            os.remove(leftover)
        except OSError:
            pass

    # WAL growth under a concurrent writer. A backup holds a read transaction for its whole
    # duration, and a read transaction is exactly what stops the WAL being checkpointed — so the
    # WAL has to absorb every write made while the copy runs, on the same volume.
    _checkpoint(path)
    stop = threading.Event()
    written = [0]

    def churn():
        con = sqlite3.connect(path, timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        rng = random.Random(4242)
        k = 50_000_000
        try:
            while not stop.is_set():
                batch = _rows(rng, k, 200, 5_000)
                con.executemany(
                    f"INSERT OR IGNORE INTO feed_articles ({','.join(_FEED_COLUMNS)}) "
                    f"VALUES ({','.join('?' * len(_FEED_COLUMNS))})", batch)
                con.commit()
                k += 200
                written[0] += 200
        except Exception:                                    # noqa: BLE001 — observational
            pass
        finally:
            con.close()

    thread = threading.Thread(target=churn, daemon=True)
    thread.start()
    concurrent = os.path.join(workdir, "concurrent.db")
    t0 = time.perf_counter()
    try:
        store_mod.backup_database(path, concurrent)
        out["concurrentBackupSeconds"] = round(time.perf_counter() - t0, 2)
    except Exception as exc:                                 # noqa: BLE001
        out["concurrentBackupError"] = f"{type(exc).__name__}: {exc}"
    wal = path + "-wal"
    out["walBytesDuringBackup"] = os.path.getsize(wal) if os.path.exists(wal) else 0
    out["rowsWrittenDuringBackup"] = written[0]
    stop.set()
    thread.join(timeout=30)
    for leftover in (concurrent, concurrent + ".tmp"):
        try:
            os.remove(leftover)
        except OSError:
            pass
    return out


# --------------------------------------------------------------------------- driver

def run_ladder(rungs, *, sources: int, writers, skip_backup: bool, calibrate: bool,
               log=print) -> dict:
    import store as store_mod

    workdir = tempfile.mkdtemp(prefix="ihbench-")
    path = os.path.join(workdir, "catalog.db")
    results: list = []
    calibration = None
    try:
        store_mod.Store(f"sqlite:///{path}").engine.dispose()   # create the schema + indexes
        if calibrate:
            log("calibrating the bulk fill against the real ingest path …")
            calibration = _calibrate(sources, random.Random(11))
            log(f"  calibration: {json.dumps(calibration)}")

        rng = random.Random(2026)
        have = 0
        for rung in rungs:
            log(f"filling to {rung:,} rows …")
            seconds = _fill(path, have, rung - have, sources, rng)
            have = rung
            _checkpoint(path)
            st = store_mod.Store(f"sqlite:///{path}")
            st.count_feed_articles()          # warm: the first query on a new engine pays for
            st.catalog_fingerprint()          # connection setup + pragmas, which is not what these
            st.storage_stats()                # three probes are here to measure
            actual, count_ms = _ms(st.count_feed_articles)
            _, fingerprint_ms = _ms(st.catalog_fingerprint)
            _, stats_ms = _ms(st.storage_stats)
            st.engine.dispose()

            stat = _dbstat(path)
            index_bytes = sum(v for k, v in stat.items()
                              if k.startswith("ix_") or k.startswith("sqlite_autoindex"))
            db_bytes = _file_bytes(path)
            res = RungResult(rows=actual, sources=sources, fillSeconds=seconds,
                             dbBytes=db_bytes,
                             bytesPerArticle=round(db_bytes / max(1, actual), 1),
                             indexBytes=index_bytes,
                             indexShare=round(index_bytes / max(1, db_bytes), 3),
                             dbstat=stat, countMs=count_ms, fingerprintMs=fingerprint_ms,
                             storageStatsMs=stats_ms)

            log(f"  {actual:,} rows · {db_bytes / 1e6:.1f} MB · {res.bytesPerArticle} B/article · "
                f"indexes {res.indexShare:.0%} · fill {seconds}s")
            log(f"  probes: count {count_ms} ms · fingerprint {fingerprint_ms} ms · "
                f"storage_stats {stats_ms} ms")
            q = _query_bench(path, sources)
            res.exclusionMs, res.scanMs = q["exclusionMs"], q["scanMs"]
            log(f"  queries: exclusion {q['exclusionMs']} ms ({q['excluded']:,} excluded) · "
                f"scan {q['scanMs']} ms")

            r = _retention_bench(path, actual)
            res.cleanupNoPolicyMs = r.get("cleanupNoPolicyMs", 0.0)
            res.cleanupCountPolicyMs = r.get("cleanupCountPolicyMs", 0.0)
            res.cleanupAgePolicyMs = r.get("cleanupAgePolicyMs", 0.0)
            res.retentionPeakRssMb = r.get("retentionPeakRssMb", 0.0)
            res.retentionRssDeltaMb = r.get("retentionRssDeltaMb", 0.0)
            res.cleanupPruned = r.get("pruned", {})
            res.cleanupErrors = r.get("errors", {})
            res.cleanupStepMs = r.get("stepMs", {})
            log(f"  retention: none {res.cleanupNoPolicyMs} ms · count {res.cleanupCountPolicyMs} ms"
                f" · age {res.cleanupAgePolicyMs} ms (RSS peak {res.retentionPeakRssMb} MB,"
                f" +{res.retentionRssDeltaMb} MB)"
                f" · pruned {res.cleanupPruned}"
                f"\n  cleanup steps (no policy): {res.cleanupStepMs.get('cleanupNoPolicyMs', {})}"
                + (f" · ERRORS {res.cleanupErrors}" if res.cleanupErrors else ""))

            for w in writers:
                res.writes[str(w)] = _write_bench(path, w, rng, 90_000_000 + rung, sources)
                d = res.writes[str(w)]
                log(f"  write x{w}: {d['articlesPerSecond']}/s · p95 {d['p95Ms']} ms · "
                    f"locked {d['locked']}")
            _checkpoint(path)

            if not skip_backup:
                res.backup = _backup_bench(path, workdir)
                log(f"  backup: copy {res.backup.get('backupSeconds')} s · integrity "
                    f"{res.backup.get('integritySeconds')} s · gzip {res.backup.get('gzipSeconds')} s"
                    f" ({res.backup.get('gzRatio')}x) · restore {res.backup.get('restoreSeconds')} s"
                    f" · WAL during backup {res.backup.get('walBytesDuringBackup', 0) / 1e6:.1f} MB"
                    f" from {res.backup.get('rowsWrittenDuringBackup', 0):,} concurrent rows")
                _checkpoint(path)
                have = store_mod.Store(f"sqlite:///{path}").count_feed_articles()

            results.append(asdict(res))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return {"rungs": results, "calibration": calibration,
            "host": {"cpus": os.cpu_count(), "sqlite": sqlite3.sqlite_version,
                     "pageSize": 4096}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M3 storage measurements for the 50k-source target.")
    ap.add_argument("--rungs", default=",".join(str(r) for r in DEFAULT_RUNGS),
                    help="comma-separated catalogue sizes")
    ap.add_argument("--sources", type=int, default=50_000,
                    help="distinct publisher hosts to spread the catalogue over")
    ap.add_argument("--writers", default=",".join(str(w) for w in DEFAULT_WRITERS),
                    help="comma-separated concurrent-writer counts")
    ap.add_argument("--skip-backup", action="store_true", help="skip the backup/restore section")
    ap.add_argument("--no-calibrate", action="store_true",
                    help="skip the bulk-vs-real calibration (the run is then unvalidated)")
    ap.add_argument("--json", default=None, help="write the full result to this path")
    args = ap.parse_args(argv)

    rungs = tuple(int(x) for x in args.rungs.split(",") if x.strip())
    writers = tuple(int(x) for x in args.writers.split(",") if x.strip())
    out = run_ladder(rungs, sources=args.sources, writers=writers,
                     skip_backup=args.skip_backup, calibrate=not args.no_calibrate)
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(out, indent=2, default=str))
        print(f"\nwrote {args.json}")
    cal = out.get("calibration")
    if cal and cal.get("verdict") == "FAIL":
        print(f"\nCALIBRATION FAILED (drift {cal['drift']}): the bulk rows are not the shape the "
              f"real path writes, so the growth numbers above measure the harness.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
