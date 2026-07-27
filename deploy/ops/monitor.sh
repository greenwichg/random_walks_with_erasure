#!/usr/bin/env bash
# Unattended health monitor for the AWS deployment. DEPLOYMENT-ONLY (read-only probes + alert).
#
# Installed as a 5-minute cron by bootstrap-ec2.sh so silent failures reach a human. It probes the OBS1
# endpoints INSIDE the api container (the engine has no host port in production, so a host curl can't reach
# it), and confirms the edge containers are running — a crash-looping web tier shows as not 'running', so
# this also catches the fail-closed crash-loop case (audit finding H4). One aggregated alert per run.
#
#   deploy/ops/monitor.sh          # exit 0 = healthy; non-zero = a problem was detected (and alerted)
#
# Env (deploy/.env): ALERT_WEBHOOK (Slack/Discord-compatible; if unset, problems are only logged).
set -uo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"
[ -f "$ENV_FILE" ] || exit 0        # not configured yet (pre-deploy) — stay quiet

problems=()
note() { echo "monitor: $1" >&2; problems+=("$1"); }

# 1) Edge containers must be running. `dc ps` fails if deploy/.env is not yet filled (the ${VAR:?} guards);
#    treat an empty result as "stack not up".
ps_state="$(dc ps --format '{{.Service}}={{.State}}' 2>/dev/null || true)"
api_up=0
for svc in api web caddy; do
  if printf '%s\n' "$ps_state" | grep -qi "^$svc=running"; then
    [ "$svc" = api ] && api_up=1
  else
    note "container '$svc' is not running"
  fi
done

# 2) Engine liveness + readiness over the internal Docker network (only if api is up).
if [ "$api_up" = 1 ]; then
  dc exec -T api python -c "import urllib.request,sys;sys.exit(0 if 'alive' in urllib.request.urlopen('http://127.0.0.1:8000/api/health/live',timeout=5).read().decode() else 1)" >/dev/null 2>&1 \
    || note "engine liveness (/api/health/live) failed"
  dc exec -T api python -c "import urllib.request,sys
try: sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready',timeout=5).status==200 else 1)
except Exception: sys.exit(1)" >/dev/null 2>&1 \
    || note "engine readiness (/api/health/ready) not 200"
fi

# 3) STORAGE — the failure mode that has no symptom until it is fatal. A full disk stops ingestion,
#    breaks backups, and can corrupt SQLite mid-write, so it is worth alerting on well before that.
#    Thresholds are advisory and env-tunable (deploy/.env): DISK_WARN_PCT, DB_WARN_MB, BACKUPS_WARN_MB.
DISK_WARN="$(env_val DISK_WARN_PCT)"; DISK_WARN="${DISK_WARN:-75}"
DB_WARN="$(env_val DB_WARN_MB)"; DB_WARN="${DB_WARN:-2000}"
BK_WARN="$(env_val BACKUPS_WARN_MB)"; BK_WARN="${BK_WARN:-10000}"
DATA_DIR="$(env_val IH_DATA_DIR)"; DATA_DIR="${DATA_DIR:-/opt/ih/data}"

used_pct="$(df --output=pcent "$DATA_DIR" 2>/dev/null | tail -1 | tr -dc '0-9')"
if [ -n "$used_pct" ] && [ "$used_pct" -ge "$DISK_WARN" ]; then
  note "disk ${used_pct}% used on $DATA_DIR (threshold ${DISK_WARN}%) — see docs/STORAGE_LIFECYCLE.md"
fi

db_mb="$(du -sm "$DATA_DIR"/*.db 2>/dev/null | awk '{s+=$1} END {print s+0}')"
if [ -n "$db_mb" ] && [ "$db_mb" -ge "$DB_WARN" ]; then
  note "database is ${db_mb} MB (threshold ${DB_WARN} MB) — check RWE_RETENTION_MAX_COUNT"
fi

bk_mb="$(du -sm "$DATA_DIR/backups" 2>/dev/null | awk '{print $1+0}')"
bk_n="$(find "$DATA_DIR/backups" -maxdepth 1 -name '*.db' 2>/dev/null | wc -l)"
if [ -n "$bk_mb" ] && [ "$bk_mb" -ge "$BK_WARN" ]; then
  note "backups are ${bk_mb} MB across ${bk_n} file(s) (threshold ${BK_WARN} MB) — is prune-backups.sh running?"
fi
echo "monitor: storage disk=${used_pct:-?}% db=${db_mb:-?}MB backups=${bk_mb:-?}MB/${bk_n:-?} files"

if [ "${#problems[@]}" -gt 0 ]; then
  # One consolidated alert so a full outage doesn't fan out into many messages.
  msg="unhealthy: $(IFS='; '; echo "${problems[*]}")"
  alert "$msg"
  exit 1
fi
echo "monitor: healthy (api + web + caddy running; engine live + ready)"
