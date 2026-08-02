#!/usr/bin/env bash
# rec-latency-probe.sh — stage-by-stage latency of the recommendation pipeline, measured on the
# RUNNING deployment: from POST /api/me/reads through to GET /api/recommendations completing.
#
# Why a probe and not a reading of the code: the repo can only say what a stage COULD cost. The
# split that decides the fix — is the wait the read write, the model rebuild, the story index, the
# ranking, or the handler's own post-passes — is a property of this catalog, this reader's history
# and this box. It has to be measured there.
#
#   bash deploy/ops/rec-latency-probe.sh                   # full flow, busiest reader
#   bash deploy/ops/rec-latency-probe.sh --email you@x.com # …or name yourself by address
#   bash deploy/ops/rec-latency-probe.sh --user 3          # …or by engine user id
#   bash deploy/ops/rec-latency-probe.sh --warm-only       # no write: warm serves + live ratios
#   bash deploy/ops/rec-latency-probe.sh --users           # just list readers (uid, email, reads)
#
# The "uid" is the engine's own user id (users.id) — the integer the web tier sends as
# X-IH-User-Id after Google sign-in. It is not shown in the product, so --users and --email exist
# so nobody has to go looking for it.
#
# WRITES: unless --warm-only, this records ONE read for the chosen user (the flow under
# measurement begins with a read; a rebuild cannot be forced any other way, because the model
# cache key IS the reader's read count). It is announced before it happens and reported after.
# Everything else is read-only.
set -uo pipefail

API_CONTAINER="${API_CONTAINER:-deploy-api-1}"
REPEATS="${REPEATS:-3}"
UID_ARG=""
EMAIL_ARG=""
MODE="full"
while [ $# -gt 0 ]; do
  case "$1" in
    --user)      UID_ARG="${2:-}"; shift 2 ;;
    --email)     EMAIL_ARG="${2:-}"; shift 2 ;;
    --repeats)   REPEATS="${2:-3}"; shift 2 ;;
    --warm-only) MODE="warm"; shift ;;
    --users)     MODE="users"; shift ;;
    -h|--help)   sed -n '2,23p' "$0"; exit 0 ;;
    *)           echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }

# Run a python program (fed on stdin) inside the api container and REFUSE TO BE SILENT about it.
# `docker exec` without -i does not attach stdin, so a here-doc arrives empty, python prints
# nothing and exits 0 — a failed section that reads exactly like a healthy empty one. Any probe
# whose failure looks like success is worse than no probe: this is a number you will quote later.
api_py() {
  local out rc
  out=$(docker exec -i "$API_CONTAINER" python - "$@" 2>&1); rc=$?
  if [ $rc -ne 0 ] || [ -z "$out" ]; then
    say "  !! NO OUTPUT (exit $rc) — this section FAILED, it is not empty."
    say "     container: $API_CONTAINER   (docker ps --filter name=$API_CONTAINER)"
    [ -n "$out" ] && say "     said: $(printf '%s' "$out" | head -5)"
    return 1
  fi
  printf '%s\n' "$out"
}

# ── The one python preamble every section reuses: an authenticated in-container HTTP client. ──
# Timed from inside the api container against 127.0.0.1 so the number is the engine's own cost
# with no proxy, TLS or network in it. The trusted-tier headers are exactly what the web tier
# sends (X-IH-Auth + X-IH-User-Id), so this measures the served path, not a private one.
read -r -d '' PREAMBLE <<'PYPRE'
import json, os, sys, time, urllib.request, urllib.error

# `python -` reads from stdin, so sys.path[0] is the CWD — not the script directory the engine
# gets from `python examples/api_fastapi.py`. Without this every `import store` in this probe
# fails with ModuleNotFoundError while the app itself is perfectly healthy. Searched rather than
# hardcoded so a moved WORKDIR says so instead of looking like a broken engine.
for _cand in ("/app/examples", os.path.join(os.getcwd(), "examples"), "/opt/ih/examples"):
    if os.path.exists(os.path.join(_cand, "store.py")):
        sys.path.insert(0, _cand)
        break
else:
    print(f"  !! cannot find the engine modules (cwd={os.getcwd()}) — looked in /app/examples,"
          f" ./examples, /opt/ih/examples", file=sys.stderr)

BASE = "http://127.0.0.1:8000"
SECRET = os.environ.get("RWE_INTERNAL_SECRET", "")

def call(method, path, uid=None, body=None):
    """(status, payload, wall_ms). Wall time is the client-side round trip: the engine's own cost
    plus loopback, which is the number a reader actually waits on."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if SECRET:
        req.add_header("X-IH-Auth", SECRET)
    if uid is not None:
        req.add_header("X-IH-User-Id", str(uid))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except Exception as e:
        # An unreachable engine must read as an unreachable engine, not as a traceback that could
        # be mistaken for a bug in what is being measured.
        return 0, f"UNREACHABLE: {e}", (time.perf_counter() - t0) * 1000.0
    ms = (time.perf_counter() - t0) * 1000.0
    try:
        return status, json.loads(raw.decode()), ms
    except Exception:
        return status, raw.decode()[:200], ms

def rec_metrics():
    """Only the series this probe is about, so a delta stays readable."""
    st, snap, _ = call("GET", "/api/metrics")
    if st != 200 or not isinstance(snap, dict):
        return {}, {}
    t = {k: v for k, v in (snap.get("timers") or {}).items()
         if k.startswith(("rec_", "read_", "story_default_view_"))}
    c = {k: v for k, v in (snap.get("counters") or {}).items()
         if k.startswith(("rec_", "read_", "story_default_view_", "story_stale_"))}
    return t, c

def delta(before, after):
    """Per-stage cost of the calls made BETWEEN two snapshots. Counts and SUMS are differenced —
    never the reported averages — so a long-lived process's history cannot drown the handful of
    calls actually under measurement (which is the whole point of taking two snapshots)."""
    out = {}
    for k, a in after.items():
        b = before.get(k) or {}
        n = int(a.get("count", 0)) - int(b.get("count", 0))
        if n <= 0:
            continue
        tot = float(a.get("sumMs", 0.0)) - float(b.get("sumMs", 0.0))
        out[k] = {"calls": n, "meanMs": round(tot / n, 1), "totalMs": round(tot, 1),
                  "p95Ms": a.get("p95Ms")}
    return out

def table(d, title):
    print(f"  {title}")
    if not d:
        print("    (no samples — the stage did not run)")
        return
    rows = sorted(d.items(), key=lambda kv: -kv[1]["meanMs"])
    w = max(len(k) for k, _ in rows)
    for k, v in rows:
        print(f"    {k.ljust(w)}  {v['meanMs']:9.1f} ms  x{v['calls']:<3} "
              f" (sum {v['totalMs']:.1f} ms, lifetime p95 {v['p95Ms']})")

def open_store():
    import store as store_mod
    return store_mod, store_mod.Store(os.environ.get("RWE_DB_URL") or store_mod.default_db_url())

def user_ids(store_mod, st_):
    from sqlalchemy import select as _sel
    with st_.session() as s:
        return list(s.scalars(_sel(store_mod.User.id).order_by(store_mod.User.id)).all())

def user_rows(store_mod, st_):
    """(uid, email, reads) per reader, busiest first — so an operator can recognise themselves
    without already knowing the number this script wants."""
    from sqlalchemy import select as _sel
    with st_.session() as s:
        rows = list(s.execute(_sel(store_mod.User.id, store_mod.User.email)).all())
    return sorted(((u, e or "", st_.count_reads(u)) for u, e in rows), key=lambda r: -r[2])

def resolve_uid(store_mod, st_, uid_arg, email_arg):
    """uid > email > busiest reader. Returns (uid, how) or (None, reason)."""
    if uid_arg:
        return (int(uid_arg), "given") if st_.get_user(int(uid_arg)) else (None, f"no user {uid_arg}")
    rows = user_rows(store_mod, st_)
    if email_arg:
        want = email_arg.strip().lower()
        for u, e, _n in rows:
            if e.strip().lower() == want:
                return u, f"matched {email_arg}"
        return None, f"no user with email {email_arg} (try --users)"
    return (rows[0][0], "busiest reader") if rows else (None, "no users")
PYPRE

say "=================================================================="
say " Recommendation latency probe — $(date -u +%FT%TZ)"
say " container: $API_CONTAINER   repeats: $REPEATS   mode: $MODE"
say "=================================================================="

# ── 0. Is the instrumented build actually the running build? ──────────────────────────────────
say ""
say "[0] Instrumentation + flag state"
api_py <<PY || true
$PREAMBLE
import personalize, evidence_resolver
ok = all(hasattr(personalize, n) for n in ("_stage", "_count", "_log_stages"))
print(f"  stage timers present : {ok}")
print(f"  stage lines reachable: {personalize._logger.isEnabledFor(20)} "
      f"(own handlers: {len(personalize._logger.handlers)})")
print(f"  RWE_STORY_SLOT       : [{os.environ.get('RWE_STORY_SLOT','')}] "
      f"-> enabled={personalize.story_slot_enabled()}")
print(f"  story index TTL      : {evidence_resolver._INDEX_TTL_S}s")
st, health, ms = call("GET", "/api/health")
print(f"  /api/health          : {st} in {ms:.1f} ms")
PY

# ── 1. Candidate readers ──────────────────────────────────────────────────────────────────────
say ""
say "[1] Readers (a model rebuild is proportional to reads x catalog, so the reader matters)"
api_py "${EMAIL_ARG}" <<PY || true
$PREAMBLE
store_mod, st_ = open_store()
rows = user_rows(store_mod, st_)
for uid, email, n in rows[:15]:
    print(f"  uid={uid:<5} reads={n:<6} {email}")
print(f"  catalog articles: {st_.count_feed_articles()}")
chosen, how = resolve_uid(store_mod, st_, "${UID_ARG}", sys.argv[1] if len(sys.argv) > 1 else "")
print(f"  -> this run will use uid={chosen} ({how})")
PY
[ "$MODE" = "users" ] && exit 0

# ── 2. WARM path ──────────────────────────────────────────────────────────────────────────────
# A second identical request with no intervening read. If this is NOT cheap, the cache is not
# holding — which is a different fix from "the rebuild is slow", so the two are measured apart.
say ""
say "[2] WARM path — repeated GET /api/recommendations, no read in between"
api_py "${EMAIL_ARG}" <<PY || true
$PREAMBLE
store_mod, st_ = open_store()
UID, how = resolve_uid(store_mod, st_, "${UID_ARG}", sys.argv[1] if len(sys.argv) > 1 else "")
if UID is None:
    print(f"  !! {how}"); raise SystemExit(0)
print(f"  uid={UID} ({how})")
before_t, before_c = rec_metrics()
walls = []
for i in range($REPEATS):
    st, body, ms = call("GET", "/api/recommendations", uid=UID)
    walls.append(ms)
    print(f"  #{i+1}: {st} {len(body) if isinstance(body, list) else '-'} cards in {ms:.1f} ms")
after_t, after_c = rec_metrics()
print("")
table(delta(before_t, after_t), "stage breakdown for the calls above")
print("  cache decisions:")
for k in sorted(set(after_c) | set(before_c)):
    d = int(after_c.get(k, 0)) - int(before_c.get(k, 0))
    if d:
        print(f"    {k}: +{d}")
print(f"  wall: min {min(walls):.1f} / max {max(walls):.1f} ms")
PY

if [ "$MODE" = "warm" ]; then
  say ""
  say "[3] COLD path — SKIPPED (--warm-only): it requires recording a read."
  say "done."
  exit 0
fi

# ── 3. COLD path — the flow the reader actually experiences ───────────────────────────────────
# One genuinely-new read (a re-post of an already-read URL is a duplicate: the read count does not
# move, the cache key does not move, and nothing rebuilds — so it would measure the warm path
# again while looking like a cold one).
say ""
say "[3] COLD path — POST /api/me/reads (1 NEW read) -> GET /api/recommendations"
say "    NOTE: this writes one read row for the chosen reader."
api_py "${EMAIL_ARG}" <<PY || true
$PREAMBLE
store_mod, st_ = open_store()
UID, how = resolve_uid(store_mod, st_, "${UID_ARG}", sys.argv[1] if len(sys.argv) > 1 else "")
if UID is None:
    print(f"  !! {how} — cannot measure the signed-in path"); raise SystemExit(0)

read_urls = {r.get("article_id") for r in st_.get_reads(UID)}
pick = None
for a in st_.list_feed_articles(limit=400):
    if a.get("canonicalUrl") not in read_urls and a.get("url"):
        pick = a
        break
if pick is None:
    print("  !! every recent catalog article is already read by this user"); raise SystemExit(0)
print(f"  uid={UID} ({how})  reads_before={st_.count_reads(UID)}")
print(f"  article: ...{str(pick['url'])[-42:]}  ({pick.get('publisher')})")

before_t, before_c = rec_metrics()
st, body, read_ms = call("POST", "/api/me/reads", uid=UID, body={"reads": [{
    "url": pick["url"], "title": pick.get("title") or "", "outlet": pick.get("publisher") or "",
    "readSource": "probe"}]})
print(f"  POST /api/me/reads      : {st} in {read_ms:9.1f} ms  {body if st != 200 else ''}")

st, recs, rec_ms = call("GET", "/api/recommendations", uid=UID)
print(f"  GET  /api/recommendations: {st} in {rec_ms:9.1f} ms  "
      f"({len(recs) if isinstance(recs, list) else '-'} cards)")
st, recs2, rec2_ms = call("GET", "/api/recommendations", uid=UID)
print(f"  GET  again (should be warm): {st} in {rec2_ms:9.1f} ms")
print(f"  END-TO-END (read -> recommendations visible): {read_ms + rec_ms:.1f} ms")

after_t, after_c = rec_metrics()
print("")
table(delta(before_t, after_t), "stage breakdown for the flow above")
print("  cache decisions:")
for k in sorted(set(after_c) | set(before_c)):
    d = int(after_c.get(k, 0)) - int(before_c.get(k, 0))
    if d:
        print(f"    {k}: +{d}")
if isinstance(recs, list) and recs:
    top = recs[0].get("article", {})
    print(f"  top card: {str(top.get('publisher') or top.get('outlet') or '?')}"
          f" — strategy={recs[0].get('strategy')}")
PY

# ── 4. The structured stage lines the run just emitted ─────────────────────────────────────────
# The metrics table gives means; these give a single request end to end, which is what separates
# "one stage is slow" from "many stages are moderately slow".
say ""
say "[4] Stage lines from the probe window (rec_model_build / rec_serve / rec_story_slot / handler)"
docker logs "$API_CONTAINER" --since 5m 2>&1 \
  | grep -E '"event": *"(rec_model_build|rec_serve|rec_story_slot|rec_model_lookup|rec_handler_stages)"' \
  | tail -14 || say "  (none captured — see [0]: are the stage lines reachable?)"

# ── 5. Story-view branch accounting ───────────────────────────────────────────────────────────
# Post Boot-P0: a request-path peek miss serves [] and KICKS the single-flighted background
# refresh — `async kicks` is the healthy boot-window signal. An `inline build` can now come ONLY
# from the analyze route (its zero-write contract) or an operator with the cache disabled; inline
# builds attributed to ordinary request traffic would mean the fix is not running.
say ""
say "[5] Story-view branch — lifetime counters"
api_py <<PY || true
$PREAMBLE
_, c = rec_metrics()
hit = int(c.get("story_default_view_peek_hit_total", 0))
inline = int(c.get("story_default_view_inline_build_total", 0))
kicks = int(c.get("story_default_view_async_kick_total", 0))
print(f"  peek hits    : {hit}")
print(f"  async kicks  : {kicks}   (boot-window misses healed in the background)")
print(f"  inline builds: {inline}   (analyze route / cache-disabled ONLY — see header)")
print(f"  stale serves : {c.get('story_stale_served_total', 0)}")
t, _ = rec_metrics()
for k in ("story_default_view_inline_build_ms", "rec_story_index_view_ms",
          "rec_story_index_build_ms", "rec_story_index_hit_ms"):
    v = t.get(k)
    if v:
        print(f"  {k:34s} count={v['count']:<5} avg={v['avgMs']:.1f} ms  max={v['maxMs']:.1f} ms")
PY

say ""
say "done."
