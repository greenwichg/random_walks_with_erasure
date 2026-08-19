"""audit_ec2_preflight.py — is this box safe to run the semantic evaluation on? READ-ONLY.

Answers the question BEFORE anything is installed or computed: does the production EC2 host
have the CPU, RAM and disk headroom for a local BERT/embedding evaluation, with the production
API and ingest staying healthy? Inspects only — it starts nothing, installs nothing, writes
nothing, and touches no container, environment variable, or configuration.

Run it on the HOST (plain `python3`, stdlib only) or inside a container; either way the figures
it reads (/proc/loadavg, /proc/meminfo, statvfs) describe the HOST, which is what must stay
healthy.

The estimates below are stated BEFORE the decision, so the go/no-go is made against numbers
rather than optimism. They are deliberately pessimistic — a preflight that under-estimates is
worse than useless.

  torch (CPU-only wheel)          ~200 MB download, ~900 MB installed
  transformers + tokenizers etc   ~250 MB installed
  sentence-transformers           ~10 MB installed
  MiniLM-L6 (bi, monolingual)     ~90 MB weights
  multilingual MiniLM-L12 (bi)    ~470 MB weights
  stsb-roberta-base (cross)       ~500 MB weights
  nli-deberta-v3-small (cross)    ~290 MB weights
  ---------------------------------------------------------------
  TOTAL DISK (no pip cache)       ~2.5 GB, budgeted at 4.0 GB
  PEAK RAM (one model at a time)  ~1.2 GB, container-capped at 2.0 GB

Thresholds (conservative, registered here so they are not adjusted to fit a result):

  disk    free >= 4.0 GB needed + 5.0 GB headroom left over  => require >= 9.0 GB free
  ram     MemAvailable >= 3.5 GB (2.0 GB cap + 1.5 GB host margin)
  load    1-minute load average <= 0.60 x cpus
  swap    less than 10% of swap in use (a swapping box is already under pressure)

Exit codes: 0 = GO, 2 = NO-GO (a resource limitation, not an error), 1 = the preflight itself
could not determine the answer. NO-GO is a legitimate, expected outcome: the box staying
healthy outranks completing the experiment.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

NEED_DISK_GB = 4.0
HEADROOM_DISK_GB = 5.0
NEED_AVAIL_RAM_GB = 3.5
EXPERIMENT_RAM_CAP_GB = 2.0
MAX_LOAD_RATIO = 0.60
MAX_SWAP_USED = 0.10


def _meminfo() -> dict:
    out = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition(":")
                out[k.strip()] = int(v.split()[0]) / (1024 * 1024)     # kB -> GiB
    except Exception:                                   # noqa: BLE001 — non-Linux host
        pass
    return out


def _loadavg():
    try:
        return os.getloadavg()
    except Exception:                                   # noqa: BLE001
        return (0.0, 0.0, 0.0)


def _disk(path: str):
    try:
        u = shutil.disk_usage(path)
        return u.free / (1024 ** 3), u.total / (1024 ** 3)
    except Exception:                                   # noqa: BLE001 — path may not exist
        return None, None


def _docker(args, timeout=20):
    for prefix in ([], ["sudo", "-n"]):
        try:
            r = subprocess.run(prefix + ["docker"] + args, capture_output=True, text=True,
                               timeout=timeout)
            if r.returncode == 0:
                return r.stdout.strip()
        except Exception:                               # noqa: BLE001 — docker may not be reachable
            continue
    return None


def main(argv=None) -> int:
    paths = list(argv or []) or ["/tmp", "/", "/var/lib/docker"]
    print("=" * 78)
    print("EC2 PREFLIGHT — read-only. Nothing is installed, started, or modified.")
    print("=" * 78)

    cpus = os.cpu_count() or 1
    l1, l5, l15 = _loadavg()
    mem = _meminfo()
    total = mem.get("MemTotal", 0.0)
    avail = mem.get("MemAvailable", 0.0)
    swap_t = mem.get("SwapTotal", 0.0)
    swap_f = mem.get("SwapFree", 0.0)
    swap_used = (swap_t - swap_f) / swap_t if swap_t > 0.01 else 0.0

    print(f"\n-- host --")
    print(f"  cpus            : {cpus}")
    print(f"  load average    : {l1:.2f} / {l5:.2f} / {l15:.2f}   "
          f"({l1 / cpus:.0%} of capacity, 1-min)")
    print(f"  memory          : {total:.1f} GB total, {avail:.1f} GB available")
    print(f"  swap            : {swap_t:.1f} GB total, {swap_used:.0%} in use")

    print(f"\n-- disk --")
    disk_free = {}
    for p in paths:
        free, tot = _disk(p)
        if free is None:
            print(f"  {p:<16}: not present")
            continue
        disk_free[p] = free
        print(f"  {p:<16}: {free:.1f} GB free of {tot:.1f} GB")

    print(f"\n-- production containers (read-only inspection) --")
    ps = _docker(["ps", "--format", "{{.Names}}\t{{.Status}}"])
    if ps:
        for line in ps.splitlines():
            print(f"  {line}")
    else:
        print(f"  docker ps unavailable (no permission or no docker) — cannot confirm container "
              f"health from here; check manually before proceeding")
    stats = _docker(["stats", "--no-stream", "--format",
                     "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"], timeout=40)
    if stats:
        print(f"\n  current usage:")
        for line in stats.splitlines():
            print(f"    {line}")

    print(f"\n-- the experiment's estimated requirements (stated before the decision) --")
    print(f"  disk needed     : {NEED_DISK_GB:.1f} GB (torch ~0.9, hf libs ~0.25, "
          f"4 checkpoints ~1.35, slack)")
    print(f"  peak RAM        : ~1.2 GB, hard-capped at {EXPERIMENT_RAM_CAP_GB:.1f} GB by the "
          f"container limit")
    print(f"  cpu             : 1 core, pinned (OMP/MKL threads = 1)")

    print(f"\n-- decision against the registered thresholds --")
    blockers = []
    cache_path = paths[0]
    free = disk_free.get(cache_path)
    want_disk = NEED_DISK_GB + HEADROOM_DISK_GB
    if free is None:
        blockers.append(f"cannot read free space on {cache_path}")
    elif free < want_disk:
        blockers.append(f"disk: {free:.1f} GB free on {cache_path}, need >= {want_disk:.1f} GB "
                        f"({NEED_DISK_GB:.1f} for the install + {HEADROOM_DISK_GB:.1f} headroom)")
    else:
        print(f"  [ok]   disk    {free:.1f} GB free on {cache_path} >= {want_disk:.1f} GB")

    if avail <= 0:
        blockers.append("cannot read MemAvailable")
    elif avail < NEED_AVAIL_RAM_GB:
        blockers.append(f"ram: {avail:.1f} GB available, need >= {NEED_AVAIL_RAM_GB:.1f} GB")
    else:
        print(f"  [ok]   ram     {avail:.1f} GB available >= {NEED_AVAIL_RAM_GB:.1f} GB")

    if l1 > MAX_LOAD_RATIO * cpus:
        blockers.append(f"load: 1-min average {l1:.2f} exceeds {MAX_LOAD_RATIO:.0%} of "
                        f"{cpus} cpus ({MAX_LOAD_RATIO * cpus:.2f}) — the box is busy now")
    else:
        print(f"  [ok]   load    {l1:.2f} <= {MAX_LOAD_RATIO * cpus:.2f}")

    if swap_used > MAX_SWAP_USED:
        blockers.append(f"swap: {swap_used:.0%} in use (> {MAX_SWAP_USED:.0%}) — the host is "
                        f"already under memory pressure")
    else:
        print(f"  [ok]   swap    {swap_used:.0%} in use <= {MAX_SWAP_USED:.0%}")

    if cpus < 2:
        blockers.append(f"cpus: {cpus} — pinning the experiment to 1 core would leave the "
                        f"production API no dedicated core")
    else:
        print(f"  [ok]   cpus    {cpus} >= 2 (1 for the experiment, {cpus - 1} left for prod)")

    print(f"\n-- verdict --")
    if blockers:
        print(f"  NO-GO — the local BERT evaluation must NOT run on this host:")
        for b in blockers:
            print(f"    * {b}")
        print(f"\n  This is a resource limitation, not a failure. Do not attempt it anyway.")
        print(f"  Safer alternatives, in order of preference:")
        print(f"    1. Run the lexical-similarity control instead (--arms lexical): zero new "
              f"dependencies, no downloads, seconds of one core. It tests the core hypothesis "
              f"(that similarity-class signals correlate in-band) at effectively no cost.")
        print(f"    2. Export the 390-pair sheet and run the embedding arms OFF this box — the "
              f"sheet is ~100 KB of public news headlines with no secrets, so a laptop or a "
              f"free Colab runtime does it with zero production risk.")
        print(f"    3. If neither is possible, leave the question open. The box staying healthy "
              f"outranks the experiment.")
        return 2
    print(f"  GO — the host has headroom for the bounded experiment. Run it with the container "
          f"caps and the in-run governor; both are mandatory, not optional.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
