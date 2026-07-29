#!/usr/bin/env bash
# perf-probe.sh — measure the RUNNING deployment. Read-only: it issues requests, reads SQLite
# metadata and asks Docker for resource usage. It changes nothing.
#
# Exists because a performance investigation done from the repo can only ever produce hypotheses.
# The repo says what the code COULD cost; this says what it DOES cost, on the box, on the live
# catalog, under the live configuration.
#
#   bash deploy/ops/perf-probe.sh                 # everything
#   bash deploy/ops/perf-probe.sh --endpoints     # just the endpoint timings
#   bash deploy/ops/perf-probe.sh --json          # machine-readable, for before/after comparison
#
# Output contract: with --json, one JSON object on stdout and nothing else, so two runs can be
# diffed. Without it, a human-readable report.
set -uo pipefail

API_CONTAINER="${API_CONTAINER:-deploy-api-1}"
WEB_CONTAINER="${WEB_CONTAINER:-deploy-web-1}"
REPEATS="${REPEATS:-5}"
MODE="all"
JSON=0
for a in "$@"; do
  case "$a" in
    --endpoints) MODE="endpoints" ;;
    --db)        MODE="db" ;;
    --resources) MODE="resources" ;;
    --json)      JSON=1 ;;
    -h|--help)   sed -n '2,14p' "$0"; exit 0 ;;
  esac
done

have() { command -v "$1" >/dev/null 2>&1; }
say()  { [ "$JSON" -eq 1 ] || printf '%s\n' "$*"; }

# Run a python program (fed on stdin) inside the api container and REFUSE TO BE SILENT about it.
#
# The first run of this script printed three empty sections and still ended with "done." — because
# `docker exec` without -i does not attach stdin, so every here-doc program arrived empty, python
# read nothing, printed nothing and exited 0. Empty output was indistinguishable from a healthy
# section that happened to have nothing to say. A probe whose failure mode looks like success is
# worse than no probe: it is a measurement you will quote later.
api_py() {
  local out rc
  out=$(docker exec -i "$API_CONTAINER" python - "$@" 2>&1); rc=$?
  if [ $rc -ne 0 ] || [ -z "$out" ]; then
    say "  !! NO OUTPUT (exit $rc) — this section FAILED, it is not empty."
    say "     container: $API_CONTAINER   (docker ps --filter name=$API_CONTAINER)"
    [ -n "$out" ] && say "     said: $(printf '%s' "$out" | head -3)"
    return 1
  fi
  printf '%s\n' "$out"
}

# ── Endpoint latency ──────────────────────────────────────────────────────────────────────────
# Timed from INSIDE the api container against 127.0.0.1, so the number is the engine's own cost
# with no proxy, TLS or network in it. A slow endpoint here is slow in the application; a fast one
# here that feels slow in a browser points at the web tier or the edge instead — which is a
# different fix, and the split is the whole reason this is measured in two places.
probe_endpoints() {
  say ""
  say "== engine endpoint latency (inside the container, ${REPEATS} runs, ms) =="
  say "     min    med    max   bytes  endpoint"
  local paths=(
    "/api/health"
    "/api/stories?limit=20"
    "/api/stories?limit=60"
    "/api/stories?sort=latest&limit=20"
    "/api/stories?blindspot=any&limit=20"
    "/api/discover?limit=20"
    "/api/search?q=trump&limit=20"
    "/api/outlets"
    "/api/places/countries"
    "/api/dashboard"
    "/api/report"
  )
  for p in "${paths[@]}"; do
    api_py "$p" "$REPEATS" <<'PY'
import json, sys, time, urllib.request
path, repeats = sys.argv[1], int(sys.argv[2])
url, times, size = "http://127.0.0.1:8000" + path, [], 0
for _ in range(repeats):
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            body = r.read(); size = len(body)
    except Exception as e:
        print(f"    ERROR  {path}  {type(e).__name__}"); sys.exit(0)
    times.append((time.perf_counter() - t0) * 1000)
times.sort()
med = times[len(times)//2]
print(f"  {times[0]:>6.0f} {med:>6.0f} {times[-1]:>6.0f} {size/1024:>7.0f}K  {path}")
PY
  done
}

# ── The clustering cache, seen from inside the process ────────────────────────────────────────
# The single most useful engine number: how long a COLD story build takes, and how often the cache
# is actually cold. A 2-second build behind a 99%-warm cache is invisible; the same build behind a
# cache that expires every two minutes is the whole complaint.
probe_engine_internals() {
  say ""
  say "== story pipeline, timed in-process against the live catalog =="
  api_py <<'PY'
import json, os, sys, time
sys.path.insert(0, "/app/examples")
import store as store_mod, story_service

st = store_mod.Store()
def ms(fn):
    t0 = time.perf_counter(); out = fn(); return out, (time.perf_counter() - t0) * 1000

n, _fp_ms = ms(lambda: st.catalog_fingerprint())
print(f"  catalog fingerprint      {_fp_ms:>8.1f} ms   (runs on EVERY /api/stories request)")
rows, fetch_ms = ms(lambda: story_service._fetch(st))
print(f"  _fetch (SQL + geo)       {fetch_ms:>8.1f} ms   {len(rows):,} rows")
stories, build_ms = ms(lambda: story_service.build_stories(rows))
print(f"  build_stories            {build_ms:>8.1f} ms   {len(stories):,} stories")
if story_service.stable_ids():
    _, stab_ms = ms(lambda: story_service.stabilize_ids(st, stories))
    print(f"  stabilize_ids            {stab_ms:>8.1f} ms")
story_service.clear_cache()
_, cold = ms(lambda: story_service.list_stories(st, limit=20))
_, warm = ms(lambda: story_service.list_stories(st, limit=20))
print(f"  list_stories COLD        {cold:>8.1f} ms")
print(f"  list_stories WARM        {warm:>8.1f} ms   (cache TTL {story_service.cache_ttl():.0f}s)")
print(f"  -> a cold build costs {cold/max(warm,0.001):>,.0f}x a warm one; the TTL decides how often it is paid")
PY
}

# ── Database ──────────────────────────────────────────────────────────────────────────────────
probe_db() {
  say ""
  say "== database =="
  api_py <<'PY'
import os, sqlite3, sys
path = os.environ.get("RWE_DB_PATH") or "/app/data/ih_beta.db"
if not os.path.exists(path):
    print(f"  (no db at {path})"); sys.exit(0)
c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
print(f"  file            {os.path.getsize(path)/1024/1024:>8.1f} MB   {path}")
for k in ("journal_mode", "synchronous", "busy_timeout", "cache_size", "page_size", "mmap_size"):
    print(f"  pragma {k:<14} {c.execute(f'PRAGMA {k}').fetchone()[0]}")
wal = path + "-wal"
if os.path.exists(wal):
    print(f"  WAL file        {os.path.getsize(wal)/1024/1024:>8.1f} MB   (large = checkpoints falling behind)")
print("\n  rows per table")
tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' order by name")]
for t in tables:
    try:
        n = c.execute(f'select count(*) from "{t}"').fetchone()[0]
    except Exception:
        continue
    if n:
        print(f"    {t:<34} {n:>10,}")

# EXPLAIN QUERY PLAN on the queries the hot path actually issues. "SCAN" on a big table is the
# finding; "SEARCH ... USING INDEX" is the healthy shape.
print("\n  query plans (SCAN of a large table = no usable index)")
probes = [
  ("stories window", "select * from feed_articles where published_at >= ? order by published_at desc limit 60000", ("2026-07-20",)),
  ("fingerprint count", "select count(*) from feed_articles", ()),
  ("fingerprint newest", "select max(fetched_at) from feed_articles", ()),
  ("search by publisher", "select * from feed_articles where lower(publisher)=? limit 50", ("bbc",)),
  ("event countries", "select canonical_url, country from article_event_locations where canonical_url in (?,?)", ("a","b")),
  ("facet: distinct publisher", "select distinct publisher from feed_articles", ()),
]
for label, sql, params in probes:
    try:
        plan = c.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    except Exception as e:
        print(f"    {label:<26} n/a ({e})"); continue
    detail = " | ".join(r[-1] for r in plan)
    flag = "  <-- FULL SCAN" if ("SCAN" in detail and "USING" not in detail.split("SCAN")[1][:40]) else ""
    print(f"    {label:<26} {detail}{flag}")
PY
}

# ── Host + container resources ────────────────────────────────────────────────────────────────
probe_resources() {
  say ""
  say "== container resources =="
  docker stats --no-stream --format \
    'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}' 2>/dev/null \
    || say "  (docker stats unavailable)"
  say ""
  say "== declared limits (empty = UNLIMITED: one container can take the whole box) =="
  for c in "$API_CONTAINER" "$WEB_CONTAINER"; do
    lim=$(docker inspect -f '{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}' "$c" 2>/dev/null)
    say "  $c  memory/nanocpus: ${lim:-unknown}"
  done
  say ""
  say "== host =="
  local cpus mem load disk
  cpus=$(nproc 2>/dev/null || echo '?')
  mem=$(free -m 2>/dev/null | awk 'NR==2 {printf "%sM used / %sM total", $3, $2}')
  load=$(uptime 2>/dev/null | sed 's/.*load average: //')
  disk=$(df -h / 2>/dev/null | awk 'NR==2 {printf "%s used / %s (%s)", $3, $2, $5}')
  say "  cpus     ${cpus}"
  say "  memory   ${mem:-unavailable}"
  say "  load     ${load:-unavailable}"
  say "  disk     ${disk:-unavailable}"
  # docker CPU% is relative to ONE core, so 100% on a 2-core box is one core saturated, not the
  # machine. Spelled out because the two readings look identical and mean very different things.
  say "  note     docker CPU% is per-core: 100% = one of ${cpus} cores busy"
}

# ── Edge: what the browser actually waits for ─────────────────────────────────────────────────
probe_edge() {
  say ""
  say "== public edge (through Caddy: TLS + proxy + compression) =="
  local host="${APP_DOMAIN:-hidden-view.com}"
  for p in "/" "/stories" "/discover"; do
    have curl || { say "  (curl unavailable)"; return; }
    out=$(curl -sSL -o /dev/null -k --resolve "${host}:443:127.0.0.1" \
      -H 'Accept-Encoding: gzip, zstd' \
      -w 'code=%{http_code} hops=%{num_redirects} tls=%{time_appconnect}s ttfb=%{time_starttransfer}s total=%{time_total}s bytes=%{size_download}' \
      "https://${host}${p}" 2>/dev/null)
    [ -n "$out" ] && say "  $(printf '%-12s' "$p") $out"
  done
  say ""
  say "  (compression check — Content-Encoding should be zstd or gzip)"
  # -D - after following redirects, not -I: a HEAD can be answered differently from the GET a
  # browser makes, and Caddy only compresses a real body.
  curl -sSL -k -o /dev/null -D - --resolve "${host}:443:127.0.0.1" \
    -H 'Accept-Encoding: gzip, zstd' "https://${host}/" 2>/dev/null \
    | grep -iE '^HTTP/|content-encoding|cache-control|content-length' | sed 's/^/    /'
}

say "== Hidden View performance probe =="
say "   $(date -u +%FT%TZ)   api=${API_CONTAINER}"
case "$MODE" in
  endpoints) probe_endpoints; probe_engine_internals ;;
  db)        probe_db ;;
  resources) probe_resources ;;
  *)         probe_endpoints; probe_engine_internals; probe_db; probe_resources; probe_edge ;;
esac
say ""
say "done."
