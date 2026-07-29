"""bench_cache_warm.py — how many story-cache rebuilds does one polling window actually cost?

Drives the REAL ``MultiSourcePoller`` with fake adapters over a real store, so the thing under
measurement is the production code path — the poller lock, ``_post_cycle``, ``storage_lifecycle``,
``warm_cache`` and the fingerprint-keyed cache — not a model of it.

It reports the five quantities that decide whether coalescing is worth doing:

* **rebuilds** — how many times the story set was actually clustered,
* **redundant rebuilds** — how many produced a result that was invalidated before any reader could
  have used it, which is the definition of waste here,
* **build CPU** — total seconds spent clustering,
* **ingest blocking** — how long adapters spent waiting on the poller lock while a warm ran,
* **reader latency** — measured with a real reader issuing requests throughout the window.

The last one is the honest counterweight: coalescing trades reader-facing warmth for CPU, and a
report that showed only the CPU win would be advocacy rather than measurement.

    python examples/bench_cache_warm.py                    # default: 6 providers, 120 s window
    python examples/bench_cache_warm.py --window 300 --providers 8
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import perf_profile as pp                                   # noqa: E402  (corpus generator)


class FakeAdapter:
    """A provider that ingests a fixed number of new articles per cycle, at a fixed interval.

    Deliberately not a subclass of SourceAdapter: the poller only ever touches this surface, and
    keeping the fake narrow makes it obvious that nothing about the real ingestion path (HTTP,
    parsing, scoring) is being measured — only the cache lifecycle downstream of a write.
    """

    def __init__(self, provider: str, interval_s: float, per_cycle: int, rng):
        self.provider = provider
        self.source_type = provider.lower()
        self._interval = interval_s
        self._per_cycle = per_cycle
        self._rng = rng
        self._n = 0
        self.cycles = 0

    def enabled(self) -> bool:
        return True

    def interval(self) -> float:
        return self._interval

    def max_articles(self):
        return None

    def config_warning(self):
        return None

    @property
    def health_key(self) -> str:
        return f"{self.source_type}://{self.provider.lower()}"

    def poll_once(self, store_, scorer, *, on_feed=None) -> dict:
        """Write `per_cycle` genuinely-new articles, exactly as a real adapter's ingest would."""
        self.cycles += 1
        rows = pp.synth_catalog(self._per_cycle, events=2, cluster_size=2,
                                seed=self._rng.randint(0, 10**9))
        new = 0
        for i, r in enumerate(rows):
            url = f"https://{self.source_type}-{self._n}-{i}.example.com/a"
            self._n += 1
            try:
                if store_.upsert_feed_article(
                        canonical_url=url, url=url, publisher=r["publisher"],
                        source_publisher=None, title=r["title"], description=r["description"],
                        body=None, published_at=r["publishedAt"], source_feed="bench",
                        scored=r["scored"], country=r.get("country"), language="en"):
                    new += 1
            except Exception:
                pass
        if on_feed is not None:
            try:
                on_feed(self.provider, self.health_key, {"new": new}, 1.0, None)
            except Exception:
                pass
        return {"new": new, "duplicates": 0, "failed": 0}


class Recorder:
    """Wraps story_service so every clustering build is counted and timed, and records WHEN each
    cache key became live and when it was invalidated — which is what makes 'redundant' measurable
    rather than asserted."""

    def __init__(self, story_service):
        self.ss = story_service
        self.builds = []                 # (t_start, t_end, fingerprint)
        self.invalidations = []          # (t, fingerprint)
        self._lock = threading.Lock()
        self._real_build = story_service.build_stories
        self._t0 = time.perf_counter()

    def now(self) -> float:
        return time.perf_counter() - self._t0

    def install(self, store_):
        real_build = self._real_build

        def counting(*a, **kw):
            t0 = self.now()
            out = real_build(*a, **kw)
            with self._lock:
                self.builds.append((t0, self.now(), None))
            return out
        self.ss.build_stories = counting

        real_fp = store_.catalog_fingerprint
        last = {"fp": None}

        def watched():
            fp = real_fp()
            with self._lock:
                if last["fp"] is not None and fp != last["fp"]:
                    self.invalidations.append((self.now(), fp))
                last["fp"] = fp
            return fp
        store_.catalog_fingerprint = watched

    def restore(self):
        self.ss.build_stories = self._real_build

    def report(self, window: float) -> dict:
        with self._lock:
            builds = list(self.builds)
            inval = list(self.invalidations)
        cpu = sum(e - s for s, e, _ in builds)
        # A build is REDUNDANT when the catalog was invalidated again before the build even
        # finished, or within `grace` after it — its result had no realistic chance of serving a
        # reader before becoming unreachable.
        grace = 2.0
        redundant = 0
        for s, e, _ in builds:
            if any(e - grace <= t <= e + grace or s < t < e for t, _ in inval):
                redundant += 1
        return {"rebuilds": len(builds), "redundantRebuilds": redundant,
                "buildCpuSec": round(cpu, 2),
                "redundantCpuSec": round(cpu * (redundant / len(builds)) if builds else 0.0, 2),
                "invalidations": len(inval)}


def run(window: float, providers: int, catalog: int, interval: float, per_cycle: int,
        coalesce: "float | None") -> dict:
    import random
    import store as store_mod
    import story_service
    import sources

    rng = random.Random(11)
    tmp = tempfile.mkdtemp(prefix="warmbench-")
    db = pathlib.Path(tmp) / "w.db"
    st = store_mod.Store(f"sqlite:///{db}")
    pp._bulk_load(st, pp.synth_catalog(catalog, events=1050, cluster_size=4))
    story_service.clear_cache()

    if coalesce is None:
        os.environ.pop("RWE_STORY_WARM_COALESCE", None)
    else:
        os.environ["RWE_STORY_WARM_COALESCE"] = str(coalesce)

    rec = Recorder(story_service)
    rec.install(st)

    # Stagger the providers so their cycles interleave the way real ones do — all firing together
    # would measure the concurrent case, which the poller lock already serializes, and miss the
    # SEQUENTIAL redundancy that is the actual finding.
    class Reg:
        def enabled(self):
            return [FakeAdapter(f"P{i}", interval, per_cycle, rng) for i in range(providers)]

    poller = sources.MultiSourcePoller(st, scorer=None, registry=Reg())
    ingest_waits = []
    real_lock = poller._lock

    class TimedLock:
        def __enter__(self):
            t0 = time.perf_counter()
            real_lock.acquire()
            ingest_waits.append(time.perf_counter() - t0)
            return self

        def __exit__(self, *a):
            real_lock.release()
            return False
    poller._lock = TimedLock()

    # A synthetic reader at a steady rate — the only honest way to state API latency, and the
    # counterweight that keeps this from being a CPU-only argument.
    reader_lat, reader_stop = [], threading.Event()

    def reader():
        while not reader_stop.is_set():
            t0 = time.perf_counter()
            try:
                story_service.list_stories(st, limit=20)
                reader_lat.append((time.perf_counter() - t0) * 1000.0)
            except Exception:
                pass
            reader_stop.wait(0.5)
    rt = threading.Thread(target=reader, daemon=True)

    poller.start()
    rt.start()
    time.sleep(window)
    reader_stop.set()
    rt.join(timeout=15)
    poller.stop(join_timeout=30)
    if hasattr(story_service, "shutdown_warmer"):
        story_service.shutdown_warmer()
    rec.restore()

    out = rec.report(window)
    lat = sorted(reader_lat)
    if lat:
        out["readerRequests"] = len(lat)
        out["readerP50Ms"] = round(lat[len(lat) // 2], 1)
        out["readerP95Ms"] = round(lat[int(len(lat) * 0.95)], 1)
        out["readerMaxMs"] = round(lat[-1], 1)
        # A miss is a request that had to cluster. The threshold is far above any warm hit
        # (sub-millisecond) and far below any build (seconds), so it does not need tuning.
        out["readerMisses"] = sum(1 for x in lat if x > 250.0)
        out["readerMissPct"] = round(100.0 * out["readerMisses"] / len(lat), 1)
    out["ingestBlockedSec"] = round(sum(ingest_waits), 2)
    out["ingestBlockedMaxSec"] = round(max(ingest_waits) if ingest_waits else 0.0, 2)
    out["coalesce"] = coalesce
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--window", type=float, default=120.0, help="seconds to run each variant")
    ap.add_argument("--providers", type=int, default=6)
    ap.add_argument("--catalog", type=int, default=20000)
    ap.add_argument("--interval", type=float, default=20.0,
                    help="per-provider poll interval; compressed from production's 600-900 s so a "
                         "window of minutes contains the same NUMBER of cycles as a real hour")
    ap.add_argument("--per-cycle", type=int, default=8, help="new articles per provider cycle")
    ap.add_argument("--coalesce", nargs="*", default=["none", "5"],
                    help="quiet-period seconds to compare; the word 'none' means the current path")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    os.environ.setdefault("RWE_LOG_LEVEL", "ERROR")
    for k, v in pp.PRODUCTION_ENV.items():
        os.environ.setdefault(k, v)

    results = []
    for c in args.coalesce:
        c = None if (c is None or str(c).lower() == "none") else float(c)
        label = "current (warm per provider)" if c is None else f"coalesced (quiet={c:g}s)"
        r = run(args.window, args.providers, args.catalog, args.interval, args.per_cycle, c)
        r["label"] = label
        results.append(r)
        print(f"  {label:<30} rebuilds {r['rebuilds']:>3}  redundant {r['redundantRebuilds']:>3}  "
              f"buildCPU {r['buildCpuSec']:>6.1f}s  ingestBlocked {r['ingestBlockedSec']:>6.1f}s  "
              f"readerP95 {r.get('readerP95Ms', 0):>7.1f}ms  miss {r.get('readerMissPct', 0):>4.1f}%",
              flush=True)

    if len(results) >= 2:
        a, b = results[0], results[-1]
        print(f"\n  rebuilds        {a['rebuilds']:>6} -> {b['rebuilds']:<6} "
              f"({_pct(a['rebuilds'], b['rebuilds'])})")
        print(f"  build CPU       {a['buildCpuSec']:>6.1f}s -> {b['buildCpuSec']:<6.1f}s "
              f"({_pct(a['buildCpuSec'], b['buildCpuSec'])})")
        print(f"  ingest blocked  {a['ingestBlockedSec']:>6.1f}s -> {b['ingestBlockedSec']:<6.1f}s "
              f"({_pct(a['ingestBlockedSec'], b['ingestBlockedSec'])})")
        print(f"  reader p50      {a.get('readerP50Ms', 0):>6.1f}ms -> {b.get('readerP50Ms', 0):<6.1f}ms")
        print(f"  reader p95      {a.get('readerP95Ms', 0):>6.1f}ms -> {b.get('readerP95Ms', 0):<6.1f}ms")
        print(f"  reader miss     {a.get('readerMissPct', 0):>6.1f}% -> {b.get('readerMissPct', 0):<6.1f}%"
              f"   <-- the counterweight; higher is worse")
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(results, indent=2))
    return 0


def _pct(a, b) -> str:
    if not a:
        return "n/a"
    return f"{(b - a) / a * 100:+.0f}%"


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
