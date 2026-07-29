"""capacity_report.py — a production-backed storage capacity report.

Every number is labelled with where it came from. Three kinds:

    [M] MEASURED   read from the live database, filesystem or kernel. A fact.
    [D] DERIVED    arithmetic over measured values only (e.g. bytes / rows).
    [P] PROJECTED  a measured rate extrapolated forward. Depends on assumptions, which are printed.

That separation is the point of this script. A capacity plan built on "an article is probably about
2 KB" is a guess wearing a number's clothes; this reads the actual page allocation per table out of
SQLite and the actual ingestion rate out of the catalog's own timestamps.

    docker exec deploy-api-1 python examples/capacity_report.py
    docker exec deploy-api-1 python examples/capacity_report.py --json     # machine-readable

Non-database storage (Docker images, container logs, backups) lives outside the container and cannot
be read from in here. The script prints the exact host commands for it rather than guessing.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import retention_policy                                      # noqa: E402
import store as store_mod                                    # noqa: E402
from sqlalchemy import text                                  # noqa: E402

GIB = 1024 ** 3
MIB = 1024 ** 2


def human(n: float) -> str:
    if n is None:
        return "—"
    for unit, div in (("GiB", GIB), ("MiB", MIB), ("KiB", 1024)):
        if abs(n) >= div:
            return f"{n / div:,.2f} {unit}"
    return f"{n:,.0f} B"


def db_path_from_url(url: str):
    """The on-disk file behind a sqlite URL, or None for in-memory / non-sqlite."""
    if not url.startswith("sqlite"):
        return None
    tail = url.split("///")[-1]
    return pathlib.Path("/" + tail.lstrip("/")) if tail and ":memory:" not in tail else None


def measure_file(path) -> dict:
    """[M] The database's real footprint: the main file plus its WAL and shared-memory sidecars.

    WAL is counted because it is real allocated space on the same volume, and under a write-heavy
    poller it is not small. A capacity number that ignores it under-reports the disk."""
    out = {"main": 0, "wal": 0, "shm": 0}
    if path is None:
        return out
    for key, suffix in (("main", ""), ("wal", "-wal"), ("shm", "-shm")):
        p = pathlib.Path(str(path) + suffix)
        try:
            out[key] = p.stat().st_size
        except OSError:
            out[key] = 0
    out["total"] = out["main"] + out["wal"] + out["shm"]
    return out


def measure_volume(path) -> dict:
    """[M] The filesystem holding the database — the EBS volume, read through statvfs.

    ``available`` is deliberately not ``total - used``: filesystems reserve blocks for root, so the
    space a process can actually write is the smaller number and is the one capacity planning must
    use."""
    target = str(path.parent if path else "/")
    try:
        st = os.statvfs(target)
    except OSError:
        return {}
    total = st.f_blocks * st.f_frsize
    free = st.f_bfree * st.f_frsize
    avail = st.f_bavail * st.f_frsize
    used = total - free
    return {"mount": target, "total": total, "used": used, "free": free, "available": avail,
            "usedPct": (100.0 * used / total) if total else 0.0,
            "reserved": free - avail}


def dbstat_sizes(session) -> dict:
    """[M] Bytes actually allocated per btree, from SQLite's own page accounting.

    ``dbstat`` reports the real page allocation — payload, per-page overhead, interior nodes and
    partially-filled pages — per table AND per index separately. That is what makes "index overhead"
    a measurement here rather than a rule of thumb.

    Returns {} when the build lacks SQLITE_ENABLE_DBSTAT_VTAB; the caller falls back and says so."""
    try:
        rows = session.execute(text(
            "SELECT name, SUM(pgsize) AS bytes, SUM(pageno IS NOT NULL) AS pages "
            "FROM dbstat GROUP BY name")).all()
    except Exception:
        return {}
    return {r[0]: {"bytes": int(r[1] or 0), "pages": int(r[2] or 0)} for r in rows}


def payload_sizes(session, tables: list) -> dict:
    """[M-ish] Fallback when dbstat is unavailable: summed column payload per table.

    Honest about what it is — this counts the DATA, not the pages holding it, so it under-reports
    real disk by the per-page and index overhead. Labelled distinctly in the output for that reason;
    a fallback that is quietly presented as the real thing is worse than no fallback."""
    out = {}
    for t in tables:
        try:
            cols = [r[1] for r in session.execute(text(f"PRAGMA table_info('{t}')")).all()]
            if not cols:
                continue
            expr = " + ".join(f"COALESCE(LENGTH(CAST(\"{c}\" AS BLOB)), 0)" for c in cols)
            total = session.execute(text(f"SELECT COALESCE(SUM({expr}), 0) FROM \"{t}\"")).scalar()
            out[t] = {"bytes": int(total or 0), "pages": 0}
        except Exception:
            continue
    return out


def schema_objects(session) -> dict:
    """[M] Every table and index in the database, with each index's parent table."""
    rows = session.execute(text(
        "SELECT type, name, tbl_name FROM sqlite_master "
        "WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%'")).all()
    tables = sorted(r[1] for r in rows if r[0] == "table")
    indexes = {r[1]: r[2] for r in rows if r[0] == "index"}
    return {"tables": tables, "indexes": indexes}


def row_counts(session, tables: list) -> dict:
    """[M] Exact row counts. COUNT(*) rather than a sampled estimate — this runs once, off the
    request path, and an approximate denominator would make every bytes-per-row figure approximate
    too."""
    out = {}
    for t in tables:
        try:
            out[t] = int(session.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar() or 0)
        except Exception:
            out[t] = -1
    return out


def ingestion_rate(session, days: int = 14) -> dict:
    """[M] Articles per day, from the catalog's own ``created_at`` — when rows actually landed.

    NOT ``published_at``: backfilling providers (GDELT especially) insert articles published days
    earlier, so a published-at histogram measures the news cycle rather than this system's intake.
    The distinction changes the answer, so it is worth being explicit about."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)
    try:
        rows = session.execute(text(
            "SELECT DATE(created_at) AS d, COUNT(*) FROM feed_articles "
            "WHERE created_at >= :since GROUP BY d ORDER BY d"), {"since": since.isoformat(" ")}).all()
    except Exception:
        return {}
    per_day = [(str(r[0]), int(r[1])) for r in rows]
    if not per_day:
        return {"perDay": [], "note": "no rows in window"}
    # The first and last buckets are partial days; drop them when there is enough left to be useful,
    # because a half-day bucket drags the mean down and would overstate the headroom.
    trimmed = len(per_day) >= 4
    full = per_day[1:-1] if trimmed else per_day
    counts = [c for _, c in full]
    mean = sum(counts) / len(counts)
    return {"perDay": per_day, "fullDays": len(full), "meanPerDay": mean,
            "minPerDay": min(counts), "maxPerDay": max(counts),
            "windowDays": days, "trimmedPartialDays": trimmed,
            # Fewer than four buckets means the first and last (both partial) could not be dropped,
            # so the mean is diluted by part-days and every date derived from it is optimistic.
            "reliable": trimmed}


def catalog_span(session) -> dict:
    """[M] Oldest and newest catalog rows — how much history the current footprint represents."""
    try:
        r = session.execute(text(
            "SELECT MIN(created_at), MAX(created_at) FROM feed_articles")).one()
    except Exception:
        return {}
    return {"first": str(r[0]) if r[0] else None, "last": str(r[1]) if r[1] else None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--days", type=int, default=14, help="ingestion-rate window (default 14)")
    args = ap.parse_args()

    url = os.environ.get("RWE_DB_URL") or "sqlite:////app/data/ih_beta.db"
    path = db_path_from_url(url)
    st = store_mod.Store()

    files = measure_file(path)
    vol = measure_volume(path)
    with st.session() as s:
        page_size = int(s.execute(text("PRAGMA page_size")).scalar() or 0)
        page_count = int(s.execute(text("PRAGMA page_count")).scalar() or 0)
        freelist = int(s.execute(text("PRAGMA freelist_count")).scalar() or 0)
        journal = str(s.execute(text("PRAGMA journal_mode")).scalar() or "")
        auto_vacuum = int(s.execute(text("PRAGMA auto_vacuum")).scalar() or 0)
        schema = schema_objects(s)
        counts = row_counts(s, schema["tables"])
        sizes = dbstat_sizes(s)
        exact = bool(sizes)
        if not exact:
            sizes = payload_sizes(s, schema["tables"])
        rate = ingestion_rate(s, args.days)
        span = catalog_span(s)
        try:
            distinct_publishers = int(s.execute(text(
                "SELECT COUNT(DISTINCT publisher) FROM feed_articles WHERE publisher <> ''")).scalar() or 0)
        except Exception:
            distinct_publishers = 0
        try:
            distinct_stories = int(s.execute(text(
                "SELECT COUNT(DISTINCT story_id) FROM story_member")).scalar() or 0)
        except Exception:
            distinct_stories = 0

    policy = retention_policy.load()

    # ---- table vs index split (measured when dbstat is present) ------------------------------- #
    table_bytes, index_bytes = {}, {}
    for name, rec in sizes.items():
        if name in schema["indexes"]:
            index_bytes[name] = rec["bytes"]
        else:
            table_bytes[name] = rec["bytes"]
    total_tables = sum(table_bytes.values())
    total_indexes = sum(index_bytes.values())
    per_table_index = {}
    for idx, parent in schema["indexes"].items():
        per_table_index[parent] = per_table_index.get(parent, 0) + index_bytes.get(idx, 0)

    articles = counts.get("feed_articles", 0)
    art_table = table_bytes.get("feed_articles", 0)
    art_index = per_table_index.get("feed_articles", 0)
    art_total = art_table + art_index
    # Per-article cost of the WHOLE database, not just its own table: event locations, scored cache,
    # story membership and their indexes all exist because articles do, and capacity planning has to
    # carry them. Both numbers are reported; the all-in one is what the headroom uses.
    db_bytes = page_size * page_count
    per_article_own = (art_total / articles) if articles else 0
    per_article_all = (db_bytes / articles) if articles else 0

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "dbUrl": url, "dbPath": str(path) if path else None,
        "sizeMethod": "dbstat (exact page allocation)" if exact else "column payload (UNDERSTATES disk)",
        "file": files, "volume": vol,
        "pragma": {"pageSize": page_size, "pageCount": page_count, "freelistPages": freelist,
                   "freelistBytes": freelist * page_size, "journalMode": journal,
                   "autoVacuum": auto_vacuum},
        "counts": counts, "tableBytes": table_bytes, "indexBytes": per_table_index,
        "totals": {"tables": total_tables, "indexes": total_indexes, "dbBytes": db_bytes},
        "entities": {"articles": articles, "publishersDistinct": distinct_publishers,
                     "storiesDistinct": distinct_stories,
                     "publisherMetadataRows": counts.get("publisher_metadata", 0)},
        "perArticleOwnBytes": per_article_own, "perArticleAllInBytes": per_article_all,
        "ingestion": rate, "catalogSpan": span, "retention": policy.describe(),
    }

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    w = 78
    print("=" * w)
    print("  PRODUCTION CAPACITY REPORT".ljust(w))
    print(f"  [M] measured   [D] derived from measurements   [P] projected".ljust(w))
    print("=" * w)
    # State the sizing method ALWAYS, not only when it degrades. A reader cannot otherwise tell an
    # exact page count from an estimate, and "which of these is a fact" is the whole contract here.
    print(f"\n[M] SIZING METHOD   {report['sizeMethod']}")
    print(f"\n[M] DATABASE FILE   {path}")
    print(f"      main            {human(files['main']):>14}")
    print(f"      WAL             {human(files['wal']):>14}")
    print(f"      shm             {human(files['shm']):>14}")
    print(f"      on disk         {human(files['total']):>14}")
    print(f"      pages           {page_count:>14,} x {page_size:,} B = {human(db_bytes)}")
    print(f"      free pages      {freelist:>14,}  ({human(freelist * page_size)} reusable, "
          f"not returned to the OS without VACUUM)")
    print(f"      journal_mode={journal}  auto_vacuum={auto_vacuum}")
    if not exact:
        print("\n      ! dbstat unavailable in this SQLite build — table sizes below are COLUMN")
        print("        PAYLOAD and understate real disk by page overhead. Index figures are absent.")

    print(f"\n[M] TABLES BY SIZE")
    print(f"      {'table':<28}{'rows':>10}{'data':>13}{'indexes':>12}{'B/row':>10}{'% db':>8}")
    ranked = sorted(schema["tables"], key=lambda t: -(table_bytes.get(t, 0) + per_table_index.get(t, 0)))
    for t in ranked:
        tb, ib, n = table_bytes.get(t, 0), per_table_index.get(t, 0), counts.get(t, 0)
        if tb + ib == 0 and n == 0:
            continue
        print(f"      {t:<28}{n:>10,}{human(tb):>13}{human(ib):>12}"
              f"{((tb + ib) / n if n else 0):>10,.0f}{100 * (tb + ib) / db_bytes if db_bytes else 0:>7.1f}%")
    print(f"      {'':<28}{'':>10}{human(total_tables):>13}{human(total_indexes):>12}")
    if exact and total_tables:
        print(f"\n[D] INDEX OVERHEAD  {human(total_indexes)} on {human(total_tables)} of data "
              f"= {100 * total_indexes / total_tables:.1f}% of data, "
              f"{100 * total_indexes / db_bytes:.1f}% of the database")

    print(f"\n[D] PER-ENTITY COST")
    print(f"      articles (rows)                 {articles:>12,}")
    print(f"        bytes/article, own table+idx  {per_article_own:>12,.0f}")
    print(f"        bytes/article, whole database {per_article_all:>12,.0f}   <- used for headroom")
    print(f"      publishers, distinct in catalog {distinct_publishers:>12,}")
    pm_rows = counts.get("publisher_metadata", 0)
    pm_bytes = table_bytes.get("publisher_metadata", 0) + per_table_index.get("publisher_metadata", 0)
    print(f"        publisher_metadata rows       {pm_rows:>12,}"
          f"   {pm_bytes / pm_rows if pm_rows else 0:,.0f} B/row")
    sm_bytes = table_bytes.get("story_member", 0) + per_table_index.get("story_member", 0)
    print(f"      stories, distinct in story_member {distinct_stories:>10,}")
    print(f"        stories are DERIVED, not stored — the only story bytes on disk are")
    print(f"        story_member ({human(sm_bytes)}) = "
          f"{sm_bytes / distinct_stories if distinct_stories else 0:,.0f} B/story")

    if rate.get("meanPerDay"):
        print(f"\n[M] INGESTION RATE  (from feed_articles.created_at, not published_at)")
        print(f"      window                 {rate['windowDays']} days, "
              f"{rate['fullDays']} bucket(s) used"
              f"{' (partial days trimmed)' if rate.get('trimmedPartialDays') else ''}")
        if not rate.get("reliable"):
            print("      ! FEWER THAN 4 DAILY BUCKETS — the leading and trailing partial days could")
            print("        not be trimmed, so this mean is diluted and every date below is")
            print("        OPTIMISTIC. Re-run once the catalog spans four days.")
        print(f"      mean                   {rate['meanPerDay']:>10,.0f} articles/day")
        print(f"      min / max              {rate['minPerDay']:>10,} / {rate['maxPerDay']:,}")
        if span.get("first"):
            print(f"      catalog spans          {span['first']}  ->  {span['last']}")

        daily_bytes = rate["meanPerDay"] * per_article_all
        print(f"\n[P] GROWTH   {human(daily_bytes)}/day   {human(daily_bytes * 30)}/month")
        print("      ASSUMES: today's bytes/article holds, ingestion stays at the measured mean,")
        print("      and catalog retention never begins pruning (see RETENTION below).")

        if vol:
            print(f"\n[M] VOLUME   {vol['mount']}")
            print(f"      total           {human(vol['total']):>14}")
            print(f"      used            {human(vol['used']):>14}   {vol['usedPct']:.1f}%")
            print(f"      available       {human(vol['available']):>14}   "
                  f"(+{human(vol['reserved'])} reserved for root)")
            print(f"\n[P] HEADROOM   articles addable before each threshold, at "
                  f"{per_article_all:,.0f} B/article")
            print(f"      {'threshold':<12}{'bytes free':>14}{'articles':>14}{'days':>10}{'date':>13}")
            for pct in (80, 90, 100):
                target = vol["total"] * pct / 100.0
                # Never promise more than the filesystem will actually hand out: ext4 reserves
                # blocks for root, so writes fail at `available`, not at 100% of `total`.
                headroom = min(target - vol["used"], vol["available"])
                if headroom <= 0:
                    print(f"      {str(pct) + '%':<12}{'ALREADY PAST':>14}{'—':>14}{'—':>10}{'—':>13}")
                    continue
                arts = headroom / per_article_all if per_article_all else 0
                days = arts / rate["meanPerDay"] if rate["meanPerDay"] else 0
                when = (datetime.now(timezone.utc) + timedelta(days=days)).date()
                print(f"      {str(pct) + '%':<12}{human(headroom):>14}{arts:>14,.0f}"
                      f"{days:>10,.0f}{str(when):>13}")
            print("      Non-database growth (images, logs, backups) is NOT in these dates — it")
            print("      consumes the same volume. Measure it with the host commands below.")
            print("      Rows are capped at the writable space, not 100% of total: the filesystem")
            print("      reserves blocks for root and writes fail before `total` is reached.")

    print(f"\n[M] RETENTION POLICY  (live, from the environment)")
    d = policy.describe()
    for k, v in d.items():
        print(f"      {k:<28}{v}")
    cat_on = policy.catalog_enabled()
    print(f"\n      catalog retention: {'ON' if cat_on else 'OFF — the catalog grows without bound'}")
    if cat_on:
        cap = policy.article_max_count
        age = policy.article_max_age_days
        if cap:
            print(f"      max_count {cap:,} vs {articles:,} rows -> "
                  f"{'CAP BINDS: steady state reached' if articles >= cap else f'{cap - articles:,} rows of headroom before it binds'}")
            steady = cap * per_article_all
            print(f"[P]   steady-state catalog size at the cap: {human(steady)} "
                  f"(at today's bytes/article)")
        if age:
            print(f"      max_age {age} days -> steady state = {age} days of ingestion")
            print(f"[P]   steady-state catalog size: "
                  f"{human(age * rate.get('meanPerDay', 0) * per_article_all)}")
        print("      NOTE: floors in corpus_health can retain rows past the cap, so the real")
        print("      steady state is >= these figures. Measured pruned-per-cycle is the truth.")
    print("\n" + "=" * w)
    print("  NON-DATABASE STORAGE — run these on the HOST (not readable from in here)")
    print("=" * w)
    print("""
  df -h /                                   # the whole volume
  docker system df -v                       # images, containers, volumes, build cache
  sudo du -sh /var/lib/docker/containers/*/*-json.log | sort -h | tail -5   # container logs
  sudo du -sh /opt/ih/data /opt/ih/backups 2>/dev/null                      # data + local backups
  sudo du -sh /var/log 2>/dev/null

  # MEASURED db growth, straight from the logs (better than any per-article projection):
  docker logs -t deploy-api-1 2>&1 | grep -o '"dbBytes": [0-9]*' | tail -20
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
