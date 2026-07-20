#!/usr/bin/env bash
# Post-deploy smoke test — validate the RUNNING production stack end-to-end. DEPLOYMENT-ONLY (read-only
# probes; no application change). Called automatically by deploy.sh / update.sh / restore.sh, and
# runnable on its own.
#
#   deploy/ops/smoke-test.sh                 # internal (Docker-network) probes + public HTTPS/redirect
#   SMOKE_SKIP_PUBLIC=1 deploy/ops/smoke-test.sh   # internal only (local/CI, or before DNS is live)
#
# Internal probes run INSIDE the api container (the engine has no host port in prod — Caddy reaches it
# only over the private Docker network), so this also confirms that private wiring works.
#
# Exit 0 = all hard checks passed (WARNs allowed); non-zero = at least one FAIL.
set -uo pipefail
source "$(dirname "$0")/_compose.sh"

pass=0 warn=0 fail=0
P() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; pass=$((pass + 1)); }
W() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; warn=$((warn + 1)); }
F() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=$((fail + 1)); }

# Run a python HTTP probe inside the api container; prints the numeric status code (000 on error).
api_code() { # $1 = path, $2 = optional "secret" to send X-IH-Auth from the container's env
  local path="$1" withsecret="${2:-}"
  dc exec -T api python -c "
import urllib.request, os, sys
h = {'X-IH-Auth': os.environ.get('RWE_INTERNAL_SECRET','')} if '${withsecret}' else {}
req = urllib.request.Request('http://127.0.0.1:8000${path}', headers=h)
try:
    print(urllib.request.urlopen(req, timeout=5).status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print('000')
" 2>/dev/null | tr -d '[:space:]'
}

echo "== Information Health — deployment smoke test =="

echo "-- Containers --"
ps_state="$(dc ps --format '{{.Service}}={{.State}}' 2>/dev/null || true)"
for svc in api web caddy; do
  if printf '%s\n' "$ps_state" | grep -qi "^$svc=running"; then P "container '$svc' running"; else F "container '$svc' not running"; fi
done

echo "-- Engine (OBS1, internal over Docker network) --"
if dc exec -T api python -c "import urllib.request,sys;sys.exit(0 if 'alive' in urllib.request.urlopen('http://127.0.0.1:8000/api/health/live',timeout=5).read().decode() else 1)" >/dev/null 2>&1; then
  P "engine liveness (/api/health/live) alive"
else
  F "engine liveness not alive"
fi
[ "$(api_code /api/health/ready)" = "200" ] && P "engine readiness (/api/health/ready) 200" || F "engine readiness not 200"

echo "-- PA1 analytics gating (internal-only) --"
[ "$(api_code /api/analytics/funnel secret)" = "200" ] && P "analytics reachable WITH the internal secret (200)" || W "analytics not 200 with the secret (no data yet is OK pre-traffic)"
[ "$(api_code /api/analytics/funnel)" = "404" ] && P "analytics is internal-only (404 WITHOUT the secret)" || F "analytics NOT gated (expected 404 without the secret — is it exposed?)"

echo "-- OBS1 metrics --"
[ "$(api_code /api/metrics secret)" = "200" ] && P "metrics reachable with the internal secret (200)" || W "metrics not 200 with the secret"

echo "-- Public edge (Caddy / TLS / redirect) --"
if [ "${SMOKE_SKIP_PUBLIC:-0}" = "1" ]; then
  W "SMOKE_SKIP_PUBLIC=1 — skipped public HTTPS checks (internal only)"
else
  DOMAIN="${APP_DOMAIN:-$(env_val APP_DOMAIN)}"
  if [ -z "$DOMAIN" ]; then
    nu="$(env_val NEXTAUTH_URL)"; nu="${nu#*://}"; DOMAIN="${nu%%/*}"   # host from NEXTAUTH_URL
  fi
  DOMAIN="${DOMAIN:-hidden-view.com}"
  echo "   (domain: $DOMAIN)"
  # Valid TLS is implied: curl WITHOUT -k fails on an invalid/absent cert (code 000).
  hcode="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "https://$DOMAIN" 2>/dev/null || echo 000)"
  case "$hcode" in
    200|301|302|307|308) P "https://$DOMAIN reachable over valid TLS (HTTP $hcode)" ;;
    000) F "https://$DOMAIN unreachable / invalid certificate (is DNS live + Caddy up? cert issued?)" ;;
    *) W "https://$DOMAIN returned HTTP $hcode" ;;
  esac
  # HTTP must redirect to HTTPS.
  rcode="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "http://$DOMAIN" 2>/dev/null || echo 000)"
  rloc="$(curl -sS -o /dev/null -D - --max-time 10 "http://$DOMAIN" 2>/dev/null | tr -d '\r' | awk 'tolower($1)=="location:"{print $2}' | head -1)"
  case "$rcode:$rloc" in
    30[128]:https://*) P "http://$DOMAIN → HTTPS redirect ($rcode)" ;;
    *) W "http://$DOMAIN did not clearly redirect to https (code=$rcode loc=${rloc:-none})" ;;
  esac
fi

echo ""
echo "== smoke: $pass PASS, $warn WARN, $fail FAIL =="
[ "$fail" -eq 0 ] || { echo "SMOKE FAILED — resolve the FAILs above." >&2; exit 1; }
echo "SMOKE OK — the running deployment answered on every hard check."
