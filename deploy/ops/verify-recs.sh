#!/usr/bin/env bash
# Recommendation-deployment verification. DEPLOYMENT-ONLY (read-only: no ingest, no writes, no
# ranking invoked beyond one anonymous showcase probe).
#
#   deploy/ops/verify-recs.sh
#
# One run reports, PASS/WARN/FAIL per line, everything the Tier-1 recommendation deployment
# (docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md) needs verified on the box:
#
#   1. version + containers    — serving commit, container state, RESTART COUNTS (a container
#                                "running" while crash-looping is the failure this catches)
#   2. flag intent vs reality  — the five Tier-1 flags in deploy/.env AND in the running api
#                                container's environment; drift means the container predates the
#                                edit → RESTART NEEDED (deploy/ops/restart.sh api)
#   3. database                — PRAGMA quick_check, row counts for the tables the features read
#                                (feed_articles, users, reads, rec_events, rec_feedback,
#                                story_member), catalog freshness (newest article age). COUNTS
#                                ONLY, never contents.
#   4. engine                  — /api/health (recommendation source, corpus generation),
#                                readiness, and the anonymous showcase feed's shape (cards,
#                                distinct publishers, strategy mix)
#   5. Tier-1 metrics          — the feed_* counters from /api/metrics (internal secret, read
#                                inside the container, value NEVER printed): the quality metrics
#                                are always-on, so feed_served_total advancing while
#                                feed_hhi_bp_total stays absent would mean the new code is not
#                                the code serving
#   6. failure signatures      — story_map_unavailable / rec_reader_state_failed /
#                                country_map_unavailable in recent api logs
#
# Run it once after the dark deploy (flags off: expect flags=0, feed_* counters present, zero
# failure signatures) and again after enabling flags + a signed-in double-load (expect
# feed_repeat_total > 0 for kind=blend). Exit codes: 0 = nothing actionable; 1 = actionable
# problem; 2 = stack not running. Secrets discipline: flag VALUES are 0/1 by construction; the
# internal secret is read only inside the container from its own environment.
set -uo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"

need_env

pass=0; warn=0; fail=0
P() { echo "PASS  $1"; pass=$((pass+1)); }
W() { echo "WARN  $1"; warn=$((warn+1)); }
F() { echo "FAIL  $1"; fail=$((fail+1)); }

if [ -z "$(dc ps -q api 2>/dev/null)" ]; then
  echo "ERROR: the api container is not running — start the stack first." >&2
  exit 2
fi

echo "== verify-recs — Tier-1 recommendation deployment =="
echo "-- 1. version + containers --"
echo "   serving checkout: $(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
for svc in api web caddy; do
  cid="$(dc ps -q "$svc" 2>/dev/null | head -1)"
  if [ -z "$cid" ]; then F "container '$svc' not running"; continue; fi
  rc="$(docker inspect -f '{{.RestartCount}}' "$cid" 2>/dev/null || echo '?')"
  st="$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null || echo '?')"
  if [ "$st" = "running" ] && [ "$rc" = "0" ]; then
    P "container '$svc' running, restart count 0"
  elif [ "$st" = "running" ]; then
    W "container '$svc' running but restart count is $rc — check its logs for a crash loop"
  else
    F "container '$svc' state '$st' (restarts: $rc)"
  fi
done

echo "-- 2. Tier-1 flags: deploy/.env intent vs running container --"
FLAGS="RWE_REC_FEEDBACK RWE_REC_REPETITION RWE_REC_MAX_PER_STORY RWE_REC_MAX_PER_TOPIC RWE_REC_BLINDSPOT"
container_env="$(dc exec -T api sh -lc 'env' 2>/dev/null || true)"
for f in $FLAGS; do
  want="$(env_val "$f")"; want="${want:-unset}"
  have="$(printf '%s\n' "$container_env" | grep -E "^${f}=" | cut -d= -f2- | head -1)"
  have="${have:-unset}"
  if [ "$want" = "$have" ]; then
    P "$f: env-file '$want' == container '$have'"
  elif [ "$want" = "unset" ] && { [ "$have" = "0" ] || [ "$have" = "unset" ]; }; then
    P "$f: unset in deploy/.env, container '$have' (default off)"
  else
    F "$f: deploy/.env says '$want' but the container was started with '$have' — RESTART NEEDED (deploy/ops/restart.sh api)"
  fi
done

echo "-- 3. database (read-only; counts, never contents) --"
db_report="$(dc exec -T api python - <<'PY' 2>/dev/null
import os, re, sqlite3
url = os.environ.get("RWE_DB_URL", "")
m = re.match(r"sqlite:///+(.+)", url)
path = "/" + m.group(1).lstrip("/") if m else "/app/data/ih_beta.db"
try:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=20)
    cur = con.cursor()
    print("integrity", cur.execute("PRAGMA quick_check").fetchone()[0])
    for t in ("feed_articles", "users", "reads", "rec_events", "rec_feedback", "story_member"):
        try:
            print(t, cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
        except sqlite3.Error as e:
            print(t, f"ERR:{type(e).__name__}")
    row = cur.execute("SELECT MAX(COALESCE(published_at, created_at)) FROM feed_articles").fetchone()
    print("newest_article", (row[0] or "none"))
    row = cur.execute(
        "SELECT COUNT(*) FROM feed_articles WHERE COALESCE(published_at, created_at) >= datetime('now', '-1 day')"
    ).fetchone()
    print("articles_last_24h", row[0])
except Exception as e:
    print("db_error", type(e).__name__)
PY
)"
if printf '%s\n' "$db_report" | grep -q "^integrity ok$"; then
  P "SQLite quick_check ok"
else
  F "SQLite quick_check did not answer ok: $(printf '%s\n' "$db_report" | grep '^integrity\|^db_error' | head -1)"
fi
printf '%s\n' "$db_report" | grep -vE "^integrity|^db_error" | sed 's/^/   /'
arts="$(printf '%s\n' "$db_report" | awk '$1=="feed_articles"{print $2}')"
case "$arts" in
  ''|*ERR*) F "feed_articles count unreadable" ;;
  0) F "feed_articles is EMPTY — a fresh database or a broken catalog, not a healthy deploy" ;;
  *) P "catalog present (${arts} articles)" ;;
esac
last24="$(printf '%s\n' "$db_report" | awk '$1=="articles_last_24h"{print $2}')"
case "$last24" in
  ''|*ERR*) W "24h ingestion count unreadable" ;;
  0) W "no articles ingested in the last 24h — check deploy/ops/verify-sources.sh" ;;
  *) P "ingestion live (${last24} articles in the last 24h)" ;;
esac

echo "-- 4. engine + showcase feed shape --"
probe() { # $1 = path, $2 = nonempty → send internal secret
  dc exec -T api python -c "
import urllib.request, os, json, sys
h = {'X-IH-Auth': os.environ.get('RWE_INTERNAL_SECRET','')} if '${2:-}' else {}
req = urllib.request.Request('http://127.0.0.1:8000${1}', headers=h)
try:
    r = urllib.request.urlopen(req, timeout=30)
    sys.stdout.write(r.read().decode())
except urllib.error.HTTPError as e:
    sys.stdout.write('HTTP:%d' % e.code)
except Exception as e:
    sys.stdout.write('EXC:' + type(e).__name__)
" 2>/dev/null
}
health="$(probe /api/health)"
if printf '%s' "$health" | grep -q '"ok": *true'; then
  src="$(printf '%s' "$health" | python3 -c "import sys,json;d=json.load(sys.stdin);r=d.get('recommendationSource') or {};print(r.get('source','?'), 'gen', r.get('generation','?'), 'feedArticles', r.get('feedArticles','?'))" 2>/dev/null || echo unparsed)"
  P "engine /api/health ok (recommendation source: $src)"
else
  F "engine /api/health not ok: $(printf '%.60s' "$health")"
fi
feed="$(probe /api/recommendations)"
feed_shape="$(printf '%s' "$feed" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('unparseable'); raise SystemExit
pubs = {(r.get('article') or {}).get('publisher') for r in d}
strats = {}
for r in d:
    strats[r.get('strategy','?')] = strats.get(r.get('strategy','?'), 0) + 1
print(len(d), 'cards;', len(pubs), 'publishers;', 'strategies', dict(sorted(strats.items())))" 2>/dev/null)"
case "$feed_shape" in
  unparseable|'') F "anonymous showcase feed unreadable: $(printf '%.40s' "$feed")" ;;
  0\ *) W "showcase feed served 0 cards" ;;
  *) P "showcase feed: $feed_shape" ;;
esac

echo "-- 5. Tier-1 feed-quality counters (names + values; internal, in-container) --"
metrics="$(probe /api/metrics secret)"
counters="$(printf '%s' "$metrics" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('unparseable'); raise SystemExit
c = d.get('counters') or {}
rows = sorted((k, v) for k, v in c.items() if k.startswith('feed_'))
if not rows:
    print('none')
for k, v in rows:
    print(f'{k} {v}')" 2>/dev/null)"
if [ "$counters" = "unparseable" ] || [ -z "$counters" ]; then
  W "metrics endpoint unreadable (is RWE_INTERNAL_SECRET set for this shell's container?)"
elif [ "$counters" = "none" ]; then
  W "no feed_* counters yet — no feed served since the api container started; load a feed and re-run"
else
  printf '%s\n' "$counters" | sed 's/^/   /'
  if printf '%s\n' "$counters" | grep -q "^feed_hhi_bp_total"; then
    P "Tier-1 quality counters live (feed_hhi_bp_total present — the new code is the code serving)"
  else
    F "feeds served but NO feed_hhi_bp_total — the running image predates the Tier-1 metrics"
  fi
fi

echo "-- 6. failure signatures in recent api logs --"
logs="$(dc logs --tail 2000 api 2>/dev/null || true)"
for sig in story_map_unavailable country_map_unavailable rec_reader_state_failed rec_shown_record_failed; do
  n="$(printf '%s\n' "$logs" | grep -c "$sig" || true)"
  if [ "${n:-0}" -eq 0 ]; then P "no '$sig' in last 2000 api log lines"; else F "'$sig' appears ${n}x in recent api logs"; fi
done

echo ""
echo "== verify-recs: $pass PASS, $warn WARN, $fail FAIL =="
[ "$fail" -eq 0 ] || { echo "VERIFY FAILED — resolve the FAILs above." >&2; exit 1; }
echo "VERIFY OK"
