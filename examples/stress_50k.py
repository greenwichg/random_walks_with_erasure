"""stress_50k.py — the staged 50,000-source pre-beta stress harness.

Runs the REAL registry, poller, store, corpus projection and clustering path against synthetic
sources, at 100 / 1k / 5k / 10k / 25k / 50k, and reports every metric in `docs/STRESS_50K_PLAN.md`
§4.2 against the hard thresholds in §4.3.

## Offline by default, and that is a safety property

A 50,000-source test must not make 50,000 real requests to real publishers. That would be an
unauthorised crawl at a scale no robots.txt review has covered, and from the receiving end it is
indistinguishable from an attack. **Every fetch in this harness is synthetic**, served from memory
by `_SyntheticFetch`; the network is never touched, and there is no flag here that changes that.
Real admitted sources are exercised the way M7 admits them — small, authorised cohorts, after the
ToS review — through the existing `audit_source_discovery.py --probe`, not through this file.

## Never touches production

The store is a temp file created per cohort and deleted after. `--db` is deliberately absent: there
is no argument that can point this at the production catalog, because a harness that ingests a
million synthetic articles needs to be incapable of doing it to real data.

## What is real

Everything except the socket:

* `sources.SourceRegistry` / `MultiSourcePoller` — the production scheduler, including the M6.3
  pool, leases, backoff and the ingest lock.
* `crawler.CrawlAdapter` — the adapter class that has to reach thousands of instances, with its
  real `collect`/`persist` split.
* `store.Store` + `rss_ingest.ingest_entries` — the real write path.
* `corpus.tier_of` — the real tier decision, which is what makes the isolation measurement mean
  something.

Usage::

    python examples/stress_50k.py --cohorts 100,1000            # a quick pass
    python examples/stress_50k.py                               # the full ladder
    python examples/stress_50k.py --cohorts 50000 --seconds 120 # one big cohort, longer
    python examples/stress_50k.py --json out.json               # machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import resource
import shutil
import statistics
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field, asdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

DEFAULT_COHORTS = (100, 1_000, 5_000, 10_000, 25_000, 50_000)

#: Articles a synthetic source yields per poll. Anchored on the live crawl probe: kait8 and kwch
#: returned 27 and 35 URLs from a 7-day news sitemap, of which 1-2 were new per cycle.
ARTICLES_PER_POLL = 2

#: Thresholds from docs/STRESS_50K_PLAN.md §4.3. A cohort failing any HARD one stops the campaign:
#: a larger cohort cannot be informative once a smaller one has broken an invariant.
HARD = {
    "shadow_leak_rows": (0, "any Tier A row from a shadow source"),
    "lock_occupancy_pct": (80.0, "lock occupancy at or above 80%"),
    "starved_sources": (0, "a source unpolled after two of its own intervals"),
    "concurrency_overrun": (0, "peak in-flight above the configured pool size"),
}
SOFT = {
    "lock_occupancy_pct": 50.0,
    "poll_failure_pct": 5.0,
    "p95_poll_ms": 1000.0,
    "peak_rss_mb": 1024.0,
}


def _sources_base():
    """`sources.SourceAdapter`, imported lazily so the module docstring stays readable at the top."""
    import sources
    return sources.SourceAdapter


# --------------------------------------------------------------------------- synthetic transport

class _SyntheticFetch:
    """A crawl discovery document, served from memory. Never opens a socket.

    Deterministic per source and per cycle so a cohort is reproducible, and cheap enough that the
    harness measures the POLLER rather than its own article generator.
    """

    def __init__(self, articles_per_poll: int = ARTICLES_PER_POLL, latency_s: float = 0.0):
        self.articles_per_poll = articles_per_poll
        self.latency_s = latency_s
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, host: str, cycle: int) -> list:
        with self._lock:
            self.calls += 1
        if self.latency_s:
            time.sleep(self.latency_s)          # model a slow publisher without being one
        now = time.time()
        return [
            {"url": f"https://{host}/2026/08/26/story-{cycle}-{i}/",
             "title": f"{host} filing {cycle}-{i} briefing notice dossier",
             "published_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - i * 60))}
            for i in range(self.articles_per_poll)
        ]


class StressAdapter(_sources_base()):
    """A source with the shape the poller cares about, and a synthetic collect().

    Not a `crawler.CrawlAdapter` subclass — that class's `fetch` builds a real `PublisherCrawler`.
    It DOES inherit `SourceAdapter`, which is the point: the contract under test is the poller's
    (`collect` off the lock, `persist` on it, health, backoff, leases), and inheriting the base
    keeps the M6.2 split real rather than mocked away.

    The first draft of this class did not inherit, and every poll raised `AttributeError` inside
    the pool's catch — surfacing as "0 polls, 100 starved sources", which reads as a scheduler
    finding rather than a harness fault. That mismatch also exposed a real defect in the M6.2 gate
    (`sources.py`), which used a non-defensive attribute lookup for `poll_once` beside a defensive
    one for the flag.
    """
    source_type = "crawl"
    FETCH_IS_STORE_FREE = True                   # the same opt-in CrawlAdapter declares (M6.2)

    def __init__(self, host: str, fetcher: _SyntheticFetch, interval_s: float,
                 fail_every: int = 0):
        self.provider = host
        self.host = host
        self._fetch = fetcher
        self._interval = interval_s
        self._fail_every = fail_every
        self.cycle = 0

    @property
    def health_key(self) -> str:
        return f"crawl://{self.host}"

    def enabled(self) -> bool:
        return True

    def interval(self) -> float:
        return self._interval

    def max_articles(self):
        return None

    def fetch(self):
        self.cycle += 1
        if self._fail_every and self.cycle % self._fail_every == 0:
            raise RuntimeError(f"synthetic outage on {self.host}")
        return self._fetch(self.host, self.cycle)

    def normalize(self, raw):
        import rss_ingest
        import sources as _s
        entries = [rss_ingest.FeedEntry(url=r["url"], title=r["title"], description="d",
                                        body=None, published_at=r["published_at"])
                   for r in raw]
        return _s.SourceBatch(provider=self.provider, source_type=self.source_type,
                              fetched_at=_s._now_iso(), entries=entries, raw_count=len(entries))


# --------------------------------------------------------------------------- instrumentation

@dataclass
class CohortResult:
    sources: int
    workers: int
    seconds: float
    polls: int = 0
    failures: int = 0
    poll_ms: list = field(default_factory=list, repr=False)
    fetch_ms: list = field(default_factory=list, repr=False)
    lock_held_s: float = 0.0
    peak_inflight: int = 0
    concurrency_overrun: int = 0
    starved_sources = 0            # None = window too short to judge
    sources_polled: int = 0
    catalog_rows: int = 0
    tier_a_rows: int = 0
    shadow_leak_rows: int = 0
    clustering_ms: float = 0.0
    fetch_ms_cluster: float = 0.0
    tier_a_scanned: int = 0
    warm_ms: float = 0.0
    warm_stood_down: bool = False
    warm2_ms: float = 0.0
    db_bytes: int = 0
    peak_rss_mb: float = 0.0
    cpu_s: float = 0.0
    registry_build_s: float = 0.0
    exclusion_sql_ms: float = 0.0

    def summary(self) -> dict:
        wall = max(self.seconds, 1e-9)
        return {
            "sources": self.sources,
            "workers": self.workers,
            "polls": self.polls,
            "polls_per_s": round(self.polls / wall, 1),
            "poll_failure_pct": round(100.0 * self.failures / max(1, self.polls), 2),
            "p50_poll_ms": round(statistics.median(self.poll_ms), 1) if self.poll_ms else 0.0,
            "p95_poll_ms": round(_p95(self.poll_ms), 1),
            "p95_fetch_ms": round(_p95(self.fetch_ms), 1),
            "lock_occupancy_pct": round(100.0 * self.lock_held_s / wall, 1),
            "peak_inflight": self.peak_inflight,
            "concurrency_overrun": self.concurrency_overrun,
            "starved_sources": self.starved_sources,
            "sources_polled": self.sources_polled,
            "coverage_pct": round(100.0 * self.sources_polled / max(1, self.sources), 1),
            "catalog_rows": self.catalog_rows,
            "tier_a_rows": self.tier_a_rows,
            "shadow_leak_rows": self.shadow_leak_rows,
            "cluster_fetch_ms": round(self.fetch_ms_cluster, 1),
            "cluster_build_ms": round(self.clustering_ms, 1),
            "cluster_rows_in": self.tier_a_scanned,
            "warm_wrapper_ms": round(self.warm_ms, 1),
            "warm_stood_down": self.warm_stood_down,
            "warm2_ms": round(self.warm2_ms, 1),
            "db_mb": round(self.db_bytes / 1e6, 1),
            "bytes_per_article": round(self.db_bytes / max(1, self.catalog_rows)),
            "peak_rss_mb": round(self.peak_rss_mb, 1),
            "cpu_pct": round(100.0 * self.cpu_s / wall, 1),
            "registry_build_s": round(self.registry_build_s, 2),
            "exclusion_sql_ms": round(self.exclusion_sql_ms, 1),
        }


def _p95(xs) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(0.95 * len(s)))]


def _rss_mb() -> float:
    # ru_maxrss is KiB on Linux, bytes on macOS. Only Linux is a deployment target here.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


# --------------------------------------------------------------------------- the cohort run

def run_cohort(n: int, *, seconds: float, workers: int, interval_s: float, fail_every: int,
               fetch_latency: float, verbose: bool = True) -> CohortResult:
    """One cohort, end to end, against a throwaway store."""
    import corpus
    import sources
    import store as store_mod

    tmp = tempfile.mkdtemp(prefix=f"stress50k_{n}_")
    db = os.path.join(tmp, "stress.db")
    res = CohortResult(sources=n, workers=workers, seconds=seconds)
    try:
        hosts = [f"s{i:05d}.stress.example" for i in range(n)]
        # EVERY synthetic source is shadow. That is the configuration under test: 50k sources must
        # be able to exist without one of them reaching Tier A, and `corpus` decides that from the
        # env exactly as it does in production.
        os.environ["RWE_CORPUS_SHADOW"] = ",".join(hosts)
        corpus._index.cache_clear()
        os.environ["RWE_POLL_WORKERS"] = str(workers)
        os.environ["RWE_SOURCE_BACKOFF_AFTER"] = "3"

        t0 = time.perf_counter()
        fetcher = _SyntheticFetch(latency_s=fetch_latency)
        registry = sources.SourceRegistry()
        for h in hosts:
            registry.register(StressAdapter(h, fetcher, interval_s, fail_every=fail_every))
        res.registry_build_s = time.perf_counter() - t0

        st = store_mod.Store(f"sqlite:///{db}")
        import rss_ingest
        inflight, guard = [0], threading.Lock()
        events, errs = [], []

        def _log(level, event, **fields):
            if event in ("source_poll", "post_cycle", "multi_source_start"):
                events.append((event, fields))
            elif event == "source_poll_cycle_failed":
                # NEVER swallow this. The pool catches adapter exceptions by design, so a
                # harness that ignored the event would report "0 polls" as a scheduler
                # finding when it is actually its own broken adapter. That happened on the
                # first run of this file.
                errs.append(fields.get("error"))

        poller = sources.MultiSourcePoller(st, rss_ingest.make_scorer(), registry=registry,
                                           log=_log)
        real_poll = poller.poll_adapter_once

        def _counted(adapter):
            with guard:
                inflight[0] += 1
                res.peak_inflight = max(res.peak_inflight, inflight[0])
                if inflight[0] > workers:
                    res.concurrency_overrun += 1
            try:
                return real_poll(adapter)
            finally:
                with guard:
                    inflight[0] -= 1

        poller.poll_adapter_once = _counted
        cpu0 = time.process_time()
        wall0 = time.perf_counter()
        poller.start()
        time.sleep(seconds)
        poller.stop(join_timeout=30.0)
        res.seconds = time.perf_counter() - wall0
        res.cpu_s = time.process_time() - cpu0
        res.peak_rss_mb = _rss_mb()

        polled = set()
        for event, f in events:
            if event != "source_poll":
                continue
            res.polls += 1
            res.failures += int(f.get("failed") or 0)
            res.poll_ms.append(float(f.get("pollMs") or 0.0))
            res.fetch_ms.append(float(f.get("fetchMs") or 0.0))
            res.lock_held_s += (float(f.get("pollMs") or 0.0)
                                + float(f.get("postCycleMs") or 0.0)) / 1000.0
            polled.add(f.get("provider"))
        # Starvation is only meaningful once a source has HAD two intervals to be served in — and
        # when it is NOT meaningful the answer must be "not measured", never 0.
        #
        # The first version left the field at its 0 default when the window was too short. At 50,000
        # sources the interval is 20,000 s and the window 25 s, so the check never ran and the
        # harness printed `starved_sources 0` — a PASS on an invariant it had not tested. That is
        # the same "a gate that cannot fire reads as a gate that passed" failure this codebase has
        # found ten times in its own instruments, reproduced here in the instrument built to find it.
        if res.seconds >= 2 * interval_s:
            res.starved_sources = sum(1 for h in hosts if h not in polled)
        else:
            res.starved_sources = None
        res.sources_polled = len(polled)

        if errs and not res.polls:
            print(f"    !! every poll raised — this is a HARNESS fault, not a finding:")
            print(f"       {errs[0]}")
        res.catalog_rows = st.count_feed_articles()
        res.tier_a_rows, res.shadow_leak_rows = _tier_census(st, corpus)
        res.exclusion_sql_ms = _time_exclusion_query(st, corpus)
        c = _time_clustering(st)
        res.fetch_ms_cluster = c["fetch_ms"]
        res.clustering_ms = c["build_ms"]
        res.tier_a_scanned = c["rows"]
        res.warm_ms = c["warm_ms"]
        res.warm_stood_down = c["warm_stood_down"]
        res.warm2_ms = c["warm2_ms"]
        res.db_bytes = sum(os.path.getsize(p) for p in pathlib.Path(tmp).glob("stress.db*"))
        return res
    finally:
        os.environ.pop("RWE_CORPUS_SHADOW", None)
        shutil.rmtree(tmp, ignore_errors=True)


def _tier_census(st, corpus) -> "tuple[int, int]":
    """Tier A rows, and how many of them came from a source that must be in shadow.

    The second number is the whole premise of the design. It is computed through `corpus.tier_of`
    — the documented authority — rather than through the SQL prefilter, so a disagreement between
    the two shows up as a leak rather than being hidden by the optimisation.
    """
    from store import FeedArticle
    from sqlalchemy import select
    tier_a = leak = 0
    with st.session() as s:
        for pub, url in s.execute(select(FeedArticle.publisher, FeedArticle.url)).all():
            if corpus.tier_of(pub, url) == "A":
                tier_a += 1
                if ".stress.example" in str(url or ""):
                    leak += 1
    return tier_a, leak


def _time_exclusion_query(st, corpus) -> float:
    """Cost of the tier prefilter's NOT IN at this cohort size (plan §3.5).

    Checked rather than trusted: 50,000 terms measured 57 ms on SQLite 3.45.1 with a 250,000
    variable limit, but the production build may cap lower, and `sorted()` over the set runs per
    call.
    """
    t0 = time.perf_counter()
    try:
        st.search_feed_articles(exclude_publishers=corpus.sql_exclusions())
    except Exception as e:                       # a variable-limit failure IS the finding
        print(f"    !! exclusion query FAILED: {type(e).__name__}: {e}")
        return -1.0
    return (time.perf_counter() - t0) * 1000.0


def _time_clustering(st) -> dict:
    """Attribute the clustering cost to its actual parts. NOT via `warm_cache` alone.

    The first version of this timed `warm_cache` and reported 24.6 ms at 1k sources against
    6,691 ms at 5k — 2.5x the rows for 272x the time, which is neither linear nor a fixed
    subprocess spawn. The measurement was unsound: `warm_cache` SINGLE-FLIGHTS on a module-level
    lock and returns `None` in microseconds when it stands down, so the number was partly "did this
    call win the lock", not "what does clustering cost".

    Measured here instead, each on its own:

    * ``fetch_ms`` / ``rows`` — `_fetch`, which applies the tier prefilter. With every source in
      shadow this must return 0, and that is the isolation result.
    * ``build_ms`` — `build_stories` over exactly those rows. This is the real clustering cost, and
      over 0 rows it must be ~0. If it is not, isolation is not buying what it claims.
    * ``warm_ms`` / ``warm_stood_down`` — the wrapper, reported separately so its overhead
      (fingerprint, event inputs, subprocess spawn) is visible as overhead rather than misread as
      clustering.
    """
    import story_service
    out = {"fetch_ms": -1.0, "rows": 0, "build_ms": -1.0, "warm_ms": -1.0,
           "warm_stood_down": False, "warm2_ms": -1.0}
    t0 = time.perf_counter()
    try:
        rows = story_service._fetch(st, report_out={})
    except Exception as e:
        print(f"    !! clustering FETCH failed: {type(e).__name__}: {e}")
        return out
    out["fetch_ms"] = (time.perf_counter() - t0) * 1000.0
    out["rows"] = len(rows)

    t1 = time.perf_counter()
    try:
        story_service.build_stories(rows, min_articles=2, min_publishers=2)
    except Exception as e:
        print(f"    !! clustering BUILD failed: {type(e).__name__}: {e}")
        return out
    out["build_ms"] = (time.perf_counter() - t1) * 1000.0

    t2 = time.perf_counter()
    try:
        n = story_service.warm_cache(st)
    except Exception as e:
        print(f"    !! warm FAILED: {type(e).__name__}: {e}")
        return out
    out["warm_ms"] = (time.perf_counter() - t2) * 1000.0
    out["warm_stood_down"] = n is None

    # A SECOND warm, immediately. This is what separates a one-time cost from a scaling one:
    # `build_subprocess_enabled` is ON by default and its worker is documented as persistent —
    # "spawn cost and the api_server import are paid once, not per build". If warm #2 is cheap, the
    # first number was startup and B7 is not a 50k blocker. If both are slow, the wrapper really is
    # doing catalogue-proportional work per warm and it is.
    t3 = time.perf_counter()
    try:
        story_service.warm_cache(st)
        out["warm2_ms"] = (time.perf_counter() - t3) * 1000.0
    except Exception:
        out["warm2_ms"] = -1.0
    return out


# --------------------------------------------------------------------------- verdicts

def check(summary: dict) -> "tuple[list, list]":
    hard, soft = [], []
    for key, (limit, why) in HARD.items():
        v = summary.get(key, 0)
        if key == "lock_occupancy_pct":
            if v >= limit:
                hard.append(f"{why}: {v}%")
        elif v is None:
            soft.append(f"{key} NOT MEASURED — the window was too short to judge it. This is not "
                        f"a pass.")
        elif v > limit:
            hard.append(f"{why}: {v}")
    for key, limit in SOFT.items():
        v = summary.get(key, 0)
        if v is not None and v > limit and not any(key in h for h in hard):
            soft.append(f"{key} {v} > {limit}")
    return hard, soft


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cohorts", default=",".join(str(c) for c in DEFAULT_COHORTS),
                    help="comma-separated source counts (default: the full ladder)")
    ap.add_argument("--seconds", type=float, default=20.0,
                    help="wall seconds to poll per cohort (default %(default)s)")
    ap.add_argument("--workers", type=int, default=16,
                    help="poll pool size; plan §3.2 derives ~16 for 50k (default %(default)s)")
    ap.add_argument("--target-rate", type=float, default=2.5,
                    help="POLLS PER SECOND the cohort should demand, held constant across cohort "
                         "sizes. The per-source interval is derived as N/rate, which is the whole "
                         "point: a FIXED interval conflates 'more sources' with 'an impossible "
                         "poll rate' — 1,000 sources at a 5 s interval demands 200 polls/s, where "
                         "50,000 sources at the interval plan §3.1 derives demands 2.5. Every "
                         "cohort above ~500 then fails on lock occupancy as a pure artifact, which "
                         "measures the compression rather than the system (default %(default)s, "
                         "matching the plan's 9,000 polls/hour lock budget)")
    ap.add_argument("--interval", type=float, default=0.0,
                    help="override the derived per-source interval (seconds). 0 = derive from "
                         "--target-rate, which is what you want unless you are deliberately "
                         "probing the saturation point")
    ap.add_argument("--fail-every", type=int, default=0,
                    help="make every Nth cycle of every source raise, to exercise backoff and "
                         "lease release (0 = no injected faults)")
    ap.add_argument("--fetch-latency", type=float, default=0.0,
                    help="synthetic seconds per fetch, to model slow publishers")
    ap.add_argument("--json", help="write the full result set here")
    args = ap.parse_args(argv)

    cohorts = [int(c) for c in args.cohorts.split(",") if c.strip()]
    print("=== 50k pre-beta stress harness ===")
    print(f"  OFFLINE: every fetch is synthetic. No publisher is contacted by this file.")
    print(f"  cohorts={cohorts} seconds={args.seconds} workers={args.workers}")
    if args.interval:
        print(f"  interval={args.interval}s (FIXED override — poll rate varies with cohort size)")
    else:
        print(f"  target-rate={args.target_rate} polls/s, interval derived as N/rate")
    print()

    out, stopped = [], False
    for n in cohorts:
        print(f"--- cohort {n:,} sources " + "-" * 40)
        interval = args.interval or max(1.0, n / max(0.01, args.target_rate))
        print(f"    (interval {interval:,.0f}s/source ⇒ {n / interval:.1f} polls/s demanded)")
        res = run_cohort(n, seconds=args.seconds, workers=args.workers,
                         interval_s=interval, fail_every=args.fail_every,
                         fetch_latency=args.fetch_latency)
        s = res.summary()
        out.append(s)
        for k in ("registry_build_s", "polls", "polls_per_s", "poll_failure_pct", "p50_poll_ms",
                  "p95_poll_ms", "p95_fetch_ms", "lock_occupancy_pct", "peak_inflight",
                  "starved_sources", "sources_polled", "coverage_pct", "catalog_rows", "tier_a_rows", "shadow_leak_rows",
                  "cluster_fetch_ms", "cluster_build_ms", "cluster_rows_in", "warm_wrapper_ms", "warm2_ms", "warm_stood_down", "db_mb", "bytes_per_article", "peak_rss_mb", "cpu_pct",
                  "exclusion_sql_ms"):
            print(f"    {k:<22} {s[k]}")
        hard, soft = check(s)
        for w in soft:
            print(f"    ~ SOFT: {w}")
        if hard:
            print(f"    *** HARD FAILURE at {n:,} sources:")
            for h in hard:
                print(f"        - {h}")
            print(f"    Campaign STOPS here. A larger cohort cannot be informative once a smaller")
            print(f"    one has broken an invariant — it can only break it worse.")
            stopped = True
            break
        print(f"    PASS")
        print()

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(out, indent=2))
        print(f"\nwrote {args.json}")
    return 1 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
