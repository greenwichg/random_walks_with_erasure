#!/usr/bin/env bash
# BR1 — production health probe. Reuses the OBS1 endpoints only (/api/health/live, /api/health/ready)
# and optionally the web app root. Alerts on failure via a webhook. Drive it from cron/systemd, or
# point an external uptime monitor (Uptime Kuma / BetterStack / Pingdom / a platform probe) at the same
# endpoints — this script is the vendor-neutral fallback that needs no third party.
#
# Operational only: read-only HTTP probes, no application change.
#
# Environment:
#   IH_BASE_URL     ENGINE base URL         (default: http://127.0.0.1:8000)
#   IH_WEB_URL      optional WEB app URL to also probe (its "/" should 200 or redirect)
#   ALERT_WEBHOOK   optional URL to POST a JSON alert to on failure (Slack-compatible: {"text": …})
#
# Exit 0 = healthy; non-zero = unhealthy (and an alert was sent if ALERT_WEBHOOK is set).
set -uo pipefail
BASE="${IH_BASE_URL:-http://127.0.0.1:8000}"

alert() {
  local msg="$1"
  echo "UNHEALTHY: $msg" >&2
  if [ -n "${ALERT_WEBHOOK:-}" ]; then
    curl -fsS -m 10 -X POST -H 'content-type: application/json' \
      -d "{\"text\":\"[Information Health] health check FAILED — $msg\"}" \
      "$ALERT_WEBHOOK" >/dev/null 2>&1 || echo "healthcheck: alert webhook POST failed" >&2
  fi
  exit 1
}

# Liveness — the process is up and serving.
if ! curl -fsS -m 5 "$BASE/api/health/live" 2>/dev/null | grep -q '"status":"alive"'; then
  alert "liveness ($BASE/api/health/live) is not alive"
fi

# Readiness — store + engine built. 200 = ready; 503 = starting/degraded; anything else = down.
ready_body="$(mktemp)"
code="$(curl -s -o "$ready_body" -m 5 -w '%{http_code}' "$BASE/api/health/ready" 2>/dev/null || echo 000)"
if [ "$code" != "200" ]; then
  detail="$(head -c 200 "$ready_body" 2>/dev/null)"; rm -f "$ready_body"
  alert "readiness ($BASE/api/health/ready) returned HTTP $code: $detail"
fi
rm -f "$ready_body"

# Optional: the browser-facing web app answers at all.
if [ -n "${IH_WEB_URL:-}" ]; then
  wcode="$(curl -s -o /dev/null -m 8 -w '%{http_code}' "$IH_WEB_URL" 2>/dev/null || echo 000)"
  case "$wcode" in
    200 | 301 | 302 | 307 | 308) ;;                       # up, or an expected auth redirect
    *) alert "web ($IH_WEB_URL) returned HTTP $wcode" ;;
  esac
fi

echo "healthy: engine live + ready${IH_WEB_URL:+, web reachable}"
