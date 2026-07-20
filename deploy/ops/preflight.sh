#!/usr/bin/env bash
# BR1 — production readiness preflight. A deterministic PASS/WARN/FAIL check of the operational
# prerequisites BEFORE inviting beta users. Run it on the deploy host with the production environment
# loaded (the same env the engine + web app boot with).
#
# Operational only: read-only checks of environment + endpoints. No application change.
#
#   set -a; . ./prod.env; set +a       # load your production env, then:
#   deploy/ops/preflight.sh            # optionally: IH_BASE_URL / IH_WEB_URL to probe live endpoints
#
# Exit 0 = all hard checks PASS (WARNs allowed); non-zero = at least one FAIL — do not launch.
set -uo pipefail

pass=0 warn=0 fail=0
P() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; pass=$((pass + 1)); }
W() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; warn=$((warn + 1)); }
F() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=$((fail + 1)); }
set_len() { v="${!1:-}"; [ -n "$v" ] && [ "${#v}" -ge "${2:-1}" ]; }

echo "== Information Health — production preflight =="

echo "-- Environment & secrets --"
[ "${RWE_ENV:-}" = "production" ] && P "RWE_ENV=production" || F "RWE_ENV must be 'production' (got '${RWE_ENV:-unset}')"
set_len RWE_INTERNAL_SECRET 16 && P "RWE_INTERNAL_SECRET set (≥16 chars)" || F "RWE_INTERNAL_SECRET missing/too short (openssl rand -base64 32)"
set_len NEXTAUTH_SECRET 16 && P "NEXTAUTH_SECRET set (≥16 chars)" || F "NEXTAUTH_SECRET missing/too short (openssl rand -base64 32)"
set_len GOOGLE_CLIENT_ID 1 && P "GOOGLE_CLIENT_ID set" || F "GOOGLE_CLIENT_ID missing (only sign-in method in prod)"
set_len GOOGLE_CLIENT_SECRET 1 && P "GOOGLE_CLIENT_SECRET set" || F "GOOGLE_CLIENT_SECRET missing"
set_len RWE_BACKEND_URL 1 && P "RWE_BACKEND_URL set ($RWE_BACKEND_URL)" || F "RWE_BACKEND_URL missing (web → engine origin)"

echo "-- HTTPS / OAuth --"
case "${NEXTAUTH_URL:-}" in
  https://*) P "NEXTAUTH_URL is https ($NEXTAUTH_URL) → Secure cookies apply" ;;
  "") F "NEXTAUTH_URL missing (OAuth callback + cookie Secure prefix)" ;;
  *) F "NEXTAUTH_URL must be https in production (got '$NEXTAUTH_URL')" ;;
esac

echo "-- Persistent database --"
db="${RWE_DB_URL:-<default data/ih_beta.db>}"
case "${RWE_DB_URL:-}" in
  *":memory:"* | *"mode=memory"*) F "RWE_DB_URL is in-memory — data vanishes on restart ($db)" ;;
  /tmp/* | *"/tmp/"* | sqlite:////tmp/*) F "RWE_DB_URL points at /tmp (ephemeral) ($db)" ;;
  "") W "RWE_DB_URL unset — defaults to data/ih_beta.db; set it explicitly to the persistent volume path" ;;
  *) P "RWE_DB_URL is a persistent path ($db)" ;;
esac

echo "-- Backups --"
bdir="${RWE_BACKUP_DIR:-$(python -c "import sys,os;sys.path.insert(0,'examples');import store;print(store.default_backup_dir(os.environ.get('RWE_DB_URL') or store.default_db_url()))" 2>/dev/null || echo '')}"
if [ -n "$bdir" ] && [ -d "$bdir" ]; then
  newest="$(ls -1t "$bdir"/*.db 2>/dev/null | head -1 || true)"
  if [ -n "$newest" ]; then
    age_h=$(( ( $(date +%s) - $(date -r "$newest" +%s 2>/dev/null || echo 0) ) / 3600 ))
    [ "$age_h" -le 25 ] && P "recent backup exists (${age_h}h old): $newest" || W "newest backup is ${age_h}h old — is the schedule running? ($newest)"
  else
    F "no backups found in $bdir — schedule deploy/ops/backup.sh before launch (blocker B-1)"
  fi
else
  W "backup dir not found yet ($bdir) — will be created on first backup; ensure the schedule is set"
fi
[ -n "${BACKUP_OFFHOST_CMD:-}" ] && P "off-host backup command configured" || W "BACKUP_OFFHOST_CMD unset — a lost volume loses local-only backups (set off-host shipment)"

echo "-- Monitoring --"
[ -n "${ALERT_WEBHOOK:-}" ] && P "ALERT_WEBHOOK configured for healthcheck alerts" || W "ALERT_WEBHOOK unset — wire an external uptime monitor OR set ALERT_WEBHOOK for deploy/ops/healthcheck.sh (blocker B-2)"

echo "-- Health / analytics endpoints (probed only if IH_BASE_URL is set) --"
if [ -n "${IH_BASE_URL:-}" ]; then
  curl -fsS -m 5 "$IH_BASE_URL/api/health/live" 2>/dev/null | grep -q '"status":"alive"' && P "engine liveness ok" || F "engine liveness ($IH_BASE_URL/api/health/live) not alive"
  rc="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$IH_BASE_URL/api/health/ready" 2>/dev/null || echo 000)"
  [ "$rc" = "200" ] && P "engine readiness 200" || F "engine readiness returned $rc"
  # analytics dashboard is internal-only: with the secret → 200; without → 404. Both confirm PA1 wiring.
  ac="$(curl -s -o /dev/null -m 5 -w '%{http_code}' -H "X-IH-Auth: ${RWE_INTERNAL_SECRET:-}" "$IH_BASE_URL/api/analytics/funnel" 2>/dev/null || echo 000)"
  [ "$ac" = "200" ] && P "analytics dashboard reachable with the internal secret" || W "analytics /api/analytics/funnel returned $ac with the secret (expected 200)"
  nc="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$IH_BASE_URL/api/analytics/funnel" 2>/dev/null || echo 000)"
  [ "$nc" = "404" ] && P "analytics dashboard is internal-only (404 without the secret)" || W "analytics dashboard returned $nc without the secret (expected 404 — is it exposed?)"
else
  W "IH_BASE_URL unset — skipped live endpoint probes (run again post-deploy with IH_BASE_URL set)"
fi

echo ""
echo "== preflight: $pass PASS, $warn WARN, $fail FAIL =="
[ "$fail" -eq 0 ] || { echo "NOT READY — resolve the FAILs above before inviting users."; exit 1; }
echo "READY — hard checks pass. Review WARNs; then follow docs/BETA_LAUNCH_CHECKLIST.md."
