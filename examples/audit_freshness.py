"""audit_freshness.py — is ingestion capturing the LATEST news from each outlet? Read-only.

    python examples/audit_freshness.py --db "$RWE_DB_URL"                    # last 24 h
    python examples/audit_freshness.py --db "$RWE_DB_URL" --hours 72 --top 40

Four questions, each answered from stored rows — never by asking a publisher — so this is safe
to run at any time against production and costs a few SELECTs.

1. **Ingestion delay.** ``feed_articles.created_at`` is when WE first saw an article and is never
   rewritten; ``published_at`` is when the publisher says it appeared. The difference, per source
   type and per outlet, is the lag a reader experiences. A row with no ``published_at`` is
   reported as undated, not guessed.
2. **Cadence as it actually ran** (``feed_health``): when each RSS feed, crawl host and API
   adapter last succeeded, how long the last RSS sweep took (every feed is fetched serially under
   the ingest lock, so the sum of last-poll latencies IS the sweep), whether the sweep is keeping
   to ``RWE_POLL_INTERVAL``, and which sources are failing.
3. **Repeated re-ingestion.** Every RSS poll re-submits every entry the feed still lists. The
   catalog recognises them (``duplicate``) so nothing is stored twice, but each one still costs
   a scoring-cache read and a ``fetched_at`` write under the ingest lock. Sized from last-cycle
   counters and from rows whose ``fetched_at`` kept advancing long after ``created_at``.
4. **Feed overflow.** A feed whose last poll found ONLY new entries may have published more items
   than it lists between two polls — the one gap the catalog cannot see directly, so it is flagged
   from the shape of the counters rather than claimed.

Every threshold is a named constant below, and the verdict lines at the end are derived from the
same numbers the tables print — a finding never rests on a figure the reader cannot see.
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import store as store_mod

#: Lag buckets (minutes) the delay tables report — an hour, a working shift, a day.
LAG_BUCKETS_MIN = (60, 360, 1440)
#: An outlet is STALE when nothing new has been first-seen for longer than this many of its own
#: mean inter-arrival gaps, and at least ``STALE_FLOOR_H`` hours — a weekly column is not stale
#: on Tuesday.
STALE_GAPS = 3.0
STALE_FLOOR_H = 6.0
#: An outlet is LAGGY when its MEDIAN lag exceeds this many poll intervals: a uniform sweep
#: explains at most one interval of delay, so two is evidence the feed itself lists late, or
#: that its source is polled less often than the sweep.
LAGGY_INTERVALS = 2.0
#: ARCHIVE when at least this share of an outlet's newly-seen rows were published before the
#: window began — a source that keeps "discovering" old articles.
ARCHIVE_SHARE = 0.25
#: UNDATED when at least this share of an outlet's rows carry no publication date.
UNDATED_SHARE = 0.5
#: A feed whose last poll imported at least this many entries with ZERO duplicates listed only
#: things we had never seen — it may have rolled its whole window between two polls.
OVERFLOW_MIN_IMPORTED = 10
#: A row re-touched this long after first sight was still being listed by its feed: the cost
#: of repeat ingestion, measured on the catalog rather than inferred from counters.
RETOUCH_AFTER_H = 1.0
#: A source whose last success is older than this many of its own intervals is not being polled
#: on schedule — backed off, dead, or the poller is stalled.
STALE_POLL_INTERVALS = 2.0
#: A row whose publication date sits this many days before its first sight did not come from a
#: live feed's recent items in any honest reading: either the feed stamps bogus dates (CNN's RSS,
#: production 2026-09-02: a median lag of 3.4 YEARS) or an archive is being re-discovered. Both
#: push the article outside every recency window the moment it lands.
SUSPECT_DATE_DAYS = 30.0

_TRUE = {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- #
# Time helpers — every timestamp becomes an aware UTC datetime or None, never a guess.
# --------------------------------------------------------------------------- #
def parse_iso(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hours(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if a is None or b is None:
        return None
    return (a - b).total_seconds() / 3600.0


def percentile(values: list, p: float) -> Optional[float]:
    """Nearest-rank percentile; ``None`` on an empty list rather than a fabricated zero."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(p * (len(ordered) - 1)))))
    return ordered[k]


def _float_env(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


# --------------------------------------------------------------------------- #
# Configuration in effect — printed, because a cadence finding is meaningless without it.
# --------------------------------------------------------------------------- #
CONFIG_KNOBS = (
    ("RWE_POLL_INTERVAL", "600", "seconds between RSS sweeps (every feed, in full, serially)"),
    ("RWE_FEED_SCHEDULER", "0", "per-feed cadence + conditional GET (feed_schedule.py)"),
    ("RWE_FEED_MIN_INTERVAL", "120", "scheduler floor, seconds"),
    ("RWE_RSS_MAX_ARTICLES", "", "per-feed entry cap (empty = whole feed)"),
    ("RWE_CRAWL_ENABLED", "", "crawl adapters on"),
    ("RWE_CRAWL_INTERVAL", "900", "seconds between crawl cycles per host (floor 300 per host)"),
    ("RWE_GDELT_ENABLED", "0", "GDELT DOC adapter on"),
    ("RWE_GDELT_POLL_INTERVAL", "900", "seconds between GDELT polls"),
    ("RWE_SOURCE_MAX_INTERVAL", "21600", "failure back-off ceiling for adapters, seconds"),
    ("RWE_STORIES_CACHE_TTL", "600", "seconds a built story set is served before a rebuild"),
    ("RWE_STORIES_SCAN_DAYS", "6", "days of catalog eligible to cluster"),
)


def config_in_effect() -> list:
    return [{"name": n, "value": os.environ.get(n, ""), "default": d, "what": w}
            for n, d, w in CONFIG_KNOBS]


def poll_interval() -> float:
    return _float_env("RWE_POLL_INTERVAL", 600.0)


def crawl_interval() -> float:
    return _float_env("RWE_CRAWL_INTERVAL", 900.0)


def gdelt_interval() -> float:
    return _float_env("RWE_GDELT_POLL_INTERVAL", 900.0)


def interval_for_key(feed_url: str, *, poll_interval_s: float, crawl_interval_s: float,
                     gdelt_interval_s: float) -> float:
    """The cadence a health row is HELD TO. RSS feeds share the sweep; a crawl host has its own
    interval; GDELT (``gdelt://…``, ``gdelt-gkg://…``) polls on its own knob and is half-hourly on
    production. Any other API key is held to the sweep — a stricter bar than some run at, so an
    off-schedule verdict there is a prompt to check, not a proven fault."""
    kind = source_kind(feed_url)
    if kind == "crawl":
        return crawl_interval_s
    if kind == "api" and str(feed_url or "").lower().startswith("gdelt"):
        return gdelt_interval_s
    return poll_interval_s


# --------------------------------------------------------------------------- #
# 1. Ingestion delay — from the catalog.
# --------------------------------------------------------------------------- #
def window_rows(store_, since: datetime) -> list:
    """Rows FIRST SEEN in the window, as plain dicts with parsed timestamps.

    Filtered on ``created_at`` rather than ``published_at`` on purpose: the question is what the
    pipeline DID in the window, and a month-old article inserted today is part of that answer
    (it is the ARCHIVE signal), whereas selecting by publication date would hide it."""
    from sqlalchemy import select
    FA = store_mod.FeedArticle
    naive = since.astimezone(timezone.utc).replace(tzinfo=None)
    out = []
    with store_.session() as s:
        q = (select(FA.publisher, FA.source_type, FA.published_at, FA.created_at, FA.fetched_at,
                    FA.source_feed, FA.url)
             .where(FA.created_at >= naive))
        for pub, stype, published, created, fetched, feed, url in s.execute(q):
            out.append({"publisher": (pub or "").strip(), "sourceType": (stype or "unknown"),
                        "published": parse_iso(published), "created": parse_iso(created),
                        "fetched": parse_iso(fetched), "sourceFeed": feed or "", "url": url or ""})
    return out


def scheduler_state(store_, health: list) -> list:
    """The per-feed scheduler columns, merged onto the RSS health rows.

    ``list_feed_health`` does not carry them — the first production run of this audit reported
    "scheduler state on 0/9 feeds" against a deployment running ``RWE_FEED_SCHEDULER=1``, which
    was this instrument's defect, not the scheduler's. Read through the store's own accessor so
    the audit and the scheduler agree on what "state" means."""
    out = []
    for r in health:
        row = dict(r)
        if source_kind(row.get("feedUrl")) == "rss":
            try:
                st = store_.feed_schedule_state(row.get("feedUrl"))
            except Exception:
                st = {}
            row["intervalS"] = st.get("interval_s")
            row["nextDueAt"] = st.get("next_due_at")
            row["hasValidator"] = bool(st.get("etag") or st.get("last_modified"))
        out.append(row)
    return out


def suspect_dates_report(rows: list, *, limit: int = 8) -> list:
    """Rows dated ``SUSPECT_DATE_DAYS`` or more before first sight, grouped by the feed/provider
    that supplied them — the list of who is stamping dates we cannot use."""
    groups = defaultdict(list)
    for r in rows:
        lag = lag_minutes(r)
        if lag is not None and lag >= SUSPECT_DATE_DAYS * 1440.0:
            groups[(r["sourceType"], r["sourceFeed"])].append(r)
    out = []
    for (stype, feed), rs in groups.items():
        lags = [lag_minutes(r) for r in rs]
        out.append({"sourceType": stype, "sourceFeed": feed, "rows": len(rs),
                    "publishers": sorted({r["publisher"] for r in rs if r["publisher"]})[:3],
                    "medianLagDays": (percentile(lags, 0.5) or 0.0) / 1440.0,
                    "example": rs[0]["url"]})
    out.sort(key=lambda g: -g["rows"])
    return out[:limit]


def lag_minutes(row: dict) -> Optional[float]:
    """first-seen minus published, in minutes. ``None`` when either side is unknown. A negative
    value (the publisher's clock ahead of ours, within the ingest clamp) is reported as zero:
    an article cannot have been ingested before it existed."""
    if row.get("published") is None or row.get("created") is None:
        return None
    return max(0.0, (row["created"] - row["published"]).total_seconds() / 60.0)


def _lag_summary(rows: list, *, since: datetime) -> dict:
    lags = [l for l in (lag_minutes(r) for r in rows) if l is not None]
    undated = sum(1 for r in rows if r.get("published") is None)
    archive = sum(1 for r in rows if r.get("published") is not None and r["published"] < since)
    out = {"n": len(rows), "dated": len(lags), "undated": undated, "archive": archive,
           "medianMin": percentile(lags, 0.5), "p90Min": percentile(lags, 0.9),
           "maxMin": max(lags) if lags else None}
    for b in LAG_BUCKETS_MIN:
        out[f"over{b}"] = sum(1 for l in lags if l > b)
    return out


def lag_report(rows: list, *, since: datetime) -> dict:
    by_source = defaultdict(list)
    for r in rows:
        by_source[r["sourceType"]].append(r)
    return {"all": _lag_summary(rows, since=since),
            "bySource": {k: _lag_summary(v, since=since) for k, v in sorted(by_source.items())}}


def outlet_report(rows: list, *, now: datetime, since: datetime, hours: float,
                  poll_interval_s: float) -> list:
    """One line per outlet, flagged. Sorted by volume; the caller decides how many to print."""
    by_pub = defaultdict(list)
    for r in rows:
        if r["publisher"]:
            by_pub[r["publisher"]].append(r)
    out = []
    for pub, prs in by_pub.items():
        summary = _lag_summary(prs, since=since)
        newest_seen = max((r["created"] for r in prs if r["created"]), default=None)
        newest_pub = max((r["published"] for r in prs if r["published"]), default=None)
        per_day = len(prs) * 24.0 / max(hours, 1e-9)
        mean_gap_h = 24.0 / per_day if per_day > 0 else None
        since_seen_h = _hours(now, newest_seen)
        flags = []
        if since_seen_h is not None and mean_gap_h is not None and \
                since_seen_h > max(STALE_FLOOR_H, STALE_GAPS * mean_gap_h):
            flags.append("STALE")
        if summary["medianMin"] is not None and \
                summary["medianMin"] > LAGGY_INTERVALS * poll_interval_s / 60.0:
            flags.append("LAGGY")
        if summary["n"] and summary["archive"] / summary["n"] >= ARCHIVE_SHARE:
            flags.append("ARCHIVE")
        if summary["n"] and summary["undated"] / summary["n"] >= UNDATED_SHARE:
            flags.append("UNDATED")
        out.append({"publisher": pub, "n": len(prs), "perDay": per_day,
                    "sources": sorted({r["sourceType"] for r in prs}),
                    "newestPublished": newest_pub, "newestSeen": newest_seen,
                    "hoursSinceSeen": since_seen_h, "hoursSincePublished": _hours(now, newest_pub),
                    "medianMin": summary["medianMin"], "p90Min": summary["p90Min"],
                    "archive": summary["archive"], "undated": summary["undated"],
                    "flags": flags})
    out.sort(key=lambda r: (-r["n"], r["publisher"].lower()))
    return out


# --------------------------------------------------------------------------- #
# 2. Cadence as observed — from feed_health.
# --------------------------------------------------------------------------- #
def source_kind(feed_url: str) -> str:
    u = str(feed_url or "").lower()
    if u.startswith(("http://", "https://")):
        return "rss"
    if u.startswith("crawl://"):
        return "crawl"
    return "api"


def cadence_report(health: Iterable[dict], *, now: datetime, poll_interval_s: float,
                   crawl_interval_s: float, gdelt_interval_s: Optional[float] = None) -> dict:
    """Per source kind: how many, how many failing, how many not polled on schedule, and for RSS
    the last sweep's wall time and the gap since it finished.

    The sweep time is the SUM of last-poll latencies because ``rss_ingest.ingest_all`` fetches
    feeds one after another — and it does so under the poller's ingest lock, so that sum is also
    how long every other adapter waited. ``feed_health.imported``/``duplicate`` hold the LAST cycle
    only (``store.record_feed_health`` assigns, never accumulates), which is exactly the
    granularity the overflow flag needs."""
    gdelt_interval_s = gdelt_interval_s if gdelt_interval_s is not None else poll_interval_s
    groups = {"rss": [], "crawl": [], "api": []}
    for r in health:
        groups[source_kind(r.get("feedUrl"))].append(r)
    interval_for = {"rss": poll_interval_s, "crawl": crawl_interval_s, "api": poll_interval_s}
    out = {}
    for kind, rows in groups.items():
        iv = interval_for[kind]
        # (row, age-in-seconds-or-None, the interval THIS row is held to)
        judged = []
        for r in rows:
            t = parse_iso(r.get("lastSuccessAt"))
            age = None if t is None else (now - t).total_seconds()
            held = interval_for_key(r.get("feedUrl"), poll_interval_s=poll_interval_s,
                                    crawl_interval_s=crawl_interval_s,
                                    gdelt_interval_s=gdelt_interval_s)
            judged.append((r, age, held))
        ages = [a for _r, a, _h in judged if a is not None]
        never = sum(1 for _r, a, _h in judged if a is None)
        stale_rows = [r for r, a, h in judged if a is not None and a > STALE_POLL_INTERVALS * h]
        failing = [r for r in rows if int(r.get("consecutiveFailures") or 0) > 0]
        unhealthy = [r for r in rows if not r.get("healthy", True)]
        latencies = [float(r.get("lastLatencyMs") or 0.0) for r in rows
                     if r.get("lastSuccessAt")]
        entry = {"tracked": len(rows), "neverSucceeded": never, "failing": len(failing),
                 "unhealthy": len(unhealthy), "notOnSchedule": len(stale_rows),
                 "intervalS": iv,
                 "oldestSuccessAgeS": max(ages) if ages else None,
                 "gapSinceLastSuccessS": min(ages) if ages else None,
                 "lastSweepS": sum(latencies) / 1000.0 if latencies else None,
                 "failingRows": sorted(failing, key=lambda r: -int(r.get("consecutiveFailures") or 0)),
                 "staleRows": sorted(stale_rows, key=lambda r: r.get("lastSuccessAt") or "")}
        if kind == "rss":
            entry["scheduled"] = sum(1 for r in rows if r.get("intervalS"))
            entry["withValidators"] = sum(1 for r in rows if r.get("hasValidator"))
            entry["overflowSuspects"] = [
                r for r in rows
                if int(r.get("totalPolls") or 0) > 1
                and int(r.get("imported") or 0) >= OVERFLOW_MIN_IMPORTED
                and int(r.get("duplicate") or 0) == 0]
            entry["missingMetadata"] = sum(int(r.get("missingMetadata") or 0) for r in rows)
        out[kind] = entry
    return out


# --------------------------------------------------------------------------- #
# 3. Repeated re-ingestion — counters and catalog, both.
# --------------------------------------------------------------------------- #
def reingest_report(health: Iterable[dict], rows: list, *, poll_interval_s: float) -> dict:
    rss = [r for r in health if source_kind(r.get("feedUrl")) == "rss" and r.get("lastSuccessAt")]
    imported = sum(int(r.get("imported") or 0) for r in rss)
    duplicate = sum(int(r.get("duplicate") or 0) for r in rss)
    processed = imported + duplicate
    per_day = duplicate * 86400.0 / poll_interval_s if poll_interval_s > 0 else 0.0
    retouched = [r for r in rows if r.get("fetched") and r.get("created")
                 and (r["fetched"] - r["created"]).total_seconds() > RETOUCH_AFTER_H * 3600.0]
    longest = max(((r["fetched"] - r["created"]).total_seconds() / 3600.0 for r in retouched),
                  default=None)
    return {"feeds": len(rss), "importedLastCycle": imported, "duplicateLastCycle": duplicate,
            "duplicateShare": (duplicate / processed) if processed else None,
            "duplicatesPerDay": per_day,
            "retouched": len(retouched), "retouchedShare": (len(retouched) / len(rows)) if rows else None,
            "longestRetouchH": longest}


# --------------------------------------------------------------------------- #
# Verdicts — sentences derived from the numbers above, nothing else.
# --------------------------------------------------------------------------- #
def findings(lag: dict, outlets: list, cadence: dict, reingest: dict, *, hours: float,
             poll_interval_s: float, suspect: Optional[list] = None) -> list:
    out = []
    a = lag["all"]
    if a["n"] == 0:
        return [f"nothing was first-seen in the last {hours:g} h — ingestion is not running, or the "
                f"window is wrong"]
    if a["medianMin"] is not None:
        budget = poll_interval_s / 60.0
        if a["medianMin"] > budget:
            out.append(f"median discovery lag {a['medianMin']:.0f} min exceeds one full sweep "
                       f"({budget:.0f} min): articles are found later than the cadence alone explains")
        else:
            out.append(f"median discovery lag {a['medianMin']:.0f} min is within one sweep "
                       f"({budget:.0f} min)")
    if a["dated"]:
        slow = a["over360"] / a["dated"]
        if slow >= 0.10:
            out.append(f"{slow * 100:.0f}% of dated rows were found more than 6 h after publication")
    if a["n"] and a["archive"] / a["n"] >= 0.10:
        out.append(f"{a['archive']} of {a['n']} rows ({a['archive'] / a['n'] * 100:.0f}%) were "
                   f"published BEFORE the window began — old articles are being admitted as new")
    if a["n"] and a["undated"] / a["n"] >= 0.10:
        out.append(f"{a['undated']} of {a['n']} rows ({a['undated'] / a['n'] * 100:.0f}%) carry no "
                   f"publication date, so their lag is unknowable and they never sort as fresh")
    for kind, label in (("rss", "RSS feeds"), ("crawl", "crawl hosts"), ("api", "API adapters")):
        c = cadence.get(kind) or {}
        if not c.get("tracked"):
            continue
        if c["notOnSchedule"]:
            out.append(f"{c['notOnSchedule']} of {c['tracked']} {label} have not succeeded within "
                       f"{STALE_POLL_INTERVALS:g}x their interval")
        if c["failing"]:
            out.append(f"{c['failing']} {label} are failing right now ({c['unhealthy']} marked unhealthy)")
    rss = cadence.get("rss") or {}
    if rss.get("lastSweepS") is not None and rss["lastSweepS"] > 0.5 * poll_interval_s:
        out.append(f"the last RSS sweep took {rss['lastSweepS']:.0f} s of a {poll_interval_s:.0f} s "
                   f"interval — fetched serially under the ingest lock, so the real cadence is "
                   f"{poll_interval_s + rss['lastSweepS']:.0f} s and every other adapter waits")
    if rss.get("overflowSuspects"):
        out.append(f"{len(rss['overflowSuspects'])} feed(s) listed ONLY unseen entries on their last "
                   f"poll — they may publish more per interval than they list (overflow)")
    if reingest["duplicateShare"] is not None and reingest["duplicateShare"] >= 0.5:
        out.append(f"{reingest['duplicateShare'] * 100:.0f}% of entries processed per sweep were "
                   f"already held (~{reingest['duplicatesPerDay']:,.0f} re-ingestions/day) — "
                   f"the cost conditional GET (RWE_FEED_SCHEDULER=1) removes")
    for g in (suspect or [])[:3]:
        out.append(f"{g['rows']} rows from {g['sourceType']} {g['sourceFeed'] or '-'} carry "
                   f"publication dates a median {g['medianLagDays']:.0f} d before first sight "
                   f"({', '.join(g['publishers']) or '-'}) — unusable as freshness")
    flagged = [o for o in outlets if o["flags"]]
    if flagged:
        by_flag = defaultdict(int)
        for o in flagged:
            for f in o["flags"]:
                by_flag[f] += 1
        out.append("outlets flagged: " + ", ".join(f"{k} {v}" for k, v in sorted(by_flag.items())))
    return out


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _m(v) -> str:
    return "-" if v is None else (f"{v:.0f}" if v < 100 else f"{v / 60:.1f}h")


def _age(h) -> str:
    if h is None:
        return "never"
    return f"{h * 60:.0f}m" if h < 1 else (f"{h:.1f}h" if h < 48 else f"{h / 24:.1f}d")


def render(cfg: list, lag: dict, outlets: list, cadence: dict, reingest: dict, verdicts: list, *,
           hours: float, top: int, poll_interval_s: float, suspect: Optional[list] = None) -> str:
    suspect = suspect or []
    L = []
    L.append("=== configuration in effect (env as this process sees it) ===")
    for k in cfg:
        shown = k["value"] if k["value"] != "" else f"(default {k['default'] or 'unset'})"
        L.append(f"  {k['name']:<26} {shown:<20} {k['what']}")

    L.append(f"\n=== 1. ingestion delay — rows first-seen in the last {hours:g} h ===")
    L.append(f"  {'source':<10} {'rows':>7} {'dated':>7} {'undated':>7} {'archive':>7} "
             f"{'median':>7} {'p90':>7} {'max':>7} {'>1h':>6} {'>6h':>6} {'>24h':>6}")
    for name, s in [("ALL", lag["all"])] + list(lag["bySource"].items()):
        L.append(f"  {name:<10} {s['n']:>7,} {s['dated']:>7,} {s['undated']:>7,} {s['archive']:>7,} "
                 f"{_m(s['medianMin']):>7} {_m(s['p90Min']):>7} {_m(s['maxMin']):>7} "
                 f"{s['over60']:>6,} {s['over360']:>6,} {s['over1440']:>6,}")
    L.append("  lag = first-seen minus published (minutes; h above 100). archive = published before "
             "the window began.")

    shown = outlets[:top]
    extra = [o for o in outlets[top:] if o["flags"]]
    L.append(f"\n=== per-outlet freshness (top {len(shown)} by volume"
             f"{f' + {len(extra)} flagged' if extra else ''}) ===")
    L.append(f"  {'rows':>5} {'/day':>6} {'seen':>7} {'pub':>7} {'median':>7} {'p90':>7} "
             f"{'arch':>5} {'undtd':>5}  {'via':<18} outlet")
    for o in shown + extra:
        flags = ("  <- " + " ".join(o["flags"])) if o["flags"] else ""
        via = "+".join(o["sources"])[:18]
        L.append(f"  {o['n']:>5} {o['perDay']:>6.1f} {_age(o['hoursSinceSeen']):>7} "
                 f"{_age(o['hoursSincePublished']):>7} {_m(o['medianMin']):>7} {_m(o['p90Min']):>7} "
                 f"{o['archive']:>5} {o['undated']:>5}  {via:<18} {o['publisher'][:40]}{flags}")
    L.append("  seen = since the outlet's newest first-seen row; pub = since its newest publication "
             "date; via = every source type that supplied a row.")
    L.append(f"  STALE: quiet for > max({STALE_FLOOR_H:g} h, {STALE_GAPS:g}x its own mean gap). "
             f"LAGGY: median lag > {LAGGY_INTERVALS:g} sweeps. ARCHIVE: >= {ARCHIVE_SHARE:.0%} "
             f"published before the window. UNDATED: >= {UNDATED_SHARE:.0%} without a date.")

    L.append("\n=== 2. cadence as observed (feed_health) ===")
    for kind, label in (("rss", "RSS feeds"), ("crawl", "crawl hosts"), ("api", "API adapters")):
        c = cadence.get(kind) or {}
        if not c.get("tracked"):
            L.append(f"  {label:<13} none tracked")
            continue
        gap = c["gapSinceLastSuccessS"]
        L.append(f"  {label:<13} tracked {c['tracked']:>4}  failing {c['failing']:>3}  "
                 f"unhealthy {c['unhealthy']:>3}  never-ok {c['neverSucceeded']:>3}  "
                 f"off-schedule {c['notOnSchedule']:>3}  "
                 f"last success {_age(gap / 3600.0) if gap is not None else 'never':>6} ago  "
                 f"(interval {c['intervalS']:.0f} s)")
        if kind == "rss":
            sweep = c["lastSweepS"]
            L.append(f"  {'':<13} last sweep ~{sweep:.0f} s serial fetch under the ingest lock; "
                     f"scheduler state on {c['scheduled']}/{c['tracked']} feeds "
                     f"(validators on {c['withValidators']}); "
                     f"missing-metadata entries last cycle {c['missingMetadata']}"
                     if sweep is not None else f"  {'':<13} no completed sweep recorded")
            for r in c["overflowSuspects"][:10]:
                L.append(f"      OVERFLOW? imported {r.get('imported')} / duplicate 0 last poll  "
                         f"{(r.get('name') or r.get('feedUrl') or '')[:60]}")
        for r in c["failingRows"][:8]:
            L.append(f"      failing x{int(r.get('consecutiveFailures') or 0):<3} "
                     f"{(r.get('name') or r.get('feedUrl') or '')[:44]:<44} "
                     f"{str(r.get('lastError') or '')[:60]}")
        for r in c["staleRows"][:8]:
            L.append(f"      off-schedule  last ok {r.get('lastSuccessAt')}  "
                     f"{(r.get('name') or r.get('feedUrl') or '')[:50]}")

    L.append("\n=== 3. repeated re-ingestion ===")
    if reingest["feeds"]:
        share = reingest["duplicateShare"]
        L.append(f"  last sweep over {reingest['feeds']} feeds: imported {reingest['importedLastCycle']:,}, "
                 f"already held {reingest['duplicateLastCycle']:,}"
                 f"{f' ({share * 100:.0f}%)' if share is not None else ''}"
                 f"  ->  ~{reingest['duplicatesPerDay']:,.0f} re-ingestions/day at "
                 f"{poll_interval_s:.0f} s")
    else:
        L.append("  no RSS feed has a recorded sweep")
    if reingest["retouchedShare"] is not None:
        L.append(f"  catalog: {reingest['retouched']:,} of the window's rows "
                 f"({reingest['retouchedShare'] * 100:.0f}%) were re-touched > {RETOUCH_AFTER_H:g} h "
                 f"after first sight (longest {_age(reingest['longestRetouchH'])})")
    L.append("  a re-ingestion is one scoring-cache read + one fetched_at write under the ingest "
             "lock; the row itself is never duplicated.")

    L.append(f"\n=== 4. suspect publication dates (>= {SUSPECT_DATE_DAYS:g} d before first sight) ===")
    if not suspect:
        L.append("  none")
    for g in suspect:
        pubs = ", ".join(g["publishers"]) or "-"
        L.append(f"  {g['rows']:>5} rows  median {g['medianLagDays']:>7.0f} d  {g['sourceType']:<11} "
                 f"{(g['sourceFeed'] or '-')[:60]}")
        L.append(f"        outlets: {pubs[:70]}   e.g. {g['example'][:80]}")
    L.append("  a date this old on a newly-seen row is a feed stamping dates we cannot use, or an "
             "archive being re-discovered; either way the article never sorts as fresh.")

    L.append("\n=== findings ===")
    for v in verdicts:
        L.append(f"  - {v}")
    return "\n".join(L)


def run(store_, *, hours: float = 24.0, top: int = 25, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    rows = window_rows(store_, since)
    try:
        health = scheduler_state(store_, store_.list_feed_health())
    except Exception:
        health = []
    pi, ci, gi = poll_interval(), crawl_interval(), gdelt_interval()
    lag = lag_report(rows, since=since)
    outlets = outlet_report(rows, now=now, since=since, hours=hours, poll_interval_s=pi)
    cadence = cadence_report(health, now=now, poll_interval_s=pi, crawl_interval_s=ci,
                             gdelt_interval_s=gi)
    reingest = reingest_report(health, rows, poll_interval_s=pi)
    suspect = suspect_dates_report(rows)
    verdicts = findings(lag, outlets, cadence, reingest, hours=hours, poll_interval_s=pi,
                        suspect=suspect)
    return {"config": config_in_effect(), "lag": lag, "outlets": outlets, "cadence": cadence,
            "reingest": reingest, "suspect": suspect, "findings": verdicts, "hours": hours,
            "top": top, "pollIntervalS": pi}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    ap.add_argument("--hours", type=float, default=24.0, help="window of first-seen rows")
    ap.add_argument("--top", type=int, default=25, help="outlets to print by volume")
    args = ap.parse_args(argv)
    st = store_mod.Store(args.db)
    r = run(st, hours=args.hours, top=args.top)
    print(render(r["config"], r["lag"], r["outlets"], r["cadence"], r["reingest"], r["findings"],
                 hours=r["hours"], top=r["top"], poll_interval_s=r["pollIntervalS"],
                 suspect=r["suspect"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
