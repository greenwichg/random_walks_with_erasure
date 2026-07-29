"""concurrency_report.py — how many concurrent users this box supports, from production measurements.

**A concurrency limit cannot be measured without load.** This does not pretend otherwise. What it
does is derive the estimate from things that ARE measured on the live box — per-endpoint service
time out of the request log, the CPU budget out of /proc, SQLite write throughput benchmarked on
this volume — and label every modelling step so the assumptions are arguable rather than buried.

    docker logs deploy-api-1 2>&1 | docker exec -i deploy-api-1 python examples/concurrency_report.py
    ... | docker exec -i deploy-api-1 python examples/concurrency_report.py --json

Provenance, same contract as capacity_report:

    [M] MEASURED   read from the live box. A fact.
    [D] DERIVED    arithmetic over measured values.
    [P] PROJECTED  a model. Assumptions printed beside it.

The single most load-bearing assumption is stated up front and everywhere it is used: **wall-clock
service time is treated as CPU time**. On an idle box serving CPU-bound Python that is nearly true,
and it errs toward *overstating* cost, so the user numbers come out conservative. Under real
concurrency wall time grows while CPU does not, so this must not be re-derived from a loaded box.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import store as store_mod                                    # noqa: E402

#: t3.medium: 2 vCPU, 24 CPU credits/hour = 0.40 vCPU sustainable. Burst to 2.0 while credits last.
VCPU_TOTAL = 2.0
VCPU_SUSTAINABLE = 0.40

#: Modelled sessions: which endpoints one user action hits. [D] — the endpoint COSTS are measured;
#: this mapping is a model of the product, and is the first thing to argue with.
WORKLOADS = {
    "homepage browse": {"GET /api/stories": 1, "GET /api/discover": 1, "POST /api/events": 2},
    "open a story": {"GET /api/stories": 1, "POST /api/me/reads": 1, "POST /api/events": 2},
    "recommendations": {"GET /api/recommendations": 1, "POST /api/events": 1},
    "create a report": {"GET /api/report": 1, "POST /api/events": 1},
    "search": {"GET /api/search": 1, "POST /api/events": 1},
}

#: Actions per session and sessions per active user per day. [P] — product assumptions, not
#: measurements. There is not enough real traffic on this deployment to measure them.
ACTIONS_PER_SESSION = 12
SESSIONS_PER_DAU = 1.4
#: Share of a day's traffic landing in the busiest hour. 1/24 = perfectly flat (unreal); news
#: products bunch hard around morning and evening. 12% is a common shape and is the default here.
PEAK_HOUR_SHARE = 0.12
#: DAU/MAU for a habitual news product. 0.25 = a user shows up ~7-8 days a month.
DAU_MAU_RATIO = 0.25


def parse_request_log(stream) -> dict:
    """[M] Per-endpoint service time from the production request log.

    Reads ``{"event": "request", "method", "path", "durationMs"}`` lines. Path parameters are
    collapsed (``/api/publishers/BBC`` -> ``/api/publishers/{}``) so a per-entity endpoint does not
    fragment into hundreds of one-sample buckets."""
    agg: dict = {}
    for line in stream:
        i = line.find('{"event"')
        if i < 0:
            i = line.find("{")
        if i < 0:
            continue
        try:
            d = json.loads(line[i:])
        except Exception:
            continue
        if d.get("event") != "request":
            continue
        path = d.get("path") or ""
        parts = [("{}" if _looks_like_id(p) else p) for p in path.split("/")]
        key = f"{d.get('method', '?')} {'/'.join(parts)}"
        agg.setdefault(key, []).append(float(d.get("durationMs") or 0.0))
    out = {}
    for key, xs in agg.items():
        xs.sort()
        out[key] = {"n": len(xs), "meanMs": statistics.fmean(xs), "p50Ms": xs[len(xs) // 2],
                    "p95Ms": xs[min(len(xs) - 1, int(len(xs) * 0.95))], "maxMs": xs[-1]}
    return out


def _looks_like_id(seg: str) -> bool:
    if not seg or seg in ("api",):
        return False
    return seg.isdigit() or (len(seg) > 12 and not seg.islower()) or "%" in seg


def measure_cpu(seconds: float) -> dict:
    """[M] Host CPU over a window. /proc/stat inside a container reports the HOST."""
    snap = lambda: list(map(int, open("/proc/stat").readline().split()[1:]))   # noqa: E731
    a = snap()
    time.sleep(seconds)
    b = snap()
    d = [y - x for x, y in zip(a, b)]
    tot = sum(d) or 1
    busy = tot - d[3] - d[4]
    return {"windowSec": seconds, "busyVcpu": VCPU_TOTAL * busy / tot,
            "stealPct": 100.0 * d[7] / tot, "idlePct": 100.0 * d[3] / tot}


def measure_memory() -> dict:
    """[M] Host memory from /proc/meminfo (also host-scoped inside the container)."""
    info = {}
    try:
        for line in open("/proc/meminfo"):
            k, _, v = line.partition(":")
            info[k] = int(v.strip().split()[0]) * 1024
    except OSError:
        return {}
    return {"total": info.get("MemTotal", 0), "available": info.get("MemAvailable", 0),
            "used": info.get("MemTotal", 0) - info.get("MemAvailable", 0)}


def bench_sqlite_writes(volume_dir: str, n: int = 400) -> dict:
    """[M] Small-transaction write throughput on THIS volume, with the engine's own pragmas.

    Benchmarked against a scratch database beside the real one — same filesystem, same page size,
    same WAL mode — never against the live database. SQLite allows one writer at a time, so this
    number is a hard ceiling on write-bearing requests per second no matter how much CPU is spare.
    Every UI interaction POSTs an analytics event, which makes this the ceiling most likely to bind
    before CPU does."""
    d = tempfile.mkdtemp(prefix="ih_wbench_", dir=volume_dir)
    path = os.path.join(d, "w.db")
    try:
        con = sqlite3.connect(path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, k TEXT, v TEXT)")
        con.commit()
        payload = "x" * 200
        t0 = time.perf_counter()
        for i in range(n):
            con.execute("INSERT INTO t (k, v) VALUES (?, ?)", (f"k{i}", payload))
            con.commit()                       # one transaction each: the analytics-write shape
        elapsed = time.perf_counter() - t0
        con.close()
        return {"transactions": n, "seconds": elapsed, "writesPerSec": n / elapsed if elapsed else 0}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


#: An endpoint needs at least this many samples before its cost is treated as a measurement.
#: Learned the hard way: the first production run priced the whole model off `/api/recommendations`
#: n=2 (mean 4,520 ms, max 7,973 ms) and `/api/search` n=1 — three samples, all of them cold-cache
#: builds, which dragged the estimate to 910 DAU. A mean over one sample is not a measurement.
MIN_SAMPLES = 5

#: p95/p50 above this means the endpoint is BIMODAL — a cheap cached path and an expensive cold
#: build sharing one name. Averaging across that mixture describes neither.
BIMODAL_RATIO = 3.0


def endpoint_cost(rec: dict, stat: str) -> float:
    """The number used for capacity. ``p50`` by default, not the mean.

    The mean is the wrong statistic here: one cold 8-second build among two samples moves it by
    4 seconds, and capacity planning cares about what a request usually costs, not about the
    arithmetic centre of a bimodal mixture."""
    return float(rec["p50Ms"] if stat == "p50" else rec["meanMs"])


def cost_of(workload: dict, endpoints: dict, fallback_ms: float, *, stat: str = "p50",
            min_samples: int = MIN_SAMPLES) -> dict:
    """[D] Milliseconds of service time per action, from measured endpoint costs.

    Reports how much of the cost came from WELL-SAMPLED endpoints. A model built on fallbacks or on
    one-sample endpoints is a guess, and the output has to say so rather than present all three
    alike."""
    total = measured = 0.0
    missing, thin, bimodal = [], [], []
    for path, count in workload.items():
        rec = endpoints.get(path)
        if not rec:
            total += fallback_ms * count
            missing.append(path)
            continue
        c = endpoint_cost(rec, stat) * count
        total += c
        if rec["n"] >= min_samples:
            measured += c
        else:
            thin.append(f"{path} (n={rec['n']})")
        if rec["p50Ms"] > 0 and rec["p95Ms"] / rec["p50Ms"] > BIMODAL_RATIO:
            bimodal.append(f"{path} (p50 {rec['p50Ms']:.0f} / p95 {rec['p95Ms']:.0f} ms)")
    return {"ms": total, "measuredMs": measured, "missing": missing, "thin": thin,
            "bimodal": bimodal, "measuredShare": (measured / total) if total else 0.0}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--cpu-window", type=float, default=60.0, help="CPU sample seconds (default 60)")
    ap.add_argument("--fallback-ms", type=float, default=25.0,
                    help="assumed cost for an endpoint with no production samples (default 25)")
    ap.add_argument("--actions-per-session", type=int, default=ACTIONS_PER_SESSION)
    ap.add_argument("--peak-hour-share", type=float, default=PEAK_HOUR_SHARE)
    ap.add_argument("--no-write-bench", action="store_true")
    ap.add_argument("--stat", choices=("p50", "mean"), default="p50",
                    help="which statistic prices an endpoint (default p50; the mean is wrecked by "
                         "a single cold-cache sample)")
    ap.add_argument("--min-samples", type=int, default=MIN_SAMPLES,
                    help=f"samples before an endpoint counts as measured (default {MIN_SAMPLES})")
    args = ap.parse_args()

    endpoints = parse_request_log(sys.stdin) if not sys.stdin.isatty() else {}
    cpu = measure_cpu(args.cpu_window)
    mem = measure_memory()
    url = os.environ.get("RWE_DB_URL") or "sqlite:////app/data/ih_beta.db"
    db_path = store_mod.sqlite_path(url)
    vol_dir = str(pathlib.Path(db_path).parent) if db_path else "/tmp"
    writes = {} if args.no_write_bench else bench_sqlite_writes(vol_dir)

    background = cpu["busyVcpu"]
    sustained_free = max(0.0, VCPU_SUSTAINABLE - background)
    burst_free = max(0.0, VCPU_TOTAL - background)

    costs = {name: cost_of(w, endpoints, args.fallback_ms, stat=args.stat,
                           min_samples=args.min_samples) for name, w in WORKLOADS.items()}
    mix_ms = statistics.fmean([c["ms"] for c in costs.values()])
    measured_share = statistics.fmean([c["measuredShare"] for c in costs.values()])

    # [P] Throughput = available CPU-seconds per second / CPU-seconds per action.
    sustained_aps = (sustained_free * 1000.0 / mix_ms) if mix_ms else 0.0
    burst_aps = (burst_free * 1000.0 / mix_ms) if mix_ms else 0.0

    # [P] Concurrency. A "concurrent user" is one with a request in flight or thinking between
    # actions; Little's law with a think time turns actions/sec into simultaneous humans.
    think_sec = 8.0
    concurrent_sustained = sustained_aps * think_sec
    concurrent_burst = burst_aps * think_sec

    # [P] Writes. Every action posts at least one analytics event.
    writes_per_action = 1.5
    write_ceiling_aps = (writes.get("writesPerSec", 0) / writes_per_action) if writes.get("writesPerSec") else None

    daily_actions = sustained_aps * 86400.0
    # A peak hour may run on burst ONLY if the day's credit budget covers it. t3.medium earns
    # 24 credits/hour = 1,440 credit-minutes/day; an hour at `burst_free` vCPU costs
    # burst_free * 60 credit-minutes, and the other 23 hours must still be paid for at whatever
    # they draw. Checked rather than assumed, because "burst" is not free and a model that spends
    # credits it has not earned is arithmetic fiction.
    credits_earned_per_day = 24.0 * 24.0                      # credit-minutes
    peak_hour_cost = burst_free * 60.0
    offpeak_cost = background * 60.0 * 23.0
    burst_hour_affordable = (peak_hour_cost + offpeak_cost) <= credits_earned_per_day
    peak_vcpu = burst_free if burst_hour_affordable else sustained_free
    peak_aps = (peak_vcpu * 1000.0 / mix_ms) if mix_ms else 0.0
    peak_limited_daily = (peak_aps * 3600.0) / args.peak_hour_share if args.peak_hour_share else 0.0
    daily_actions_effective = min(daily_actions, peak_limited_daily)
    binding_daily = "peak-hour" if peak_limited_daily < daily_actions else "flat-traffic"
    dau = daily_actions_effective / (args.actions_per_session * SESSIONS_PER_DAU)
    mau = dau / DAU_MAU_RATIO

    bottlenecks = [("CPU (sustained, t3.medium credit baseline)", sustained_aps)]
    if write_ceiling_aps is not None:
        bottlenecks.append(("SQLite single-writer lock", write_ceiling_aps))
    if mem.get("total"):
        # ~12 MiB per in-flight request is generous for this stack; memory is listed to be ruled
        # out with a number rather than by assertion.
        bottlenecks.append(("memory", (mem["available"] / (12 * 1024 * 1024)) / think_sec))
    bottlenecks.sort(key=lambda kv: kv[1])

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "cpu": cpu, "memory": mem, "sqliteWrites": writes,
        "endpoints": endpoints, "costs": costs,
        "budget": {"totalVcpu": VCPU_TOTAL, "sustainableVcpu": VCPU_SUSTAINABLE,
                   "backgroundVcpu": background, "sustainedFreeVcpu": sustained_free,
                   "burstFreeVcpu": burst_free},
        "estimates": {"actionsPerSecSustained": sustained_aps, "actionsPerSecBurst": burst_aps,
                      "burstHourAffordable": burst_hour_affordable, "bindingDailyLimit": binding_daily,
                      "concurrentSustained": concurrent_sustained, "concurrentBurst": concurrent_burst,
                      "dau": dau, "mau": mau, "measuredShareOfCost": measured_share},
        "firstBottleneck": bottlenecks[0][0] if bottlenecks else None,
    }
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    w = 82
    print("=" * w)
    print("  CONCURRENCY ESTIMATE".ljust(w))
    print("  [M] measured   [D] derived   [P] projected (a model — assumptions printed)".ljust(w))
    print("=" * w)

    print(f"\n[M] CPU over {cpu['windowSec']:.0f}s   background {background:.2f} vCPU   "
          f"steal {cpu['stealPct']:.2f}%")
    print(f"      t3.medium: {VCPU_TOTAL:.1f} vCPU total, {VCPU_SUSTAINABLE:.2f} vCPU sustainable "
          f"(24 credits/hr)")
    print(f"      free for users:  {sustained_free:.2f} vCPU sustained   {burst_free:.2f} vCPU burst")
    if mem.get("total"):
        print(f"[M] MEMORY   {mem['used'] / 1024**3:.2f} / {mem['total'] / 1024**3:.2f} GiB used, "
              f"{mem['available'] / 1024**3:.2f} GiB available")
    if writes.get("writesPerSec"):
        print(f"[M] SQLITE WRITES   {writes['writesPerSec']:,.0f} commits/sec "
              f"({writes['transactions']} one-row transactions on this volume, WAL + synchronous=NORMAL)")
    elif writes.get("error"):
        print(f"[M] SQLITE WRITES   benchmark failed: {writes['error']}")

    if endpoints:
        print(f"\n[M] ENDPOINT SERVICE TIME  (production request log, {sum(e['n'] for e in endpoints.values()):,} samples)")
        print(f"      {'endpoint':<44}{'n':>7}{'mean':>9}{'p95':>9}{'max':>9}")
        for k, e in sorted(endpoints.items(), key=lambda kv: -kv[1]["meanMs"] * kv[1]["n"])[:14]:
            print(f"      {k:<44}{e['n']:>7,}{e['meanMs']:>8.1f}{e['p95Ms']:>9.1f}{e['maxMs']:>9.1f}")
    else:
        print("\n  ! NO REQUEST LOG ON STDIN — every endpoint cost below is the fallback assumption.")
        print("    Pipe production logs in:  docker logs deploy-api-1 2>&1 | docker exec -i … ")

    print(f"\n[D] COST PER USER ACTION   (priced at {args.stat}; the endpoint MIX is modelled)")
    print(f"      {'workload':<24}{'ms/action':>12}{'well-sampled':>14}  gaps")
    for name, c in costs.items():
        gaps = ", ".join([p.split()[-1] for p in c["missing"]] +
                         [t.split()[-2] + " " + t.split()[-1] for t in c["thin"]]) or "—"
        print(f"      {name:<24}{c['ms']:>12.1f}{100 * c['measuredShare']:>13.0f}%  {gaps}")
    print(f"      {'mean of workloads':<24}{mix_ms:>12.1f}{100 * measured_share:>13.0f}%")
    all_thin = sorted({t for c in costs.values() for t in c["thin"]})
    all_bimodal = sorted({b for c in costs.values() for b in c["bimodal"]})
    if all_thin:
        print(f"\n      ! THIN SAMPLES (< {args.min_samples}) — these are not measurements yet:")
        for t in all_thin:
            print(f"          {t}")
    if all_bimodal:
        print(f"\n      ! BIMODAL (p95/p50 > {BIMODAL_RATIO:.0f}) — a cached path and a cold build "
              f"sharing one name;")
        print(f"        the p50 prices the cached path, which is the right choice for steady state,")
        print(f"        but it means the cold cost is real and unmodelled:")
        for b in all_bimodal:
            print(f"          {b}")
    if measured_share < 0.5:
        print(f"\n      ! Under half this model's cost comes from well-sampled endpoints. Every")
        print(f"        user number below is a placeholder until real traffic exists. Generate load.")

    print(f"\n[P] THROUGHPUT AND CONCURRENCY")
    print(f"      ASSUMES: wall time = CPU time (conservative); {think_sec:.0f}s think time between")
    print(f"      actions; {writes_per_action} DB writes per action; a {args.actions_per_session}-action session.")
    print(f"      {'':<34}{'sustained':>14}{'burst':>14}")
    print(f"      {'actions/sec':<34}{sustained_aps:>14,.1f}{burst_aps:>14,.1f}")
    print(f"      {'concurrent users':<34}{concurrent_sustained:>14,.0f}{concurrent_burst:>14,.0f}")
    print(f"      sustained = within the credit baseline, indefinitely.")
    print(f"      burst     = both vCPU, drains CPU credits — hours, not days.")
    if write_ceiling_aps is not None:
        print(f"      SQLite write ceiling: {write_ceiling_aps:,.0f} actions/sec "
              f"({'not binding' if write_ceiling_aps > burst_aps else 'BINDS BEFORE CPU'})")

    print(f"\n[P] DAU / MAU")
    print(f"      ASSUMES: {100 * args.peak_hour_share:.0f}% of a day's traffic in the busiest hour, "
          f"{SESSIONS_PER_DAU} sessions/DAU,")
    print(f"      {args.actions_per_session} actions/session, DAU/MAU = {DAU_MAU_RATIO}.")
    print(f"      peak hour runs at {peak_vcpu:.2f} vCPU — "
          f"{'burst affordable within the daily credit budget' if burst_hour_affordable else 'BURST NOT AFFORDABLE, held to baseline'}")
    mark = lambda which: "  <- binds" if which == binding_daily else ""      # noqa: E731
    print(f"      {'flat-traffic ceiling':<34}{daily_actions:>14,.0f} actions/day{mark('flat-traffic')}")
    print(f"      {'peak-hour ceiling':<34}{peak_limited_daily:>14,.0f} actions/day{mark('peak-hour')}")
    print(f"      {'DAU':<34}{dau:>14,.0f}")
    print(f"      {'MAU':<34}{mau:>14,.0f}")

    print(f"\n[!] FIRST BOTTLENECK")
    print(f"      (CPU and SQLite are measured ceilings; memory assumes ~12 MiB per in-flight")
    print(f"       request, which is a rule-out rather than a measurement)")
    for name, cap in bottlenecks:
        print(f"      {name:<44}{cap:>12,.1f} actions/sec")
    print(f"      -> {bottlenecks[0][0]}")
    print(f"\n  VALIDATION: none of this is a load test. These numbers are a model over measured")
    print(f"  per-request costs, and the way to falsify them is to generate load and watch where it")
    print(f"  actually breaks. Treat them as a hypothesis with a stated derivation, not a result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
