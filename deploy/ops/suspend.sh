#!/usr/bin/env bash
# Suspend the application cleanly, BEFORE the instance is stopped. DEPLOYMENT-ONLY.
#
#   deploy/ops/suspend.sh
#
# This is the host half of the suspend workflow (.github/workflows/suspend.yml); the workflow issues
# the `aws ec2 stop-instances` afterwards. Split that way on purpose: everything that touches data
# happens here, where the app lives, and the only AWS control-plane call lives in the workflow, where
# the short-lived OIDC role lives.
#
# Order matters and is the whole safety argument:
#   1. FINAL BACKUP + integrity check + off-host copy to S3 — taken while the engine is still up, via
#      the same consistent-online path the hourly cron uses. If this fails we abort and stay serving:
#      never suspend a system whose last known-good backup failed.
#   2. GRACEFUL STOP of the containers (`compose stop`, not `down`) — SQLite closes cleanly and the WAL
#      is checkpointed before the disk goes cold. Containers, volumes, networks, and the Caddy
#      certificate store all survive untouched; only the processes end.
#   3. A marker file recording who/when/what commit, so resume can report the suspend window and an
#      operator can tell "deliberately suspended" from "crashed".
#
# IDEMPOTENT: running it twice is harmless (the second run backs up again and finds the stack already
# stopped). Data is never moved, copied out, or deleted — the database stays exactly where it is on
# the instance's root volume, which is preserved because the instance is STOPPED, never terminated.
set -uo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"

need_env

DATA_DIR="$(env_val IH_DATA_DIR)"; DATA_DIR="${DATA_DIR:-/opt/ih/data}"
MARKER="$DATA_DIR/.suspended"

echo "== suspend: final backup + integrity check + off-host copy =="
if [ -n "$(env_val IH_S3_BUCKET)" ]; then
  "$OPS_DIR/backup-offhost.sh" --backup-now || {
    echo "SUSPEND_RESULT=aborted reason=backup_failed" >&2
    alert "suspend ABORTED: the pre-suspend backup/integrity check failed — still serving, nothing was stopped"
    exit 1
  }
else
  backup_run backup || {
    echo "SUSPEND_RESULT=aborted reason=backup_failed" >&2
    alert "suspend ABORTED: the pre-suspend backup failed — still serving, nothing was stopped"
    exit 1
  }
  echo "suspend: NOTE IH_S3_BUCKET is unset — the backup is local-only (on the preserved root volume)."
fi

echo "== suspend: stopping the stack gracefully (containers stop; data/volumes/certs stay) =="
dc stop || { echo "SUSPEND_RESULT=aborted reason=stop_failed" >&2; exit 1; }

printf 'suspendedAt=%s\ncommit=%s\nby=%s\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(git rev-parse HEAD 2>/dev/null || echo unknown)" \
  "${SUSPEND_ACTOR:-manual}" > "$MARKER" 2>/dev/null || true

dc ps
echo ""
echo "suspend: application stopped cleanly. The DATABASE IS INTACT on the root volume."
echo "suspend: the instance itself is still running — the workflow stops it next."
echo "SUSPEND_RESULT=ready"
